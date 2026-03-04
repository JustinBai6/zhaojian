# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run locally:**
```bash
FLASK_APP=app.py flask run
```

**Run with gunicorn (matches production):**
```bash
gunicorn app:app --bind 0.0.0.0:5000 --timeout 240 --workers 2
```

**Install dependencies:**
```bash
pip install flask requests gunicorn
```

No test suite exists. Manual testing via the browser UI or curl against the local server.

## Architecture

**照鉴 (Zhaojian)** is a Flask + SQLite journaling app powered by DeepSeek Reasoner. The core design is an **Agent + Skills** system where analytical lenses are dynamically selected per-entry rather than applied uniformly.

### Two-file backend

- **`app.py`** — Flask routes, DB access, streaming SSE endpoint, message assembly, extraction call
- **`skills.py`** — Skill registry, heuristic scorer, all prompt builders (system, extraction, query, synthesis)

### Two-phase LLM pipeline

Every journal reflection triggers two separate API calls to DeepSeek Reasoner:

1. **Streaming call** (`generate()` in `app.py`): yields SSE events to the frontend in real time. Streams `reasoning_content` (stored but hidden from UI) then `content` (the observation).
2. **Non-streaming extraction call** (`_run_extraction()` in `app.py`): runs after streaming completes, parses structured JSON from the observation — affect valence/intensity, high-frequency words, salience markers, patterns update for the container archive.

### Skill selection flow

`select_skills()` in `skills.py` runs Python-side heuristics (keyword matching, word repeat scoring, sentence length analysis) to score and rank all skills, then returns the top 1–3. Their prompts are injected into the system prompt before the streaming call. The `distress` skill is an override — if triggered, it suppresses all other skills.

### Data hierarchy

`users` → `containers` (psychological domains) → `threads` (individual entries) → `messages`

- `containers.patterns` (JSON text column) stores a cumulative archive of recurring words and linguistic patterns, updated after each extraction call.
- `messages.thinking` stores the model's reasoning process (not shown in UI).
- `messages.derived_state` stores the extraction JSON.
- `messages.skills_used` stores the IDs of skills applied.

### Thread types

- `reflect` — full Agent + Skills analysis
- `vent` — immediate "已记录" acknowledgment, no AI call (unless distress override fires)
- `query` — user asks questions about their own data; retrieves entries + derived states, returns data without interpretation

### Frontend

Single-page app in `index.html` (~3000 lines of vanilla JS + CSS). 3-panel layout: container list / thread list+dashboard / chat or dashboard. Communicates with the backend via SSE (`/api/reflect`) for streaming and REST for everything else. No framework, no build step.

### Adding a new skill

In `skills.py`, call `_register(Skill(...))` with the required fields, then add scoring logic in `select_skills()`. The `distress` skill shows how to implement an override that suppresses all others.

## Environment variables

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` | Shared free-trial key (users can also supply their own via `/api/config`) |
| `ZHAOJIAN_SECRET` | Flask session secret |
| `ZHAOJIAN_INVITE` | Registration invite code |
| `DB_DIR` | Directory for `zhaojian.db` (defaults to app.py location) |

## Deployment

Railway.app via `Procfile` and `railway.json`. Push to GitHub and set the env vars above.
