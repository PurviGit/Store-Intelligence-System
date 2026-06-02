# Store Intelligence System — Architecture Design

## Overview

This system converts raw CCTV footage from a physical retail store into a live analytics API — the "Google Analytics for offline retail." Starting from five camera feeds at Brigade Bangalore (store ID: `STORE_BLR_002`), it produces a continuous stream of structured behavioural events and surfaces them through a queryable REST API with a live web dashboard.

The entire pipeline is designed around one business metric: **offline store conversion rate** — the fraction of visitors who complete a purchase.

---

## System Architecture

```
CCTV Clips (5 cameras)
       │
       ▼
┌─────────────────────────────┐
│  Detection Layer (pipeline/)│
│  detect.py ──► tracker.py   │
│       └──► emit.py          │
│  Output: events.jsonl       │
└────────────┬────────────────┘
             │ POST /events/ingest (batches of 100)
             ▼
┌─────────────────────────────────────────────────────┐
│  Intelligence API (app/)                            │
│  FastAPI + SQLAlchemy + SQLite                      │
│  ├── POST /events/ingest (idempotent, dedup)        │
│  ├── GET  /stores/{id}/metrics                      │
│  ├── GET  /stores/{id}/funnel                       │
│  ├── GET  /stores/{id}/heatmap                      │
│  ├── GET  /stores/{id}/anomalies                    │
│  └── GET  /health                                   │
└────────────────────────┬────────────────────────────┘
                         │ polls every 5 seconds
                         ▼
               ┌──────────────────┐
               │ Live Dashboard   │
               │ Flask + HTML/JS  │
               │ localhost:3000   │
               └──────────────────┘
```

## Stage 1 — Detection Layer

**Model:** OpenCV MOG2 (Mixture of Gaussians) background subtractor.

I chose MOG2 over YOLOv8 deliberately. The problem requires docker compose up to work reliably on any machine without a GPU. YOLOv8 requires downloading model weights and introduces additional runtime dependencies. While it provides higher accuracy, especially under occlusion, I prioritised reliability, CPU-only execution, and acceptance-gate success for this challenge. MOG2 runs immediately inside the Docker container with no external downloads. The trade-off is lower detection accuracy in crowded scenes and heavy occlusion, but for fixed-angle retail cameras it performs sufficiently well.

In a production deployment I would migrate to YOLOv8n combined with ByteTrack for more robust detection and tracking under occlusion. The architecture isolates the detection layer from the event-processing and analytics layers, making the detector fully swappable without requiring changes to the event schema or API implementation.

**Tracking:** Custom IoU + centroid tracker (`tracker.py`). Each detection is matched to existing tracks by bounding box IoU (threshold 0.3). Tracks that disappear for more than 45 frames (~3 seconds) are moved to an `ExitedVisitor` list.

**Re-ID:** When a new detection appears within 120px of a recently exited visitor's centroid, with a bounding box area ratio between 0.5 and 2.0, the same `visitor_id` is reused and a `REENTRY` event is emitted rather than a new `ENTRY`. This directly solves the re-entry inflation problem described in the challenge.

**Staff Classification:** Heuristic — detections with bounding box area > 12,000px² that persist for more than 150 frames are classified as `is_staff=true`. Staff events are stored but excluded from all customer-facing metrics.

**Group Entry:** A blob wider than 200px is split into two sub-detections before tracking, so three people entering simultaneously produce three ENTRY events rather than one.

**Camera Roles:**
| Camera | Role | Events emitted |
|--------|------|----------------|
| CAM 1 | Entry/Exit threshold | ENTRY, EXIT, REENTRY |
| CAM 2 | Main floor | ZONE_ENTER, ZONE_EXIT, ZONE_DWELL (SKINCARE/HAIRCARE) |
| CAM 3 | Billing counter | BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON |
| CAM 4 | Product zone | ZONE_ENTER (MAKEUP, DERMDOC) |
| CAM 5 | Product zone | ZONE_ENTER (EB_KOREAN, BATH_BODY) |

## Stage 2 — Event Schema

All events conform to the required schema: `event_id` (UUID v4), `store_id` (STORE_BLR_002), `camera_id`, `visitor_id`, `event_type`, `timestamp` (ISO-8601 UTC), `zone_id`, `dwell_ms`, `is_staff`, `confidence`, and `metadata` (queue_depth, sku_zone, session_seq).

Eight event types are supported: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY.

## Stage 3 — Intelligence API

**Framework:** FastAPI — automatic OpenAPI docs, Pydantic v2 validation, async-capable.

**Database:** SQLite via SQLAlchemy 2.0, configurable via `DATABASE_URL` env var. Switching to PostgreSQL in production is a one-line config change.

**Conversion Rate Calculation:** No customer_id exists in POS data. I correlate by time window: visitors who entered the BILLING zone within 5 minutes before a POS transaction timestamp count as converted. This is the method specified in the problem statement.

**Idempotency:** Each event carries a UUID `event_id`. `POST /events/ingest` checks for existing `event_id` before inserting — identical payloads can be sent multiple times safely.

**Graceful Degradation:** Database unavailability returns HTTP 503 with a structured JSON body (no stack traces). Checked by the DB middleware on every request.

**Structured Logging:** Every request logs: `trace_id`, `endpoint`, `method`, `status_code`, `latency_ms`, `store_id` as JSON.

## Stage 4 — Live Dashboard

Flask web app at port 3000 polls the API every 5 seconds and renders:

- Real-time metrics cards (visitors, conversion, queue depth, abandonment)
- Animated conversion funnel with drop-off percentages
- Active anomalies with severity colour coding
- Zone heatmap with frequency scores (0–100)

---

## AI-Assisted Decisions

**Decision 1 — MOG2 vs YOLOv8 for detection**
Claude suggested starting with YOLOv8n (nano) because it has better accuracy on partial occlusion. I overrode this for a specific reason: the acceptance gate requires `docker compose up` on a clean machine. YOLOv8 downloads a 6MB model on first run, which fails in air-gapped CI environments. MOG2 requires no downloads. In a production deployment I would migrate to YOLOv8n + ByteTrack for stronger detection and tracking under occlusion. The architecture isolates the detection layer from the analytics layer, allowing the detector to be swapped without changing the event schema or API implementation. I documented this trade-off in CHOICES.md and added the upgrade path as a comment in detect.py.

**Decision 2 — SQLite vs PostgreSQL**
Claude's initial suggestion was PostgreSQL with a docker-compose healthcheck. I tested this and found it introduces a race condition: FastAPI starts in ~0.3s but PostgreSQL takes 4–8s to be ready, causing the acceptance gate to fail on first check. I switched to SQLite (starts instantly) with a `DATABASE_URL` env var so upgrading to PostgreSQL is one config line. Claude agreed once I explained the race condition.

**Decision 3 — Re-ID window for re-entry**
Claude suggested a 5-minute re-entry window. I reduced it to 2 minutes (1800 frames at 15fps) after reasoning that a genuine re-entry (someone steps out and comes back) happens within 2 minutes, while anything longer is more likely a new visitor from the same direction. I noted this disagreement in CHOICES.md.
