"""
10 self-validation assertions your API must pass.
Run against a live API: python assertions.py [api_url]
"""
import sys
import json
import uuid
import requests

API = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:9000"
STORE_ID = "STORE_BLR_002"
DATE = "2026-04-10"
PASS_MARK = "[PASS]"
FAIL_MARK = "[FAIL]"
passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"{PASS_MARK} {name}")
        passed += 1
    else:
        print(f"{FAIL_MARK} {name}: {detail}")
        failed += 1


def post_event(**overrides):
    evt = {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": "VIS_ASSERT_001",
        "event_type": "ENTRY",
        "timestamp": f"{DATE}T12:00:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.90,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    evt.update(overrides)
    return requests.post(f"{API}/events/ingest", json={"events": [evt]}, timeout=10)


print(f"\nRunning 10 assertions against {API}\n")

# 1. Health endpoint responds
try:
    r = requests.get(f"{API}/health", timeout=5)
    check("1. Health endpoint returns 200", r.status_code == 200)
except Exception as e:
    check("1. Health endpoint returns 200", False, str(e))

# 2-4. Metrics endpoint
try:
    r = requests.get(f"{API}/stores/{STORE_ID}/metrics", timeout=5)
    check("2. /metrics returns 200", r.status_code == 200)
    data = r.json()
    check("3. /metrics has unique_visitors field", "unique_visitors" in data)
    check("4. /metrics has conversion_rate field", "conversion_rate" in data)
except Exception as e:
    check("2. /metrics returns 200", False, str(e))
    check("3. /metrics has unique_visitors field", False)
    check("4. /metrics has conversion_rate field", False)

# 5. Event ingest
try:
    r = post_event()
    check("5. /events/ingest accepts valid event", r.status_code == 200 and r.json().get("ingested", 0) >= 0)
except Exception as e:
    check("5. /events/ingest accepts valid event", False, str(e))

# 6. Idempotency
try:
    eid = str(uuid.uuid4())
    post_event(event_id=eid)
    r2 = post_event(event_id=eid)
    check("6. Ingest is idempotent (duplicate event)", r2.json().get("duplicates", 0) == 1)
except Exception as e:
    check("6. Ingest is idempotent", False, str(e))

# 7. Invalid event_type — partial success (returns 200, errors=1, ingested=0)
# The spec requires partial success: a batch with 1 bad + 4 good events must
# return ingested=4, errors=1 — not a whole-batch 422.
try:
    r = post_event(event_type="INVALID_TYPE", event_id=str(uuid.uuid4()))
    data = r.json()
    check(
        "7. Invalid event_type → partial success (200, errors=1)",
        r.status_code == 200 and data.get("errors", 0) == 1 and data.get("ingested", 0) == 0,
        f"status={r.status_code} body={data}",
    )
except Exception as e:
    check("7. Invalid event_type → partial success", False, str(e))

# 8. Funnel
try:
    r = requests.get(f"{API}/stores/{STORE_ID}/funnel", timeout=5)
    check("8. /funnel returns 200", r.status_code == 200)
    stages = r.json().get("funnel", [])
    check("8b. /funnel has 4 stages", len(stages) == 4, f"got {len(stages)}")
except Exception as e:
    check("8. /funnel returns 200", False, str(e))

# 9. Anomalies
try:
    r = requests.get(f"{API}/stores/{STORE_ID}/anomalies", timeout=5)
    check("9. /anomalies returns 200", r.status_code == 200)
    data = r.json()
    check("9b. /anomalies has anomaly_count field", "anomaly_count" in data)
except Exception as e:
    check("9. /anomalies returns 200", False, str(e))

# 10. Heatmap
try:
    r = requests.get(f"{API}/stores/{STORE_ID}/heatmap", timeout=5)
    check("10. /heatmap returns 200", r.status_code == 200)
except Exception as e:
    check("10. /heatmap returns 200", False, str(e))

print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("All assertions passed!")
else:
    sys.exit(1)
