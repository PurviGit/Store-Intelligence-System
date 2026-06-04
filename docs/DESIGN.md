# Store Intelligence System — Architecture Design

## Overview

This system converts raw CCTV footage from physical retail stores into a live analytics API — the "Google Analytics for offline retail." It processes **eight camera feeds across two stores** — Store 1 (`STORE_BLR_001`, 4 cameras) and Brigade Bangalore (`STORE_BLR_002`, 4 cameras) — producing a continuous stream of structured behavioural events surfaced through a queryable REST API with a live web dashboard that supports switching between both stores.

The entire pipeline is designed around one business metric: **offline store conversion rate** — the fraction of visitors who complete a purchase.

---

## System Architecture

```
                           ┌─────────────────────────────────────┐
   Mode A (no clips)  ───► │  generate_demo_events.py            │
   Mode B (synthetic) ───► │  generate_demo_clips.py + detect.py │ ──► events.jsonl
   Mode C (real clips) ──► │  detect.py + tracker.py + emit.py   │
                           └──────────────────┬──────────────────┘
                                              │ POST /events/ingest (batches of 500)
CCTV Clips (8 cameras, 2 stores)              ▼
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

**Model:** YOLOv8n (nano) — the lightest YOLO v8 variant, optimised for CPU-only inference.

YOLOv8n detects persons as class=0 with per-detection confidence scores (0.35–0.99). It runs at effective ~3fps on CPU when processing every 5th frame from 15fps source — sufficient for retail CCTV where events happen on a seconds timescale. The model weights (6MB) are pre-downloaded during `docker build`, so the container starts with zero download latency.

**Why YOLOv8n over MOG2:** MOG2 detects moving pixels, not persons. It cannot distinguish a shopping bag from a person, produces no real confidence scores, and incorrectly merges groups of people entering together into a single blob. YOLOv8n resolves all three issues: person-tight bounding boxes, real confidence values directly from the detector, and separate detections for each person in a group (group assignment uses centroid proximity ≤80px).

**Why YOLOv8n over YOLOv8x/RT-DETR:** The larger models require a GPU to run at useful frame rates. YOLOv8n is the only YOLO variant that achieves practical throughput on CPU. The detection accuracy difference between nano and small/medium is marginal for fixed retail cameras with controlled backgrounds.

**Tracking:** Custom IoU + centroid tracker (`tracker.py`). Each detection is matched to existing tracks by bounding box IoU (threshold 0.3). Tracks that disappear for more than 30 processed frames (~16 seconds at 15fps/every_n=8) are moved to an `ExitedVisitor` list.

**Re-ID (intra-camera):** When a new detection appears within **200px** of a recently exited visitor's **initial entry centroid** (the position where they first appeared at the door, not their last-seen position inside the store), within a 4,500-frame window (5 minutes at 15fps), with a bounding box area ratio between 0.4 and 2.5, the same `visitor_id` is reused and a `REENTRY` event is emitted.

**Cross-camera session stitching (API layer, `app/sessions.py`):** Each camera runs an independent tracker so visitor_ids are camera-scoped. The funnel uses a two-pass `SessionStitcher` to correctly attribute zone visits and billing events to entry sessions:
- **Pass 1 — Direct ID match**: zone event visitor_id is directly in the entry sessions set. This handles scoring-harness test data where visitor_ids are consistent across cameras.
- **Pass 2 — Time-window match**: if no direct match, the zone event timestamp is checked against each entry session window `[entry_ts, exit_ts + 60s]`. If it falls within any window, it belongs to that session. This handles real pipeline data with camera-scoped IDs.
Covered by `tests/test_session_stitching.py` — 8 test cases including the scoring-harness scenario and the camera-scoped-ID scenario.

Critical fix implemented: previous versions stored the *last-seen* centroid (inside the store) for re-entry matching. This never matched because re-entering customers approach from the door (initial position), not from where they were last seen browsing. Storing the *initial entry centroid* fixes this. Verified correct with a unit test: a visitor exits at frame 300, re-enters at frame 1500 within 3px of original door position → same `visitor_id` returned, `is_reentry=True`.

**On REENTRY=0 in provided clips:** The provided entry camera clips are 2-5 minutes long (the PS describes 20-minute clips). In 2-5 minutes, no customer physically left the store and returned — which is realistic. The detection logic is correct and fires when someone genuinely leaves and returns. The unit test proves this.

**Staff Classification (4-signal heuristic):** Faces are fully blurred so uniform/facial recognition is impossible. Four complementary signals in `classify_staff()`:

1. **Sustained presence + large area**: bbox area > 3,500px² AND 60+ processed frames. Staff near counters produce slightly larger person-boxes (apron/uniform bulk).
2. **High confidence + very long presence**: conf > 0.93 AND 120+ frames AND area > 2,500px². Staff stand still in fixed positions, producing more stable, high-confidence detections.
3. **Direction reversals**: y-velocity sign changes > 8 in 100+ frames. Staff move back and forth (restocking, helping customers). Customers walk predominantly in one direction. Tracked via `track.direction_reversals` counter updated in `Tracker.update()`.
4. **Presence ratio**: if track spans > 35% of total clip frames with 80+ frames of presence. A customer who browses for 35% of a 20-minute clip (7 minutes) is unusual enough to warrant staff classification.

Thresholds are intentionally conservative to avoid false positives on tall customers standing still. Unit tests in `test_pipeline_units.py` cover all 4 signal paths.

**Group Entry:** A blob wider than 120px is split into two sub-detections before tracking (single person ≈ 60–80px wide in these cameras; 120px+ indicates 2+ people). Each sub-detection gets a unique `visitor_id` with the same `group_id`, so three people entering simultaneously produce three ENTRY events rather than one.

**Camera Roles — Store 1 (STORE_BLR_001):**

| File | Camera ID | Role | Events emitted |
|------|-----------|------|----------------|
| `CAM 3 - entry.mp4` | CAM_ENTRY_03 | Entry/Exit threshold | ENTRY, EXIT, REENTRY |
| `CAM 1 - zone.mp4` | CAM_ZONE_01 | Zone — Minimalist/Aqualogica/Foxtale | ZONE_ENTER, ZONE_EXIT, ZONE_DWELL |
| `CAM 2 - zone.mp4` | CAM_ZONE_02 | Zone — Salm/TFS/JC | ZONE_ENTER, ZONE_EXIT, ZONE_DWELL |
| `CAM 5 - billing.mp4` | CAM_BILLING_05 | Billing counter | BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON |

**Camera Roles — Store 2 (STORE_BLR_002):**

| File | Camera ID | Role | Events emitted |
|------|-----------|------|----------------|
| `entry 1.mp4` | CAM_ENTRY_01 | Entry/Exit threshold | ENTRY, EXIT, REENTRY |
| `entry 2.mp4` | CAM_ENTRY_01B | Entry/Exit threshold (second door) | ENTRY, EXIT, REENTRY |
| `zone.mp4` | CAM_ZONE_02 | Zone — EB Korean/DermDoc | ZONE_ENTER, ZONE_EXIT, ZONE_DWELL |
| `billing_area.mp4` | CAM_BILLING_03 | Billing counter | BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON |

## Stage 2 — Event Schema

All events conform to the required schema specified in the problem statement and validated against `sample_events.jsonl` (the reference resource provided). Core fields: `event_id` (UUID v4), `store_id` (STORE_BLR_001 or STORE_BLR_002), `camera_id`, `visitor_id`, `event_type`, `timestamp` (ISO-8601 UTC), `zone_id`, `dwell_ms`, `is_staff`, `confidence`, and `metadata` (queue_depth, sku_zone, session_seq).

The updated resource `sample_events.jsonl` introduced additional fields now fully supported: `gender_pred`, `age_pred`, `age_bucket` (demographic signals — heuristic proxies since faces are blurred), `is_face_hidden` (always true per spec), `group_id`/`group_size` (populated when blob-splitting detects simultaneous entry), `zone_hotspot_x`/`zone_hotspot_y` (pixel coordinates from `store_layout.json`), `zone_name`, `zone_type`, and `is_revenue_zone`.

Eight event types are supported: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, ZONE_DWELL, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON, REENTRY.

## Stage 3 — Intelligence API

**Framework:** FastAPI — automatic OpenAPI docs, Pydantic v2 validation, async-capable.

**Database:** SQLite via SQLAlchemy 2.0, configurable via `DATABASE_URL` env var. Switching to PostgreSQL in production is a one-line config change.

**Conversion Rate Calculation:** No customer_id exists in POS data. I correlate by time window: visitors who entered the BILLING zone within 5 minutes before a POS transaction timestamp count as converted. This is the method specified in the problem statement.

**Idempotency:** Each event carries a UUID `event_id`. `POST /events/ingest` checks for existing `event_id` before inserting — identical payloads can be sent multiple times safely.

**Partial ingest (important design choice):** `POST /events/ingest` accepts `List[Any]` at the Pydantic request level, then validates each event individually inside the handler. A batch of 500 events where 3 are malformed returns `{ingested: 497, errors: 3}` with per-event error detail — the whole batch does NOT fail with 422. This is the spec's explicit requirement ("Partial success on malformed events") and was a deliberate deviation from the default FastAPI pattern where `List[EventIn]` in the request model causes the entire batch to fail if any single event is invalid. The initial AI-generated code used `List[EventIn]` (cleaner but wrong for this requirement); I caught this discrepancy when writing the partial-success test and fixed it.

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

**Decision 1 — Trajectory-based ENTRY/EXIT direction (AI suggested position heuristic, I overrode it)**

Claude's initial suggestion for entry/exit detection was: "check if the centroid y-coordinate is in the top or bottom half of the frame." I implemented this first and tested it — it produced ~15% mis-classifications when people entered from one side of a door or when the camera was mounted at a non-standard height.

My fix: buffer 8 frames of centroid history per track, then compute a linear regression slope over the y-coordinates. Positive slope = person moving downward into store = ENTRY. Negative slope = moving toward door = EXIT. This delays the event by ~0.5s (8 frames at 15fps / every_n=5 = ~8 actual processed frames) but the direction classification accuracy improved significantly. For truly ambiguous cases (|slope| < 3px/frame), we fall back to the centroid position — but this only happens when someone is nearly stationary, which is rare at a door threshold.

Implemented in `tracker.py::_confirm_direction()` and `tracker.py::_net_y_velocity()`. Unit tests in `tests/test_session_stitching.py` verify correct direction for hand-crafted centroid histories.

**Decision 2 — Cross-camera session stitching (AI suggested OSNet Re-ID, I chose time-window)**

Claude suggested OSNet embeddings for cross-camera person Re-ID. Correct for production, wrong for this context: GPU not available in the acceptance-gate Docker container, and faces are fully blurred removing the strongest appearance signal.

My fix: `app/sessions.py` — `SessionStitcher` with two-pass matching. Pass 1 is direct visitor_id match (scoring harness scenario with consistent IDs). Pass 2 is time-window match: zone event at timestamp T belongs to entry session S if `entry_ts(S) ≤ T ≤ exit_ts(S) + 60s`.

**Handling overlapping sessions (high-traffic stores)**: When multiple entry sessions are open simultaneously (two visitors in the store at once), a zone event could fall within multiple session windows. I use "nearest start wins" — assign the event to the session whose `entry_ts` is closest to (but before) the event timestamp. This is correct because the person physically closest to the entry threshold is most likely the source of the zone event.

**Decision 3 — SQLite over PostgreSQL**

Claude's initial suggestion was PostgreSQL with a `depends_on: {condition: service_healthy}` healthcheck. I tested it — PostgreSQL startup takes 4–8s, causing intermittent acceptance gate failures. SQLite starts instantly. Upgrade path: set `DATABASE_URL=postgresql://...` env var, no application code changes needed.

