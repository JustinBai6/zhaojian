"""
照鉴 (Zhaojian) — Agent + Skills Architecture
Container → Thread → Messages with dynamic skill selection.
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
    # Migration: add skills_used column if it doesn't exist
    try:
        db.execute("ALTER TABLE messages ADD COLUMN skills_used TEXT")
    except sqlite3.OperationalError:
        pass  # Column already exists
    # Migration: add patterns column to containers
    try:
        db.execute("ALTER TABLE containers ADD COLUMN patterns TEXT DEFAULT '{}'")
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

# === Skills Info (new endpoint) ===
@app.route("/api/skills", methods=["GET"])
@login_required
def list_skills():
    """Return available skills metadata for UI display."""
    return jsonify({"skills": [
        {"id": s.id, "name": s.name, "label": s.label, "description": s.description}
        for s in SKILLS.values() if s.id != "distress"
    ]})

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
    db.execute("INSERT INTO containers VALUES (?,?,?,?,?)", (cid, uid(), d["name"], d.get("description",""), now))
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
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", text, None, None, now))
    if ttype == "vent":
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "assistant", "已记录。", None, None, now))
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
        "messages": [{**dict(m), "skills_used": m["skills_used"]} for m in msgs]
    })

@app.route("/api/threads/<tid>", methods=["DELETE"])
@login_required
def delete_thread(tid):
    db = get_db()
    db.execute("DELETE FROM messages WHERE thread_id=?", (tid,))
    db.execute("DELETE FROM threads WHERE id=? AND user_id=?", (tid, uid()))
    db.commit(); db.close(); return jsonify({"status": "ok"})

# === Reply ===
@app.route("/api/threads/<tid>/reply", methods=["POST"])
@login_required
def reply(tid):
    d = request.json; now = datetime.now().isoformat(); db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error": "Not found"}), 404
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", d["text"], None, None, now))
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

    # Load accumulated container patterns
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
                stream=True, timeout=120)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"; return

            # Send skill selection metadata to frontend
            yield f"data: {json.dumps({'type':'skills','skills': skill_ids})}\n\n"

            think, content = [], []
            for line in resp.iter_lines():
                if not line: continue
                dec = line.decode("utf-8")
                if not dec.startswith("data: "): continue
                pay = dec[6:]
                if pay == "[DONE]":
                    full_content = "".join(content)
                    # Parse out patterns JSON if present
                    observation, patterns_json = _split_patterns(full_content)
                    sdb = get_db()
                    sdb.execute("INSERT INTO messages VALUES (?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:8], tid, "assistant", observation,
                         "".join(think), json.dumps(skill_ids), datetime.now().isoformat()))
                    sdb.execute("UPDATE threads SET updated=? WHERE id=?", (datetime.now().isoformat(), tid))
                    # Update container patterns if model produced them
                    if patterns_json:
                        sdb.execute("UPDATE containers SET patterns=? WHERE id=?",
                            (patterns_json, t["container_id"]))
                    sdb.commit(); sdb.close()
                    yield f"data: {json.dumps({'type':'done'})}\n\n"; break
                try:
                    ch = json.loads(pay); delta = ch.get("choices",[{}])[0].get("delta",{})
                    if rc := delta.get("reasoning_content"):
                        think.append(rc); yield f"data: {json.dumps({'type':'thinking','text':rc})}\n\n"
                    if ct := delta.get("content"):
                        content.append(ct); yield f"data: {json.dumps({'type':'content','text':ct})}\n\n"
                except: pass
        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'type':'error','text':'Timeout'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type':'error','text':str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")


PATTERNS_MARKER = "---PATTERNS---"

def _split_patterns(content: str) -> tuple[str, str | None]:
    """Split model output into observation and patterns JSON.
    Returns (observation_text, patterns_json_string_or_None)."""
    if PATTERNS_MARKER not in content:
        return content.strip(), None
    parts = content.split(PATTERNS_MARKER, 1)
    observation = parts[0].strip()
    raw_json = parts[1].strip()
    # Clean up common formatting issues
    if raw_json.startswith("```"):
        raw_json = raw_json.strip("`").strip()
        if raw_json.startswith("json"):
            raw_json = raw_json[4:].strip()
    # Validate it's actual JSON
    try:
        json.loads(raw_json)
        return observation, raw_json
    except json.JSONDecodeError:
        # Model produced garbage after marker — keep observation, discard patterns
        return observation, None


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
