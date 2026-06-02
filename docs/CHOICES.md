# Engineering Decisions — Store Intelligence System

## Decision 1: Detection Model Choice

### Options Considered

| Option        | Accuracy    | GPU Required             | Setup Complexity | Download Size |
| ------------- | ----------- | ------------------------ | ---------------- | ------------- |
| YOLOv8n       | High        | Optional (but preferred) | Medium           | ~6MB model    |
| YOLOv8x       | Very High   | Required                 | High             | ~130MB model  |
| MOG2 (OpenCV) | Medium      | No                       | Zero             | Built-in      |
| MediaPipe     | Medium-High | No                       | Low              | ~30MB         |
| RT-DETR       | High        | Required                 | High             | ~200MB        |

### What AI (Claude) Suggested

Claude recommended YOLOv8n as the starting point: "Better accuracy on partial occlusion cases, active community, and the nano model is small enough to run on CPU at reduced fps." It also suggested MediaPipe as a fallback.

### What I Chose and Why

**OpenCV MOG2**, and here is the specific reason: the acceptance gate is `docker compose up` on a clean machine with no GPU. YOLOv8 downloads its model weights at runtime. In an air-gapped environment (or slow internet), this fails silently — the container starts but the pipeline crashes on first clip. MOG2 is part of the OpenCV standard library, ships with the Docker image, and starts in 0ms.

The trade-off I am making consciously: MOG2 is less accurate on heavy occlusion and cannot classify objects by type (it cannot distinguish a shopping bag from a person without additional heuristics). My group-blob-splitting heuristic (blobs wider than 200px → two people) partially compensates. In a production deployment I would migrate to YOLOv8n combined with ByteTrack for more robust detection and tracking under occlusion. I chose MOG2 for this challenge because reliability, Docker startup simplicity, and acceptance-gate success were higher priorities than maximizing detection accuracy. The architecture isolates the detection layer from the event-processing and API layers, making the detector fully swappable without requiring changes to the downstream analytics pipeline.

**Where I disagree with the AI:** Claude said YOLOv8 accuracy justifies the complexity. I disagree for this specific challenge context: a system that scores 8/10 on detection accuracy but passes the acceptance gate beats a system that scores 10/10 in theory but fails the gate. The README documents the upgrade path clearly.

---

## Decision 2: Event Schema Design

### Options Considered

**Option A:** Flat schema — all fields at top level, no nested metadata.

```json
{"event_id": "...", "queue_depth": 2, "sku_zone": "SKINCARE", "session_seq": 5, ...}
```

**Option B:** Nested metadata object (what I implemented)

```json
{
  "event_id": "...",
  "metadata": { "queue_depth": 2, "sku_zone": "SKINCARE", "session_seq": 5 }
}
```

**Option C:** Polymorphic schema — different fields per event_type, validated by a discriminated union.

### What AI Suggested

Claude suggested Option C (discriminated union): "Each event_type has a different payload shape — BILLING_QUEUE_JOIN needs queue_depth, ZONE_DWELL needs dwell_ms. A discriminated union enforces this at validation time." This is architecturally correct.

### What I Chose and Why

**Option B** (nested metadata), matching the schema specified in the problem statement verbatim. The problem statement's example shows a `metadata` object with `queue_depth`, `sku_zone`, and `session_seq`. I followed this spec exactly rather than what the AI suggested, because the scoring harness tests against the specified schema.

**Where I disagree with the AI:** The discriminated union is better software engineering but would fail the scoring harness if it validates against the reference schema. I noted this is a case where spec-compliance beats elegance.

---

## Decision 3: API Architecture — SQLite vs PostgreSQL

### Options Considered

**Option A:** PostgreSQL via docker-compose with healthcheck dependency.

- Pro: Production-grade, concurrent writes, time-series extensions.
- Con: Race condition on startup. FastAPI starts in 0.3s, Postgres in 4–8s.

**Option B:** SQLite with DATABASE_URL env var.

- Pro: Zero startup latency, no external process, works everywhere.
- Con: Single-writer limitation. Serializes concurrent writes.

**Option C:** DuckDB (in-process OLAP database).

- Pro: Fast analytics queries, no server process.
- Con: Less mature FastAPI integration, less community support.

### What AI Suggested

Claude initially suggested PostgreSQL with a `depends_on: {condition: service_healthy}` healthcheck. It then provided a correct docker-compose.yml. When I tested it, the healthcheck added 30+ seconds to startup and still failed intermittently.

### What I Chose and Why

**SQLite** (Option B), configured via `DATABASE_URL` environment variable. The specific reason: the acceptance gate evaluators run `docker compose up`, wait a few seconds, then hit the API. A startup that fails intermittently fails the gate.

The SQLite limitation (single writer) is real and documented in DESIGN.md. At 40 live stores sending events simultaneously, the write bottleneck would appear. The fix is well-understood: swap `DATABASE_URL` to `postgresql://...` and add `asyncpg` as the async driver. No application code changes needed — only the env var and driver.

**Where I agree with the AI:** At production scale (40 stores, real-time), PostgreSQL is the right answer. The AI was right about the architecture. I am making a pragmatic choice for the challenge context while documenting the production upgrade path clearly.

---

## Decision 4: VLM for Zone Classification — Considered and Rejected

### What I Evaluated

The problem statement includes `store_layout.json` with zone definitions. I explored whether a Vision Language Model (VLM) could automatically classify which zone a customer was in, rather than using camera-to-zone mapping from the layout file.

**Approach I tried:** Prompted GPT-4V with frames from CAM 2 (main floor) asking: *"Which zone is the person in? Zones are: SKINCARE (left wall), HAIRCARE (center), MAKEUP (right). Answer with zone name only."*

**Result:** The VLM correctly identified zones in ~60% of frames but failed when:
- Multiple people were in different zones simultaneously (it picked one)
- Products were not clearly visible (blurred frame, low light)
- The person was between zones (transient positions)
- Latency was 1.8–3.2 seconds per frame — unusable at 15fps

### What AI Suggested

Claude suggested using CLIP embeddings to match frame crops against zone reference images, which would be faster than GPT-4V. Estimated 200ms per frame, still too slow.

### What I Chose and Why

**Rule-based zone assignment from `store_layout.json`:** Each camera is assigned a fixed zone in the camera metadata. CAM 2 → SKINCARE/HAIRCARE, CAM 4 → MAKEUP/DERMDOC, CAM 5 → EB_KOREAN/BATH_BODY. This is:
- **Deterministic:** No hallucinations
- **Instant:** Zero additional latency
- **Accurate:** Fixed cameras have fixed coverage areas

**Where I would change this decision:** If the store reconfigures shelving frequently, the fixed camera-to-zone mapping breaks. In that case, I would use a lightweight object detector (not VLM) to identify product category labels in frame, and update `store_layout.json` automatically. The VLM approach remains too slow for real-time use without dedicated GPU inference.
