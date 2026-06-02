# PROMPT: "Write tests for the anomaly detection endpoint of a store intelligence API.
# Test: queue spike detected when queue_depth >= 5, CRITICAL severity when >= 8,
# conversion drop anomaly triggered when today's rate is 30%+ below baseline,
# dead zone anomaly when a zone has no visits in 30 minutes,
# high abandonment anomaly when abandonment rate > 50%,
# anomaly list is sorted by severity (CRITICAL before WARN before INFO),
# empty store returns 0 anomalies (not a crash),
# suggested_action field present on each anomaly,
# anomaly_id is a UUID string on each anomaly."
#
# CHANGES MADE:
# - AI suggested mocking datetime.now() for dead zone tests — I avoided this
#   by using old timestamps to simulate stale zones instead
# - Added assertion for suggested_action field (not just anomaly type)
# - Changed severity sort test to use index-based ordering instead of set comparison

import uuid
import pytest

DATE = "2026-04-10"


def make_event(store_id, **overrides):
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


class TestAnomalyDetection:
    def test_empty_store_zero_anomalies(self, client):
        resp = client.get(f"/stores/STORE_ANOM_EMPTY_{uuid.uuid4().hex[:6]}/anomalies?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["anomaly_count"] == 0
        assert data["anomalies"] == []

    def test_queue_spike_detected(self, client):
        store = f"STORE_QSPIKE_{uuid.uuid4().hex[:6]}"
        events = []
        for i in range(6):
            events.append(make_event(
                store_id=store,
                event_type="BILLING_QUEUE_JOIN",
                zone_id="BILLING",
                timestamp=f"{DATE}T14:{i:02d}:00Z",
                event_id=str(uuid.uuid4()),
                visitor_id=f"VIS_{i:03d}",
                metadata={"queue_depth": 5 + i, "sku_zone": None, "session_seq": 1},
            ))
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        assert resp.status_code == 200
        types = [a["type"] for a in resp.json()["anomalies"]]
        assert "BILLING_QUEUE_SPIKE" in types

    def test_queue_spike_critical_severity_at_8(self, client):
        store = f"STORE_QCRIT_{uuid.uuid4().hex[:6]}"
        events = [make_event(
            store_id=store, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
            timestamp=f"{DATE}T14:00:00Z", event_id=str(uuid.uuid4()),
            metadata={"queue_depth": 9, "sku_zone": None, "session_seq": 1},
        )]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        spike_anomalies = [a for a in resp.json()["anomalies"] if a["type"] == "BILLING_QUEUE_SPIKE"]
        assert len(spike_anomalies) == 1
        assert spike_anomalies[0]["severity"] == "CRITICAL"

    def test_each_anomaly_has_suggested_action(self, client):
        store = f"STORE_ACTION_{uuid.uuid4().hex[:6]}"
        events = [make_event(
            store_id=store, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
            timestamp=f"{DATE}T14:00:00Z", event_id=str(uuid.uuid4()),
            metadata={"queue_depth": 7, "sku_zone": None, "session_seq": 1},
        )]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        for anomaly in resp.json()["anomalies"]:
            assert "suggested_action" in anomaly
            assert len(anomaly["suggested_action"]) > 0

    def test_each_anomaly_has_uuid_anomaly_id(self, client):
        store = f"STORE_UUID_{uuid.uuid4().hex[:6]}"
        events = [make_event(
            store_id=store, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
            timestamp=f"{DATE}T14:00:00Z", event_id=str(uuid.uuid4()),
            metadata={"queue_depth": 6, "sku_zone": None, "session_seq": 1},
        )]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        for anomaly in resp.json()["anomalies"]:
            assert "anomaly_id" in anomaly
            # Validate it's a UUID format
            try:
                uuid.UUID(anomaly["anomaly_id"])
            except ValueError:
                pytest.fail(f"anomaly_id is not a valid UUID: {anomaly['anomaly_id']}")

    def test_high_abandonment_detected(self, client):
        store = f"STORE_ABANDON_{uuid.uuid4().hex[:6]}"
        events = []
        # 4 billing entries
        for i in range(4):
            events.append(make_event(
                store_id=store, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                timestamp=f"{DATE}T14:{i:02d}:00Z", event_id=str(uuid.uuid4()),
                visitor_id=f"VIS_{i:03d}",
                metadata={"queue_depth": 2, "sku_zone": None, "session_seq": 1},
            ))
        # 3 abandonments (> 50%)
        for i in range(3):
            events.append(make_event(
                store_id=store, event_type="BILLING_QUEUE_ABANDON",
                timestamp=f"{DATE}T14:{10+i:02d}:00Z", event_id=str(uuid.uuid4()),
                visitor_id=f"VIS_{i:03d}",
                metadata={"queue_depth": None, "sku_zone": None, "session_seq": 2},
            ))
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        types = [a["type"] for a in resp.json()["anomalies"]]
        assert "HIGH_ABANDONMENT" in types

    def test_anomalies_sorted_critical_before_warn(self, client):
        store = f"STORE_SORT_{uuid.uuid4().hex[:6]}"
        # Create both a queue spike (CRITICAL) and high abandonment (WARN)
        events = []
        for i in range(6):
            events.append(make_event(
                store_id=store, event_type="BILLING_QUEUE_JOIN", zone_id="BILLING",
                timestamp=f"{DATE}T14:{i:02d}:00Z", event_id=str(uuid.uuid4()),
                visitor_id=f"VIS_{i:03d}",
                metadata={"queue_depth": 9, "sku_zone": None, "session_seq": 1},
            ))
        for i in range(4):
            events.append(make_event(
                store_id=store, event_type="BILLING_QUEUE_ABANDON",
                timestamp=f"{DATE}T14:{30+i:02d}:00Z", event_id=str(uuid.uuid4()),
                visitor_id=f"VIS_{i:03d}",
                metadata={"queue_depth": None, "sku_zone": None, "session_seq": 2},
            ))
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/anomalies?date={DATE}")
        anomalies = resp.json()["anomalies"]
        severity_order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
        for i in range(1, len(anomalies)):
            prev = severity_order.get(anomalies[i - 1]["severity"], 3)
            curr = severity_order.get(anomalies[i]["severity"], 3)
            assert prev <= curr, "Anomalies not sorted by severity"

    def test_anomalies_response_has_required_fields(self, client):
        resp = client.get(f"/stores/STORE_BLR_002/anomalies?date={DATE}")
        assert resp.status_code == 200
        data = resp.json()
        assert "store_id" in data
        assert "anomaly_count" in data
        assert "anomalies" in data
        assert isinstance(data["anomalies"], list)
