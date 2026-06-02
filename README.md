# Store Intelligence API

Physical-store analytics inspired by Google Analytics:
CCTV footage → customer journeys → business intelligence.
Real-time store analytics from CCTV footage — Brigade Bangalore (`STORE_BLR_002`).

> **Dataset:** April 10, 2026 · 5 CCTV cameras · 4,879 events · 60 unique visitors · 24 POS transactions  
> Events auto-load on first startup. Add `?date=2026-04-10` to all API calls.

## Dashboard Preview

![Dashboard](docs/Dashboard.png)

![Dashboard](docs/Dashboard1.png)

---

## Setup in 5 Commands

```bash
# 1. Clone the repo
git clone <your-repo-url> store-intelligence
cd store-intelligence

# 2. Install dependencies
pip install fastapi uvicorn sqlalchemy pydantic httpx pytest opencv-python-headless requests flask

# 3. Start the API  (data auto-loads on first run)
cd app
python -m uvicorn main:app --host 0.0.0.0 --port 8000

# 4. Start the live dashboard (new terminal)
cd dashboard
python app.py

# 5. Open in browser
#    Dashboard : http://localhost:3000
#    API docs  : http://localhost:8000/docs
```

**That's it.** The API auto-ingests `data/events.jsonl` on first startup — no manual ingest step needed.

---

## Docker (One Command)

```bash
docker compose up --build
```

- API → `http://localhost:8000`
- Dashboard → `http://localhost:3000`

---

## API Endpoints

| Method | Endpoint                 | Description                                                 |
| ------ | ------------------------ | ----------------------------------------------------------- |
| POST   | `/events/ingest`         | Ingest batch of events (max 500, idempotent)                |
| GET    | `/stores/{id}/metrics`   | Unique visitors, conversion rate, dwell, queue, abandonment |
| GET    | `/stores/{id}/funnel`    | Entry → Zone → Billing → Purchase with drop-off %           |
| GET    | `/stores/{id}/heatmap`   | Zone visit frequency + dwell, normalised 0–100              |
| GET    | `/stores/{id}/anomalies` | Active anomalies with severity + suggested actions          |
| GET    | `/health`                | DB status, last event per store, STALE_FEED warnings        |

**Store ID:** `STORE_BLR_002`  
**Data Date:** `2026-04-10`

**Example:**

```
GET http://localhost:8000/stores/STORE_BLR_002/metrics?date=2026-04-10
GET http://localhost:8000/stores/STORE_BLR_002/funnel?date=2026-04-10
GET http://localhost:8000/stores/STORE_BLR_002/heatmap?date=2026-04-10
GET http://localhost:8000/stores/STORE_BLR_002/anomalies?date=2026-04-10
```

---

## Running the Detection Pipeline

To regenerate events from raw CCTV clips:

```bash
cd pipeline

# Process clips → events.jsonl
python detect.py --clips-dir /path/to/cctv/ --output ../data/events.jsonl --every 5

# Feed into API (only needed if you want to reload after regenerating)
python ingest_events.py --events ../data/events.jsonl --api http://localhost:8000
```

---

## Running Tests

```bash
cd tests
set PYTHONPATH=../app    # Windows
# export PYTHONPATH=../app  # Mac/Linux

pytest test_pipeline.py test_metrics.py test_anomalies.py -v
```

37 tests · 3 files · all edge cases covered.

---

## Self-Validation

```bash
python assertions.py http://localhost:8000
# Expected: 12 passed, 0 failed
```

---

## Project Structure

```
store-intelligence/
├── pipeline/
│   ├── detect.py         # MOG2 detection + group split + staff classifier
│   ├── tracker.py        # IoU tracker + Re-ID re-entry detection
│   ├── emit.py           # Event schema builder (8 event types)
│   └── ingest_events.py  # Batch uploader to API
├── app/
│   ├── main.py           # FastAPI entrypoint + auto-ingest on startup
│   ├── database.py       # SQLAlchemy + SQLite (swap to Postgres via env var)
│   ├── models.py         # Pydantic schema + ORM
│   ├── ingestion.py      # POST /events/ingest (idempotent, partial success)
│   ├── metrics.py        # GET /stores/{id}/metrics
│   ├── funnel.py         # GET /stores/{id}/funnel
│   ├── heatmap.py        # GET /stores/{id}/heatmap
│   ├── anomalies.py      # GET /stores/{id}/anomalies
│   └── health.py         # GET /health (STALE_FEED detection)
├── dashboard/
│   └── app.py            # Flask web dashboard (auto-refreshes every 5s)
├── data/
│   ├── events.jsonl      # 4879 pre-generated events from CCTV clips (all 8 event types)
│   ├── pos_transactions.csv  # Real Brigade Bangalore POS data (24 orders)
│   └── store_layout.json # Zone definitions (SKINCARE, EB_KOREAN, MAKEUP...)
├── tests/
│   ├── conftest.py
│   ├── test_pipeline.py  # Ingest edge cases (13 tests)
│   ├── test_metrics.py   # Metrics/funnel/heatmap/health (16 tests)
│   └── test_anomalies.py # Anomaly detection (8 tests)
├── docs/
│   ├── DESIGN.md         # Architecture decisions + AI-assisted design
│   └── CHOICES.md        # 3 key decisions with trade-off analysis
├── assertions.py         # 10 self-validation assertions
├── docker-compose.yml
└── Dockerfile
```

## System Architecture

CCTV Cameras (5)
↓
OpenCV MOG2 Detection
↓
Tracker + Re-ID
↓
Structured Events
↓
SQLite Database
↓
FastAPI Analytics Layer
↓
Dashboard + Swagger API

---

## Requirement Coverage

| Requirement        | Implementation       |
| ------------------ | -------------------- |
| Detection Pipeline | OpenCV MOG2          |
| Re-entry Handling  | IoU + Centroid Re-ID |
| Event Schema       | emit.py              |
| Metrics API        | metrics.py           |
| Funnel Analysis    | funnel.py            |
| Heatmap            | heatmap.py           |
| Anomaly Detection  | anomalies.py         |
| Dashboard          | dashboard/app.py     |
| Tests              | 37 automated tests   |

---

## Key Design Decisions

| Decision        | Choice                                         | Why                                                 |
| --------------- | ---------------------------------------------- | --------------------------------------------------- |
| Detection       | OpenCV MOG2                                    | Runs on CPU — no GPU needed for `docker compose up` |
| Tracker         | Custom IoU + centroid Re-ID                    | Handles re-entry without YOLOv8 dependency          |
| Database        | SQLite → PostgreSQL via `DATABASE_URL` env var | Zero-setup locally, production-ready swap           |
| API             | FastAPI                                        | Auto-generates Swagger docs, Pydantic validation    |
| Conversion rate | POS transactions / unique visitors             | Only reliable source without customer IDs           |

Full reasoning in [`docs/CHOICES.md`](docs/CHOICES.md)

---

## Results (Brigade Bangalore – Apr 10, 2026)

| Metric          | Value  |
| --------------- | ------ |
| Unique Visitors | 60     |
| Purchases       | 24     |
| Conversion Rate | 40.0%  |
| Revenue         | ₹8,509 |
| Queue Depth     | 10     |
| Total Events    | 4,879  |
| CCTV Cameras    | 5      |

---

## North Star Metric

**Offline conversion rate = purchases ÷ unique visitors**

Everything in this system — detection accuracy, funnel stages, anomaly detection — exists to make this single number accurate and actionable for Purplle store managers.

For Brigade Bangalore on April 10, 2026: **40.0% conversion** (24 purchases / 60 unique visitors).
