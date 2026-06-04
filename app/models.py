"""
Pydantic event schema (validation) and SQLAlchemy ORM models.
Aligned with sample_events.jsonl resource schema from updated problem statement.
"""
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, String, Integer, Float, Boolean, Text
from database import Base
import datetime

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "ZONE_ENTER", "ZONE_EXIT",
    "ZONE_DWELL", "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "REENTRY",
}

AGE_BUCKETS = {"18-24", "25-34", "35-44", "45+"}


# ─── SQLAlchemy ORM ─────────────────────────────────────────────────────────────

class EventORM(Base):
    __tablename__ = "events"

    event_id         = Column(String, primary_key=True)
    store_id         = Column(String, index=True, nullable=False)
    camera_id        = Column(String, nullable=False)
    visitor_id       = Column(String, nullable=False)
    event_type       = Column(String, nullable=False)
    timestamp        = Column(String, index=True, nullable=False)
    zone_id          = Column(String, nullable=True)
    zone_name        = Column(String, nullable=True)
    zone_type        = Column(String, nullable=True)
    is_revenue_zone  = Column(String, nullable=True, default="No")
    dwell_ms         = Column(Integer, default=0)
    is_staff         = Column(Boolean, default=False)
    confidence       = Column(Float, default=0.0)
    is_face_hidden   = Column(Boolean, default=True)
    gender_pred      = Column(String, nullable=True)
    age_pred         = Column(Integer, nullable=True)
    age_bucket       = Column(String, nullable=True)
    group_id         = Column(String, nullable=True)
    group_size       = Column(Integer, nullable=True)
    zone_hotspot_x   = Column(Float, nullable=True)
    zone_hotspot_y   = Column(Float, nullable=True)
    # metadata fields
    queue_depth      = Column(Integer, nullable=True)
    sku_zone         = Column(String, nullable=True)
    session_seq      = Column(Integer, default=1)


# ─── Pydantic Schema ─────────────────────────────────────────────────────────────

class EventMetadata(BaseModel):
    queue_depth: Optional[int] = None
    sku_zone:    Optional[str] = None
    session_seq: int = 1


class EventIn(BaseModel):
    event_id:        str
    store_id:        str
    camera_id:       str
    visitor_id:      str
    event_type:      str
    timestamp:       str
    zone_id:         Optional[str] = None
    zone_name:       Optional[str] = None
    zone_type:       Optional[str] = None
    is_revenue_zone: Optional[str] = "No"
    dwell_ms:        int = 0
    is_staff:        bool = False
    confidence:      float = Field(ge=0.0, le=1.0, default=0.85)
    is_face_hidden:  bool = True
    gender_pred:     Optional[str] = None
    age_pred:        Optional[int] = None
    age_bucket:      Optional[str] = None
    group_id:        Optional[str] = None
    group_size:      Optional[int] = None
    zone_hotspot_x:  Optional[float] = None
    zone_hotspot_y:  Optional[float] = None
    metadata:        Optional[EventMetadata] = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v):
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event_type '{v}'. Must be one of {VALID_EVENT_TYPES}")
        return v

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v):
        try:
            datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"Invalid ISO-8601 timestamp: {v}")
        return v


class IngestRequest(BaseModel):
    events: List[Any]  # Accept raw dicts; validated per-event in the endpoint


class IngestResponse(BaseModel):
    ingested:      int
    duplicates:    int
    errors:        int
    error_details: List[dict] = []
