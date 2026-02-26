"""
照鉴 (Zhaojian) — Agent + Skills Architecture
Container → Thread → Messages with dynamic skill selection.
Per-entry Derived State extraction via combined output.
"""
import os, json, uuid, sqlite3, hashlib, secrets
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, Response, send_file, jsonify, session
import requests as http_requests

from skills import select_skills, build_system_prompt, SKILLS

app = Flask(__name__)
app.secret_key = os.environ.get("ZHAOJIAN_SECRET", secrets.token_hex(32))
db_dir = os.environ.get("DB_DIR", Path(__file__).parent)
DB_PATH = Path(db_dir) / "zhaojian.db"
INVITE_CODE = os.environ.get("ZHAOJIAN_INVITE", "zhaojian2026")
SHARED_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

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
    # Migrations
    migrations = [
        "ALTER TABLE messages ADD COLUMN skills_used TEXT",
        "ALTER TABLE containers ADD COLUMN patterns TEXT DEFAULT '{}'",
        "ALTER TABLE messages ADD COLUMN derived_state TEXT",
    ]
    for m in migrations:
        try:
            db.execute(m)
        except sqlite3.OperationalError:
            pass  # Column already exists
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
    """Download the database file. Protected by invite code."""
    if code != INVITE_CODE:
        return jsonify({"error": "Invalid code"}), 403
    if not DB_PATH.exists():
        return jsonify({"error": "No database found"}), 404
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

# === Skills Info ===
@app.route("/api/skills", methods=["GET"])
@login_required
def list_skills():
    """Return available skills metadata for UI display."""
    return jsonify({"skills": [
        {"id": s.id, "name": s.name, "label": s.label, "description": s.description}
        for s in SKILLS.values() if s.id != "distress"
    ]})

# === Derived State Query ===
@app.route("/api/containers/<cid>/states", methods=["GET"])
@login_required
def container_states(cid):
    """Return all derived states for entries in a container, for trend visualization."""
    db = get_db()
    # Verify container ownership
    c = db.execute("SELECT * FROM containers WHERE id=? AND user_id=?", (cid, uid())).fetchone()
    if not c: db.close(); return jsonify({"error": "Not found"}), 404
    # Get all threads in this container
    threads = db.execute("SELECT id FROM threads WHERE container_id=? AND user_id=?", (cid, uid())).fetchall()
    thread_ids = [t["id"] for t in threads]
    if not thread_ids:
        db.close(); return jsonify({"states": []})
    # Get all user messages with derived states
    placeholders = ",".join("?" * len(thread_ids))
    rows = db.execute(f"""
        SELECT m.id, m.thread_id, m.content, m.derived_state, m.timestamp
        FROM messages m
        WHERE m.thread_id IN ({placeholders}) AND m.role='user' AND m.derived_state IS NOT NULL
        ORDER BY m.timestamp ASC
    """, thread_ids).fetchall()
    db.close()
    states = []
    for r in rows:
        try:
            ds = json.loads(r["derived_state"])
        except (json.JSONDecodeError, TypeError):
            continue
        states.append({
            "message_id": r["id"],
            "thread_id": r["thread_id"],
            "timestamp": r["timestamp"],
            "preview": r["content"][:60],
            **ds
        })
    return jsonify({"states": states})


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
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", text, None, None, now, None))
    if ttype == "vent":
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "assistant", "已记录。", None, None, now, None))
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
    return jsonify({
        "thread": dict(t),
        "messages": [{**dict(m), "skills_used": m["skills_used"], "derived_state": m["derived_state"]} for m in msgs]
    })

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
    """Delete a user message and its paired assistant response."""
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
    if next_asst:
        db.execute("DELETE FROM messages WHERE id=?", (next_asst["id"],))
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
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error": "Not found"}), 404
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", d["text"], None, None, now, None))
    db.execute("UPDATE threads SET updated=? WHERE id=?", (now, tid))
    db.commit(); db.close()
    return jsonify({"stream": True})

