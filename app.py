"""
照鉴 (Zhaojian) — Agent + Skills Architecture
Container → Thread → Messages with dynamic skill selection.
Per-entry Derived State extraction via combined output.
User-directed entry-level context pinning.
"""
import os, json, uuid, sqlite3, hashlib, secrets
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, Response, send_file, jsonify, session
import requests as http_requests

from skills import select_skills, build_system_prompt, build_query_prompt, build_synthesis_prompt, SKILLS, _count_matches, HEDGE_PATTERNS, COUNTABLE_PATTERNS

# Negation words for server-side text analysis
_NEGATION_PATTERNS = [r"不[^，。？！\s]{0,4}", r"没有?", r"无法", r"别", r"勿", r"未"]
# Pivot words for server-side text analysis (subset of COUNTABLE_PATTERNS)
_PIVOT_PATTERNS = [r"但是", r"可是", r"不过", r"然而", r"却"]

app = Flask(__name__)
app.secret_key = os.environ.get("ZHAOJIAN_SECRET", secrets.token_hex(32))
db_dir = os.environ.get("DB_DIR", Path(__file__).parent)
DB_PATH = Path(db_dir) / "zhaojian.db"
INVITE_CODE = os.environ.get("ZHAOJIAN_INVITE", "zhaojian2026")
SHARED_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
CONTEXT_ID_MK = "---CONTEXT_ID---"

