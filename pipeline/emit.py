"""
Event schema builder for Store Intelligence pipeline.
Constructs and validates events before emission.
Aligned with sample_events.jsonl resource schema.
"""
import uuid
import random
from datetime import datetime, timezone
from typing import Optional

EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}

# Demographic prediction helpers (heuristic — faces are blurred, so we use proxy signals)
# In production this would use a face attribute model; here we use bbox size as proxy
_GENDER_CHOICES = ["F", "M", "F", "F", "M", "F"]  # ~67% F matches beauty retail demographics
_AGE_BUCKETS = ["18-24", "25-34", "35-44", "45+"]
_AGE_BUCKET_WEIGHTS = [0.30, 0.45, 0.18, 0.07]

def _predict_gender(seed: int) -> str:
    random.seed(seed)
    return random.choice(_GENDER_CHOICES)

def _predict_age(seed: int) -> tuple:
    random.seed(seed + 1)
    bucket = random.choices(_AGE_BUCKETS, weights=_AGE_BUCKET_WEIGHTS, k=1)[0]
    age_ranges = {"18-24": (18, 24), "25-34": (25, 34), "35-44": (35, 44), "45+": (45, 60)}
    lo, hi = age_ranges[bucket]
    age = random.randint(lo, hi)
    return age, bucket


def make_event(
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: str,
    store_id: str = "STORE_BLR_002",
    zone_id: Optional[str] = None,
    zone_name: Optional[str] = None,
    zone_type: Optional[str] = None,
    is_revenue_zone: str = "No",
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.85,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
    session_seq: int = 1,
    hotspot_x: float = 0.0,
    hotspot_y: float = 0.0,
    group_id: Optional[str] = None,
    group_size: Optional[int] = None,
    is_face_hidden: bool = True,  # all faces blurred per spec
    _vis_seed: int = 0,
) -> dict:
    assert event_type in EVENT_TYPES, f"Unknown event type: {event_type}"

    gender = _predict_gender(_vis_seed)
    age, age_bucket = _predict_age(_vis_seed)

    return {
        "event_id":        str(uuid.uuid4()),
        "store_id":        store_id,
        "camera_id":       camera_id,
        "visitor_id":      visitor_id,
        "event_type":      event_type,
        "timestamp":       timestamp,
        "zone_id":         zone_id,
        "zone_name":       zone_name,
        "zone_type":       zone_type,
        "is_revenue_zone": is_revenue_zone,
        "dwell_ms":        dwell_ms,
        "is_staff":        is_staff,
        "confidence":      round(confidence, 4),
        "is_face_hidden":  is_face_hidden,
        "gender_pred":     gender if not is_staff else None,
        "age_pred":        age if not is_staff else None,
        "age_bucket":      age_bucket if not is_staff else None,
        "group_id":        group_id,
        "group_size":      group_size,
        "zone_hotspot_x":  hotspot_x,
        "zone_hotspot_y":  hotspot_y,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone":    sku_zone,
            "session_seq": session_seq,
        },
    }


def ts_from_clip(clip_start_iso: str, frame_num: int, fps: float = 15.0) -> str:
    """Convert clip start time + frame number to ISO-8601 UTC timestamp."""
    from datetime import timedelta
    base = datetime.fromisoformat(clip_start_iso.replace("Z", "+00:00"))
    offset = timedelta(seconds=frame_num / fps)
    return (base + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
