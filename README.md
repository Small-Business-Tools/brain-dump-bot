# idea-bot

Personal AI-powered idea capture and scoring system.  
Telegram → Python → Claude API → SQLite → You.

---

## Setup

### 1. Get your keys

| Key | Where |
|-----|-------|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/botfather) on Telegram → /newbot |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `ALLOWED_USER_ID` | Message [@userinfobot](https://t.me/userinfobot) on Telegram |
| `RAILWAY_TOKEN` | Railway dashboard → Account Settings → Tokens |

---

### 2. Clone and configure locally

```bash
git clone https://github.com/YOUR_USERNAME/idea-bot.git
cd idea-bot
cp .env.example .env
# Edit .env with your keys
```

---

### 3. Run locally (optional, to test)

```bash
pip install -r requirements.txt
python bot.py
```

---

### 4. Deploy to Railway

**First deploy (manual):**
```bash
npm install -g @railway/cli
railway login
railway init        # creates a new project
railway up          # deploys
```

**Set environment variables in Railway dashboard:**  
Go to your project → Variables → add each key from `.env.example`.

**Add a persistent volume for the database:**  
Railway dashboard → your service → Volumes → Add Volume → mount at `/data`

**After first deploy, all future deploys are automatic:**  
Push to `main` → GitHub Actions → Railway redeploys within ~60 seconds.

---

### 5. Add RAILWAY_TOKEN to GitHub

GitHub repo → Settings → Secrets and variables → Actions → New secret  
Name: `RAILWAY_TOKEN`, Value: your token from Railway dashboard.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Introduction |
| `/list` | All idea clusters with scores |
| `/digest` | Your top ideas with next actions |
| Any text | Captures as a new idea |

---

## Scoring model

| Dimension | Weight | Source |
|-----------|--------|--------|
| Idea density | 30% | Auto-calculated from your entries |
| Revenue fit | 25% | Claude |
| Effort (inverted) | 25% | Claude |
| Novelty | 20% | Claude |

**Idea density** = (entry count × 40%) + (span of days × 35%) + (content depth × 25%)

The more you return to an idea and add detail, the higher its density score.

---

## File structure

```
idea-bot/
├── bot.py              # Telegram listener + command routing
├── claude_client.py    # Claude API calls (categorise, cross-link, score)
├── store.py            # SQLite read/write
├── scorer.py           # Density + full scoring logic
├── digest.py           # Weekly digest builder
├── requirements.txt
├── railway.toml        # Railway deploy config
├── .env.example        # Key template (never commit .env)
└── .github/
    └── workflows/
        └── deploy.yml  # Auto-deploy on push to main
```
