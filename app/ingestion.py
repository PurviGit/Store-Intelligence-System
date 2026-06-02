from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import EventORM, IngestRequest, IngestResponse, EventIn
from pydantic import ValidationError

router = APIRouter()


def _save_event(db: Session, event: EventIn) -> bool:
    """Save event to DB. Returns True if new, False if duplicate."""
    existing = db.query(EventORM).filter(EventORM.event_id == event.event_id).first()
    if existing:
        return False  # duplicate — idempotent

    meta = event.metadata or {}
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()

    orm_event = EventORM(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=event.visitor_id,
        event_type=event.event_type,
        timestamp=event.timestamp,
        zone_id=event.zone_id,
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        queue_depth=meta.get("queue_depth") if isinstance(meta, dict) else None,
        sku_zone=meta.get("sku_zone") if isinstance(meta, dict) else None,
        session_seq=meta.get("session_seq", 1) if isinstance(meta, dict) else 1,
    )
    db.add(orm_event)
    return True


@router.post("/events/ingest", response_model=IngestResponse)
def ingest_events(request: IngestRequest, db: Session = Depends(get_db)):
    """
    Ingest a batch of up to 500 events.
    Idempotent by event_id. Partial success on malformed events.
    """
    if len(request.events) > 500:
        raise HTTPException(status_code=400, detail="Batch size exceeds 500 events")

    ingested = 0
    duplicates = 0
    errors = 0
    error_details = []

    for event in request.events:
        try:
            is_new = _save_event(db, event)
            if is_new:
                ingested += 1
            else:
                duplicates += 1
        except Exception as e:
            errors += 1
            error_details.append({"event_id": getattr(event, "event_id", "unknown"), "error": str(e)})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"DB commit failed: {str(e)}")

    return IngestResponse(
        ingested=ingested,
        duplicates=duplicates,
        errors=errors,
        error_details=error_details,
    )


@router.post("/events/ingest/raw")
def ingest_raw(payload: dict, db: Session = Depends(get_db)):
    """
    Accept raw JSON batch for partial validation (events may have individual errors).
    """
    raw_events = payload.get("events", [])
    if not isinstance(raw_events, list):
        raise HTTPException(status_code=400, detail="'events' must be a list")

    ingested = 0
    duplicates = 0
    errors = 0
    error_details = []

    for i, raw in enumerate(raw_events):
        try:
            event = EventIn(**raw)
            is_new = _save_event(db, event)
            if is_new:
                ingested += 1
            else:
                duplicates += 1
        except (ValidationError, Exception) as e:
            errors += 1
            error_details.append({"index": i, "event_id": raw.get("event_id", "?"), "error": str(e)})

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

    return IngestResponse(
        ingested=ingested,
        duplicates=duplicates,
        errors=errors,
        error_details=error_details,
    )