**Decision 4 — Appearance-based Re-ID: HSV color histograms (AI suggested Siamese net, I chose histograms)**

Claude's initial suggestion was a Siamese network (e.g., OSNet) for cross-camera appearance Re-ID. Correct for production, wrong for three specific reasons in this context:
1. Faces are fully blurred — the strongest appearance signal is unavailable.
2. GPU not available in the acceptance-gate Docker container.
3. OSNet requires torchreid dependency (~200MB), bloating the container.

**What I implemented**: HSV color histograms from bounding box crops. Implemented in `tracker.py::compute_color_histogram()` and `histogram_similarity()`.

- **Why HSV, not RGB**: HSV separates hue (colour) from value (brightness). Retail lighting varies significantly between natural light and fluorescent — RGB-based matching fails when brightness changes. Using only H and S channels (hue + saturation, not value/brightness) makes the histogram robust to the lighting variation the problem statement explicitly mentions.
- **Threshold (0.35)**: deliberately loose. Clothing viewed from different angles (3/4 view vs frontal) has moderate correlation (~0.45–0.65), not high correlation. A tight threshold (0.8) would reject legitimate re-entries. The geometric gate (200px, area ratio) is the primary filter; the histogram gate catches the specific edge case of two different people at the same door position.
- **Graceful degradation**: when no frame is available (test harness events ingested via POST), `color_hist=None` and the matcher falls back to geometry-only. Re-entry detection still works.

**The specific edge case fixed**: Two different customers appear at the same door position 10 seconds apart. Without appearance features, the second person would be incorrectly linked as a re-entry (same visitor_id). With histogram Re-ID, blue clothing vs red clothing → histogram correlation ~0.1 << 0.35 threshold → correctly identified as two separate visitors. Tested in `test_session_stitching.py::TestAppearanceReID::test_appearance_mismatch_blocks_false_reentry`.
