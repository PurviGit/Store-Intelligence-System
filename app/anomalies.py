from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from database import get_db
from models import EventORM
from datetime import datetime, timezone, timedelta
from typing import Optional, List
import uuid

router = APIRouter()

QUEUE_SPIKE_THRESHOLD = 5
DEAD_ZONE_MINUTES = 30
CONVERSION_DROP_THRESHOLD = 0.3  # 30% drop vs recent avg


def _now_utc():
    return datetime.now(timezone.utc)


def _detect_queue_spike(db: Session, store_id: str, date_str: str) -> Optional[dict]:
    """BILLING_QUEUE_SPIKE: queue depth > threshold."""
    max_depth = db.query(func.max(EventORM.queue_depth)).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "BILLING_QUEUE_JOIN",
        EventORM.timestamp.like(f"{date_str}%"),
    ).scalar()

    if max_depth and max_depth >= QUEUE_SPIKE_THRESHOLD:
        return {
            "anomaly_id": str(uuid.uuid4()),
            "type": "BILLING_QUEUE_SPIKE",
            "severity": "CRITICAL" if max_depth >= 8 else "WARN",
            "description": f"Billing queue depth reached {max_depth}",
            "suggested_action": "Open additional billing counter or redirect customers",
            "detected_at": _now_utc().isoformat(),
            "details": {"max_queue_depth": max_depth},
        }
    return None


def _detect_conversion_drop(db: Session, store_id: str, date_str: str) -> Optional[dict]:
    """CONVERSION_DROP: today's rate < recent average by threshold."""
    # Today's visitors
    today_visitors = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "ENTRY",
        EventORM.is_staff == False,
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()

    today_billing = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.zone_id == "BILLING",
        EventORM.is_staff == False,
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()

    if today_visitors < 5:
        return None  # not enough data

    today_rate = today_billing / today_visitors if today_visitors > 0 else 0.0

    # Baseline: all-time rate from stored data (7-day avg approximation)
    all_visitors = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "ENTRY",
        EventORM.is_staff == False,
    ).count()

    all_billing = db.query(distinct(EventORM.visitor_id)).filter(
        EventORM.store_id == store_id,
        EventORM.zone_id == "BILLING",
        EventORM.is_staff == False,
    ).count()

    baseline_rate = all_billing / all_visitors if all_visitors > 0 else today_rate

    if baseline_rate > 0 and today_rate < baseline_rate * (1 - CONVERSION_DROP_THRESHOLD):
        drop_pct = round((baseline_rate - today_rate) / baseline_rate * 100, 1)
        return {
            "anomaly_id": str(uuid.uuid4()),
            "type": "CONVERSION_DROP",
            "severity": "WARN",
            "description": f"Conversion rate {round(today_rate*100,1)}% is {drop_pct}% below baseline {round(baseline_rate*100,1)}%",
            "suggested_action": "Review zone engagement and staff assistance levels",
            "detected_at": _now_utc().isoformat(),
            "details": {"today_rate": today_rate, "baseline_rate": baseline_rate, "drop_pct": drop_pct},
        }
    return None


def _detect_dead_zones(db: Session, store_id: str, reference_time: datetime) -> List[dict]:
    """DEAD_ZONE: no zone visits in past 30 minutes relative to reference_time."""
    cutoff = (reference_time - timedelta(minutes=DEAD_ZONE_MINUTES)).strftime("%Y-%m-%dT%H:%M:%SZ")
    anomalies = []

    all_zones = db.query(distinct(EventORM.zone_id)).filter(
        EventORM.store_id == store_id,
        EventORM.zone_id.isnot(None),
    ).all()

    for (zone_id,) in all_zones:
        if zone_id in ("ENTRY_THRESHOLD", "BILLING"):
            continue
        recent = db.query(EventORM).filter(
            EventORM.store_id == store_id,
            EventORM.zone_id == zone_id,
            EventORM.timestamp >= cutoff,
        ).count()
        if recent == 0:
            anomalies.append({
                "anomaly_id": str(uuid.uuid4()),
                "type": "DEAD_ZONE",
                "severity": "INFO",
                "description": f"Zone {zone_id} had no visits in the last {DEAD_ZONE_MINUTES} min of the session",
                "suggested_action": f"Check zone {zone_id} for display issues or redirect staff",
                "detected_at": _now_utc().isoformat(),
                "details": {"zone_id": zone_id, "window_minutes": DEAD_ZONE_MINUTES},
            })
    return anomalies