# === Observe (stream) with Skill Selection ===
@app.route("/api/threads/<tid>/observe", methods=["POST"])
@login_required
def observe(tid):
    db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error":"Not found"}), 404
    container = db.execute("SELECT * FROM containers WHERE id=?", (t["container_id"],)).fetchone()
    msgs = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC", (tid,)).fetchall()

    container_patterns = container["patterns"] if container["patterns"] and container["patterns"] != '{}' else None

    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone()
    db.close()

    api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY
    if not api_key:
        def err(): yield f"data: {json.dumps({'type':'error','text':'没有可用的API Key。'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    # ── Skill Selection ──
    user_texts = [m["content"] for m in msgs if m["role"] == "user"]
    latest_text = user_texts[-1] if user_texts else ""
    all_user_text = "\n".join(user_texts)

    selected = select_skills(
        text=all_user_text,
        has_cross_thread_context=container_patterns is not None,
        max_skills=3,
    )
    skill_ids = [s.id for s in selected]
    system_prompt = build_system_prompt(selected)

    api_msgs = _build_messages(system_prompt, container, msgs, container_patterns)

    def generate():
        try:
            resp = http_requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-reasoner", "messages": api_msgs, "stream": True},
                stream=True, timeout=240)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"; return

            yield f"data: {json.dumps({'type':'skills','skills': skill_ids})}\n\n"

            think, content = [], []
            patterns_started = False
            derived_started = False
            content_buffer = ""
            PATTERNS_MK = "---PATTERNS---"
            DERIVED_MK = "---DERIVED---"

            for line in resp.iter_lines():
                if not line: continue
                dec = line.decode("utf-8")
                if not dec.startswith("data: "): continue
                pay = dec[6:]
                if pay == "[DONE]":
                    full_content = "".join(content)
                    observation, patterns_json, derived_json = _split_output(full_content)

                    # Find the last user message to attach derived state to
                    last_user_mid = None
                    for m in reversed(msgs):
                        if m["role"] == "user":
                            last_user_mid = m["id"]
                            break

                    sdb = get_db()
                    sdb.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:8], tid, "assistant", observation,
                         "".join(think), json.dumps(skill_ids), datetime.now().isoformat(), None))
                    sdb.execute("UPDATE threads SET updated=? WHERE id=?", (datetime.now().isoformat(), tid))
                    if patterns_json:
                        sdb.execute("UPDATE containers SET patterns=? WHERE id=?",
                            (patterns_json, t["container_id"]))
                    if derived_json and last_user_mid:
                        sdb.execute("UPDATE messages SET derived_state=? WHERE id=?",
                            (derived_json, last_user_mid))
                    sdb.commit(); sdb.close()

                    # Flush remaining buffered content
                    if content_buffer and not patterns_started and not derived_started:
                        yield f"data: {json.dumps({'type':'content','text':content_buffer})}\n\n"

                    # Send derived state to frontend for display
                    if derived_json:
                        try:
                            yield f"data: {json.dumps({'type':'derived','data':json.loads(derived_json)})}\n\n"
                        except json.JSONDecodeError:
                            pass

                    yield f"data: {json.dumps({'type':'done'})}\n\n"; break
                try:
                    ch = json.loads(pay); delta = ch.get("choices",[{}])[0].get("delta",{})
                    if rc := delta.get("reasoning_content"):
                        think.append(rc); yield f"data: {json.dumps({'type':'thinking','text':rc})}\n\n"
                    if ct := delta.get("content"):
                        content.append(ct)
                        if derived_started or patterns_started:
                            # Past markers — don't send to frontend
                            # But check if we transitioned from patterns to derived
                            if patterns_started and not derived_started:
                                content_buffer += ct
                                if DERIVED_MK in content_buffer:
                                    derived_started = True
                                    content_buffer = ""
                            continue
                        content_buffer += ct
                        # Check for patterns marker
                        if PATTERNS_MK in content_buffer:
                            before = content_buffer.split(PATTERNS_MK)[0]
                            if before.strip():
                                yield f"data: {json.dumps({'type':'content','text':before.rstrip()})}\n\n"
                            content_buffer = ""
                            patterns_started = True
                        elif len(content_buffer) > 200:
                            flush = content_buffer[:-20]
                            content_buffer = content_buffer[-20:]
                            yield f"data: {json.dumps({'type':'content','text':flush})}\n\n"
                except: pass
        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'type':'error','text':'Timeout'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


def _split_output(content: str) -> tuple[str, str | None, str | None]:
    """Split model output into observation, patterns JSON, and derived state JSON.
    Returns (observation_text, patterns_json_or_None, derived_json_or_None)."""
    PATTERNS_MK = "---PATTERNS---"
    DERIVED_MK = "---DERIVED---"

    observation = content.strip()
    patterns_json = None
    derived_json = None

    # Split off patterns
    if PATTERNS_MK in observation:
        parts = observation.split(PATTERNS_MK, 1)
        observation = parts[0].strip()
        remainder = parts[1].strip()

        # Split off derived from remainder
        if DERIVED_MK in remainder:
            pat_part, der_part = remainder.split(DERIVED_MK, 1)
            patterns_json = _clean_json(pat_part.strip())
            derived_json = _clean_json(der_part.strip())
        else:
            patterns_json = _clean_json(remainder)
    elif DERIVED_MK in observation:
        # Edge case: no patterns marker but derived exists
        parts = observation.split(DERIVED_MK, 1)
        observation = parts[0].strip()
        derived_json = _clean_json(parts[1].strip())

    return observation, patterns_json, derived_json


def _clean_json(raw: str) -> str | None:
    """Clean up common JSON formatting issues and validate."""
    if not raw:
        return None
    # Strip markdown code fences
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        json.loads(raw)
        return raw
    except json.JSONDecodeError:
        return None


def _build_messages(system_prompt, container, thread_msgs, container_patterns):
    """Build the API message array with dynamic system prompt."""
    out = [{"role": "system", "content": system_prompt}]
    ctx = f"容器名称: {container['name']}\n"
    if container["description"]: ctx += f"容器描述: {container['description']}\n"
    if container_patterns:
        ctx += f"\n=== 容器累积模式档案 ===\n{container_patterns}\n"
    for i, m in enumerate(thread_msgs):
        c = m["content"]
        if i == 0 and m["role"] == "user": c = ctx + "\n=== 当前日记 ===\n" + c
        out.append({"role": m["role"], "content": c})
    return out


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  照鉴 · Zhaojian (Agent+Skills)")
    print(f"  http://localhost:{port}")
    print(f"  Skills: {', '.join(SKILLS.keys())}")
    print(f"  Invite: {INVITE_CODE}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
