"""
Tracker and Re-ID module.
Handles bounding-box IoU tracking, visitor_id assignment,
trajectory-based direction detection, and appearance-based re-entry Re-ID.

## Direction Detection
Rather than testing centroid position at first detection (fragile — depends on
camera mounting height), we buffer 8 frames of centroid history per track and
compute the net y-velocity via linear regression. In a standard door-facing camera:
  - Entering customers walk INTO store  → y increases (positive slope = ENTRY)
  - Exiting customers walk TOWARD door → y decreases (negative slope = EXIT)
We wait DIRECTION_BUFFER_FRAMES before emitting ENTRY/EXIT, trading 0.5s latency
for a significant accuracy improvement over the position heuristic.

## Re-ID (Re-entry Detection)
Two-stage matching — geometric first (fast), then appearance (accurate):

Stage 1 — Geometric:
  - Centroid distance < REENTRY_DIST_PX (200px ≈ doorway width at 1080p)
  - Bounding box area ratio in [0.4, 2.5] (same body size within ±60%)
  - Within REENTRY_WINDOW_FRAMES (4500 = 5 min at 15fps)

Stage 2 — Appearance (HSV color histogram):
  - Extract 16×16 HSV histogram from bounding box crop
  - H+S channels only (brightness/V excluded — too lighting-dependent)
  - Compare with saved histogram using OpenCV HISTCMP_CORREL
  - Threshold: correlation > 0.35 (clothing colour is stable even with lighting change)

Combined score = hist_correlation × (1 - dist/REENTRY_DIST_PX)
→ picks the best geometric+appearance match.

Why color histograms (not face Re-ID or deep embedding):
  - Faces are fully blurred per problem statement — face Re-ID impossible
  - OSNet/torchreid requires GPU inference — unavailable in acceptance-gate container
  - Color histograms run on CPU in <0.5ms per crop — zero latency impact
  - Clothing colour is the most stable discriminator available in this scenario
"""
import math
import numpy as np
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False


# ─── Appearance feature extraction ─────────────────────────────────────────

HIST_BINS = 16          # 16×16 HSV histogram → 256-dim feature vector
HIST_SIMILARITY_THRESHOLD = 0.35  # minimum histogram correlation for re-entry match
                                   # 0.35 is intentionally loose — clothing seen from
                                   # different angles has moderate colour correlation


def compute_color_histogram(frame: np.ndarray, bbox: tuple) -> Optional[np.ndarray]:
    """
    Compute a normalised HSV color histogram from a bounding box crop.

    Uses H and S channels (hue + saturation). Omits V (brightness) because
    lighting changes in retail CCTV affect brightness significantly but not hue.

    Returns a flattened float32 array of length HIST_BINS² or None on failure.
    """
    if not _CV2_AVAILABLE or frame is None:
        return None
    try:
        x, y, w, h = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        x1, y1 = max(0, x), max(0, y)
        x2, y2 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
        if x2 <= x1 + 4 or y2 <= y1 + 4:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None,
                            [HIST_BINS, HIST_BINS], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist.flatten().astype(np.float32)
    except Exception:
        return None


def histogram_similarity(h1: Optional[np.ndarray],
                         h2: Optional[np.ndarray]) -> float:
    """
    Correlation between two normalised histograms.
    Range: [-1, 1] where 1.0 = identical, 0.0 = no correlation.
    Returns 0.0 if either histogram is None (geometric-only fallback).
    """
    if not _CV2_AVAILABLE or h1 is None or h2 is None:
        return 0.0
    try:
        return float(cv2.compareHist(h1.reshape(-1, 1), h2.reshape(-1, 1),
                                     cv2.HISTCMP_CORREL))
    except Exception:
        return 0.0


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class Track:
    track_id: int
    visitor_id: str
    cx: float
    cy: float
    initial_cx: float       # centroid at FIRST detection (door position)
    initial_cy: float
    area: float
    last_seen_frame: int
    first_seen_frame: int = 0
    session_seq: int = 0
    is_staff: bool = False
    zone_id: Optional[str] = None
    centroid_history: deque = field(default_factory=lambda: deque(maxlen=20))
    direction: Optional[str] = None
    # Staff detection signals
    direction_reversals: int = 0       # y-velocity sign changes (staff move back/forth)
    last_vy_sign: int = 0              # sign of last y-velocity (+1, -1, 0)
    # Stored appearance histogram from first detection (at door = cleanest crop)
    color_hist: Optional[np.ndarray] = None


@dataclass
class ExitedVisitor:
    visitor_id: str
    cx: float           # initial (door-position) centroid
    cy: float
    area: float
    exit_frame: int
    color_hist: Optional[np.ndarray] = None   # appearance at time of exit


