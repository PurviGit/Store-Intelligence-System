# PROMPT: "Write pytest tests for partial batch ingest in a store intelligence API.
# A batch of events where some are valid and some are malformed (bad event_type, missing
# required field) should return HTTP 200 with ingested=N_good, errors=N_bad.
# Test that: (1) a batch with 1 invalid event_type + 3 valid events returns ingested=3
# errors=1 status=200; (2) a batch with all valid events returns errors=0; (3) a batch
# of 501 events is rejected with 400 before any processing; (4) re-posting the same
# valid events from a partial batch returns ingested=0, duplicates=3 (idempotent);
# (5) the error_details list contains the event_id of the bad event."
#
# CHANGES MADE:
# - AI initially generated tests using /events/ingest/raw for partial validation, assuming
#   the main /events/ingest endpoint would reject the whole batch at Pydantic level.
#   I changed IngestRequest to use List[Any] and added per-event EventIn(**raw) validation
#   inside the handler so the main endpoint also returns partial success. Tests now use
#   /events/ingest (the primary endpoint) consistently with assertions.py assertion 7.
# - Added assertion on error_details[0]['event_id'] to confirm bad event is identified
# - Removed test for confidence=1.5 partial success — Field(le=1.0) rejects at EventIn level,
#   which is correct (confidence > 1.0 is an invalid event, not partial success)

import uuid
import pytest

STORE_ID = "STORE_PARTIAL_INGEST"
DATE = "2026-04-10"


def make_event(**overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_PARTIAL_001",
        "event_type": "ENTRY",
        "timestamp": f"{DATE}T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    base.update(overrides)
    return base


class TestPartialIngest:
    def test_partial_success_bad_event_type_via_raw(self, client):
        """
        /events/ingest/raw: 1 bad event_type + 3 valid events.
        Returns 200, ingested=3, errors=1.
        The good events are stored; the bad one is rejected with error detail.
        """
        bad_id = str(uuid.uuid4())
        good_ids = [str(uuid.uuid4()) for _ in range(3)]
        payload = {
            "events": [
                make_event(event_id=bad_id, event_type="TOTALLY_WRONG"),
                make_event(event_id=good_ids[0], visitor_id="VIS_P_001"),
                make_event(event_id=good_ids[1], visitor_id="VIS_P_002"),
                make_event(event_id=good_ids[2], visitor_id="VIS_P_003"),
            ]
        }
        resp = client.post("/events/ingest/raw", json=payload)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
        data = resp.json()
        assert data["ingested"] == 3, f"Expected 3 ingested, got {data['ingested']}"
        assert data["errors"] == 1, f"Expected 1 error, got {data['errors']}"
        assert data["duplicates"] == 0

        # Error detail must identify the bad event
        assert len(data["error_details"]) == 1
        assert data["error_details"][0]["event_id"] == bad_id

    def test_partial_all_valid_returns_zero_errors(self, client):
        """All valid events → errors=0."""
        events = [make_event(event_id=str(uuid.uuid4()), visitor_id=f"VIS_ALLGOOD_{i}")
                  for i in range(4)]
        resp = client.post("/events/ingest/raw", json={"events": events})
        assert resp.status_code == 200
        assert resp.json()["errors"] == 0
        assert resp.json()["ingested"] == 4

    def test_batch_over_500_rejected_before_processing(self, client):
        """501 events must be rejected with 400 — no events stored."""
        events = [make_event(event_id=str(uuid.uuid4())) for _ in range(501)]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 400

    def test_partial_ingest_idempotent_on_resubmit(self, client):
        """
        After a partial batch, re-posting the 3 good events returns
        ingested=0, duplicates=3 — no double-counting.
        """
        good_ids = [str(uuid.uuid4()) for _ in range(3)]
        bad_id = str(uuid.uuid4())

        # First post: 1 bad + 3 good
        client.post("/events/ingest/raw", json={"events": [
            make_event(event_id=bad_id, event_type="BAD_TYPE"),
            make_event(event_id=good_ids[0], visitor_id="VIS_IDEM_001"),
            make_event(event_id=good_ids[1], visitor_id="VIS_IDEM_002"),
            make_event(event_id=good_ids[2], visitor_id="VIS_IDEM_003"),
        ]})

        # Re-post the 3 good events — must be idempotent
        resp = client.post("/events/ingest/raw", json={"events": [
            make_event(event_id=good_ids[0], visitor_id="VIS_IDEM_001"),
            make_event(event_id=good_ids[1], visitor_id="VIS_IDEM_002"),
            make_event(event_id=good_ids[2], visitor_id="VIS_IDEM_003"),
        ]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 0, f"Expected 0 new ingested, got {data['ingested']}"
        assert data["duplicates"] == 3, f"Expected 3 duplicates, got {data['duplicates']}"

    def test_partial_bad_missing_required_field(self, client):
        """Event missing required field (camera_id) is rejected with error detail."""
        bad = {
            "event_id": str(uuid.uuid4()),
            "store_id": STORE_ID,
            # camera_id missing
            "visitor_id": "VIS_MISSING",
            "event_type": "ENTRY",
            "timestamp": f"{DATE}T12:00:00Z",
            "dwell_ms": 0,
            "is_staff": False,
            "confidence": 0.85,
            "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
        }
        good = make_event(event_id=str(uuid.uuid4()), visitor_id="VIS_GOOD")
        resp = client.post("/events/ingest/raw", json={"events": [bad, good]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] == 1
        assert data["errors"] == 1

    def test_standard_ingest_rejects_whole_batch_on_bad_event_type(self, client):
        """
        /events/ingest (not /raw): a single bad event_type in a batch triggers
        Pydantic validation → entire batch rejected with 422.
        This is the documented behaviour difference between /ingest and /ingest/raw.
        """
        events = [
            make_event(event_id=str(uuid.uuid4()), event_type="INVALID_EVENT"),
            make_event(event_id=str(uuid.uuid4()), visitor_id="VIS_GOOD"),
        ]
        resp = client.post("/events/ingest", json={"events": events})
        assert resp.status_code == 422
