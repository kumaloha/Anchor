# Anchor

**Opinion tracking and verification system for X (Twitter) and YouTube content.**

Anchor collects posts and videos from public commentators, uses LLMs to extract and classify opinions into four types (prediction / history / advice / commentary), and tracks/verifies them over time.

---

## Features

- 🐦 **X (Twitter) & YouTube collection** — automated crawling via tweepy and yt-dlp
- 🎙️ **Video transcription** — openai-whisper for YouTube audio-to-text
- 🤖 **LLM extraction** — GPT-4o extracts distinct opinions from raw text/transcripts
- 🏷️ **4-type classification** — prediction, history, advice, commentary with type-specific attributes
- ✅ **Automated verification** — per-type trackers check predictions, validate historical claims, evaluate advice
- 📋 **REST API** — full CRUD + async task dispatch via Celery
- ⏰ **Beat scheduling** — crawls all active bloggers every 6 hours

---

## Architecture

```
Anchor/
├── backend/
│   ├── app/
│   │   ├── core/           # Config, database, Celery setup
│   │   ├── models/         # SQLAlchemy ORM models (PostgreSQL)
│   │   ├── schemas/        # Pydantic v2 schemas
│   │   ├── services/
│   │   │   ├── collectors/ # Twitter + YouTube data collection
│   │   │   ├── extractors/ # LLM opinion extraction
│   │   │   ├── processors/ # Opinion classification
│   │   │   └── trackers/   # Per-type verification trackers
│   │   ├── api/            # FastAPI routers and endpoints
│   │   └── tasks/          # Celery tasks
│   ├── alembic/            # Database migrations
│   ├── requirements.txt
│   └── run.py              # Convenience runner
└── docker-compose.yml      # PostgreSQL + Redis
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Docker + Docker Compose
- ffmpeg (for yt-dlp audio processing)

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

### 2. Start Infrastructure

```bash
cd /path/to/Anchor
docker-compose up -d
```

This starts:
- PostgreSQL on `localhost:5432` (user/pass/db: `anchor`)
- Redis on `localhost:6379`

### 3. Install Python Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
cp .env.example .env
# Edit .env with your keys:
# - OPENAI_API_KEY
# - TWITTER_BEARER_TOKEN (optional, only needed for X crawling)
```

### 5. Run Database Migrations

```bash
cd backend
alembic upgrade head
```

Or use the convenience runner:

```bash
python run.py migrate
```

### 6. Start the API Server

```bash
python run.py
# or
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs available at: http://localhost:8000/docs

### 7. Start the Celery Worker (separate terminal)

```bash
cd backend
python run.py worker
```

### 8. Start the Beat Scheduler (separate terminal, optional)

```bash
cd backend
python run.py beat
```

---

## API Reference

### Bloggers

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/bloggers` | Add a new blogger |
| `GET` | `/api/bloggers` | List all bloggers |
| `GET` | `/api/bloggers/{id}` | Get blogger by ID |
| `PATCH` | `/api/bloggers/{id}` | Update blogger |
| `DELETE` | `/api/bloggers/{id}` | Delete blogger |

**Add a blogger:**
```json
POST /api/bloggers
{
  "platform": "youtube",
  "url": "https://www.youtube.com/@SomeChannel",
  "name": "Some Channel",
  "description": "Finance commentary"
}
```

### Ingest

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/ingest/crawl/{blogger_id}` | Trigger crawl for a blogger |
| `POST` | `/api/ingest/url` | Ingest a single URL |
| `POST` | `/api/ingest/manual` | Submit raw text manually |

**Manual ingest:**
```json
POST /api/ingest/manual
{
  "blogger_id": 1,
  "text": "I believe BTC will reach $200k by end of 2025.",
  "language": "en"
}
```

### Opinions

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/opinions` | List opinions (with filters) |
| `GET` | `/api/opinions/{id}` | Get opinion detail |
| `PATCH` | `/api/opinions/{id}` | Update opinion |
| `DELETE` | `/api/opinions/{id}` | Delete opinion |
| `GET` | `/api/opinions/{id}/verifications` | List verification records |

**Filter opinions:**
```
GET /api/opinions?opinion_type=prediction&status=pending&blogger_id=1
```

### Tracking

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/tracking/run/{opinion_id}` | Trigger tracking for an opinion |
| `GET` | `/api/tracking/summary` | Get overview statistics |

---

## Opinion Types

| Type | Description | Key Attributes |
|------|-------------|----------------|
| **prediction** | Forward-looking claim about future events | deadline, verification status |
| **history** | Claim about past events | completeness, assumption level, verifiability |
| **advice** | Recommendation or prescriptive guidance | basis, rarity score, importance score, action items |
| **commentary** | Analysis or critique of a person/event | sentiment, target subject, public opinion |

---

## Data Models

### Opinion Status Flow

```
pending → tracking → verified
                   → refuted
                   → expired
                   → closed
```

### Opinion Abstraction Levels

- **Level 1**: Raw/verbatim — closely mirrors source wording
- **Level 2**: Summary — paraphrased/compressed
- **Level 3**: Core theme — high-level synthesis

---

## Configuration

All settings are in `backend/.env`. See `.env.example` for the full list.

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection string |
| `OPENAI_API_KEY` | OpenAI API key (required for extraction/classification) |
| `TWITTER_BEARER_TOKEN` | Twitter API v2 bearer token (required for X crawling) |
| `OPENAI_MODEL` | Model to use (default: `gpt-4o`) |
| `WHISPER_MODEL` | Whisper model size (default: `base`) |
| `CRAWL_INTERVAL_HOURS` | How often to crawl all bloggers (default: `6`) |
| `YT_MAX_RECENT_VIDEOS` | Max videos to fetch per YouTube channel (default: `5`) |

---

## Development

```bash
# Check API docs
open http://localhost:8000/docs

# Check task status
celery -A app.core.celery_app.celery_app inspect active

# Monitor Celery (optional)
pip install flower
celery -A app.core.celery_app.celery_app flower
```

---

## License

See [LICENSE](LICENSE).
