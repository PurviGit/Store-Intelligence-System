from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database import get_db
from models import EventORM, IngestRequest, IngestResponse, EventIn
from pydantic import ValidationError
from typing import Optional

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
        zone_name=event.zone_name,
        zone_type=event.zone_type,
        is_revenue_zone=event.is_revenue_zone or "No",
        dwell_ms=event.dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        is_face_hidden=event.is_face_hidden,
        gender_pred=event.gender_pred,
        age_pred=event.age_pred,
        age_bucket=event.age_bucket,
        group_id=event.group_id,
        group_size=event.group_size,
        zone_hotspot_x=event.zone_hotspot_x,
        zone_hotspot_y=event.zone_hotspot_y,
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
    Idempotent by event_id. Partial success: valid events are ingested even if
    others in the batch are malformed — bad events are counted in errors, not
    returned as 422 (which would fail the whole batch).
    """
    if len(request.events) > 500:
        raise HTTPException(status_code=400, detail="Batch size exceeds 500 events")

    ingested = 0
    duplicates = 0
    errors = 0
    error_details = []

    for i, raw in enumerate(request.events):
        # Per-event validation — a bad event must not fail the whole batch
        try:
            if isinstance(raw, dict):
                event = EventIn(**raw)
            elif isinstance(raw, EventIn):
                event = raw
            else:
                raise ValueError(f"Event must be a dict, got {type(raw).__name__}")
        except (ValidationError, Exception) as e:
            errors += 1
            eid = raw.get("event_id", "?") if isinstance(raw, dict) else "?"
            error_details.append({"index": i, "event_id": eid, "error": str(e)})
            continue  # skip this event, keep processing the rest

        try:
            is_new = _save_event(db, event)
            if is_new:
                ingested += 1
            else:
                duplicates += 1
        except Exception as e:
            errors += 1
            error_details.append({"index": i, "event_id": event.event_id, "error": str(e)})

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


@router.get("/events/recent")
def get_recent_events(
    store_id: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
):
    """
    Return the N most recent events (used by dashboard live feed).
    """
    q = db.query(EventORM)
    if store_id:
        q = q.filter(EventORM.store_id == store_id)
    rows = q.order_by(desc(EventORM.timestamp)).limit(limit).all()
    return {
        "events": [
            {
                "event_id":   r.event_id,
                "store_id":   r.store_id,
                "camera_id":  r.camera_id,
                "visitor_id": r.visitor_id,
                "event_type": r.event_type,
                "timestamp":  r.timestamp,
                "zone_id":    r.zone_id,
                "is_staff":   r.is_staff,
                "confidence": round(r.confidence, 3) if r.confidence else 0.0,
                "dwell_ms":   r.dwell_ms,
            }
            for r in rows
        ],
        "total": len(rows),
    }


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
