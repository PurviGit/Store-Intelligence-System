"""
Event schema builder for Store Intelligence pipeline.
Constructs and validates events before emission.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional, Any


EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY"
}

STORE_ID = "STORE_BLR_002"


def make_event(
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: str,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.85,
    queue_depth: Optional[int] = None,
    sku_zone: Optional[str] = None,
    session_seq: int = 1,
) -> dict:
    assert event_type in EVENT_TYPES, f"Unknown event type: {event_type}"
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": round(confidence, 4),
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone": sku_zone,
            "session_seq": session_seq,
        },
    }


def ts_from_clip(clip_start_iso: str, frame_num: int, fps: float = 15.0) -> str:
    """Convert clip start time + frame number to ISO-8601 UTC timestamp."""
    from datetime import timedelta
    base = datetime.fromisoformat(clip_start_iso.replace("Z", "+00:00"))
    offset = timedelta(seconds=frame_num / fps)
    return (base + offset).strftime("%Y-%m-%dT%H:%M:%SZ")
