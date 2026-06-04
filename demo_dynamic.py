"""
Dynamic computation proof -- for reviewers and acceptance gate verification.

Run this script against a live API to demonstrate that metrics respond
to newly ingested events in real time, not from pre-baked/hardcoded values.

Usage:
    python demo_dynamic.py                        # default: http://localhost:9000
    python demo_dynamic.py http://localhost:9000

What it does:
    1. Reads current visitor count from /metrics (baseline)
    2. Injects 5 new ENTRY events for a synthetic test store
    3. Reads /metrics again -- shows the count increased by exactly 5
    4. Injects 3 BILLING_QUEUE_JOIN events with queue_depth=8
    5. Reads /anomalies -- shows BILLING_QUEUE_SPIKE CRITICAL appeared
    6. Runs /funnel -- shows 4-stage session-level deduplication
    7. Re-submits the same events -- confirms idempotency (no double-count)

Exit code 0 = all checks passed. Non-zero = something is wrong.
"""
import sys
import uuid
import json
import requests

API = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:9000"
DEMO_STORE = f"DEMO_DYNAMIC_{uuid.uuid4().hex[:8].upper()}"
DATE = "2026-04-10"  # matches the CCTV clip recording date in the dataset
PASS = "\033[92m[PASS]\033[0m"
FAIL = "\033[91m[FAIL]\033[0m"
INFO = "\033[94m[INFO]\033[0m"
passed = failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        print(f"{PASS} {label}")
        passed += 1
    else:
        print(f"{FAIL} {label}" + (f": {detail}" if detail else ""))
        failed += 1


def make_entry(visitor_id, ts_minute=0):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": DEMO_STORE,
        "camera_id": "CAM_ENTRY_01",
        "visitor_id": visitor_id,
        "event_type": "ENTRY",
        "timestamp": f"{DATE}T10:{ts_minute:02d}:00Z",
        "zone_id": None,
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.91,
        "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }


def make_billing(visitor_id, queue_depth, ts_minute=30):
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": DEMO_STORE,
        "camera_id": "CAM_BILLING_03",
        "visitor_id": visitor_id,
        "event_type": "BILLING_QUEUE_JOIN",
        "timestamp": f"{DATE}T10:{ts_minute:02d}:00Z",
        "zone_id": "BILLING",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.88,
        "metadata": {"queue_depth": queue_depth, "sku_zone": None, "session_seq": 3},
    }


print(f"\n{INFO} Dynamic Computation Proof")
print(f"{INFO} API: {API}")
print(f"{INFO} Test store: {DEMO_STORE}")
print(f"{INFO} (Fresh store -- no pre-ingested data)\n")

# ── 1. Baseline: fresh store has zero metrics ──────────────────────────────
try:
    r = requests.get(f"{API}/stores/{DEMO_STORE}/metrics?date={DATE}", timeout=5)
    check("1. Fresh store metrics returns 200", r.status_code == 200)
    baseline = r.json()
    check("2. Fresh store unique_visitors starts at 0", baseline.get("unique_visitors") == 0,
          f"got {baseline.get('unique_visitors')}")
    check("3. Fresh store conversion_rate starts at 0.0", baseline.get("conversion_rate") == 0.0,
          f"got {baseline.get('conversion_rate')}")
except Exception as e:
    check("1. Fresh store metrics returns 200", False, str(e))
    sys.exit(1)

# ── 2. Ingest 5 new ENTRY events ──────────────────────────────────────────
visitor_ids = [f"VIS_DEMO_{i:03d}" for i in range(5)]
entry_events = [make_entry(vid, ts_minute=i) for i, vid in enumerate(visitor_ids)]
r = requests.post(f"{API}/events/ingest", json={"events": entry_events}, timeout=10)
check("4. Ingest 5 ENTRY events returns 200", r.status_code == 200)
ingest_data = r.json()
check("5. All 5 events ingested (not duplicates)", ingest_data.get("ingested") == 5,
      f"ingested={ingest_data.get('ingested')}, dup={ingest_data.get('duplicates')}")

# ── 3. Metrics now reflect the 5 new visitors ─────────────────────────────
r = requests.get(f"{API}/stores/{DEMO_STORE}/metrics?date={DATE}", timeout=5)
after = r.json()
check("6. unique_visitors is now exactly 5", after.get("unique_visitors") == 5,
      f"got {after.get('unique_visitors')}")
print(f"    visitors: {baseline.get('unique_visitors')} -> {after.get('unique_visitors')} (+5 from new events)")

