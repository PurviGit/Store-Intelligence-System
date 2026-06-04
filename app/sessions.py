"""
Cross-Camera Session Stitcher.

Problem: visitor_ids are camera-scoped. A person's entry camera ID
(e.g. STOREBRL002CAMENTRY01_00003) is different from their zone camera ID
(e.g. STOREBRL002CAMZONE02_00007). Direct visitor_id matching across cameras
fails — Stage 2 of the funnel would always be 0 when using real pipeline data.

Solution: Two-pass session matching.

Pass 1 — Direct ID match (handles test harness data where visitor_ids are consistent):
  - Zone/billing event visitor_id directly in the set of entry visitor_ids → matched.

Pass 2 — Time-window match (handles real pipeline data with camera-scoped IDs):
  - Build session windows from ENTRY+EXIT pairs: {session_id → (entry_ts, exit_ts)}
  - A zone/billing event at timestamp T belongs to session S if
    entry_ts(S) ≤ T ≤ exit_ts(S) + 60s  (60s buffer for camera lag).
  - This is correct: a person can only be in a zone AFTER they entered the store
    and BEFORE they exited. Any zone event in that window belongs to that session.
  - Multiple sessions may overlap (high-traffic stores); each zone event is assigned
    to the NEAREST session start.

This makes the funnel correct for:
  1. Synthetic test data with consistent visitor_ids (scoring harness)
  2. Real pipeline data where each camera tracks independently
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Set, Tuple, Optional
from sqlalchemy.orm import Session as DBSession
from models import EventORM
from sqlalchemy import distinct


ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _ts_to_sec(ts_str: str) -> float:
    """Convert ISO timestamp string to seconds since epoch."""
    dt = _parse_ts(ts_str)
    return dt.timestamp() if dt else 0.0


class SessionStitcher:
    """
    Builds cross-camera sessions from a store's events and provides
    methods to count how many sessions had zone visits, billing, purchases.
    """

    def __init__(self, db: DBSession, store_id: str, date_str: str,
                 session_timeout_s: int = 3600):
        self.store_id = store_id
        self.date_str = date_str
        self.timeout_s = session_timeout_s
        self._db = db

        # entry visitor_ids (ground truth for Stage 1)
        self.entry_visitors: Set[str] = set()
        # session windows: visitor_id → (entry_epoch, exit_epoch)
        self.sessions: Dict[str, Tuple[float, float]] = {}

        self._build_sessions()

    def _build_sessions(self):
        """Build entry session windows from ENTRY + EXIT events."""
        entries = self._db.query(EventORM).filter(
            EventORM.store_id == self.store_id,
            EventORM.event_type == "ENTRY",
            EventORM.is_staff.is_(False),
            EventORM.timestamp.like(f"{self.date_str}%"),
        ).order_by(EventORM.timestamp).all()

        exits = self._db.query(EventORM).filter(
            EventORM.store_id == self.store_id,
            EventORM.event_type == "EXIT",
            EventORM.timestamp.like(f"{self.date_str}%"),
        ).all()

        # Map visitor_id → latest exit epoch
        exit_map: Dict[str, float] = {}
        for e in exits:
            t = _ts_to_sec(e.timestamp)
            if e.visitor_id not in exit_map or t > exit_map[e.visitor_id]:
                exit_map[e.visitor_id] = t

        for entry in entries:
            vid = entry.visitor_id
            self.entry_visitors.add(vid)
            entry_t = _ts_to_sec(entry.timestamp)
            exit_t  = exit_map.get(vid, entry_t + self.timeout_s)
            # Ensure exit is at least 1 minute after entry
            exit_t = max(exit_t, entry_t + 60)
            self.sessions[vid] = (entry_t, exit_t)

    def _find_session_for_ts(self, event_ts: str) -> Optional[str]:
        """
        Find which session a timestamp falls into.
        Returns the visitor_id of the best-matching session,
        or None if no session matches.
        The 'best match' is the session whose entry_ts is closest and before event_ts.
        """
        t = _ts_to_sec(event_ts)
        if not t:
            return None

        best_vid = None
        best_gap = float("inf")
        buffer_s = 60  # 60s buffer for camera processing lag

        for vid, (entry_t, exit_t) in self.sessions.items():
            if entry_t <= t <= exit_t + buffer_s:
                gap = t - entry_t  # prefer session that started most recently before event
                if gap < best_gap:
                    best_gap = gap
                    best_vid = vid

        return best_vid

    def count_zone_sessions(self, zone_events: List[EventORM]) -> int:
        """
        Count how many unique entry sessions had at least one zone visit.
        Uses Pass 1 (direct ID) then Pass 2 (time window).
        """
        matched_sessions: Set[str] = set()

        for ev in zone_events:
            vid = ev.visitor_id
            # Pass 1: direct visitor_id match
            if vid in self.entry_visitors:
                matched_sessions.add(vid)
                continue
            # Pass 2: time-window match (camera-scoped IDs)
            session_vid = self._find_session_for_ts(ev.timestamp)
            if session_vid:
                matched_sessions.add(session_vid)

        return len(matched_sessions)

    def count_billing_sessions(self, billing_events: List[EventORM]) -> int:
        """Count unique entry sessions that reached billing (same logic)."""
        matched_sessions: Set[str] = set()

        for ev in billing_events:
            vid = ev.visitor_id
            if vid in self.entry_visitors:
                matched_sessions.add(vid)
                continue
            session_vid = self._find_session_for_ts(ev.timestamp)
            if session_vid:
                matched_sessions.add(session_vid)

        return len(matched_sessions)

    def count_entry_sessions(self) -> int:
        """Total unique customer entry sessions (Stage 1)."""
        return len(self.entry_visitors)

    @property
    def session_count(self) -> int:
        return len(self.sessions)