# ─── Tracker ────────────────────────────────────────────────────────────────

class Tracker:
    """
    IoU + centroid tracker with trajectory direction detection and
    appearance-augmented Re-ID (HSV color histogram).

    Re-ID pipeline:
      1. Geometric gate (fast O(n) check — distance + area ratio + time window)
      2. Appearance gate (HSV histogram correlation ≥ 0.35)
      3. Combined score = histogram_correlation × (1 - dist/REENTRY_DIST_PX)
         → picks best geometric+appearance match among candidates
    """

    REENTRY_DIST_PX       = 200     # doorway width in pixels at typical 1080p
    AREA_RATIO_MIN        = 0.4
    AREA_RATIO_MAX        = 2.5
    MAX_FRAMES_LOST       = 90      # 6s at 15fps before track is considered gone
    REENTRY_WINDOW_FRAMES = 4500    # 5 min at 15fps
    DIRECTION_BUFFER_FRAMES = 8     # frames buffered before direction confirmed
    IOU_MATCH_THRESHOLD   = 0.25    # lowered to handle partial occlusion
    MIN_DIRECTION_VELOCITY = 3.0    # px/frame noise floor for direction

    def __init__(self):
        self._tracks: Dict[int, Track] = {}
        self._exited: List[ExitedVisitor] = []
        self._next_id = 1
        self._visitor_counter = 0

    def _new_visitor_id(self) -> str:
        self._visitor_counter += 1
        prefix = getattr(self, "_camera_prefix", "VIS")
        return f"{prefix}_{self._visitor_counter:05d}"

    def _centroid(self, bbox) -> Tuple[float, float, float]:
        x, y, w, h = bbox
        return x + w / 2, y + h / 2, w * h

    def _dist(self, cx1, cy1, cx2, cy2) -> float:
        return math.sqrt((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2)

    def _net_y_velocity(self, history: deque) -> float:
        """
        Linear regression slope of y vs frame index.
        Positive = moving downward (into store = ENTRY).
        Negative = moving upward (toward door = EXIT).
        """
        pts = list(history)
        n = len(pts)
        if n < 3:
            return 0.0
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(p[1] for p in pts) / n
        num = sum((xs[i] - x_mean) * (pts[i][1] - y_mean) for i in range(n))
        den = sum((xs[i] - x_mean) ** 2 for i in range(n))
        return num / den if den > 0 else 0.0

    def _confirm_direction(self, track: Track) -> Optional[str]:
        """Determine ENTRY or EXIT from centroid trajectory."""
        if len(track.centroid_history) < self.DIRECTION_BUFFER_FRAMES:
            return None
        vel = self._net_y_velocity(track.centroid_history)
        if abs(vel) < self.MIN_DIRECTION_VELOCITY:
            # Ambiguous horizontal movement — use initial y position as fallback
            pts = list(track.centroid_history)
            avg_y = sum(p[1] for p in pts) / len(pts)
            return 'entry' if avg_y > 0 else 'exit'
        return 'entry' if vel > 0 else 'exit'

    def _match_exited(self, cx: float, cy: float, area: float,
                      color_hist: Optional[np.ndarray],
                      current_frame: int) -> Optional[str]:
        """
        Two-stage Re-ID:
          Stage 1: geometric gate (distance + area ratio + time window)
          Stage 2: appearance gate (histogram correlation ≥ threshold)
          Score:   combined geometric+appearance → pick best candidate
        """
        candidates = []

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

            # Stage 1 passed — check appearance
            hist_sim = histogram_similarity(color_hist, ev.color_hist)

            # If we have valid histograms, require minimum similarity
            if (color_hist is not None and ev.color_hist is not None
                    and hist_sim < HIST_SIMILARITY_THRESHOLD):
                continue  # appearance mismatch — different person

            # Combined score (appearance × proximity bonus)
            prox_score = 1.0 - (dist / self.REENTRY_DIST_PX)
            # When no histograms available, fall back to proximity-only
            score = (hist_sim * prox_score) if (
                color_hist is not None and ev.color_hist is not None
            ) else prox_score

            candidates.append((score, ev.visitor_id))

        if not candidates:
            return None

        # Return the candidate with the highest combined score
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

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
        detections: list of {bbox, confidence, is_staff, group_id, group_size,
                              color_hist (optional)}
        Returns list of track results.
        """
        results = []
        matched_track_ids = set()

        for det in detections:
            bbox       = det["bbox"]
            cx, cy, area = self._centroid(bbox)
            conf       = det.get("confidence", 0.85)
            is_staff   = det.get("is_staff", False)
            group_id   = det.get("group_id")
            group_size = det.get("group_size")
            color_hist = det.get("color_hist")     # may be None if no frame available

            # Match to existing track via IoU
            best_iou = 0.0
            best_tid = None
            for tid, track in self._tracks.items():
                side = math.sqrt(max(track.area, 1))
                approx_bbox = (track.cx - side / 2, track.cy - side / 2, side, side)
                iou = self._iou(bbox, approx_bbox)
                if iou > best_iou:
                    best_iou = iou
                    best_tid = tid

            is_new = False
            is_reentry = False

            if best_tid is not None and best_iou > self.IOU_MATCH_THRESHOLD:
                track = self._tracks[best_tid]
                # Track y-velocity direction changes for staff detection.
                # Staff move back and forth (many reversals); customers move linearly.
                if len(track.centroid_history) >= 2:
                    prev_y = track.centroid_history[-1][1]
                    vy = cy - prev_y
                    vy_sign = 1 if vy > 1 else (-1 if vy < -1 else 0)
                    if vy_sign != 0 and track.last_vy_sign != 0 and vy_sign != track.last_vy_sign:
                        track.direction_reversals += 1
                    if vy_sign != 0:
                        track.last_vy_sign = vy_sign
                track.cx = cx
                track.cy = cy
                track.area = area
                track.last_seen_frame = frame_num
                track.session_seq += 1
                track.centroid_history.append((cx, cy))
                vid = track.visitor_id
                tid = best_tid
            else:
                # Attempt Re-ID match (geometric + appearance)
                reentry_vid = self._match_exited(cx, cy, area, color_hist, frame_num)
                if reentry_vid:
                    vid = reentry_vid
                    is_reentry = True
                    self._exited = [e for e in self._exited
                                    if e.visitor_id != reentry_vid]
                else:
                    vid = self._new_visitor_id()
                    is_new = True

                tid = self._next_id
                self._next_id += 1
                h = deque(maxlen=20)
                h.append((cx, cy))
                track = Track(
                    track_id=tid,
                    visitor_id=vid,
                    cx=cx, cy=cy,
                    initial_cx=cx, initial_cy=cy,
                    area=area,
                    last_seen_frame=frame_num,
                    first_seen_frame=frame_num,
                    session_seq=1,
                    is_staff=is_staff,
                    centroid_history=h,
                    color_hist=color_hist,   # store appearance at door position
                )
                self._tracks[tid] = track

            matched_track_ids.add(tid)
            results.append({
                "track_id":          tid,
                "visitor_id":        vid,
                "is_new":            is_new,
                "is_reentry":        is_reentry,
                "is_staff":          is_staff,
                "cx": cx, "cy": cy,
                "area": area,
                "confidence":        conf,
                "session_seq":       track.session_seq,
                "group_id":          group_id,
                "group_size":        group_size,
                "centroid_history":  track.centroid_history,
                "direction_reversals": track.direction_reversals,
                "frames_in_scene":   frame_num - track.first_seen_frame,
            })

        # Mark lost tracks
        lost = [tid for tid, t in self._tracks.items()
                if frame_num - t.last_seen_frame > self.MAX_FRAMES_LOST
                and tid not in matched_track_ids]
        for tid in lost:
            track = self._tracks.pop(tid)
            self._exited.append(ExitedVisitor(
                visitor_id=track.visitor_id,
                cx=track.initial_cx, cy=track.initial_cy,
                area=track.area, exit_frame=frame_num,
                color_hist=track.color_hist,   # save appearance for re-entry matching
            ))

        # Prune stale exited visitors
        self._exited = [e for e in self._exited
                        if frame_num - e.exit_frame <= self.REENTRY_WINDOW_FRAMES]
        return results

    def get_direction(self, track_id: int) -> Optional[str]:
        track = self._tracks.get(track_id)
        return self._confirm_direction(track) if track else None

    def get_active_tracks(self) -> List[Track]:
        return list(self._tracks.values())

    def flush_exits(self, frame_num: int) -> List[str]:
        exits = []
        lost = [tid for tid, t in self._tracks.items()
                if frame_num - t.last_seen_frame > self.MAX_FRAMES_LOST]
        for tid in lost:
            track = self._tracks.pop(tid)
            exits.append(track.visitor_id)
            self._exited.append(ExitedVisitor(
                visitor_id=track.visitor_id,
                cx=track.initial_cx, cy=track.initial_cy,
                area=track.area, exit_frame=frame_num,
                color_hist=track.color_hist,
            ))
        return exits
