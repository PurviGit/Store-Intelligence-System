from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from database import get_db, check_db_health
from models import EventORM
from datetime import datetime, timezone, timedelta
import time

router = APIRouter()

# Prometheus-style metrics endpoint for observability
_REQUEST_COUNT: dict = {}
_REQUEST_LATENCY: dict = {}
_INGEST_TOTAL = 0
_START_TIME = time.time()


def record_request(endpoint: str, latency_ms: float):
    _REQUEST_COUNT[endpoint] = _REQUEST_COUNT.get(endpoint, 0) + 1
    if endpoint not in _REQUEST_LATENCY:
        _REQUEST_LATENCY[endpoint] = []
    _REQUEST_LATENCY[endpoint].append(latency_ms)
    if len(_REQUEST_LATENCY[endpoint]) > 1000:
        _REQUEST_LATENCY[endpoint] = _REQUEST_LATENCY[endpoint][-500:]


@router.get("/metrics/prometheus", response_class=PlainTextResponse,
            summary="Prometheus-format metrics for observability")
def prometheus_metrics(db: Session = Depends(get_db)):
    """
    Prometheus-format metrics endpoint.
    Covers: request counts, latency, event ingestion totals, uptime.
    Complements structured JSON logs and trace_id per request.
    """
    uptime_s = time.time() - _START_TIME
    try:
        total_events = db.query(func.count(EventORM.event_id)).scalar() or 0
        store_count = db.query(func.count(distinct(EventORM.store_id))).scalar() or 0
    except Exception:
        total_events = 0
        store_count = 0

    lines = [
        "# HELP store_intelligence_uptime_seconds Time since API startup",
        "# TYPE store_intelligence_uptime_seconds gauge",
        f"store_intelligence_uptime_seconds {uptime_s:.1f}",
        "",
        "# HELP store_intelligence_events_total Total events in database",
        "# TYPE store_intelligence_events_total counter",
        f"store_intelligence_events_total {total_events}",
        "",
        "# HELP store_intelligence_stores_active Number of stores with events",
        "# TYPE store_intelligence_stores_active gauge",
        f"store_intelligence_stores_active {store_count}",
        "",
        "# HELP store_intelligence_requests_total Total requests per endpoint",
        "# TYPE store_intelligence_requests_total counter",
    ]
    for endpoint, count in _REQUEST_COUNT.items():
        safe = endpoint.replace("/", "_").replace("{", "").replace("}", "").lstrip("_")
        lines.append(f'store_intelligence_requests_total{{endpoint="{endpoint}"}} {count}')

    lines += [
        "",
        "# HELP store_intelligence_request_latency_p99_ms P99 request latency per endpoint",
        "# TYPE store_intelligence_request_latency_p99_ms gauge",
    ]
    for endpoint, latencies in _REQUEST_LATENCY.items():
        if latencies:
            sorted_l = sorted(latencies)
            p99 = sorted_l[int(len(sorted_l) * 0.99)]
            lines.append(f'store_intelligence_request_latency_p99_ms{{endpoint="{endpoint}"}} {p99:.1f}')

    return "\n".join(lines) + "\n"

STALE_THRESHOLD_MINUTES = 10


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """
    Service health: DB status, last event per store, STALE_FEED warning if > 10 min lag.
    This is what an on-call engineer checks first.
    """
    db_ok = check_db_health()
    now = datetime.now(timezone.utc)
    stale_cutoff = (now - timedelta(minutes=STALE_THRESHOLD_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")

    stores_health = []
    try:
        store_rows = db.query(
            EventORM.store_id,
            func.max(EventORM.timestamp).label("last_event"),
            func.count(EventORM.event_id).label("total_events"),
        ).group_by(EventORM.store_id).all()

        for row in store_rows:
            last_ts = row.last_event
            is_stale = last_ts is None or last_ts < stale_cutoff
            stores_health.append({
                "store_id": row.store_id,
                "last_event_timestamp": last_ts,
                "total_events": row.total_events,
                "feed_status": "STALE_FEED" if is_stale else "OK",
            })
    except Exception as e:
        stores_health = [{"error": str(e)}]

    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "connected" if db_ok else "unavailable",
        "checked_at": now.isoformat(),
        "stale_threshold_minutes": STALE_THRESHOLD_MINUTES,
        "stores": stores_health,
    }