def _detect_high_abandonment(db: Session, store_id: str, date_str: str) -> Optional[dict]:
    """HIGH_ABANDONMENT: abandonment rate > 50%."""
    billing_entries = db.query(EventORM).filter(
        EventORM.store_id == store_id,
        EventORM.zone_id == "BILLING",
        EventORM.is_staff == False,
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()

    abandoned = db.query(EventORM).filter(
        EventORM.store_id == store_id,
        EventORM.event_type == "BILLING_QUEUE_ABANDON",
        EventORM.timestamp.like(f"{date_str}%"),
    ).count()

    if billing_entries < 3:
        return None

    rate = abandoned / billing_entries
    if rate > 0.5:
        return {
            "anomaly_id": str(uuid.uuid4()),
            "type": "HIGH_ABANDONMENT",
            "severity": "WARN",
            "description": f"Billing abandonment rate is {round(rate*100,1)}%",
            "suggested_action": "Investigate checkout friction — long wait times or out-of-stock items",
            "detected_at": _now_utc().isoformat(),
            "details": {"abandonment_rate": round(rate, 4), "abandoned": abandoned, "billing_entries": billing_entries},
        }
    return None


def _detect_stale_camera(db: Session, store_id: str, reference_time: datetime, is_historical: bool) -> List[dict]:
    """
    STALE_CAMERA_FEED: camera silent for > 10 min.
    For historical data (date != today), uses the last event time per camera
    as the reference so we detect cameras that went silent DURING the recorded session,
    not cameras that are 'offline' simply because the recording ended months ago.
    """
    anomalies = []

    all_cams = db.query(distinct(EventORM.camera_id)).filter(
        EventORM.store_id == store_id,
    ).all()

    for (cam_id,) in all_cams:
        if not cam_id:
            continue

        if is_historical:
            # For historical data: find the last event from this camera
            # and the last event across ALL cameras — if a camera stopped
            # sending 10+ minutes before the session ended, it was stale.
            last_cam = db.query(func.max(EventORM.timestamp)).filter(
                EventORM.store_id == store_id,
                EventORM.camera_id == cam_id,
            ).scalar()
            last_any = db.query(func.max(EventORM.timestamp)).filter(
                EventORM.store_id == store_id,
            ).scalar()
            if not last_cam or not last_any:
                continue
            # Only flag if camera stopped 10+ min before session ended
            try:
                t_cam = datetime.fromisoformat(last_cam.replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(last_any.replace("Z", "+00:00"))
                gap = (t_end - t_cam).total_seconds() / 60
                if gap < 10:
                    continue  # camera was active until near end of session
            except Exception:
                continue
            anomalies.append({
                "anomaly_id": str(uuid.uuid4()),
                "type": "STALE_CAMERA_FEED",
                "severity": "WARN",
                "description": f"Camera {cam_id} stopped sending {round(gap,0):.0f} min before session end",
                "suggested_action": "Camera went silent during recorded session — check mounting/connectivity",
                "detected_at": _now_utc().isoformat(),
                "details": {"camera_id": cam_id, "gap_minutes": round(gap, 1), "mode": "historical"},
            })
        else:
            # Live mode: standard 10-min cutoff from now
            cutoff = (reference_time - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
            recent = db.query(EventORM).filter(
                EventORM.store_id == store_id,
                EventORM.camera_id == cam_id,
                EventORM.timestamp >= cutoff,
            ).count()
            if recent == 0:
                anomalies.append({
                    "anomaly_id": str(uuid.uuid4()),
                    "type": "STALE_CAMERA_FEED",
                    "severity": "CRITICAL",
                    "description": f"Camera {cam_id} has not sent events in over 10 minutes",
                    "suggested_action": "Check camera connectivity and restart feed if necessary",
                    "detected_at": _now_utc().isoformat(),
                    "details": {"camera_id": cam_id, "cutoff": cutoff, "mode": "live"},
                })
    return anomalies


@router.get("/stores/{store_id}/anomalies")
def get_anomalies(
    store_id: str,
    date: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Active anomalies: queue spike, conversion drop, dead zone, high abandonment, stale camera.
    Severity: INFO / WARN / CRITICAL. Includes suggested_action per anomaly.
    """
    date_str = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    is_historical = (date_str != today_str)

    # For historical dates, use the last event timestamp as reference;
    # for today, use current clock time.
    if is_historical:
        last_ts = db.query(func.max(EventORM.timestamp)).filter(
            EventORM.store_id == store_id,
            EventORM.timestamp.like(f"{date_str}%"),
        ).scalar()
        if last_ts:
            try:
                reference_time = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            except Exception:
                reference_time = _now_utc()
        else:
            reference_time = _now_utc()
    else:
        reference_time = _now_utc()

    anomalies = []

    spike = _detect_queue_spike(db, store_id, date_str)
    if spike:
        anomalies.append(spike)

    conv_drop = _detect_conversion_drop(db, store_id, date_str)
    if conv_drop:
        anomalies.append(conv_drop)

    anomalies.extend(_detect_dead_zones(db, store_id, reference_time))

    high_abandon = _detect_high_abandonment(db, store_id, date_str)
    if high_abandon:
        anomalies.append(high_abandon)

    anomalies.extend(_detect_stale_camera(db, store_id, reference_time, is_historical))

    # Sort by severity: CRITICAL > WARN > INFO
    severity_order = {"CRITICAL": 0, "WARN": 1, "INFO": 2}
    anomalies.sort(key=lambda a: severity_order.get(a["severity"], 3))

    return {
        "store_id": store_id,
        "date": date_str,
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "generated_at": _now_utc().isoformat(),
    }
