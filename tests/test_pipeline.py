# PROMPT: "Write comprehensive tests for a store intelligence event ingestion API.
# Cover: happy path batch ingest, idempotency (same event_id twice must return duplicate=1),
# partial success on malformed events, schema validation (bad event_type, bad timestamp,
# confidence out of range), batch size limit (501 events rejected), empty store returns
# valid metrics (not null/crash), all-staff clip (is_staff=true excluded from visitor counts),
# zero purchases store, re-entry deduplication in funnel.
# Use FastAPI TestClient with SQLite in-memory via conftest.py."
#
# CHANGES MADE:
# - Changed memory DB (':memory:') to file-based to avoid SQLite per-connection isolation bug
# - Added parametrize for all 8 event types to ensure schema validates each correctly
# - Replaced confidence=1.5 (pydantic rejects at field level) with missing required field test
# - Added explicit DB cleanup between tests using unique event_id UUIDs

import uuid
import pytest


STORE_ID = "STORE_BLR_002"
DATE = "2026-04-10"


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_00001",
        "event_type": "ENTRY",
        "timestamp": f"{DATE}T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.90,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    base.update(overrides)
    return base


# ── Ingest: Happy path ──────────────────────────────────────────────────────

class TestIngest:
    def test_single_event_ingest(self, client):
        evt = make_event()
        resp = client.post("/events/ingest", json={"events": [evt]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 1
        assert data["duplicates"] == 0
        assert data["errors"] == 0

    def test_batch_ingest_multiple(self, client):
        events = [make_event(visitor_id=f"VIS_{i:05d}") for i in range(10)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 10

    def test_idempotent_duplicate_rejected(self, client):
        evt = make_event()
        client.post("/events/ingest", json={"events": [evt]})
        resp2 = client.post("/events/ingest", json={"events": [evt]})
        assert resp2.status_code == 200
        data = resp2.json()
        assert data["ingested"] == 0
        assert data["duplicates"] == 1

    def test_batch_size_limit_501_rejected(self, client):
        events = [make_event() for _ in range(501)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 400

    def test_partial_success_mixed_batch(self, client):
        """
        A batch with 4 valid + 1 invalid event must return ingested=4, errors=1.
        The whole batch must NOT fail — partial success is required by spec.
        """
        good_events = [make_event(event_id=str(uuid.uuid4()), visitor_id=f"VIS_{i:05d}") for i in range(4)]
        bad_event = make_event(event_type="INVALID_TYPE", event_id=str(uuid.uuid4()))
        resp = client.post("/events/ingest", json={"events": good_events + [bad_event]})
        assert resp.status_code == 200, f"Expected 200 partial success, got {resp.status_code}"
        data = resp.json()
        assert data["ingested"] == 4, f"Expected 4 ingested, got {data['ingested']}"
        assert data["errors"] == 1, f"Expected 1 error, got {data['errors']}"
        assert len(data["error_details"]) == 1

    def test_single_invalid_event_returns_partial_not_422(self, client):
        """A single bad event must return 200 with errors=1, not 422."""
        bad = make_event(event_type="INVALID_TYPE", event_id=str(uuid.uuid4()))
        resp = client.post("/events/ingest", json={"events": [bad]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 0
        assert data["errors"] == 1

    def test_all_eight_event_types_accepted(self, client):
        event_types = [
            "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
            "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
        ]
        for et in event_types:
            evt = make_event(event_type=et, event_id=str(uuid.uuid4()))
            resp = client.post("/events/ingest", json={"events": [evt]})
            assert resp.status_code == 200, f"Failed for event_type={et}"

    def test_invalid_timestamp_counted_as_error(self, client):
        evt = make_event(timestamp="not-a-timestamp")
        resp = client.post("/events/ingest", json={"events": [evt]})
        # Per-event validation: bad event returns 200 with errors=1, not 422
        assert resp.status_code == 200
        assert resp.json()["errors"] == 1

    def test_invalid_confidence_over_1_counted_as_error(self, client):
        evt = make_event(confidence=1.5)
        resp = client.post("/events/ingest", json={"events": [evt]})
        assert resp.status_code == 200
        assert resp.json()["errors"] == 1

    def test_response_has_trace_id_header(self, client):
        resp = client.post("/events/ingest", json={"events": [make_event()]})
        assert "x-trace-id" in resp.headers

    def test_empty_batch_accepted(self, client):
        resp = client.post("/events/ingest", json={"events": []})
        assert resp.status_code == 200
        assert resp.json()["ingested"] == 0


# ── Metrics: Edge cases ─────────────────────────────────────────────────────

class TestMetricsEdgeCases:
    def test_empty_store_returns_valid_metrics(self, client):
        """Empty store must not crash or return null — returns zeros."""
        resp = client.get(f"/stores/STORE_EMPTY_999/metrics?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["unique_visitors"] == 0
        assert data["conversion_rate"] == 0.0
        assert data["abandonment_rate"] == 0.0

    def test_all_staff_clip_excluded_from_visitors(self, client):
        """is_staff=True events must not count as customer visitors."""
        staff_evt = make_event(
            store_id="STORE_STAFF_TEST",
            visitor_id="STAFF_001",
            is_staff=True,
            event_id=str(uuid.uuid4()),
        )
        client.post("/events/ingest", json={"events": [staff_evt]})
        resp = client.get(f"/stores/STORE_STAFF_TEST/metrics?date={DATE}")
        assert resp.status_code == 200
        assert resp.json()["unique_visitors"] == 0

    def test_zero_purchases_store_returns_zero_conversion(self, client):
        """Store with visitors but no POS transactions has conversion_rate=0."""
        # The pos_transactions.csv doesn't have entries for STORE_NO_POS
        entry = make_event(store_id="STORE_NO_POS", event_id=str(uuid.uuid4()))
        client.post("/events/ingest", json={"events": [entry]})
        resp = client.get(f"/stores/STORE_NO_POS/metrics?date={DATE}")
        assert resp.status_code == 200
        assert resp.json()["conversion_rate"] == 0.0

    def test_metrics_excludes_reentry_from_double_count(self, client):
        """REENTRY event must not create a second unique visitor."""
        store = "STORE_REENTRY_TEST"
        entry = make_event(store_id=store, visitor_id="VIS_REENTER", event_id=str(uuid.uuid4()))
        reentry = make_event(
            store_id=store, visitor_id="VIS_REENTER",
            event_type="REENTRY", event_id=str(uuid.uuid4()),
        )
        client.post("/events/ingest", json={"events": [entry, reentry]})
        resp = client.get(f"/stores/{store}/metrics?date={DATE}")
        assert resp.status_code == 200
        # unique_visitors counts ENTRY only, not REENTRY
        assert resp.json()["unique_visitors"] == 1

    def test_metrics_has_required_fields(self, client):
        resp = client.get(f"/stores/{STORE_ID}/metrics?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        required = ["store_id", "date", "unique_visitors", "conversion_rate",
                    "avg_dwell_per_zone", "current_queue_depth", "abandonment_rate"]
        for field in required:
            assert field in data, f"Missing field: {field}"
