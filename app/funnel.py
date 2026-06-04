"""
Conversion Funnel — 4 stages, cross-camera session stitching.

Stage 1: Unique customer entries (entry camera, staff excluded)
Stage 2: Sessions with at least one product-zone visit
Stage 3: Sessions that reached billing (BILLING_QUEUE_JOIN or POS-anchored)
Stage 4: Sessions with a confirmed POS purchase

Cross-camera Re-ID:
  visitor_ids are camera-scoped by the detection pipeline. The SessionStitcher
  (sessions.py) uses a two-pass approach:
    Pass 1: direct visitor_id match (works for test harness data with consistent IDs)
    Pass 2: time-window match (works for real pipeline data with camera-scoped IDs)
  This makes the funnel correct in both test and production scenarios.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import distinct, func
from database import get_db
from models import EventORM
from datetime import datetime, timezone
from typing import Optional
from metrics import load_pos_transactions, _latest_event_date
from sessions import SessionStitcher

router = APIRouter()


@router.get("/stores/{store_id}/funnel")
def get_funnel(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Conversion funnel: Entry → Zone Browse → Billing Queue → Purchase.

    Session is the unit. Re-entries do NOT double-count a visitor
    (REENTRY events share the same visitor_id as the original ENTRY).

    Cross-camera sessions are stitched by time window when visitor_ids differ
    across cameras (real pipeline scenario). When visitor_ids are consistent
    across cameras (test harness scenario), direct matching is used.
    """
    date_str = date or _latest_event_date(db, store_id)

    # ── Build cross-camera session stitcher ───────────────────────────────────
    # This resolves the camera-scoped visitor_id problem for all funnel stages.
    stitcher = SessionStitcher(db, store_id, date_str)

    # ── Stage 1: Unique customer entry sessions ───────────────────────────────
    stage1_visitors = stitcher.count_entry_sessions()

    # ── Stage 2: Sessions with product-zone engagement ───────────────────────
    # Fetch all zone events (non-staff, non-entry-threshold, non-billing zones).
    zone_events = db.query(EventORM).filter(
        EventORM.store_id == store_id,
        EventORM.is_staff.is_(False),
        EventORM.zone_id.isnot(None),
        EventORM.zone_id != "ENTRY_THRESHOLD",
        EventORM.zone_id != "BILLING",
        EventORM.event_type.in_(["ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT"]),
        EventORM.timestamp.like(f"{date_str}%"),
    ).all()

    stage2_visitors_raw = stitcher.count_zone_sessions(zone_events)
    # Physical constraint: can't browse if you haven't entered
    stage2_visitors = min(stage2_visitors_raw, stage1_visitors)

    # ── Stage 3: Sessions that reached billing ───────────────────────────────
    billing_events = db.query(EventORM).filter(
        EventORM.store_id == store_id,
        EventORM.is_staff.is_(False),
        EventORM.event_type.in_(["BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON",
                                  "ZONE_ENTER", "ZONE_DWELL"]),
        EventORM.zone_id == "BILLING",
        EventORM.timestamp.like(f"{date_str}%"),
    ).all()

    billing_sessions = stitcher.count_billing_sessions(billing_events)

    # Fallback: POS transactions + abandonments (most reliable ground truth)
    transactions = load_pos_transactions(store_id, date_str)
    num_purchases = len(transactions)

    abandon_count = db.query(func.count(distinct(EventORM.visitor_id))).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "BILLING_QUEUE_ABANDON",
        EventORM.dwell_ms > 5000,
        EventORM.timestamp.like(f"{date_str}%"),
    ).scalar() or 0

    # Use the larger of CCTV count vs POS-anchored count, capped at Stage 1
    # Stage 3 = CCTV billing arrivals (primary signal).
    # POS provides a minimum floor only: at minimum, num_purchases visitors reached billing.
    # Previously used max(billing_sessions, num_purchases + abandons) which could inflate
    # Stage 3 above reality. Now billing_sessions is authoritative; POS is just a floor.
    stage3_raw = max(billing_sessions, num_purchases)
    stage3_visitors = min(stage3_raw, stage1_visitors)

    # ── Stage 4: Confirmed purchases (POS ground truth) ──────────────────────
    stage4_visitors = min(num_purchases, stage3_visitors)

    def drop_pct(prev: int, curr: int) -> float:
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
                "description": "Unique customer entry sessions (staff excluded, re-entries deduplicated)",
                "visitors": stage1_visitors,
                "drop_off_pct": 0.0,
            },
            {
                "stage": 2,
                "label": "Zone Browse",
                "description": "Sessions with at least one product-zone visit (cross-camera stitched)",
                "visitors": stage2_visitors,
                "drop_off_pct": drop_pct(stage1_visitors, stage2_visitors),
            },
            {
                "stage": 3,
                "label": "Billing Queue",
                "description": "Sessions that reached billing (CCTV + POS-anchored, deduplicated)",
                "visitors": stage3_visitors,
                "drop_off_pct": drop_pct(stage2_visitors, stage3_visitors),
            },
            {
                "stage": 4,
                "label": "Purchase",
                "description": "Sessions with confirmed POS transaction",
                "visitors": stage4_visitors,
                "drop_off_pct": drop_pct(stage3_visitors, stage4_visitors),
            },
        ],
        "overall_conversion_pct": (
            round(stage4_visitors / stage1_visitors * 100, 1)
            if stage1_visitors > 0 else 0.0
        ),
        "cross_camera_reid": True,
        "reid_method": "time_window_stitching",
        "note": (
            "Stage 2 uses two-pass cross-camera session stitching: "
            "direct visitor_id match (pass 1) + time-window match (pass 2). "
            "This handles both test data with consistent IDs and real pipeline data "
            "with camera-scoped IDs."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
