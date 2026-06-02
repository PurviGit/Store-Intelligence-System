"""
Tracker and Re-ID module.
Handles bounding-box IoU tracking, visitor_id assignment, and re-entry detection.
"""
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Track:
    track_id: int
    visitor_id: str
    cx: float           # centroid x
    cy: float           # centroid y
    area: float         # bounding box area
    last_seen_frame: int
    session_seq: int = 0
    is_staff: bool = False
    zone_id: Optional[str] = None
    zone_enter_frame: Optional[int] = None


@dataclass
class ExitedVisitor:
    visitor_id: str
    cx: float
    cy: float
    area: float
    exit_frame: int


class Tracker:
    """
    IoU + centroid tracker with re-entry Re-ID.

    Re-entry detection:
      When a new detection appears within REENTRY_DIST_PX of an exited
      visitor's last centroid AND area ratio is within AREA_RATIO_RANGE,
      the same visitor_id is reused and a REENTRY event is emitted.
    """

    REENTRY_DIST_PX = 120
    AREA_RATIO_MIN = 0.5
    AREA_RATIO_MAX = 2.0
    MAX_FRAMES_LOST = 45         # ~3s at 15fps before track is dropped
    REENTRY_WINDOW_FRAMES = 1800 # ~2 minutes at 15fps

    def __init__(self):
        self._tracks: Dict[int, Track] = {}
        self._exited: List[ExitedVisitor] = []
        self._next_id = 1
        self._visitor_counter = 0

    def _new_visitor_id(self) -> str:
        self._visitor_counter += 1
        # Camera prefix prevents ID collision when multiple cameras run independently
        prefix = getattr(self, "_camera_prefix", "VIS")
        return f"{prefix}_{self._visitor_counter:05d}"

    def _centroid(self, bbox) -> Tuple[float, float, float]:
        x, y, w, h = bbox
        return x + w / 2, y + h / 2, w * h

    def _dist(self, cx1, cy1, cx2, cy2) -> float:
        return math.sqrt((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2)

    def _match_exited(self, cx: float, cy: float, area: float, current_frame: int) -> Optional[str]:
        """Try to match a new detection to a recently exited visitor (Re-ID)."""
        best_vid = None
        best_dist = float("inf")
        for ev in self._exited:
            age = current_frame - ev.exit_frame
            if age > self.REENTRY_WINDOW_FRAMES:
                continue
            dist = self._dist(cx, cy, ev.cx, ev.cy)
            if dist > self.REENTRY_DIST_PX:
                continue
            ratio = area / ev.area if ev.area > 0 else 999
            if not (self.AREA_RATIO_MIN <= ratio <= self.AREA_RATIO_MAX):
                continue
            if dist < best_dist:
                best_dist = dist
                best_vid = ev.visitor_id
        return best_vid

    def _iou(self, b1, b2) -> float:
        x1, y1, w1, h1 = b1
        x2, y2, w2, h2 = b2
        ix = max(0, min(x1 + w1, x2 + w2) - max(x1, x2))
        iy = max(0, min(y1 + h1, y2 + h2) - max(y1, y2))
        inter = ix * iy
        union = w1 * h1 + w2 * h2 - inter
        return inter / union if union > 0 else 0.0

    def update(self, detections: List[dict], frame_num: int) -> List[dict]:
        """
        detections: list of {bbox: (x,y,w,h), confidence: float, is_staff: bool}
        Returns list of track events: {track_id, visitor_id, is_new, is_reentry, is_staff,
                                       cx, cy, area, confidence, session_seq}
        """
        results = []
        matched_track_ids = set()

        for det in detections:
            bbox = det["bbox"]
            cx, cy, area = self._centroid(bbox)
            conf = det.get("confidence", 0.85)
            is_staff = det.get("is_staff", False)

            # Try to match to existing track via IoU
            best_iou = 0.0
            best_tid = None
            for tid, track in self._tracks.items():
                # reconstruct approx bbox from centroid+area
                side = math.sqrt(track.area)
                approx_bbox = (track.cx - side/2, track.cy - side/2, side, side)
                iou = self._iou(bbox, approx_bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid

            is_new = False
            is_reentry = False

            if best_tid is not None and best_iou > 0.3:
                track = self._tracks[best_tid]
                track.cx = cx
                track.cy = cy
                track.area = area
                track.last_seen_frame = frame_num
                track.session_seq += 1
                vid = track.visitor_id
                tid = best_tid
            else:
                # New detection — check re-entry
                reentry_vid = self._match_exited(cx, cy, area, frame_num)
                if reentry_vid:
                    vid = reentry_vid
                    is_reentry = True
                    # Remove from exited list
                    self._exited = [e for e in self._exited if e.visitor_id != reentry_vid]
                else:
                    vid = self._new_visitor_id()
                    is_new = True

                tid = self._next_id
                self._next_id += 1
                track = Track(
                    track_id=tid,
                    visitor_id=vid,
                    cx=cx, cy=cy, area=area,
                    last_seen_frame=frame_num,
                    session_seq=1,
                    is_staff=is_staff,
                )
                self._tracks[tid] = track

            matched_track_ids.add(tid)
            results.append({
                "track_id": tid,
                "visitor_id": vid,
                "is_new": is_new,
                "is_reentry": is_reentry,
                "is_staff": is_staff,
                "cx": cx,
                "cy": cy,
                "area": area,
                "confidence": conf,
                "session_seq": track.session_seq,
            })

        # Mark lost tracks and move to exited list
        lost = [tid for tid, t in self._tracks.items()
                if frame_num - t.last_seen_frame > self.MAX_FRAMES_LOST
                and tid not in matched_track_ids]
        for tid in lost:
            track = self._tracks.pop(tid)
            self._exited.append(ExitedVisitor(
                visitor_id=track.visitor_id,
                cx=track.cx, cy=track.cy, area=track.area,
                exit_frame=frame_num,
            ))

        # Prune stale exited visitors
        self._exited = [e for e in self._exited
                        if frame_num - e.exit_frame <= self.REENTRY_WINDOW_FRAMES]

        return results

    def get_active_tracks(self) -> List[Track]:
        return list(self._tracks.values())

    def flush_exits(self, frame_num: int) -> List[str]:
        """Return visitor_ids of tracks that just became lost (for EXIT events)."""
        exits = []
        lost = [tid for tid, t in self._tracks.items()
                if frame_num - t.last_seen_frame > self.MAX_FRAMES_LOST]
        for tid in lost:
            track = self._tracks.pop(tid)
            exits.append(track.visitor_id)
            self._exited.append(ExitedVisitor(
                visitor_id=track.visitor_id,
                cx=track.cx, cy=track.cy, area=track.area,
                exit_frame=frame_num,
            ))
        return exits
