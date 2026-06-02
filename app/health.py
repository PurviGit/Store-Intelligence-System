from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from database import get_db, check_db_health
from models import EventORM
from datetime import datetime, timezone, timedelta

router = APIRouter()

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
