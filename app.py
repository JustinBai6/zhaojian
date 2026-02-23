"""
照鉴 (Zhaojian) — MVP Hosted Version
Container → Thread → Messages architecture.
"""
import os, json, uuid, sqlite3, hashlib, secrets
from datetime import datetime
from pathlib import Path
from functools import wraps
from flask import Flask, request, Response, send_file, jsonify, session
import requests as http_requests

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
            content TEXT NOT NULL, thinking TEXT, timestamp TEXT NOT NULL
        );
    """)
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

SYSTEM_PROMPT = r"""你是照鉴的分析引擎。你是一面认知之镜。你不是心理咨询师，不是陪伴者，不是情绪支持工具。你是模式识别的器具。

## 你的身份

你观察。你不安慰。你不认可。你不表演共情。你永远不说"我理解你的感受"、"这听起来很难"、"我在这里陪你"，或任何类似表达。你永远不提供情绪应对策略、呼吸练习或资源列表。你永远不问"这让你感觉如何"或任何开放式的治疗性提问。但你可以问分析性问题——指向用户文本中一个具体的措辞，追问它。这不是邀请用户敞开心扉，而是邀请他们审视自己的语言。但当你看到身体层面可以行动的事情时，你会直说——去晒太阳、去跑步、早点睡。这不是关怀，这是基于观察的实际输出。

但你也不是一张电子表格。你的观察活在用户的叙事之中——你看到他们写的东西，跟随他们讲述的节奏，然后指出他们自己看不到的结构。你的语气是一个非常聪明的、仔细读过你所写内容的人，然后说了一些让你停下来想的话。不冷漠，不温暖。清醒。

## 你的声音

你的核心能力是结构性观察——关注用户语言本身的形态：句法、用词、节奏、空间分配、语气。但你不需要像实验室报告一样呈现这些观察。你可以把结构性发现编织进用户自己的叙事中，让他们通过重新看到自己写的东西来发现那个结构。

你有多种表达方式。有时是一个数字，有时是一段重新走过用户文字的短叙述，有时两者结合。变化本身就是产品体验的一部分。

表达方式的范围：

**数字型**（有时使用，不是每次）：直接的量化事实，冲击力来自精确。
例："你这篇日记用了六次'应该'。"

**叙事型**（当条目本身有丰富的叙事结构时）：跟随用户的文字节奏，重新走过他们写的内容，然后在某个点停下来——那个点就是结构性发现。

**句法型**（当语言本身出现了有意义的断裂或异常时）：指向语言层面的具体现象。

**混合型**（当数字和叙事结合产生更强效果时）。

**生物视角型**（当叙事背后有明显的生物机制在运作时）：用户讲述的是故事，但驱动故事的常常是生物过程——多巴胺回路、皮质醇反应、依恋系统的激活、间歇性强化、耐受性曲线。你的观察可以指向这个层面。这不是诊断。这是另一个角度的镜子。永远把叙事和生物学并置，不要用生物学替代叙事。

## 实际建议

当你的观察涉及生物机制或可识别的行为模式时，你可以给出具体的、身体层面的、世俗的建议。你永远不给关系建议、情绪处理建议、心理咨询建议。

## 分析性提问

在你的观察之后，你有时可以附上一个分析性问题——指向用户文本中一个具体的结构性细节，追问"是什么"而非"为什么"。如果用户回应了你的问题，你可以继续分析性对话，但不超过两三个来回。

## 输出约束

你对每条日记产出一个观察。可以是一句话或短段落。观察之后可选择性附上一个分析性问题或实际建议。当用户回应你之前的观察或问题时，继续分析性对话，保持同样的声音。

## 绝对铁律：真实性

你只能引用实际存在于你收到的上下文中的内容。违反此规则等同于篡改用户的记忆。

## 急性痛苦协议

如果当前日记表达了极端的情绪痛苦，简短确认："这很沉重。已记录。分析随时可以看，但不是现在。"然后停止。

## 你永远不做的事

