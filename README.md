# 照鉴 · Zhaojian

Cognitive mirror journaling. Powered by DeepSeek Reasoner.

## Architecture: Agent + Skills

The analysis engine uses a modular **Agent + Skills** architecture:

**Agent Core** — Slim identity prompt defining voice, constraints, and orchestration logic. The agent knows *how* to be a cognitive mirror but delegates *what lens to apply* to skills.

**Skills** — Modular analytical capabilities, each with:
- Trigger heuristics (Python-side pattern matching)
- Analytical instructions (injected into the system prompt)
- Priority weights (for selection ranking)

### Available Skills

| Skill | ID | What it does |
|---|---|---|
| 量化扫描 | `quantitative` | Count word frequency, space allocation ratios, sentence length patterns |
| 叙事追踪 | `narrative` | Follow narrative arc, find structural breaks and omissions |
| 句法透视 | `syntax` | Analyze word choice, hedge markers, grammatical anomalies |
| 生物透镜 | `biological` | Map narratives to biological mechanisms (dopamine, cortisol, etc.) |
| 跨线索织网 | `cross_thread` | Find patterns across journal entries in the same container |
| 时间棱镜 | `temporal` | Analyze temporal structure — tense usage, time reference distribution |
| 急性协议 | `distress` | Override: detect acute distress, acknowledge and stop |

### How Selection Works

1. User writes a journal entry
2. Python heuristics score each skill against the entry text (keyword matching, length analysis, pattern counting)
3. Top 1-3 skills are selected and injected into the system prompt
4. The agent (DeepSeek Reasoner) chooses the best-fit lens from the shortlist
5. Skill metadata is streamed to the frontend and stored with the message

### Adding New Skills

In `skills.py`, register a new skill:

```python
_register(Skill(
    id="your_skill",
    name="中文名",
    label="english-label",
    description="What this skill does",
    priority=1,
    triggers=["keyword1", "keyword2"],
    prompt="""### 技能：你的技能名
    Instructions for the agent when using this skill..."""
))
```

Then add trigger patterns and scoring logic in `select_skills()`.

## Data Structure

- **Containers**: Psychological domains (e.g. "work stress", "relationships")
- **Threads**: Individual journal entries within a container
- **Messages**: Back-and-forth conversation within a thread — write, receive observation, respond, continue

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask server, routes, streaming, message building |
| `skills.py` | Skill definitions, heuristic selector, prompt composer |
| `index.html` | Main app UI (3-panel layout) |
| `login.html` | Auth page |

## Deploy

Push to GitHub, deploy on Railway, set env vars.

## Env vars

| Variable | Description |
|---|---|
| `DEEPSEEK_API_KEY` | Shared key for free-trial users |
| `ZHAOJIAN_SECRET` | Session encryption (any random string) |
| `ZHAOJIAN_INVITE` | Registration invite code |