def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, api_key TEXT DEFAULT '', created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            description TEXT DEFAULT '', created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS threads (
            id TEXT PRIMARY KEY, container_id TEXT NOT NULL, user_id TEXT NOT NULL,
            title TEXT NOT NULL, type TEXT NOT NULL DEFAULT 'reflect',
            created TEXT NOT NULL, updated TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, thinking TEXT, skills_used TEXT,
            timestamp TEXT NOT NULL
        );
    """)
    migrations = [
        "ALTER TABLE messages ADD COLUMN skills_used TEXT",
        "ALTER TABLE containers ADD COLUMN patterns TEXT DEFAULT '{}'",
        "ALTER TABLE messages ADD COLUMN derived_state TEXT",
        "ALTER TABLE messages ADD COLUMN msg_type TEXT DEFAULT 'reflect'",
    ]
    for m in migrations:
        try:
            db.execute(m)
        except sqlite3.OperationalError:
            pass
    db.commit(); db.close()

init_db()
hash_pw = lambda p: hashlib.sha256(p.encode()).hexdigest()

def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session: return jsonify({"error": "Not authenticated"}), 401
        return f(*a, **kw)
    return d

def uid(): return session.get("user_id")


# === Auth ===
@app.route("/")
def index():
    return send_file("login.html") if "user_id" not in session else send_file("index.html")

@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.json; u = d.get("username","").strip(); p = d.get("password","").strip()
    if not u or not p: return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(p) < 4: return jsonify({"error": "密码至少4位"}), 400
    if d.get("invite_code","").strip() != INVITE_CODE: return jsonify({"error": "邀请码无效"}), 403
    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone():
        db.close(); return jsonify({"error": "用户名已存在"}), 409
    user_id = uuid.uuid4().hex[:12]
    db.execute("INSERT INTO users VALUES (?,?,?,?,?)", (user_id, u, hash_pw(p), "", datetime.now().isoformat()))
    db.commit(); db.close()
    session["user_id"] = user_id; session["username"] = u
    return jsonify({"status": "ok", "username": u})

@app.route("/api/auth/login", methods=["POST"])
def login():
    d = request.json; db = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (d.get("username",""),)).fetchone()
    db.close()
    if not user or user["password_hash"] != hash_pw(d.get("password","")): return jsonify({"error": "用户名或密码错误"}), 401
    session["user_id"] = user["id"]; session["username"] = user["username"]
    return jsonify({"status": "ok", "username": user["username"]})

@app.route("/api/auth/logout", methods=["POST"])
def logout(): session.clear(); return jsonify({"status": "ok"})

@app.route("/api/auth/me")
def me():
    if "user_id" not in session: return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "username": session.get("username")})

# === Config ===
@app.route("/api/backup/<code>")
def backup_db(code):
    if code != INVITE_CODE: return jsonify({"error": "Invalid code"}), 403
    if not DB_PATH.exists(): return jsonify({"error": "No database found"}), 404
    return send_file(str(DB_PATH), as_attachment=True, download_name="zhaojian_backup.db")

@app.route("/api/config", methods=["GET","POST"])
@login_required
def config():
    db = get_db()
    if request.method == "POST":
        db.execute("UPDATE users SET api_key=? WHERE id=?", (request.json.get("api_key",""), uid()))
        db.commit(); db.close(); return jsonify({"status": "ok"})
    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone(); db.close()
    return jsonify({"has_own_key": bool(user["api_key"]) if user else False, "has_shared_key": bool(SHARED_API_KEY)})

@app.route("/api/skills", methods=["GET"])
@login_required
def list_skills():
    return jsonify({"skills": [
        {"id": s.id, "name": s.name, "label": s.label, "description": s.description}
        for s in SKILLS.values() if s.id != "distress"
    ]})

# === Derived State Query ===
@app.route("/api/containers/<cid>/states", methods=["GET"])
@login_required
def container_states(cid):
    db = get_db()
    c = db.execute("SELECT * FROM containers WHERE id=? AND user_id=?", (cid, uid())).fetchone()
    if not c: db.close(); return jsonify({"error": "Not found"}), 404
    threads = db.execute("SELECT id FROM threads WHERE container_id=? AND user_id=?", (cid, uid())).fetchall()
    thread_ids = [t["id"] for t in threads]
    if not thread_ids: db.close(); return jsonify({"states": []})
    placeholders = ",".join("?" * len(thread_ids))
    rows = db.execute(f"""
        SELECT m.id, m.thread_id, m.content, m.derived_state, m.timestamp
        FROM messages m WHERE m.thread_id IN ({placeholders}) AND m.role='user' AND m.derived_state IS NOT NULL
        ORDER BY m.timestamp ASC
    """, thread_ids).fetchall()
    db.close()
    states = []
    for r in rows:
        try: ds = json.loads(r["derived_state"])
        except (json.JSONDecodeError, TypeError): continue
        states.append({"message_id": r["id"], "thread_id": r["thread_id"], "timestamp": r["timestamp"], "preview": r["content"][:60], **ds})
    return jsonify({"states": states})

# === Container Trends (computed from derived states) ===
@app.route("/api/containers/<cid>/trends", methods=["GET"])
@login_required
def container_trends(cid):
    """Compute aggregate trends from derived states for dashboard display."""
    db = get_db()
    c = db.execute("SELECT * FROM containers WHERE id=? AND user_id=?", (cid, uid())).fetchone()
    if not c: db.close(); return jsonify({"error": "Not found"}), 404
    threads = db.execute("SELECT id FROM threads WHERE container_id=? AND user_id=?", (cid, uid())).fetchall()
    thread_ids = [t["id"] for t in threads]
    if not thread_ids: db.close(); return jsonify({"trends": None, "reason": "no_threads"})
    placeholders = ",".join("?" * len(thread_ids))
    rows = db.execute(f"""
        SELECT m.id, m.thread_id, m.content, m.derived_state, m.timestamp
        FROM messages m WHERE m.thread_id IN ({placeholders}) AND m.role='user'
        ORDER BY m.timestamp ASC
    """, thread_ids).fetchall()
    db.close()

    # Parse all derived states and compute text-based signals from raw content
    entries = []
    for r in rows:
        ds = None
        if r["derived_state"]:
            try: ds = json.loads(r["derived_state"])
            except (json.JSONDecodeError, TypeError): pass
        text = r["content"] or ""
        wc = max(len(text), 1)
        entries.append({
            "timestamp": r["timestamp"],
            "has_state": ds is not None,
            "derived": ds,
            "word_count": wc,
            # Always computed server-side from raw text for reliable timeline
            "hedge_ratio": round(_count_matches(text, HEDGE_PATTERNS) / wc, 4),
            "negation_ratio": round(_count_matches(text, _NEGATION_PATTERNS) / wc, 4),
            "pivot_ratio": round(_count_matches(text, _PIVOT_PATTERNS) / wc, 4),
        })

    total_entries = len(entries)
    entries_with_state = [e for e in entries if e["has_state"]]

    if not entries:
        return jsonify({"trends": None, "reason": "no_derived_states", "total_entries": total_entries})

    # 1. Language signal timeline — uses ALL entries (computed from raw text, no LLM required)
    language_timeline = [
        {
            "timestamp": e["timestamp"],
            "hedge_ratio": e["hedge_ratio"],
            "negation_ratio": e["negation_ratio"],
            "pivot_ratio": e["pivot_ratio"],
        }
        for e in entries
    ]

    # 2. Affect timeline (from AI-extracted valence/intensity in derived state)
    affect_timeline = []
    for e in entries_with_state:
        ds = e["derived"]
        v = ds.get("affect_valence")
        i = ds.get("affect_intensity")
        if v is not None and i is not None:
            try:
                affect_timeline.append({
                    "timestamp": e["timestamp"],
                    "valence": float(v),
                    "intensity": float(i),
                })
            except (TypeError, ValueError):
                pass

    # 3. High-frequency word distribution (aggregate across entries)
    word_counts = {}
    for e in entries_with_state:
        for word in (e["derived"].get("high_frequency_words") or []):
            word = word.strip()
            if word: word_counts[word] = word_counts.get(word, 0) + 1
    word_freq = sorted(word_counts.items(), key=lambda x: -x[1])[:15]

    # 4. Syntactic signal frequency (aggregate across entries)
    signal_counts = {}
    for e in entries_with_state:
        for sig in (e["derived"].get("syntactic_signals") or []):
            sig = sig.strip()
            if sig: signal_counts[sig] = signal_counts.get(sig, 0) + 1
    signal_freq = sorted(signal_counts.items(), key=lambda x: -x[1])[:10]

    # 5. Salience markers (collect unique, recent first)
    salience = []
    seen = set()
    for e in reversed(entries_with_state):
        for m in (e["derived"].get("salience_markers") or []):
            m_lower = m.strip().lower()
            if m_lower and m_lower not in seen:
                seen.add(m_lower)
                salience.append(m.strip())
            if len(salience) >= 12: break
        if len(salience) >= 12: break

    # 6. Aggregate stats (from raw-text computed ratios across all entries)
    avg_hedge_ratio = sum(e["hedge_ratio"] for e in entries) / len(entries) if entries else 0
    avg_negation_ratio = sum(e["negation_ratio"] for e in entries) / len(entries) if entries else 0
    avg_pivot_ratio = sum(e["pivot_ratio"] for e in entries) / len(entries) if entries else 0

    # 7. Entry activity (entries per day for the activity heatmap)
    day_counts = {}
    for e in entries:
        day = e["timestamp"][:10]
        day_counts[day] = day_counts.get(day, 0) + 1

    return jsonify({
        "trends": {
            "total_entries": total_entries,
            "entries_with_state": len(entries_with_state),
            "affect_timeline": affect_timeline,
            "language_timeline": language_timeline,
            "avg_hedge_ratio": round(avg_hedge_ratio, 4),
            "avg_negation_ratio": round(avg_negation_ratio, 4),
            "avg_pivot_ratio": round(avg_pivot_ratio, 4),
            "word_freq": [{"word": w, "count": c} for w, c in word_freq],
            "signal_freq": [{"signal": s, "count": c} for s, c in signal_freq],
            "salience_markers": salience,
            "activity": [{"date": d, "count": c} for d, c in sorted(day_counts.items())],
        }
    })


# === Cross-Container Synthesis ===
@app.route("/api/synthesis", methods=["GET"])
@login_required
def synthesis():
    """Aggregate data across all user containers for cross-container analysis."""
    db = get_db()
    containers = db.execute("SELECT * FROM containers WHERE user_id=? ORDER BY created ASC", (uid(),)).fetchall()
    if not containers:
        db.close()
        return jsonify({"containers": [], "cross": {"total_entries": 0, "shared_words": [], "shared_signals": [], "temporal_overlaps": []}})

    container_data = []
    all_word_map = {}    # word -> {container_name: count}
    all_signal_map = {}  # signal -> {container_name: count}
    all_dates_map = {}   # date -> [container_names]
    total_entries = 0

    for c in containers:
        threads = db.execute("SELECT id FROM threads WHERE container_id=? AND user_id=?", (c["id"], uid())).fetchall()
        thread_ids = [t["id"] for t in threads]
        if not thread_ids:
            container_data.append({
                "id": c["id"], "name": c["name"], "entry_count": 0,
                "patterns": {}, "avg_hedge_ratio": 0, "avg_negation_ratio": 0,
                "top_words": [], "top_signals": [], "date_range": []
            })
            continue

        placeholders = ",".join("?" * len(thread_ids))
        rows = db.execute(f"""
            SELECT m.derived_state, m.timestamp FROM messages m
            WHERE m.thread_id IN ({placeholders}) AND m.role='user'
            ORDER BY m.timestamp ASC
        """, thread_ids).fetchall()

        entry_count = len(rows)
        total_entries += entry_count
        hedge_ratios, negation_ratios = [], []
        word_counts, signal_counts = {}, {}
        dates = []

        for r in rows:
            dates.append(r["timestamp"][:10])
            if not r["derived_state"]:
                continue
            try:
                ds = json.loads(r["derived_state"])
            except (json.JSONDecodeError, TypeError):
                continue
            wc = ds.get("word_count") or 1
            hc = ds.get("hedge_count")
            nc = ds.get("negation_count")
            if isinstance(hc, (int, float)):
                hedge_ratios.append(float(hc) / max(int(wc), 1))
            if isinstance(nc, (int, float)):
                negation_ratios.append(float(nc) / max(int(wc), 1))
            for word in (ds.get("high_frequency_words") or []):
                word = word.strip()
                if word:
                    word_counts[word] = word_counts.get(word, 0) + 1
                    all_word_map.setdefault(word, {})[c["name"]] = all_word_map.get(word, {}).get(c["name"], 0) + 1
            for sig in (ds.get("syntactic_signals") or []):
                sig = sig.strip()
                if sig:
                    signal_counts[sig] = signal_counts.get(sig, 0) + 1
                    all_signal_map.setdefault(sig, {})[c["name"]] = all_signal_map.get(sig, {}).get(c["name"], 0) + 1
            for d in dates:
                all_dates_map.setdefault(d, set()).add(c["name"])

        patterns = {}
        if c["patterns"] and c["patterns"] != '{}':
            try:
                patterns = json.loads(c["patterns"])
            except (json.JSONDecodeError, TypeError):
                pass

        avg_h = round(sum(hedge_ratios) / len(hedge_ratios), 4) if hedge_ratios else 0
        avg_n = round(sum(negation_ratios) / len(negation_ratios), 4) if negation_ratios else 0
        top_words = [w for w, _ in sorted(word_counts.items(), key=lambda x: -x[1])[:5]]
        top_signals = [s for s, _ in sorted(signal_counts.items(), key=lambda x: -x[1])[:5]]
        date_range = [dates[0], dates[-1]] if dates else []

        container_data.append({
            "id": c["id"], "name": c["name"], "entry_count": entry_count,
            "patterns": patterns, "avg_hedge_ratio": avg_h, "avg_negation_ratio": avg_n,
            "top_words": top_words, "top_signals": top_signals,
            "date_range": date_range
        })

    # Cross-container intersections
    shared_words = []
    for word, cmap in all_word_map.items():
        if len(cmap) >= 2:
            containers_list = sorted(cmap.keys())
            counts = [cmap[cn] for cn in containers_list]
            shared_words.append({"word": word, "containers": containers_list, "counts": counts})
    shared_words.sort(key=lambda x: -sum(x["counts"]))

    shared_signals = []
    for sig, cmap in all_signal_map.items():
        if len(cmap) >= 2:
            containers_list = sorted(cmap.keys())
            counts = [cmap[cn] for cn in containers_list]
            shared_signals.append({"signal": sig, "containers": containers_list, "counts": counts})
    shared_signals.sort(key=lambda x: -sum(x["counts"]))

    temporal_overlaps = []
    for date, cnames in sorted(all_dates_map.items()):
        if len(cnames) >= 2:
            temporal_overlaps.append({"date": date, "containers": sorted(cnames)})

    db.close()
    return jsonify({
        "containers": container_data,
        "cross": {
            "total_entries": total_entries,
            "shared_words": shared_words[:20],
            "shared_signals": shared_signals[:10],
            "temporal_overlaps": temporal_overlaps[-20:]
        }
    })


@app.route("/api/synthesis/analyze", methods=["POST"])
@login_required
def synthesis_analyze():
    """AI-driven cross-container narrative synthesis via streaming SSE."""
    db = get_db()
    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone()
    containers = db.execute("SELECT id, name, description, patterns FROM containers WHERE user_id=?", (uid(),)).fetchall()
    db.close()

    api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY
    if not api_key:
        def err():
            yield f"data: {json.dumps({'type':'error','text':'没有可用的API Key。'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    # Build context from container pattern archives
    context_parts = []
    has_patterns = False
    for c in containers:
        pat = c["patterns"] if c["patterns"] and c["patterns"] != '{}' else None
        if pat:
            has_patterns = True
        context_parts.append(
            f"=== 容器: {c['name']} ===\n"
            f"描述: {c['description'] or '无'}\n"
            f"模式档案: {pat or '（尚无数据）'}\n"
        )

    if not has_patterns:
        def no_data():
            yield f"data: {json.dumps({'type':'error','text':'容器中尚无足够的模式数据进行综合分析。请先在多个容器中写几篇审视类日记。'})}\n\n"
        return Response(no_data(), mimetype="text/event-stream")

    system_prompt = build_synthesis_prompt()
    user_content = "\n".join(context_parts)
    api_msgs = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]

    def generate():
        try:
            resp = http_requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-reasoner", "messages": api_msgs, "stream": True},
                stream=True, timeout=240)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"
                return
            think, content = [], []
            in_content = False
            for line in resp.iter_lines():
                if not line:
                    continue
                dec = line.decode("utf-8")
                if not dec.startswith("data: "):
                    continue
                pay = dec[6:]
                if pay == "[DONE]":
                    yield f"data: {json.dumps({'type':'done'})}\n\n"
                    break
                try:
                    ch = json.loads(pay)
                    delta = ch.get("choices", [{}])[0].get("delta", {})
                    if rc := delta.get("reasoning_content"):
                        think.append(rc)
                        yield f"data: {json.dumps({'type':'thinking','text':rc})}\n\n"
                    if ct := delta.get("content"):
                        content.append(ct)
                        if not in_content:
                            in_content = True
                        yield f"data: {json.dumps({'type':'content','text':ct})}\n\n"
                except:
                    pass
        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'type':'error','text':'Timeout'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


# === Container Entries Browser (for entry-level pinning) ===
@app.route("/api/containers/<cid>/entries", methods=["GET"])
@login_required
def container_entries(cid):
    """Return all user entries in a container for the context picker."""
    db = get_db()
    c = db.execute("SELECT * FROM containers WHERE id=? AND user_id=?", (cid, uid())).fetchone()
    if not c: db.close(); return jsonify({"error": "Not found"}), 404
    exclude_tid = request.args.get("exclude_thread")
    threads = db.execute("SELECT id, title, created FROM threads WHERE container_id=? AND user_id=? ORDER BY updated DESC", (cid, uid())).fetchall()
    thread_map = {t["id"]: t for t in threads}
    thread_ids = [t["id"] for t in threads]
    if exclude_tid and exclude_tid in thread_ids:
        thread_ids = [tid for tid in thread_ids if tid != exclude_tid]
    if not thread_ids: db.close(); return jsonify({"entries": []})
    placeholders = ",".join("?" * len(thread_ids))
    rows = db.execute(f"""
        SELECT m.id, m.thread_id, m.content, m.derived_state, m.timestamp
        FROM messages m WHERE m.thread_id IN ({placeholders}) AND m.role='user'
        ORDER BY m.timestamp DESC
    """, thread_ids).fetchall()
    entries = []
    for r in rows:
        t = thread_map.get(r["thread_id"])
        ds = None
        if r["derived_state"]:
            try: ds = json.loads(r["derived_state"])
            except (json.JSONDecodeError, TypeError): pass
        asst = db.execute(
            "SELECT content FROM messages WHERE thread_id=? AND role='assistant' AND timestamp > ? ORDER BY timestamp ASC LIMIT 1",
            (r["thread_id"], r["timestamp"])).fetchone()
        entries.append({
            "message_id": r["id"], "thread_id": r["thread_id"],
            "thread_title": t["title"] if t else "", "timestamp": r["timestamp"],
            "preview": r["content"][:100], "observation": asst["content"] if asst else None,
            "derived_state": ds,
        })
    db.close()
    return jsonify({"entries": entries})

# === Containers ===
@app.route("/api/containers", methods=["GET"])
@login_required
def list_containers():
    db = get_db()
    cs = db.execute("SELECT * FROM containers WHERE user_id=? ORDER BY created DESC", (uid(),)).fetchall()
    result = []
    for c in cs:
        n = db.execute("SELECT COUNT(*) as n FROM threads WHERE container_id=?", (c["id"],)).fetchone()["n"]
        result.append({**dict(c), "thread_count": n})
    db.close(); return jsonify({"containers": result})

@app.route("/api/containers", methods=["POST"])
@login_required
def create_container():
    d = request.json; cid = uuid.uuid4().hex[:8]; now = datetime.now().isoformat()
    db = get_db()
    db.execute("INSERT INTO containers(id, user_id, name, description, created, patterns) VALUES (?,?,?,?,?,?)",
        (cid, uid(), d["name"], d.get("description",""), now, "{}"))
    db.commit(); db.close()
    return jsonify({"id": cid, "name": d["name"], "description": d.get("description",""), "created": now, "thread_count": 0})

@app.route("/api/containers/<cid>", methods=["DELETE"])
@login_required
def delete_container(cid):
    db = get_db()
    for t in db.execute("SELECT id FROM threads WHERE container_id=? AND user_id=?", (cid, uid())).fetchall():
        db.execute("DELETE FROM messages WHERE thread_id=?", (t["id"],))
    db.execute("DELETE FROM threads WHERE container_id=? AND user_id=?", (cid, uid()))
    db.execute("DELETE FROM containers WHERE id=? AND user_id=?", (cid, uid()))
    db.commit(); db.close(); return jsonify({"status": "ok"})

# === Threads ===
@app.route("/api/containers/<cid>/threads", methods=["GET"])
@login_required
def list_threads(cid):
    db = get_db()
    ts = db.execute("SELECT * FROM threads WHERE container_id=? AND user_id=? ORDER BY updated DESC", (cid, uid())).fetchall()
    result = []
    for t in ts:
        n = db.execute("SELECT COUNT(*) as n FROM messages WHERE thread_id=?", (t["id"],)).fetchone()["n"]
        first = db.execute("SELECT content FROM messages WHERE thread_id=? ORDER BY timestamp ASC LIMIT 1", (t["id"],)).fetchone()
        result.append({**dict(t), "msg_count": n, "preview": first["content"][:80] if first else ""})
    db.close(); return jsonify({"threads": result})

@app.route("/api/containers/<cid>/threads", methods=["POST"])
@login_required
def create_thread(cid):
    d = request.json; tid = uuid.uuid4().hex[:8]; now = datetime.now().isoformat()
    text = d["text"]; ttype = d.get("type", "reflect")
    title = text[:40] + ("..." if len(text) > 40 else "")
    db = get_db()
    db.execute("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", (tid, cid, uid(), title, ttype, now, now))
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", text, None, None, now, None, ttype))
    if ttype == "vent":
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "assistant", "已记录。", None, None, now, None, "vent"))
        db.commit(); db.close(); return jsonify({"thread_id": tid, "stream": False})
    db.commit(); db.close()
    return jsonify({"thread_id": tid, "stream": True})

@app.route("/api/threads/<tid>", methods=["GET"])
@login_required
def get_thread(tid):
    db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error": "Not found"}), 404
    msgs = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC", (tid,)).fetchall()
    db.close()
    return jsonify({"thread": dict(t), "messages": [{**dict(m), "skills_used": m["skills_used"], "derived_state": m["derived_state"], "msg_type": m["msg_type"]} for m in msgs]})

@app.route("/api/threads/<tid>", methods=["DELETE"])
@login_required
def delete_thread(tid):
    db = get_db()
    db.execute("DELETE FROM messages WHERE thread_id=?", (tid,))
    db.execute("DELETE FROM threads WHERE id=? AND user_id=?", (tid, uid()))
    db.commit(); db.close(); return jsonify({"status": "ok"})

# === Reply ===
@app.route("/api/messages/<mid>", methods=["DELETE"])
@login_required
def delete_message(mid):
    db = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
    if not msg: db.close(); return jsonify({"error": "Not found"}), 404
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (msg["thread_id"], uid())).fetchone()
    if not t: db.close(); return jsonify({"error": "Not authorized"}), 403
    if msg["role"] != "user": db.close(); return jsonify({"error": "只能删除自己的日记"}), 400
    next_asst = db.execute(
        "SELECT id FROM messages WHERE thread_id=? AND role='assistant' AND timestamp > ? ORDER BY timestamp ASC LIMIT 1",
        (msg["thread_id"], msg["timestamp"])).fetchone()
    db.execute("DELETE FROM messages WHERE id=?", (mid,))
    if next_asst: db.execute("DELETE FROM messages WHERE id=?", (next_asst["id"],))
    remaining = db.execute("SELECT COUNT(*) as n FROM messages WHERE thread_id=?", (msg["thread_id"],)).fetchone()["n"]
    deleted_thread = False
    if remaining == 0:
        db.execute("DELETE FROM threads WHERE id=?", (msg["thread_id"],))
        deleted_thread = True
    db.commit(); db.close()
    return jsonify({"status": "ok", "thread_deleted": deleted_thread})

@app.route("/api/threads/<tid>/reply", methods=["POST"])
@login_required
def reply(tid):
    d = request.json; now = datetime.now().isoformat(); db = get_db()
    msg_type = d.get("type", "reflect")
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error": "Not found"}), 404
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", d["text"], None, None, now, None, msg_type))
    db.execute("UPDATE threads SET updated=? WHERE id=?", (now, tid))
    if msg_type == "vent":
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "assistant", "已记录。", None, None, now, None, "vent"))
        db.commit(); db.close()
        return jsonify({"stream": False})
    db.commit(); db.close()
    return jsonify({"stream": True})

# === Observe (stream) with Skill Selection ===
@app.route("/api/threads/<tid>/observe", methods=["POST"])
@login_required
def observe(tid):
    d = request.json or {}
    pinned_context_id = d.get("pinned_context_id")
    pinned_entry_ids = d.get("pinned_entry_ids") or []

    db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error":"Not found"}), 404
    container = db.execute("SELECT * FROM containers WHERE id=?", (t["container_id"],)).fetchone()
    msgs = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC", (tid,)).fetchall()
    container_patterns = container["patterns"] if container["patterns"] and container["patterns"] != '{}' else None
    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone()

    # Detect last user message's type (per-message, not per-thread)
    last_user_type = "reflect"
    for m in reversed(msgs):
        if m["role"] == "user":
            last_user_type = m["msg_type"] or "reflect"
            break

    is_query = last_user_type == "query"

    # Query mode: different prompt construction
    if is_query:
        query_context = _build_query_context(db, container, uid())
        db.close()

        api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY
        if not api_key:
            def err(): yield f"data: {json.dumps({'type':'error','text':'没有可用的API Key。'})}\n\n"
            return Response(err(), mimetype="text/event-stream")

        system_prompt = build_query_prompt()
        api_msgs = _build_query_messages(system_prompt, container, container_patterns, query_context, msgs)

        def generate_query():
            try:
                resp = http_requests.post("https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={"model": "deepseek-reasoner", "messages": api_msgs, "stream": True},
                    stream=True, timeout=240)
                if resp.status_code != 200:
                    yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"; return
                yield f"data: {json.dumps({'type':'skills','skills':['query']})}\n\n"
                think, content = [], []
                in_content = False
                for line in resp.iter_lines():
                    if not line: continue
                    dec = line.decode("utf-8")
                    if not dec.startswith("data: "): continue
                    pay = dec[6:]
                    if pay == "[DONE]":
                        full_content = "".join(content).strip()
                        sdb = get_db()
                        sdb.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
                            (uuid.uuid4().hex[:8], tid, "assistant", full_content, "".join(think), json.dumps(["query"]), datetime.now().isoformat(), None, "query"))
                        sdb.execute("UPDATE threads SET updated=? WHERE id=?", (datetime.now().isoformat(), tid))
                        sdb.commit(); sdb.close()
                        yield f"data: {json.dumps({'type':'done'})}\n\n"; break
                    try:
                        ch = json.loads(pay); delta = ch.get("choices",[{}])[0].get("delta",{})
                        if rc := delta.get("reasoning_content"):
                            think.append(rc); yield f"data: {json.dumps({'type':'thinking','text':rc})}\n\n"
                        if ct := delta.get("content"):
                            content.append(ct)
                            if not in_content:
                                in_content = True
                            yield f"data: {json.dumps({'type':'content','text':ct})}\n\n"
                    except: pass
            except http_requests.exceptions.Timeout:
                yield f"data: {json.dumps({'type':'error','text':'Timeout'})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

        return Response(generate_query(), mimetype="text/event-stream")

    # === Reflect mode (existing logic) ===
    context_block = None
    pinned_context_info = None
    auto_context_threads = None

    # Entry-level pins take priority
    if pinned_entry_ids:
        entry_blocks = []
        for eid in pinned_entry_ids[:5]:
            emsg = db.execute("SELECT * FROM messages WHERE id=? AND role='user'", (eid,)).fetchone()
            if not emsg: continue
            et = db.execute("SELECT t.*, c.name as container_name FROM threads t JOIN containers c ON c.id=t.container_id WHERE t.id=? AND t.user_id=?",
                (emsg["thread_id"], uid())).fetchone()
            if not et: continue
            asst = db.execute(
                "SELECT content FROM messages WHERE thread_id=? AND role='assistant' AND timestamp > ? ORDER BY timestamp ASC LIMIT 1",
                (emsg["thread_id"], emsg["timestamp"])).fetchone()
            date_str = emsg["timestamp"][:10]
            block = f"[来源]: {et['container_name']}  [日期]: {date_str}\n[日记]: {emsg['content']}\n"
            if asst: block += f"[照鉴]: {asst['content']}\n"
            entry_blocks.append(block)
        if entry_blocks:
            context_block = (
                f"\n=== 用户指定参照记录（{len(entry_blocks)}条） ===\n" +
                "\n---\n".join(entry_blocks) +
                "=== 参照结束 ===\n"
            )
            pinned_context_info = {"type": "entries", "count": len(entry_blocks), "auto": False}

    elif pinned_context_id:
        ctx_thread = db.execute(
            "SELECT t.*, c.name as container_name FROM threads t JOIN containers c ON c.id = t.container_id WHERE t.id=? AND t.user_id=?",
            (pinned_context_id, uid())).fetchone()
        if ctx_thread:
            ctx_msgs = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC", (pinned_context_id,)).fetchall()
            user_content = next((m["content"] for m in ctx_msgs if m["role"] == "user"), "")
            ai_content = next((m["content"] for m in ctx_msgs if m["role"] == "assistant"), "")
            date_str = ctx_thread["created"][:10]
            context_block = (
                f"\n=== 指定参照记录 ===\n[来源]: {ctx_thread['container_name']}  [日期]: {date_str}\n"
                f"[日记]: {user_content}\n[照鉴]: {ai_content}\n=== 参照结束 ===\n"
            )
            pinned_context_info = {"type": "thread", "thread_id": pinned_context_id, "title": ctx_thread["title"], "preview": user_content[:80], "auto": False}
    else:
        same_container = db.execute(
            "SELECT t.id, t.title, t.container_id, c.name as container_name, t.created "
            "FROM threads t JOIN containers c ON c.id = t.container_id "
            "WHERE t.container_id=? AND t.id!=? AND t.user_id=? ORDER BY t.updated DESC LIMIT 10",
            (t["container_id"], tid, uid())).fetchall()
        other_threads = []
        if len(same_container) < 10:
            needed = 10 - len(same_container)
            same_ids = [r["id"] for r in same_container] + [tid]
            placeholders = ",".join("?" * len(same_ids))
            other_threads = db.execute(
                f"SELECT t.id, t.title, t.container_id, c.name as container_name, t.created "
                f"FROM threads t JOIN containers c ON c.id = t.container_id "
                f"WHERE t.container_id!=? AND t.user_id=? AND t.id NOT IN ({placeholders}) ORDER BY t.updated DESC LIMIT ?",
                [t["container_id"], uid()] + same_ids + [needed]).fetchall()
        all_ctx_threads = list(same_container) + list(other_threads)
        summaries = []
        for ct in all_ctx_threads[:10]:
            first_user = db.execute("SELECT content FROM messages WHERE thread_id=? AND role='user' ORDER BY timestamp ASC LIMIT 1", (ct["id"],)).fetchone()
            preview = first_user["content"][:50] if first_user else ""
            summaries.append({"id": ct["id"], "container_name": ct["container_name"], "date": ct["created"][:10], "title": ct["title"], "preview": preview})
        if summaries:
            lines = "\n".join(f"[ID:{s['id']}] [{s['container_name']}] {s['date']}: {s['title']} — {s['preview']}" for s in summaries)
            context_block = f"\n=== 可选参照记录 ===\n{lines}\n=== 选择说明 ===\n在输出末尾附加 {CONTEXT_ID_MK} 换行后写入最相关记录的ID（仅ID），若无相关则写 none。\n"
            auto_context_threads = {s["id"]: s for s in summaries}

    # Cross-container pattern injection
    cross_container_block = None
    other_containers = db.execute(
        "SELECT id, name, patterns FROM containers WHERE user_id=? AND id!=?",
        (uid(), t["container_id"])).fetchall()
    has_cross_container_ctx = False
    if other_containers:
        cc_parts = []
        for oc in other_containers:
            if oc["patterns"] and oc["patterns"] != '{}':
                cc_parts.append(f"[{oc['name']}]: {oc['patterns']}")
        if cc_parts:
            has_cross_container_ctx = True
            cross_container_block = "\n=== 其他容器模式档案 ===\n" + "\n".join(cc_parts) + "\n=== 其他容器档案结束 ===\n"

    db.close()

    api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY
    if not api_key:
        def err(): yield f"data: {json.dumps({'type':'error','text':'没有可用的API Key。'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    user_texts = [m["content"] for m in msgs if m["role"] == "user"]
    all_user_text = "\n".join(user_texts)
    selected = select_skills(text=all_user_text, has_cross_thread_context=container_patterns is not None, has_cross_container_context=has_cross_container_ctx, max_skills=3)
    skill_ids = [s.id for s in selected]
    system_prompt = build_system_prompt(selected)
    # Combine context blocks: thread context + cross-container context
    combined_context = (context_block or "") + (cross_container_block or "")
    api_msgs = _build_messages(system_prompt, container, msgs, container_patterns, combined_context or None)

    def generate():
        try:
            resp = http_requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-reasoner", "messages": api_msgs, "stream": True},
                stream=True, timeout=240)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"; return
            yield f"data: {json.dumps({'type':'skills','skills': skill_ids})}\n\n"
            if pinned_context_info:
                yield f"data: {json.dumps({'type':'context','data':pinned_context_info})}\n\n"
            think, content = [], []
            patterns_started = False; derived_started = False; content_buffer = ""
            PATTERNS_MK = "---PATTERNS---"; DERIVED_MK = "---DERIVED---"
            for line in resp.iter_lines():
                if not line: continue
                dec = line.decode("utf-8")
                if not dec.startswith("data: "): continue
                pay = dec[6:]
                if pay == "[DONE]":
                    full_content = "".join(content)
                    observation, patterns_json, derived_json, ctx_id = _split_output(full_content)
                    last_user_mid = None
                    for m in reversed(msgs):
                        if m["role"] == "user": last_user_mid = m["id"]; break
                    sdb = get_db()
                    sdb.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:8], tid, "assistant", observation, "".join(think), json.dumps(skill_ids), datetime.now().isoformat(), None, "reflect"))
                    sdb.execute("UPDATE threads SET updated=? WHERE id=?", (datetime.now().isoformat(), tid))
                    if patterns_json: sdb.execute("UPDATE containers SET patterns=? WHERE id=?", (patterns_json, t["container_id"]))
                    if derived_json and last_user_mid: sdb.execute("UPDATE messages SET derived_state=? WHERE id=?", (derived_json, last_user_mid))
                    sdb.commit(); sdb.close()
                    if content_buffer and not patterns_started and not derived_started:
                        yield f"data: {json.dumps({'type':'content','text':content_buffer})}\n\n"
                    if derived_json:
                        try: yield f"data: {json.dumps({'type':'derived','data':json.loads(derived_json)})}\n\n"
                        except json.JSONDecodeError: pass
                    if auto_context_threads and ctx_id and ctx_id in auto_context_threads:
                        ctx_s = auto_context_threads[ctx_id]
                        yield f"data: {json.dumps({'type':'context','data':{'type':'thread','thread_id':ctx_id,'title':ctx_s['title'],'preview':ctx_s['preview'],'auto':True}})}\n\n"
                    yield f"data: {json.dumps({'type':'done'})}\n\n"; break
                try:
                    ch = json.loads(pay); delta = ch.get("choices",[{}])[0].get("delta",{})
                    if rc := delta.get("reasoning_content"):
                        think.append(rc); yield f"data: {json.dumps({'type':'thinking','text':rc})}\n\n"
                    if ct := delta.get("content"):
                        content.append(ct)
                        if derived_started or patterns_started:
                            if patterns_started and not derived_started:
                                content_buffer += ct
                                if DERIVED_MK in content_buffer: derived_started = True; content_buffer = ""
                            continue
                        content_buffer += ct
                        if PATTERNS_MK in content_buffer:
                            before = content_buffer.split(PATTERNS_MK)[0]
                            if before.strip(): yield f"data: {json.dumps({'type':'content','text':before.rstrip()})}\n\n"
                            content_buffer = ""; patterns_started = True
                        elif len(content_buffer) > 200:
                            flush = content_buffer[:-20]; content_buffer = content_buffer[-20:]
                            yield f"data: {json.dumps({'type':'content','text':flush})}\n\n"
                except: pass
        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'type':'error','text':'Timeout'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


def _split_output(content: str) -> tuple[str, str | None, str | None, str | None]:
    PATTERNS_MK = "---PATTERNS---"; DERIVED_MK = "---DERIVED---"
    observation = content.strip(); patterns_json = None; derived_json = None; context_id = None
    def _extract_derived_and_context(der_part: str):
        if CONTEXT_ID_MK in der_part:
            der_str, ctx_str = der_part.split(CONTEXT_ID_MK, 1)
            cid = ctx_str.strip()
            return _clean_json(der_str.strip()), (None if cid == "none" else cid)
        return _clean_json(der_part.strip()), None
    if PATTERNS_MK in observation:
        parts = observation.split(PATTERNS_MK, 1); observation = parts[0].strip(); remainder = parts[1].strip()
        if DERIVED_MK in remainder:
            pat_part, der_part = remainder.split(DERIVED_MK, 1)
            patterns_json = _clean_json(pat_part.strip()); derived_json, context_id = _extract_derived_and_context(der_part)
        else: patterns_json = _clean_json(remainder)
    elif DERIVED_MK in observation:
        parts = observation.split(DERIVED_MK, 1); observation = parts[0].strip()
        derived_json, context_id = _extract_derived_and_context(parts[1])
    return observation, patterns_json, derived_json, context_id

def _clean_json(raw: str) -> str | None:
    if not raw: return None
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.startswith("json"): raw = raw[4:].strip()
    try: json.loads(raw); return raw
    except json.JSONDecodeError: return None

def _build_messages(system_prompt, container, thread_msgs, container_patterns, context_block=None):
    out = [{"role": "system", "content": system_prompt}]
    ctx = f"容器名称: {container['name']}\n"
    if container["description"]: ctx += f"容器描述: {container['description']}\n"
    if container_patterns: ctx += f"\n=== 容器累积模式档案 ===\n{container_patterns}\n"
    if context_block: ctx += context_block
    for i, m in enumerate(thread_msgs):
        c = m["content"]
        if i == 0 and m["role"] == "user": c = ctx + "\n=== 当前日记 ===\n" + c
        out.append({"role": m["role"], "content": c})
    return out


def _build_query_context(db, container, uid_val):
    """Assemble all container entries with derived states for query mode."""
    threads = db.execute(
        "SELECT id, title, created FROM threads WHERE container_id=? AND user_id=? ORDER BY created ASC",
        (container["id"], uid_val)).fetchall()
    if not threads:
        return ""

    thread_ids = [t["id"] for t in threads]
    placeholders = ",".join("?" * len(thread_ids))

    # Get all user messages with derived states
    rows = db.execute(f"""
        SELECT m.id, m.thread_id, m.content, m.derived_state, m.timestamp
        FROM messages m WHERE m.thread_id IN ({placeholders}) AND m.role='user'
        ORDER BY m.timestamp ASC
    """, thread_ids).fetchall()

    # Also get assistant observations for each user message
    entries = []
    for r in rows:
        ds = None
        if r["derived_state"]:
            try:
                ds = json.loads(r["derived_state"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Get the observation that followed this entry
        obs = db.execute(
            "SELECT content FROM messages WHERE thread_id=? AND role='assistant' AND timestamp > ? ORDER BY timestamp ASC LIMIT 1",
            (r["thread_id"], r["timestamp"])).fetchone()

        entry = {
            "date": r["timestamp"][:10],
            "text": r["content"][:500],  # Cap per-entry text to manage token budget
            "observation": obs["content"][:200] if obs and obs["content"] != "已记录。" else None,
            "derived": ds,
        }
        entries.append(entry)

    if not entries:
        return ""

    # Build condensed context block
    lines = [f"=== 容器日记档案（共{len(entries)}条） ===\n"]
    for i, e in enumerate(entries):
        line = f"[{e['date']}] {e['text']}"
        if e["derived"]:
            ds = e["derived"]
            meta_parts = []
            if ds.get("hedge_count") is not None:
                meta_parts.append(f"软化词:{ds['hedge_count']}")
            if ds.get("negation_count") is not None:
                meta_parts.append(f"否定词:{ds['negation_count']}")
            if ds.get("syntactic_signals"):
                meta_parts.append(f"信号:{','.join(ds['syntactic_signals'][:3])}")
            if ds.get("high_frequency_words"):
                meta_parts.append(f"高频词:{','.join(ds['high_frequency_words'][:5])}")
            if ds.get("salience_markers"):
                meta_parts.append(f"显著:{','.join(ds['salience_markers'][:3])}")
            if meta_parts:
                line += f"\n  [{' | '.join(meta_parts)}]"
        if e["observation"]:
            line += f"\n  [观察]: {e['observation']}"
        lines.append(line)

    lines.append("\n=== 档案结束 ===")
    return "\n".join(lines)


def _build_query_messages(system_prompt, container, container_patterns, query_context, thread_msgs):
    """Build messages for query mode."""
    out = [{"role": "system", "content": system_prompt}]
    ctx = f"容器名称: {container['name']}\n"
    if container["description"]:
        ctx += f"容器描述: {container['description']}\n"
    if container_patterns:
        ctx += f"\n=== 容器累积模式档案 ===\n{container_patterns}\n"
    ctx += "\n" + query_context + "\n"

    for i, m in enumerate(thread_msgs):
        c = m["content"]
        if i == 0 and m["role"] == "user":
            c = ctx + "\n=== 查询 ===\n" + c
        out.append({"role": m["role"], "content": c})
    return out

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  照鉴 · Zhaojian (Agent+Skills)")
    print(f"  http://localhost:{port}")
    print(f"  Skills: {', '.join(SKILLS.keys())}")
    print(f"  Invite: {INVITE_CODE}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
