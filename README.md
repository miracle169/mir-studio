# Mir Studio

Personal AI content intelligence system. Voice-first thought capture → platform-ready content for LinkedIn, Beehiiv newsletter, and Instagram reels.

---

## Stack

- **Backend**: Python 3.11 + Flask 3.x
- **Database**: SQLite (WAL mode)
- **AI**: Anthropic Claude (claude-sonnet-4-5-20250929)
- **Scheduler**: APScheduler (daily 6am intel pipeline)
- **Deploy**: Railway.app

---

## First-Time Setup

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USERNAME/mir-studio
cd mir-studio
pip install -r requirements.txt
```

### 2. Create your `.env` file

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY and API_SECRET_KEY
```

Generate a secret key:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Initialise the database and seed Mir's knowledge base

```bash
python seed_kb.py
```

This runs once. Safe to re-run — uses `INSERT OR REPLACE`.

### 4. Run locally

```bash
python app.py
# Open http://localhost:5000
```

---

## Deploy to Railway

1. Push this repo to GitHub
2. Create a new Railway project → **Deploy from GitHub repo**
3. Add environment variables in Railway dashboard (copy from `.env.example`):
   - `ANTHROPIC_API_KEY`
   - `API_SECRET_KEY`
   - `CLAUDE_MODEL` (optional)
   - `DB_PATH` → set to `/app/mir_studio.db`
4. Add a **Volume** in Railway:
   - Mount path: `/app`
   - This persists the SQLite database across deploys
5. After first deploy, open the Railway shell and run:
   ```bash
   python seed_kb.py
   ```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Your Anthropic API key |
| `API_SECRET_KEY` | ✅ | Protects `/api/analytics/instagram` endpoint |
| `CLAUDE_MODEL` | ❌ | Defaults to `claude-sonnet-4-5-20250929` |
| `DB_PATH` | ❌ | Defaults to `mir_studio.db` |
| `PORT` | ❌ | Set automatically by Railway |

---

## API Endpoints

| Method | Route | Description |
|---|---|---|
| `GET` | `/` | Main app |
| `GET` | `/api/status` | Health check + streak |
| `POST` | `/api/settings/apikey` | Save API key to DB |
| `POST` | `/api/generate` | Generate content |
| `GET/POST` | `/api/raw-ideas` | Raw idea bank |
| `DELETE` | `/api/raw-ideas/<id>` | Delete idea |
| `GET` | `/api/content` | Content history |
| `PUT` | `/api/content/<id>` | Update content (rating, text) |
| `POST` | `/api/content/<id>/post` | Mark as posted |
| `DELETE` | `/api/content/<id>` | Delete content |
| `GET` | `/api/intel` | Cached intel |
| `POST` | `/api/intel/refresh` | Force intel refresh |
| `GET/POST` | `/api/stories` | Story bank |
| `GET` | `/api/stats` | Gamification stats |
| `GET` | `/api/kb` | Knowledge base viewer |
| `POST` | `/api/upload` | Photo upload (Claude Vision) |
| `POST` | `/api/analytics/instagram` | Ingest reel analytics (Cowork) |

### Instagram Analytics Endpoint

Protected by `X-API-Key` header. Called by Cowork Chrome automation task every 2 days.

```bash
curl -X POST https://your-app.railway.app/api/analytics/instagram \
  -H "X-API-Key: YOUR_API_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "reels": [
      {
        "reel_id": "reel_abc123",
        "caption": "First line of the caption...",
        "plays": 4200,
        "likes": 180,
        "comments": 24,
        "shares": 35,
        "saves": 67,
        "reach": 3800,
        "posted_at": "2026-03-01T10:00:00"
      }
    ]
  }'
```

---

## Cowork Integration

The Cowork scheduled task (`instagram-analytics-collector`) runs every 2 days:
1. Opens Instagram Professional Dashboard in Chrome
2. Reads reel metrics from the page
3. POSTs data to `/api/analytics/instagram`
4. The app stores metrics + auto-classifies reels (top/mid/low tier)
5. Future reel scripts are informed by what performs best

---

## Daily Workflow

**Morning** — Intel arrives at 6am automatically. Open app, check Intel tab for angles on trending topics.

**Any time** — Hit the mic button, speak your thought. Platform pills auto-highlight. Tap Generate. Copy. Post.

**After posting a reel** — Do nothing. Cowork handles analytics every 2 days.

**Sunday night** — Weekly wrap card appears in Home with your totals.

---

## Project Structure

```
mir-studio/
├── app.py              # Flask backend, all routes, APScheduler
├── seed_kb.py          # One-time KB seeder (voice profile + stories)
├── requirements.txt
├── Procfile
├── .env.example
├── templates/
│   └── index.html      # Full 5-tab mobile SPA
└── static/
    ├── style.css       # iOS design system
    └── app.js          # All frontend logic
```
