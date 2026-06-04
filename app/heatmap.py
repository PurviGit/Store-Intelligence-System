from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from database import get_db
from models import EventORM
from datetime import datetime, timezone
from metrics import _latest_event_date
from typing import Optional
import json, os

router = APIRouter()

# Load zone metadata from store_layout.json (multi-store format)
def _load_zone_meta() -> dict:
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "data", "store_layout.json"),
        "data/store_layout.json",
        "../data/store_layout.json",
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p) as f:
                layout = json.load(f)
            # Handle both single-store {zones:[]} and multi-store {stores:[{zones:[]}]}
            if "stores" in layout:
                result = {}
                for store in layout["stores"]:
                    for z in store.get("zones", []):
                        result[z["zone_id"]] = z
                return result
            return {z["zone_id"]: z for z in layout.get("zones", [])}
    return {}

ZONE_META = _load_zone_meta()


@router.get("/stores/{store_id}/heatmap")
def get_heatmap(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Zone visit frequency + avg dwell, normalised 0-100.
    Includes data_confidence flag if fewer than 20 sessions in window.
    """
    date_str = date or _latest_event_date(db, store_id)

    # Include billing zone events (BILLING_QUEUE_JOIN) alongside product zones
    rows = db.query(
        EventORM.zone_id,
        func.count(distinct(EventORM.visitor_id)).label("unique_visitors"),
        func.count(EventORM.event_id).label("visit_events"),
        func.avg(EventORM.dwell_ms).label("avg_dwell_ms"),
    ).filter(
        EventORM.store_id == store_id,
        EventORM.event_type.in_([
            "ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT",
            "BILLING_QUEUE_JOIN",  # billing zone footfall
        ]),
        EventORM.is_staff == False,
        EventORM.zone_id.isnot(None),
        EventORM.zone_id != "ENTRY_THRESHOLD",  # exclude entry threshold
        EventORM.timestamp.like(f"{date_str}%"),
    ).group_by(EventORM.zone_id).all()

    if not rows:
        return {
            "store_id": store_id,
            "date": date_str,
            "zones": [],
            "data_confidence": "LOW",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Normalise visit_events to 0-100
    max_visits = max(r.visit_events for r in rows)
    max_dwell = max((r.avg_dwell_ms or 0) for r in rows)

    # Confidence is based on unique entering visitors (Stage 1), NOT the sum of
    # per-zone unique visitors. The previous logic summed zone visitors (5 visitors
    # × 3 zones = 15), which could flip LOW→HIGH incorrectly. The spec says
    # "fewer than 20 sessions in window" — session = one visitor visit, not one
    # zone event. Use distinct ENTRY count as the session count.
    unique_session_count = db.query(
        func.count(distinct(EventORM.visitor_id))
    ).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "ENTRY",
        EventORM.is_staff == False,
        EventORM.timestamp.like(f"{date_str}%"),
    ).scalar() or 0
    confidence = "HIGH" if unique_session_count >= 20 else "LOW"

    zones = []
    for r in rows:
        if r.zone_id is None:
            continue
        freq_score = round(r.visit_events / max_visits * 100, 1) if max_visits > 0 else 0.0
        dwell_score = round((r.avg_dwell_ms or 0) / max_dwell * 100, 1) if max_dwell > 0 else 0.0
        meta = ZONE_META.get(r.zone_id, {})
        zones.append({
            "zone_id": r.zone_id,
            "sku_zone": meta.get("sku_zone"),        # from store_layout.json
            "camera_id": meta.get("camera_id"),      # from store_layout.json
            "unique_visitors": r.unique_visitors,
            "visit_events": r.visit_events,
            "avg_dwell_ms": round(r.avg_dwell_ms or 0, 1),
            "frequency_score": freq_score,
            "dwell_score": dwell_score,
        })

    # Sort by frequency score descending
    zones.sort(key=lambda z: z["frequency_score"], reverse=True)

    return {
        "store_id": store_id,
        "date": date_str,
        "zones": zones,
        "data_confidence": confidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
