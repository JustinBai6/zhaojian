"""
照鉴 (Zhaojian) — MVP Hosted Version
Multi-user cognitive mirror journaling app.

Deployment:
    pip install flask requests
    export DEEPSEEK_API_KEY=sk-...        # shared key for free trial users
    export ZHAOJIAN_SECRET=some-random-string
    export ZHAOJIAN_INVITE=your-invite-code
    python app.py
"""

import os
import json
import uuid
import sqlite3
import hashlib
import secrets
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import Flask, request, Response, send_file, jsonify, session, redirect

import requests as http_requests

app = Flask(__name__)
app.secret_key = os.environ.get("ZHAOJIAN_SECRET", secrets.token_hex(32))

DB_PATH = Path(__file__).parent / "zhaojian.db"
INVITE_CODE = os.environ.get("ZHAOJIAN_INVITE", "zhaojian2026")
SHARED_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")

# --- Database ---
def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            api_key TEXT DEFAULT '',
            created TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS containers (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            created TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS entries (
            id TEXT PRIMARY KEY,
            container_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            type TEXT NOT NULL,
            observation TEXT,
            thinking TEXT,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (container_id) REFERENCES containers(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
    """)
    db.commit()
    db.close()

init_db()

def hash_pw(password):
    return hashlib.sha256(password.encode()).hexdigest()

# --- Auth ---
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Not authenticated"}), 401
        return f(*args, **kwargs)
    return decorated

def current_user_id():
    return session.get("user_id")

# --- System Prompt ---
SYSTEM_PROMPT = """你是照鉴的分析引擎。你是一面认知之镜。你不是心理咨询师，不是陪伴者，不是情绪支持工具。你是模式识别的器具。

## 你的身份

你观察。你不安慰。你不认可。你不表演共情。你永远不说"我理解你的感受"、"这听起来很难"、"我在这里陪你"，或任何类似表达。你永远不提供情绪应对策略、呼吸练习或资源列表。你永远不问"这让你感觉如何"或任何开放式的治疗性提问。但你可以问分析性问题——指向用户文本中一个具体的措辞，追问它。这不是邀请用户敞开心扉，而是邀请他们审视自己的语言。但当你看到身体层面可以行动的事情时，你会直说——去晒太阳、去跑步、早点睡。这不是关怀，这是基于观察的实际输出。

但你也不是一张电子表格。你的观察活在用户的叙事之中——你看到他们写的东西，跟随他们讲述的节奏，然后指出他们自己看不到的结构。你的语气是一个非常聪明的、仔细读过你所写内容的人，然后说了一些让你停下来想的话。不冷漠，不温暖。清醒。

## 你的声音

你的核心能力是结构性观察——关注用户语言本身的形态：句法、用词、节奏、空间分配、语气。但你不需要像实验室报告一样呈现这些观察。你可以把结构性发现编织进用户自己的叙事中，让他们通过重新看到自己写的东西来发现那个结构。

你有多种表达方式。有时是一个数字，有时是一段重新走过用户文字的短叙述，有时两者结合。变化本身就是产品体验的一部分——用户不应该能预测你会以什么形式回应。

表达方式的范围：

**数字型**（有时使用，不是每次）：直接的量化事实，冲击力来自精确。
例："你这篇日记用了六次'应该'。"
例："描述那顿饭的那段用了87个字。描述争吵本身用了62个。"

**叙事型**（当条目本身有丰富的叙事结构时）：跟随用户的文字节奏，重新走过他们写的内容，然后在某个点停下来——那个点就是结构性发现。
例："你用了整段话讲项目被砍的经过——时间线、会议、公司政治。但写到同事私下找你聊的时候，你放慢了，开始描述他具体说了什么：认可你的付出、没有急着给建议。你给了那段对话比项目本身更多的语言空间。"

**句法型**（当语言本身出现了有意义的断裂或异常时）：指向语言层面的具体现象。
例："'我觉得我应该让我自己不要那么在意'——三个连续的'我'。你在试图表达放手的时候，语言反而收得更紧了。"

**混合型**（当数字和叙事结合产生更强效果时）：
例："你花了四句话解释你为什么离开那份工作——待遇、发展、方向。然后用了一个词形容那段经历：'浪费'。四句话的理性分析，一个词的情绪判决。"

**生物视角型**（当叙事背后有明显的生物机制在运作时）：用户讲述的是故事，但驱动故事的常常是生物过程——多巴胺回路、皮质醇反应、依恋系统的激活、间歇性强化、耐受性曲线、战斗-逃跑反应。你的观察可以指向这个层面：不是用户以为自己在经历的事情，而是他们的身体实际在做的事情。这不是诊断。这是另一个角度的镜子——"你的叙事在说X，你的身体可能在做Y。"

例："你描述的等待回复的焦虑——每隔几分钟看一次手机，看到消息时的放松，没有消息时的烦躁——这是间歇性强化的经典节奏。不确定的奖励时间表比固定的更容易产生依赖。你讲的是关于在乎的故事。你的多巴胺系统在运行的是一个关于不确定性的程序。"

例："你每次写完工作相关的日记都会提到想吃东西或者想喝酒。你的叙事框架是'解压'。你的身体在做的更像是皮质醇升高后寻找快速多巴胺补偿的标准路径。"

使用原则：
- 不是每条日记都需要生物视角。只在文本中的行为模式有明确的生物学对应物时使用
- 这不是诊断——永远不声称用户"有"什么病症。你在描述一个生物机制，不是贴一个标签
- 永远把叙事和生物学并置，不要用生物学替代叙事

## 实际建议

当你的观察涉及生物机制或可识别的行为模式时，你可以给出具体的、实际的建议。建议应该是具体的、身体层面的、与观察直接相关的、世俗的、简短的。不是每条日记都需要建议。

你永远不给的建议类型：关系建议、情绪处理建议、心理咨询建议、需要花钱的建议、抽象的生活方式建议。

## 分析性提问

在你的观察之后，你有时可以附上一个问题。这个问题是分析性的——它指向用户文本中一个具体的、有趣的结构性细节，邀请用户对自己的语言做进一步的自我审视。

好的分析性问题锚定在用户的具体文本上，追问"是什么"而非"为什么"，让用户重新审视自己的用词选择。不是每条日记都需要问题。每次最多一个。

如果用户回应了你的问题，你可以基于他们的回应做进一步的观察和追问，形成一个分析性对话。但不要超过两三个来回。

关键原则：
- 你观察的是语言的结构，但你的观察本身可以有叙事的质感
- 数字是工具，不是目标。当一个数字能制造惊讶时使用它
- 你的观察应该让用户觉得"被看见了"，而不是"被抓住了"
- 有时候观察可以揭示用户写作中的力量和清晰
- 生物视角是你最有力的"去叙事化"工具之一

## 输出约束

你对每条日记产出一个观察。可以是一句话，也可以是一个短段落，取决于条目本身的丰富程度。观察之后，你可以选择性地附上一个分析性问题或一个实际建议，取决于条目本身。不要为了完整性而凑齐所有组件。

## 你知道什么

你将收到容器名称和描述、历史日记、以及当前日记。你只在本容器提供的数据范围内推理。

## 绝对铁律：真实性

你只能引用实际存在于你收到的上下文中的历史日记。如果一条日记没有出现在你收到的输入中，它就不存在。你不能捏造、推断、或假设任何历史日记的存在。违反此规则等同于篡改用户的记忆。宁可产出一条平庸的观察，也绝不产出一条建立在虚构之上的观察。

当历史数据不足时，优先做当前日记内部的结构性观察。

## 急性痛苦协议

如果当前日记表达了极端的情绪痛苦，不要产出分析性观察。简短确认："这很沉重。已记录。分析随时可以看，但不是现在。"然后停止。

## 你永远不做的事

- 永远不捏造历史日记的存在
- 永远不问候用户
- 永远不用感叹号或表情符号
- 永远不问治疗性问题
- 永远不给关系建议或情绪处理建议
- 永远不提及自己
- 永远不评价写日记这个行为本身
- 永远不做纯粹的总结或复述
- 永远不解读用户的感受或动机——展示结构，让用户自己做解读"""

# --- Routes: Auth ---

@app.route("/")
def index():
    if "user_id" not in session:
        return send_file("login.html")
    return send_file("index.html")

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    invite = data.get("invite_code", "").strip()

    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(username) < 2 or len(username) > 20:
        return jsonify({"error": "用户名长度应在2-20之间"}), 400
    if len(password) < 4:
        return jsonify({"error": "密码至少4位"}), 400
    if invite != INVITE_CODE:
        return jsonify({"error": "邀请码无效"}), 403

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        db.close()
        return jsonify({"error": "用户名已存在"}), 409

    uid = str(uuid.uuid4())[:12]
    db.execute(
        "INSERT INTO users (id, username, password_hash, created) VALUES (?, ?, ?, ?)",
        (uid, username, hash_pw(password), datetime.now().isoformat())
    )
    db.commit()
    db.close()

    session["user_id"] = uid
    session["username"] = username
    return jsonify({"status": "ok", "username": username})

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    db = get_db()
    user = db.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ?", (username,)
    ).fetchone()
    db.close()

    if not user or user["password_hash"] != hash_pw(password):
        return jsonify({"error": "用户名或密码错误"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"status": "ok", "username": user["username"]})

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"status": "ok"})

@app.route("/api/auth/me", methods=["GET"])
def me():
    if "user_id" not in session:
        return jsonify({"authenticated": False})
    return jsonify({"authenticated": True, "username": session.get("username")})

# --- Routes: Config ---

@app.route("/api/config", methods=["GET", "POST"])
@login_required
def config():
    db = get_db()
    uid = current_user_id()
    if request.method == "POST":
        data = request.json
        db.execute("UPDATE users SET api_key = ? WHERE id = ?", (data.get("api_key", ""), uid))
        db.commit()
        db.close()
        return jsonify({"status": "ok"})

    user = db.execute("SELECT api_key FROM users WHERE id = ?", (uid,)).fetchone()
    db.close()
    has_own_key = bool(user["api_key"]) if user else False
    has_shared_key = bool(SHARED_API_KEY)
    return jsonify({"has_own_key": has_own_key, "has_shared_key": has_shared_key})

# --- Routes: Containers ---

@app.route("/api/containers", methods=["GET"])
@login_required
def list_containers():
    db = get_db()
    uid = current_user_id()
    containers = db.execute(
        "SELECT * FROM containers WHERE user_id = ? ORDER BY created DESC", (uid,)
    ).fetchall()

    result = []
    for c in containers:
        entries = db.execute(
            "SELECT * FROM entries WHERE container_id = ? ORDER BY timestamp ASC", (c["id"],)
        ).fetchall()
        result.append({
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "created": c["created"],
            "entries": [dict(e) for e in entries],
        })
    db.close()
    return jsonify({"containers": result})

@app.route("/api/containers", methods=["POST"])
@login_required
def create_container():
    data = request.json
    uid = current_user_id()
    cid = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    db = get_db()
    db.execute(
        "INSERT INTO containers (id, user_id, name, description, created) VALUES (?, ?, ?, ?, ?)",
        (cid, uid, data["name"], data.get("description", ""), now)
    )
    db.commit()
    db.close()

    return jsonify({
        "id": cid, "name": data["name"],
        "description": data.get("description", ""),
        "created": now, "entries": [],
    })

@app.route("/api/containers/<cid>", methods=["DELETE"])
@login_required
def delete_container(cid):
    db = get_db()
    uid = current_user_id()
    db.execute("DELETE FROM entries WHERE container_id = ? AND user_id = ?", (cid, uid))
    db.execute("DELETE FROM containers WHERE id = ? AND user_id = ?", (cid, uid))
    db.commit()
    db.close()
    return jsonify({"status": "ok"})

# --- Routes: Entries ---

@app.route("/api/entry", methods=["POST"])
@login_required
def create_entry():
    data = request.json
    uid = current_user_id()
    cid = data["container_id"]

    db = get_db()
    container = db.execute(
        "SELECT * FROM containers WHERE id = ? AND user_id = ?", (cid, uid)
    ).fetchone()
    if not container:
        db.close()
        return jsonify({"error": "Container not found"}), 404

    eid = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()

    if data["type"] == "vent":
        db.execute(
            "INSERT INTO entries (id, container_id, user_id, text, type, observation, thinking, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, cid, uid, data["text"], "vent", "已记录。", None, now)
        )
        db.commit()
        entry = dict(db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone())
        db.close()
        return jsonify({"entry": entry, "stream": False})

    # Reflect
    db.execute(
        "INSERT INTO entries (id, container_id, user_id, text, type, observation, thinking, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (eid, cid, uid, data["text"], "reflect", None, None, now)
    )
    db.commit()
    entry = dict(db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone())
    db.close()
    return jsonify({"entry": entry, "stream": True, "entry_id": eid})

@app.route("/api/observe", methods=["POST"])
@login_required
def observe():
    data = request.json
    uid = current_user_id()
    cid = data["container_id"]
    eid = data["entry_id"]

    db = get_db()
    container = db.execute(
        "SELECT * FROM containers WHERE id = ? AND user_id = ?", (cid, uid)
    ).fetchone()
    entry = db.execute(
        "SELECT * FROM entries WHERE id = ? AND user_id = ?", (eid, uid)
    ).fetchone()
    all_entries = db.execute(
        "SELECT * FROM entries WHERE container_id = ? AND user_id = ? ORDER BY timestamp ASC",
        (cid, uid)
    ).fetchall()
    db.close()

    if not container or not entry:
        return jsonify({"error": "Not found"}), 404

    # Get API key: user's own key first, then shared
    user_db = get_db()
    user = user_db.execute("SELECT api_key FROM users WHERE id = ?", (uid,)).fetchone()
    user_db.close()
    api_key = (user["api_key"] if user and user["api_key"] else "") or SHARED_API_KEY

    if not api_key:
        def error_gen():
            yield f"data: {json.dumps({'type': 'error', 'text': '没有可用的API Key。请在设置中输入你的DeepSeek API Key。'})}\n\n"
        return Response(error_gen(), mimetype="text/event-stream")

    messages = _build_messages(container, all_entries, entry)

    def generate():
        try:
            resp = http_requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "deepseek-reasoner",
                    "messages": messages,
                    "stream": True,
                },
                stream=True,
                timeout=120,
            )

            if resp.status_code != 200:
                error_msg = resp.text[:500]
                yield f"data: {json.dumps({'type': 'error', 'text': f'API error {resp.status_code}: {error_msg}'})}\n\n"
                return

            thinking_parts = []
            content_parts = []

            for line in resp.iter_lines():
                if not line:
                    continue
                decoded = line.decode("utf-8")
                if not decoded.startswith("data: "):
                    continue
                payload = decoded[6:]
                if payload == "[DONE]":
                    # Save results
                    save_db = get_db()
                    save_db.execute(
                        "UPDATE entries SET thinking = ?, observation = ? WHERE id = ?",
                        ("".join(thinking_parts), "".join(content_parts), eid)
                    )
                    save_db.commit()
                    save_db.close()
                    yield f"data: {json.dumps({'type': 'done'})}\n\n"
                    break
                try:
                    chunk = json.loads(payload)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})

                    rc = delta.get("reasoning_content")
                    if rc:
                        thinking_parts.append(rc)
                        yield f"data: {json.dumps({'type': 'thinking', 'text': rc})}\n\n"

                    ct = delta.get("content")
                    if ct:
                        content_parts.append(ct)
                        yield f"data: {json.dumps({'type': 'content', 'text': ct})}\n\n"
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

        except http_requests.exceptions.Timeout:
            yield f"data: {json.dumps({'type': 'error', 'text': 'Request timed out'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    return Response(generate(), mimetype="text/event-stream")

def _build_messages(container, all_entries, current_entry):
    # History = all entries before the current one
    history = [e for e in all_entries if e["timestamp"] < current_entry["timestamp"]]

    history_text = ""
    for e in history:
        ts = e["timestamp"][:16].replace("T", " ")
        history_text += f"\n---\n日期: {ts}\n类型: {e['type']}\n内容: {e['text']}\n"
        if e["observation"] and e["type"] == "reflect":
            history_text += f"系统观察: {e['observation']}\n"

    user_content = f"容器名称: {container['name']}\n"
    if container["description"]:
        user_content += f"容器描述: {container['description']}\n"

    if history_text:
        user_content += f"\n=== 历史日记 ==={history_text}\n"

    user_content += f"\n=== 当前日记 ===\n{current_entry['text']}"

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"\n  照鉴 · Zhaojian MVP")
    print(f"  http://localhost:{port}")
    print(f"  Invite code: {INVITE_CODE}\n")
    app.run(debug=False, port=port, host="0.0.0.0")