# ── 4. Idempotency: re-submit same events ─────────────────────────────────
r2 = requests.post(f"{API}/events/ingest", json={"events": entry_events}, timeout=10)
r2_data = r2.json()
check("7. Re-submitting same events: 0 new ingested (idempotent)", r2_data.get("ingested") == 0,
      f"got ingested={r2_data.get('ingested')}")
check("8. Re-submitting same events: 5 duplicates reported", r2_data.get("duplicates") == 5,
      f"got dup={r2_data.get('duplicates')}")

# Count still 5 after re-submission
r = requests.get(f"{API}/stores/{DEMO_STORE}/metrics?date={DATE}", timeout=5)
check("9. unique_visitors still 5 after idempotent re-submit", r.json().get("unique_visitors") == 5)

# ── 5. Billing events trigger anomaly ─────────────────────────────────────
billing_events = [make_billing(vid, queue_depth=8 + i, ts_minute=30 + i)
                  for i, vid in enumerate(visitor_ids[:3])]
requests.post(f"{API}/events/ingest", json={"events": billing_events}, timeout=10)

r = requests.get(f"{API}/stores/{DEMO_STORE}/anomalies?date={DATE}", timeout=5)
anomalies_data = r.json()
check("10. /anomalies returns 200", r.status_code == 200)
types = [a["type"] for a in anomalies_data.get("anomalies", [])]
check("11. BILLING_QUEUE_SPIKE anomaly detected from new events", "BILLING_QUEUE_SPIKE" in types,
      f"got anomaly types: {types}")
spike = next((a for a in anomalies_data.get("anomalies", []) if a["type"] == "BILLING_QUEUE_SPIKE"), None)
if spike:
    check("12. Queue spike is CRITICAL (queue_depth >= 8)", spike["severity"] == "CRITICAL",
          f"got severity={spike['severity']}")
    check("13. Queue spike has suggested_action", bool(spike.get("suggested_action")))

# ── 6. Funnel shows session-level deduplication ───────────────────────────
# Add a REENTRY for visitor 0 -- should not inflate Stage 1 count
reentry = {
    "event_id": str(uuid.uuid4()),
    "store_id": DEMO_STORE,
    "camera_id": "CAM_ENTRY_01",
    "visitor_id": visitor_ids[0],
    "event_type": "REENTRY",
    "timestamp": f"{DATE}T11:00:00Z",
    "zone_id": None,
    "dwell_ms": 0,
    "is_staff": False,
    "confidence": 0.85,
    "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 6},
}
requests.post(f"{API}/events/ingest", json={"events": [reentry]}, timeout=10)

r = requests.get(f"{API}/stores/{DEMO_STORE}/funnel?date={DATE}", timeout=5)
funnel_data = r.json()
check("14. /funnel returns 200", r.status_code == 200)
stages = funnel_data.get("funnel", [])
check("15. /funnel has 4 stages", len(stages) == 4, f"got {len(stages)}")
stage1 = next((s for s in stages if s["stage"] == 1), {})
check("16. Stage 1 = 5 (REENTRY did not double-count)", stage1.get("visitors") == 5,
      f"Stage 1 visitors = {stage1.get('visitors')}")
for i in range(1, len(stages)):
    check(f"17.{i} Stage {i+1} <= Stage {i} (funnel monotonic)",
          stages[i]["visitors"] <= stages[i - 1]["visitors"])

# ── 7. Staff excluded ────────────────────────────────────────────────────
staff_evt = {
    "event_id": str(uuid.uuid4()),
    "store_id": DEMO_STORE,
    "camera_id": "CAM_ZONE_02",
    "visitor_id": "STAFF_DEMO_001",
    "event_type": "ENTRY",
    "timestamp": f"{DATE}T09:00:00Z",
    "zone_id": None,
    "dwell_ms": 0,
    "is_staff": True,
    "confidence": 0.94,
    "metadata": {"queue_depth": None, "sku_zone": None, "session_seq": 1},
}
requests.post(f"{API}/events/ingest", json={"events": [staff_evt]}, timeout=10)
r = requests.get(f"{API}/stores/{DEMO_STORE}/metrics?date={DATE}", timeout=5)
check("18. Staff event does not increase unique_visitors", r.json().get("unique_visitors") == 5,
      f"got {r.json().get('unique_visitors')} (expected 5, not 6)")

# ── Summary ──────────────────────────────────────────────────────────────
print(f"\n{'='*55}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("\nv All checks passed -- metrics compute dynamically from events.")
    print("  The API does NOT serve pre-baked/hardcoded values.\n")
else:
    print(f"\n{failed} check(s) failed. Review output above.\n")
    sys.exit(1)