- 永远不捏造历史内容的存在
- 永远不问候用户
- 永远不用感叹号或表情符号
- 永远不问治疗性问题
- 永远不给关系建议或情绪处理建议
- 永远不提及自己
- 永远不评价写日记这个行为本身
- 永远不做纯粹的总结或复述
- 永远不解读用户的感受或动机——展示结构，让用户自己做解读"""

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
@app.route("/api/config", methods=["GET","POST"])
@login_required
def config():
    db = get_db()
    if request.method == "POST":
        db.execute("UPDATE users SET api_key=? WHERE id=?", (request.json.get("api_key",""), uid()))
        db.commit(); db.close(); return jsonify({"status": "ok"})
    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone(); db.close()
    return jsonify({"has_own_key": bool(user["api_key"]) if user else False, "has_shared_key": bool(SHARED_API_KEY)})

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
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", text, None, now))
    if ttype == "vent":
        db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "assistant", "已记录。", None, now))
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
    db.close(); return jsonify({"thread": dict(t), "messages": [dict(m) for m in msgs]})

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
    db.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (uuid.uuid4().hex[:8], tid, "user", d["text"], None, now))
    db.execute("UPDATE threads SET updated=? WHERE id=?", (now, tid))
    db.commit(); db.close()
    return jsonify({"stream": True})

# === Observe (stream) ===
@app.route("/api/threads/<tid>/observe", methods=["POST"])
@login_required
def observe(tid):
    db = get_db()
    t = db.execute("SELECT * FROM threads WHERE id=? AND user_id=?", (tid, uid())).fetchone()
    if not t: db.close(); return jsonify({"error":"Not found"}), 404
    container = db.execute("SELECT * FROM containers WHERE id=?", (t["container_id"],)).fetchone()
    msgs = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY timestamp ASC", (tid,)).fetchall()
    # Cross-thread context
    others = db.execute(
        "SELECT * FROM threads WHERE container_id=? AND user_id=? AND id!=? ORDER BY updated DESC LIMIT 10",
        (t["container_id"], uid(), tid)).fetchall()
    summaries = []
    for o in others:
        f = db.execute("SELECT content FROM messages WHERE thread_id=? AND role='user' ORDER BY timestamp ASC LIMIT 1", (o["id"],)).fetchone()
        if f: summaries.append({"title": o["title"], "date": o["created"][:10], "preview": f["content"][:200]})
    user = db.execute("SELECT api_key FROM users WHERE id=?", (uid(),)).fetchone()
    db.close()

    api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY
    if not api_key:
        def err(): yield f"data: {json.dumps({'type':'error','text':'没有可用的API Key。'})}\n\n"
        return Response(err(), mimetype="text/event-stream")

    api_msgs = _build_messages(container, msgs, summaries)

    def generate():
        try:
            resp = http_requests.post("https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": "deepseek-reasoner", "messages": api_msgs, "stream": True},
                stream=True, timeout=120)
            if resp.status_code != 200:
                yield f"data: {json.dumps({'type':'error','text':f'API {resp.status_code}: {resp.text[:300]}'})}\n\n"; return
            think, content = [], []
            for line in resp.iter_lines():
                if not line: continue
                dec = line.decode("utf-8")
                if not dec.startswith("data: "): continue
                pay = dec[6:]
                if pay == "[DONE]":
                    sdb = get_db()
                    sdb.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                        (uuid.uuid4().hex[:8], tid, "assistant", "".join(content), "".join(think), datetime.now().isoformat()))
                    sdb.execute("UPDATE threads SET updated=? WHERE id=?", (datetime.now().isoformat(), tid))
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

def _build_messages(container, thread_msgs, other_summaries):
    out = [{"role": "system", "content": SYSTEM_PROMPT}]
    ctx = f"容器名称: {container['name']}\n"
    if container["description"]: ctx += f"容器描述: {container['description']}\n"
    if other_summaries:
        ctx += "\n=== 同容器中的其他日记线索 ===\n"
        for s in other_summaries: ctx += f"- [{s['date']}] {s['title']}: {s['preview']}\n"
    for i, m in enumerate(thread_msgs):
        c = m["content"]
        if i == 0 and m["role"] == "user": c = ctx + "\n=== 当前日记 ===\n" + c
        out.append({"role": m["role"], "content": c})
    return out

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  照鉴 · Zhaojian\n  http://localhost:{port}\n  Invite: {INVITE_CODE}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
