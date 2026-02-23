# 照鉴 · Zhaojian — Hosted Version

A multi-user cognitive mirror journaling app.

## Deploy to Railway (easiest, ~5 minutes)

### 1. Put the code on GitHub

Go to [github.com/new](https://github.com/new), create a new repository (private is fine).

On your Mac, in Terminal:

```bash
cd ~/Desktop/zhaojian-hosted   # wherever you put these files
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO_NAME.git
git push -u origin main
```

If you've never used git, you may need to install it first:
```bash
xcode-select --install
```

### 2. Deploy on Railway

1. Go to [railway.app](https://railway.app) and sign in with GitHub
2. Click **"New Project"** → **"Deploy from GitHub Repo"**
3. Select your repo
4. Railway will auto-detect it's a Python app. Click **"Deploy"**

### 3. Set environment variables

In your Railway project, go to **Variables** and add:

| Variable | Value |
|---|---|
| `DEEPSEEK_API_KEY` | `sk-...` (your DeepSeek key — this is the shared free-trial key for all users) |
| `ZHAOJIAN_SECRET` | Any random string (e.g. `myapp-secret-abc123`) |
| `ZHAOJIAN_INVITE` | An invite code you make up (e.g. `mirror2026`) — give this to your testers |
| `PORT` | `8080` |

### 4. Get your URL

Railway gives you a URL like `https://zhaojian-production-xxxx.up.railway.app`. 

Click **Settings** → **Networking** → **Generate Domain** if you don't see one.

Send that URL + your invite code to your testers. Done.

---

## How it works

- Users register with the invite code, then log in
- Each user has their own containers and entries (fully isolated)
- Users can optionally set their own DeepSeek API key in the UI; otherwise your shared key is used
- Data is stored in a SQLite file on the server

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DEEPSEEK_API_KEY` | Yes | Shared DeepSeek API key for users who don't bring their own |
| `ZHAOJIAN_SECRET` | Yes | Session encryption key (any random string) |
| `ZHAOJIAN_INVITE` | No | Invite code for registration (default: `zhaojian2026`) |
| `PORT` | No | Server port (default: `8080`) |

## Cost estimate

DeepSeek Reasoner pricing (as of early 2025):
- ~$2.19/million input tokens, ~$8.87/million output tokens
- A typical journal entry + observation ≈ 2K-4K tokens
- 20 users × 3 entries/day × 30 days ≈ $5-15/month

## Local development

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-...
export ZHAOJIAN_SECRET=dev-secret
export ZHAOJIAN_INVITE=test
python app.py
# → http://localhost:8080
```
# zhaojian
# zhaojian
