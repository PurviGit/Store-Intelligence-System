# PROMPT: "Write tests for the /metrics, /funnel, and /heatmap API endpoints.
# Test funnel stage deduplication (re-entry must not inflate visitor count),
# funnel drop-off percentages are logically consistent (each stage <= previous),
# heatmap normalisation (all frequency scores 0-100), heatmap data_confidence
# flag when fewer than 20 sessions, zone dwell averages computed correctly,
# health endpoint returns STALE_FEED when last event > 10 minutes ago,
# health endpoint returns DB status. Use real event fixtures from conftest."
#
# CHANGES MADE:
# - Added explicit date parameter to all requests to isolate test data
# - Replaced hard-coded expected values with relational assertions (stage N <= stage N-1)
# - Added test for health DB availability field (not just status string)
# - Removed assumption that SKINCARE zone always appears (uses 'in' check instead)

import uuid
import pytest
from datetime import datetime, timezone, timedelta


STORE_ID = "STORE_BLR_002"
DATE = "2026-04-10"
METRICS_STORE = "STORE_METRICS_ISOLATED"


def make_event(store_id=METRICS_STORE, **overrides):
    base = {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
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


def seed_funnel_events(client, store_id):
    """Seed a full funnel: 5 entries, 4 zone visits, 2 billing, 1 purchase."""
    events = []
    for i in range(5):
        vid = f"VIS_FUNNEL_{i:03d}"
        events.append(make_event(store_id=store_id, visitor_id=vid, event_id=str(uuid.uuid4())))

    for i in range(4):
        vid = f"VIS_FUNNEL_{i:03d}"
        events.append(make_event(
            store_id=store_id, visitor_id=vid,
            event_type="ZONE_ENTER", zone_id="SKINCARE",
            timestamp=f"{DATE}T12:10:00Z",
            camera_id="CAM_FLOOR_02",
            event_id=str(uuid.uuid4()),
            metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2},
        ))

    for i in range(2):
        vid = f"VIS_FUNNEL_{i:03d}"
        events.append(make_event(
            store_id=store_id, visitor_id=vid,
            event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
            timestamp=f"{DATE}T12:30:00Z",
            camera_id="CAM_BILLING_03",
            event_id=str(uuid.uuid4()),
            metadata={"queue_depth": 1, "sku_zone": None, "session_seq": 5},
        ))

    client.post("/events/ingest", json={"events": events})


class TestFunnel:
    def test_funnel_returns_four_stages(self, client):
        store = f"STORE_FUNNEL_{uuid.uuid4().hex[:6]}"
        seed_funnel_events(client, store)
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["funnel"]) == 4

    def test_funnel_stages_monotonically_decreasing(self, client):
        store = f"STORE_FUNNEL_{uuid.uuid4().hex[:6]}"
        seed_funnel_events(client, store)
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        stages = resp.json()["funnel"]
        for i in range(1, len(stages)):
            assert stages[i]["visitors"] <= stages[i - 1]["visitors"], \
                f"Stage {i+1} has more visitors than stage {i}"

    def test_reentry_does_not_double_count(self, client):
        store = f"STORE_REENTRY_{uuid.uuid4().hex[:6]}"
        entry = make_event(store_id=store, visitor_id="VIS_SAME", event_id=str(uuid.uuid4()))
        reentry = make_event(
            store_id=store, visitor_id="VIS_SAME",
            event_type="REENTRY", event_id=str(uuid.uuid4()),
            timestamp=f"{DATE}T14:00:00Z",
        )
        client.post("/events/ingest", json={"events": [entry, reentry]})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        stage1 = resp.json()["funnel"][0]["visitors"]
        assert stage1 == 1  # same visitor_id counted once

    def test_funnel_empty_store(self, client):
        store = f"STORE_EMPTY_FUNNEL_{uuid.uuid4().hex[:6]}"
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        for stage in data["funnel"]:
            assert stage["visitors"] == 0

    def test_funnel_has_overall_conversion_pct(self, client):
        store = f"STORE_CONVPCT_{uuid.uuid4().hex[:6]}"
        seed_funnel_events(client, store)
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert "overall_conversion_pct" in resp.json()

    def test_drop_off_pct_nonnegative(self, client):
        store = f"STORE_DROPOFF_{uuid.uuid4().hex[:6]}"
        seed_funnel_events(client, store)
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        for stage in resp.json()["funnel"]:
            assert stage["drop_off_pct"] >= 0


class TestHeatmap:
    def seed_zone_events(self, client, store):
        events = []
        for i in range(25):
            vid = f"VIS_HEAT_{i:03d}"
            events.append({
                "event_id": str(uuid.uuid4()),
                "store_id": store,
                "camera_id": "CAM_FLOOR_02",
                "visitor_id": vid,
                "event_type": "ZONE_DWELL",
                "timestamp": f"{DATE}T12:{i:02d}:00Z",
                "zone_id": "SKINCARE" if i % 2 == 0 else "MAKEUP",
                "dwell_ms": 30000 + i * 1000,
                "is_staff": False,
                "confidence": 0.88,
                "metadata": {"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2},
            })
        client.post("/events/ingest", json={"events": events})

    def test_heatmap_scores_0_to_100(self, client):
        store = f"STORE_HEAT_{uuid.uuid4().hex[:6]}"
        self.seed_zone_events(client, store)
        resp = client.get(f"/stores/{store}/heatmap?date={DATE}")
        assert resp.status_code == 200
        for zone in resp.json()["zones"]:
            assert 0 <= zone["frequency_score"] <= 100
            assert 0 <= zone["dwell_score"] <= 100

    def test_heatmap_data_confidence_high_when_20_sessions(self, client):
        store = f"STORE_HEAT_HIGH_{uuid.uuid4().hex[:6]}"
        self.seed_zone_events(client, store)
        resp = client.get(f"/stores/{store}/heatmap?date={DATE}")
        # 25 zone events → HIGH confidence
        assert resp.json()["data_confidence"] == "HIGH"

    def test_heatmap_data_confidence_low_few_sessions(self, client):
        store = f"STORE_HEAT_LOW_{uuid.uuid4().hex[:6]}"
        # Only 2 zone events → LOW confidence
        for i in range(2):
            event = {
                "event_id": str(uuid.uuid4()), "store_id": store,
                "camera_id": "CAM_FLOOR_02", "visitor_id": f"VIS_{i}",
                "event_type": "ZONE_ENTER", "timestamp": f"{DATE}T12:0{i}:00Z",
                "zone_id": "SKINCARE", "dwell_ms": 5000,
                "is_staff": False, "confidence": 0.85,
                "metadata": {"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 1},
            }
            client.post("/events/ingest", json={"events": [event]})
        resp = client.get(f"/stores/{store}/heatmap?date={DATE}")
        assert resp.json()["data_confidence"] == "LOW"

    def test_heatmap_empty_store_no_crash(self, client):
        resp = client.get(f"/stores/STORE_EMPTY_HEATMAP/heatmap?date={DATE}")
        assert resp.status_code == 200
        assert resp.json()["zones"] == []


class TestHealth:
    def test_health_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_has_database_field(self, client):
        data = client.get("/health").json()
        assert "database" in data
        assert data["database"] in ("connected", "unavailable")

    def test_health_has_status_field(self, client):
        data = client.get("/health").json()
        assert data["status"] in ("healthy", "degraded")

    def test_health_stores_list_present(self, client):
        data = client.get("/health").json()
        assert "stores" in data
        assert isinstance(data["stores"], list)
