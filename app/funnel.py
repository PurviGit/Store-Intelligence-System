from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import distinct, func
from database import get_db
from models import EventORM
from datetime import datetime, timezone
from typing import Optional
from metrics import load_pos_transactions

router = APIRouter()


@router.get("/stores/{store_id}/funnel")
def get_funnel(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Conversion funnel: Entry -> Zone Visit -> Billing Queue -> Purchase
    Session is the unit. Re-entries do NOT double-count a visitor.

    Cross-camera note: each camera runs an independent tracker, so visitor_ids
    are camera-scoped (prefixed). Cross-camera Re-ID is V2 scope. Funnel stages
    use per-camera data with POS transactions as the authoritative billing anchor.
    """
    date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Stage 1: Unique customer entries ────────────────────────────────────
    # Source: entry camera (CAM_ENTRY_01). De-duplicated by visitor_id.
    # REENTRY events share the same visitor_id so they don't inflate count.
    stage1_visitors = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "ENTRY",
        EventORM.is_staff == False,
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()

    # ── Stage 2: Zone engagement sessions ───────────────────────────────────
    # Source: floor/zone cameras. Distinct zone sessions (visitor_ids from
    # product zone cameras). Capped at Stage 1 — can't browse more than entered.
    # This is a conservative lower bound; true value requires cross-camera Re-ID.
    PRODUCT_ZONES = ["SKINCARE", "MAKEUP", "EB_KOREAN", "HAIRCARE", "DERMDOC", "BATH_BODY"]
    zone_sessions = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.is_staff == False,
        EventORM.zone_id.in_(PRODUCT_ZONES),
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()
    # Zone sessions are from independent camera trackers.
    # We cap at stage1 (physical max) since we can't have more browsers than entrants.
    stage2_visitors = min(zone_sessions, stage1_visitors) if stage1_visitors > 0 else zone_sessions

    # ── Stage 3: Billing zone reached ───────────────────────────────────────
    # Source: POS transactions + billing abandonments.
    # POS data is the most reliable anchor — each transaction = one real purchase.
    # Billing reached = purchases + those who queued but abandoned.
    transactions = load_pos_transactions(store_id, date_str)
    num_purchases = len(transactions)

    # Only count abandonments with dwell > 5s — filters out clip-end flush artefacts
    abandoned_count = db.query(func.count(distinct(EventORM.visitor_id))).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "BILLING_QUEUE_ABANDON",
        EventORM.dwell_ms > 5000,
        EventORM.timestamp.like(f"{date_str}%"),
    ).scalar() or 0

    # Billing visitors = confirmed purchases + real abandonments (POS-anchored)
    stage3_visitors = min(num_purchases + abandoned_count, stage2_visitors)

    # ── Stage 4: Confirmed purchases ────────────────────────────────────────
    # Source: POS transaction records — most reliable data in the system.
    stage4_visitors = min(num_purchases, stage3_visitors)

    def drop_pct(prev, curr):
        if prev == 0:
            return 0.0
        return round((prev - curr) / prev * 100, 1)

    return {
        "store_id": store_id,
        "date": date_str,
        "funnel": [
            {
                "stage": 1,
                "label": "Entry",
                "description": "Unique customer entries (entry camera, staff excluded)",
                "visitors": stage1_visitors,
                "drop_off_pct": 0.0,
            },
            {
                "stage": 2,
                "label": "Zone Browse",
                "description": "Visitors who engaged with product zones",
                "visitors": stage2_visitors,
                "drop_off_pct": drop_pct(stage1_visitors, stage2_visitors),
            },
            {
                "stage": 3,
                "label": "Billing Queue",
                "description": "Visitors who reached billing (purchases + abandonments)",
                "visitors": stage3_visitors,
                "drop_off_pct": drop_pct(stage2_visitors, stage3_visitors),
            },
            {
                "stage": 4,
                "label": "Purchase",
                "description": "Confirmed POS transactions",
                "visitors": stage4_visitors,
                "drop_off_pct": drop_pct(stage3_visitors, stage4_visitors),
            },
        ],
        "overall_conversion_pct": round(stage4_visitors / stage1_visitors * 100, 1) if stage1_visitors > 0 else 0.0,
        "cross_camera_reid": False,
        "note": "Stage 2 uses zone-camera sessions. Cross-camera Re-ID planned for V2.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
