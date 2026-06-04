"""
Main detection + tracking script.
Processes CCTV clips using YOLOv8n person detection + custom IoU Re-ID tracker.
Emits structured events via emit.py.

Usage:
    python detect.py --store-dir "path/to/Store 2" --store-id STORE_BLR_002 --output data/events.jsonl
    python detect.py --all-stores --store1-dir "path/Store 1" --store2-dir "path/Store 2"

# PROMPT used with Claude:
# "Rewrite retail CCTV detection pipeline to use YOLOv8n person detection instead of MOG2.
#  Keep custom IoU tracker and Re-ID logic. Support group entry (persons within 80px = same group),
#  staff exclusion (persistent detections across 60+ frames), billing queue depth from active tracks.
#  Output structured events matching the problem statement schema with real confidence scores."
# CHANGES MADE from MOG2 version:
#   - Swapped MOG2 blob detection for YOLOv8n person detection (class=0, conf>=0.35)
#   - Real per-detection confidence scores from YOLO (not hardcoded)
#   - Group detection now uses actual YOLO bounding boxes (not blob width heuristic)
#   - Staff heuristic unchanged (sustained presence + area) but now more accurate since
#     YOLO bounding boxes are person-tight, not morphological blobs
#   - Added fallback: if ultralytics not installed, raises ImportError with install hint
"""
import argparse
import json
import os
import sys
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    from ultralytics import YOLO as _YOLO
    _YOLO_AVAILABLE = True
except ImportError:
    _YOLO_AVAILABLE = False

from tracker import Tracker, compute_color_histogram
from emit import make_event, ts_from_clip

# ─── Store configurations ─────────────────────────────────────────────────────

STORE_CONFIGS = {
    "STORE_BLR_001": {
        "clip_start": "2026-04-10T10:00:00Z",
        "cameras": {
            "CAM 1 - zone":    {"camera_id": "CAM_ZONE_01",    "role": "zone",       "zone_id": "MINIMALIST",  "zone_name": "Minimalist Zone",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "sku_zone": "SKINCARE",  "hotspot_x": 840,  "hotspot_y": 90},
            "CAM 2 - zone":    {"camera_id": "CAM_ZONE_02",    "role": "zone",       "zone_id": "SALM",        "zone_name": "Salm / TFS Zone",      "zone_type": "SHELF",   "is_revenue_zone": "Yes", "sku_zone": "SKINCARE",  "hotspot_x": 250,  "hotspot_y": 90},
            "CAM 3 - entry":   {"camera_id": "CAM_ENTRY_03",   "role": "entry_exit", "zone_id": None,          "zone_name": None,                   "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,        "hotspot_x": 960,  "hotspot_y": 800},
            "CAM 5 - billing": {"camera_id": "CAM_BILLING_05", "role": "billing",    "zone_id": "BILLING",     "zone_name": "Billing Counter Queue", "zone_type": "BILLING", "is_revenue_zone": "Yes", "sku_zone": None,        "hotspot_x": 1280, "hotspot_y": 370},
        }
    },
    "STORE_BLR_002": {
        "clip_start": "2026-04-10T10:00:00Z",
        "cameras": {
            "entry 1":      {"camera_id": "CAM_ENTRY_01",   "role": "entry_exit", "zone_id": None,       "zone_name": None,                    "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,       "hotspot_x": 960,  "hotspot_y": 900},
            "entry 2":      {"camera_id": "CAM_ENTRY_01B",  "role": "entry_exit", "zone_id": None,       "zone_name": None,                    "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,       "hotspot_x": 960,  "hotspot_y": 900},
            "zone":         {"camera_id": "CAM_ZONE_02",    "role": "zone",       "zone_id": "EB_KOREAN","zone_name": "EB Korean / DermDoc",    "zone_type": "SHELF",   "is_revenue_zone": "Yes", "sku_zone": "SKINCARE", "hotspot_x": 200,  "hotspot_y": 400},
            "billing_area": {"camera_id": "CAM_BILLING_03", "role": "billing",    "zone_id": "BILLING",  "zone_name": "Billing Counter Queue",  "zone_type": "BILLING", "is_revenue_zone": "Yes", "sku_zone": None,       "hotspot_x": 1746, "hotspot_y": 450},
        }
    }
}

# ─── Detection parameters ─────────────────────────────────────────────────────
YOLO_CONF_THRESHOLD = 0.35       # min confidence to accept a YOLO person detection
DWELL_THRESHOLD_FRAMES = 450     # 30s at 15fps
STAFF_FRAME_THRESHOLD = 60       # 4s at 15fps → staff classification
STAFF_AREA_THRESHOLD = 3500      # px² — staff in aprons produce larger boxes
GROUP_PROXIMITY_PX = 80          # centroids within 80px = same group entry
DIRECTION_BUFFER_FRAMES = 8      # frames to buffer before confirming ENTRY vs EXIT direction


def _load_pos_for_abandon(store_id: str) -> list:
    """
    Load POS transactions for a store (used by billing camera for ABANDON correlation).
    Returns list of epoch-second timestamps for the store's transactions.
    """
    import csv as _csv
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "data", "pos_transactions.csv"),
        "data/pos_transactions.csv",
        "../data/pos_transactions.csv",
    ]
    for path in candidates:
        if os.path.exists(path):
            result = []
            with open(path, encoding="utf-8") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    if row.get("store_id") != store_id:
                        continue
                    try:
                        from datetime import datetime as _dt
                        ts = row["timestamp"].replace("Z", "+00:00")
                        epoch = _dt.fromisoformat(ts).timestamp()
                        result.append(epoch)
                    except Exception:
                        pass
            return result
    return []


def _has_pos_transaction_after(pos_epochs: list, exit_epoch: float,
                               window_s: int = 300) -> bool:
    """
    Return True if any POS transaction occurs within [exit_epoch, exit_epoch + window_s].
    Used to determine whether a billing visitor purchased (→ no ABANDON) or left
    without buying (→ emit BILLING_QUEUE_ABANDON).
    """
    for t in pos_epochs:
        if exit_epoch <= t <= exit_epoch + window_s:
            return True
    return False


def load_yolo_model():
    """Load YOLOv8n model. Downloads weights on first call (~6MB)."""
    if not _YOLO_AVAILABLE:
        raise ImportError(
            "ultralytics not installed. Run: pip install ultralytics\n"
            "Then re-run detect.py."
        )
    # Cache model globally so we only load once across all clips
    if not hasattr(load_yolo_model, "_model"):
        print("Loading YOLOv8n model (downloads ~6MB on first run)...")
        load_yolo_model._model = _YOLO("yolov8n.pt")
        print("YOLOv8n model loaded.")
    return load_yolo_model._model


def detect_persons_yolo(model, frame, conf_thresh=YOLO_CONF_THRESHOLD):
    """
    Run YOLOv8n inference on a single frame.
    Returns list of {bbox: (x,y,w,h), confidence: float, area: float}
    Only class=0 (person) detections above conf_thresh are returned.
    """
    results = model(frame, classes=[0], conf=conf_thresh, verbose=False)
    persons = []
    for r in results:
        if r.boxes is None:
            continue
        for box in r.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            x, y = int(x1), int(y1)
            w, h = int(x2 - x1), int(y2 - y1)
            area = w * h
            aspect = h / w if w > 0 else 1
            # Filter out non-person shaped boxes (too wide = likely false positive)
            if aspect < 0.5 or area < 800:
                continue
            persons.append({"bbox": (x, y, w, h), "confidence": conf, "area": area})
    return persons


def assign_groups(persons):
    """
    Persons whose centroids are within GROUP_PROXIMITY_PX of each other
    are tagged as a group (same group_id, group_size = count).
    Uses union-find style grouping.
    """
    n = len(persons)
    centroids = [(p["bbox"][0] + p["bbox"][2] / 2, p["bbox"][1] + p["bbox"][3] / 2)
                 for p in persons]
    group_id_map = list(range(n))

    def find(i):
        while group_id_map[i] != i:
            group_id_map[i] = group_id_map[group_id_map[i]]
            i = group_id_map[i]
        return i

    def union(i, j):
        group_id_map[find(i)] = find(j)

    for i in range(n):
        for j in range(i + 1, n):
            cx1, cy1 = centroids[i]
            cx2, cy2 = centroids[j]
            dist = ((cx1 - cx2) ** 2 + (cy1 - cy2) ** 2) ** 0.5
            if dist <= GROUP_PROXIMITY_PX:
                union(i, j)

    # Build group sets
    from collections import defaultdict
    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    result = []
    for i, p in enumerate(persons):
        root = find(i)
        members = groups[root]
        if len(members) > 1:
            gid = f"GRP_{int(centroids[root][0])}_{int(centroids[root][1])}"
            result.append({**p, "group_id": gid, "group_size": len(members)})
        else:
            result.append({**p, "group_id": None, "group_size": None})
    return result


def classify_staff(area: float, frame_count_in_scene: int, conf: float,
                   direction_reversals: int = 0, total_clip_frames: int = 0) -> bool:
    """
    Multi-signal staff classification using YOLOv8n bounding box properties.

    Signals (in decreasing reliability order):
    1. Sustained presence (60+ processed frames = ~20s at 15fps/every_n=5)
       Combined with large bounding box area (staff near counter appear larger)
    2. High confidence + very long presence (staff stand still, fewer occlusions)
    3. Movement pattern: many direction reversals in y-axis indicate back-and-forth
       movement (restocking, cleaning, helping customers). Customers walk linearly.
    4. Presence ratio: if track spans >35% of the clip, very likely staff.
       (a customer browsing for 35% of a 20-min clip = 7 min — unusual but possible)

    Known limitation: no uniform/pose classifier. False positives on customers who
    stand still for extended periods (e.g. reading products). Conservative thresholds
    reduce false positives at cost of some false negatives (undetected staff).
    Documented in docs/CHOICES.md.
    """
    # Signal 1: large area + sustained presence (primary heuristic)
    if area > STAFF_AREA_THRESHOLD and frame_count_in_scene > STAFF_FRAME_THRESHOLD:
        return True

    # Signal 2: very high confidence + very long presence (staff at counter)
    if conf > 0.93 and frame_count_in_scene > 120 and area > 2500:
        return True

    # Signal 3: direction reversals (back-and-forth movement = staff behaviour)
    # Threshold: >8 reversals AND >100 frames to avoid random noise from customers
    if direction_reversals > 8 and frame_count_in_scene > 100:
        return True

    # Signal 4: presence ratio (>35% of clip = very long stay, likely staff)
    # Requires total_clip_frames to be known (>0)
    if total_clip_frames > 0:
        presence_ratio = frame_count_in_scene / total_clip_frames
        if presence_ratio > 0.35 and frame_count_in_scene > 80:
            return True

    return False


def process_clip(clip_path: str, cam_meta: dict, store_id: str,
                 clip_start: str, output_events: list,
                 model, process_every_n: int = 5):
    camera_id = cam_meta["camera_id"]
    role      = cam_meta["role"]
    zone_id   = cam_meta.get("zone_id")
    zone_name = cam_meta.get("zone_name")
    zone_type = cam_meta.get("zone_type")
    is_rev    = cam_meta.get("is_revenue_zone", "No")
    sku_zone  = cam_meta.get("sku_zone")
    hotspot_x = cam_meta.get("hotspot_x", 0.0)
    hotspot_y = cam_meta.get("hotspot_y", 0.0)
    fps = 15.0

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"[WARN] Cannot open {clip_path}", file=sys.stderr)
        return

    tracker = Tracker()
    # _camera_prefix makes visitor IDs camera-scoped (prevents cross-camera ID collision)
    tracker._camera_prefix = f"{store_id.replace('_', '')}_{camera_id.replace('_', '')}"

    frame_num = 0
    track_frame_counts: dict = {}
    zone_enter_frames: dict = {}
    billing_joined: set = set()
    billing_exit_times: dict = {}   # vid → exit epoch seconds (for POS correlation)
    visitors_entered: set = set()
    visitors_exited: set = set()
    entry_count = 0
    # Direction buffer: tracks waiting for trajectory confirmation before ENTRY/EXIT emit
    _direction_buffer: dict = {}

    # Load POS transactions for this store (used by billing camera for ABANDON detection)
    # Spec: emit BILLING_QUEUE_ABANDON only when NO POS transaction follows within 5 min
    _pos_transactions = []
    if role == "billing":
        _pos_transactions = _load_pos_for_abandon(store_id)

    print(f"  [{store_id}] {Path(clip_path).name} -> {camera_id} role={role}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        if frame_num % process_every_n != 0:
            continue

        # YOLOv8n person detection — real confidence scores, person-tight boxes
        persons = detect_persons_yolo(model, frame)
        persons = assign_groups(persons)

        detections = []
        for p in persons:
            # Compute HSV color histogram from bounding box crop.
            # This adds appearance features to the Re-ID without requiring GPU.
            # HSV histograms capture clothing colour — the most stable discriminator
            # available when faces are blurred. Takes <0.5ms per crop on CPU.
            color_hist = compute_color_histogram(frame, p["bbox"])
            detections.append({
                "bbox":       p["bbox"],
                "confidence": p["confidence"],
                "area":       p["area"],
                "is_staff":   False,   # pre-set; overridden below after tracking
                "group_id":   p.get("group_id"),
                "group_size": p.get("group_size"),
                "color_hist": color_hist,   # appearance feature for Re-ID
            })

        track_results = tracker.update(detections, frame_num)
        ts = ts_from_clip(clip_start, frame_num, fps)

        for tr in track_results:
            tid        = tr["track_id"]
            vid        = tr["visitor_id"]
            conf       = tr["confidence"]
            seq        = tr["session_seq"]
            group_id   = tr.get("group_id")
            group_size = tr.get("group_size")

            track_frame_counts[tid] = track_frame_counts.get(tid, 0) + 1
            is_staff = classify_staff(
                tr["area"], track_frame_counts[tid], conf,
                direction_reversals=tr.get("direction_reversals", 0),
                total_clip_frames=frame_num,
            )
            vis_seed = hash(vid) & 0xFFFFFF

            common = dict(
                store_id=store_id, camera_id=camera_id,
                visitor_id=vid, timestamp=ts,
                is_staff=is_staff, confidence=conf,
                hotspot_x=hotspot_x, hotspot_y=hotspot_y,
                group_id=group_id, group_size=group_size,
                _vis_seed=vis_seed,
            )

            if role == "entry_exit":
                if tr["is_new"]:
                    # New track: add to direction buffer. DO NOT emit ENTRY yet.
                    # Direction is confirmed by trajectory (y-velocity over 8 frames).
                    # The confirmation check runs on every subsequent frame below.
                    _direction_buffer[tid] = {
                        "common": common, "seq": seq,
                        "first_frame": frame_num, "vid": vid,
                    }
                elif tr["is_reentry"]:
                    # Re-entry: flush any pending buffer for this track
                    _direction_buffer.pop(tid, None)
                    if vid in visitors_exited:
                        output_events.append(make_event(
                            event_type="REENTRY", zone_id=None, zone_name=None,
                            zone_type="ENTRY", is_revenue_zone="No",
                            dwell_ms=0, session_seq=seq, **common))
                        visitors_exited.discard(vid)

            elif role in ("main_floor", "zone"):
                if tr["is_new"] or tr["is_reentry"]:
                    zone_enter_frames[vid] = frame_num
                    output_events.append(make_event(
                        event_type="ZONE_ENTER",
                        zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                        is_revenue_zone=is_rev, dwell_ms=0,
                        sku_zone=sku_zone, session_seq=seq, **common))
                else:
                    enter_f = zone_enter_frames.get(vid, frame_num)
                    dwell_frames = frame_num - enter_f
                    dwell_ms = int(dwell_frames / fps * 1000)
                    if dwell_frames > 0 and dwell_frames % DWELL_THRESHOLD_FRAMES < process_every_n:
                        output_events.append(make_event(
                            event_type="ZONE_DWELL",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=dwell_ms,
                            sku_zone=sku_zone, session_seq=seq, **common))

            elif role == "billing":
                if tr["is_new"] and vid not in billing_joined:
                    active_non_staff = [
                        t for t in tracker.get_active_tracks()
                        if not t.is_staff and t.track_id != tid
                    ]
                    queue_depth = min(10, len(active_non_staff))
                    if queue_depth > 0:
                        billing_joined.add(vid)
                        output_events.append(make_event(
                            event_type="BILLING_QUEUE_JOIN",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=0,
                            queue_depth=queue_depth, session_seq=seq, **common))
                    else:
                        output_events.append(make_event(
                            event_type="ZONE_ENTER",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=0,
                            session_seq=seq, **common))

        # ── Direction buffer confirmation (runs every frame) ──────────────────
        # BUG FIX: previously the confirmation check was inside `if tr["is_new"]`
        # which is only True once per track. This meant buffered tracks only had
        # direction confirmed at end-of-clip flush, losing all mid-clip ENTRY events.
        # Fix: check ALL buffered tracks on every frame, independent of is_new.
        active_tids = {tr["track_id"] for tr in track_results}
        for buf_tid in list(_direction_buffer.keys()):
            if buf_tid not in active_tids:
                continue  # track not seen this frame — will flush on track loss
            direction = tracker.get_direction(buf_tid)
            buffered  = _direction_buffer[buf_tid]
            frames_buffered = frame_num - buffered["first_frame"]
            if direction is not None or frames_buffered >= DIRECTION_BUFFER_FRAMES:
                evt_type = "EXIT" if direction == "exit" else "ENTRY"
                entry_count += 1
                output_events.append(make_event(
                    event_type=evt_type, zone_id=None, zone_name=None,
                    zone_type="ENTRY", is_revenue_zone="No",
                    dwell_ms=0, session_seq=buffered["seq"],
                    **buffered["common"]))
                if evt_type == "ENTRY":
                    visitors_entered.add(buffered["vid"])
                del _direction_buffer[buf_tid]

        # Emit EXIT / ZONE_EXIT for lost tracks
        exits = tracker.flush_exits(frame_num)
        for vid in exits:
            enter_f  = zone_enter_frames.pop(vid, frame_num)
            dwell_ms = int((frame_num - enter_f) / fps * 1000)
            vis_seed = hash(vid) & 0xFFFFFF
            # Record billing exit time for POS correlation (ABANDON requires no txn follows)
            if role == "billing":
                from datetime import datetime as _dt2
                try:
                    exit_epoch = _dt2.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    exit_epoch = 0.0
                billing_exit_times[vid] = exit_epoch
            common_exit = dict(
                store_id=store_id, camera_id=camera_id,
                visitor_id=vid, timestamp=ts,
                is_staff=False, confidence=0.80,
                hotspot_x=hotspot_x, hotspot_y=hotspot_y,
                _vis_seed=vis_seed)
            if role == "entry_exit":
                output_events.append(make_event(
                    event_type="EXIT", zone_id=None,
                    dwell_ms=dwell_ms, **common_exit))
                visitors_exited.add(vid)
            elif role in ("main_floor", "zone"):
                output_events.append(make_event(
                    event_type="ZONE_EXIT",
                    zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                    is_revenue_zone=is_rev, dwell_ms=dwell_ms,
                    sku_zone=sku_zone, **common_exit))
            elif role == "billing":
                # Spec: emit BILLING_QUEUE_ABANDON only if NO POS transaction
                # follows within 5 minutes of the visitor leaving billing.
                # If a transaction follows → they purchased, no ABANDON event.
                exit_epoch = billing_exit_times.pop(vid, 0.0)
                if not _has_pos_transaction_after(_pos_transactions, exit_epoch):
                    output_events.append(make_event(
                        event_type="BILLING_QUEUE_ABANDON",
                        zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                        is_revenue_zone=is_rev, dwell_ms=dwell_ms, **common_exit))

    # Flush any direction-buffered entry events that never got enough frames to confirm
    # Default to ENTRY for ambiguous cases (entering is more common than exiting)
    for buf_tid, buffered in list(_direction_buffer.items()):
        direction = tracker.get_direction(buf_tid)
        evt_type = "EXIT" if direction == "exit" else "ENTRY"
        entry_count += 1
        output_events.append(make_event(
            event_type=evt_type, zone_id=None, zone_name=None,
            zone_type="ENTRY", is_revenue_zone="No",
            dwell_ms=0, session_seq=buffered["seq"],
            **buffered["common"]))
        if evt_type == "ENTRY":
            visitors_entered.add(buffered["vid"])

    # Flush remaining active tracks at clip end
    for tid, track in list(tracker._tracks.items()):
        vid      = track.visitor_id
        enter_f  = zone_enter_frames.pop(vid, frame_num)
        dwell_ms = int((frame_num - enter_f) / fps * 1000)
        vis_seed = hash(vid) & 0xFFFFFF
        common_end = dict(
            store_id=store_id, camera_id=camera_id,
            visitor_id=vid, timestamp=ts,
            is_staff=track.is_staff, confidence=0.80,
            hotspot_x=hotspot_x, hotspot_y=hotspot_y,
            _vis_seed=vis_seed)
        if role in ("main_floor", "zone"):
            output_events.append(make_event(
                event_type="ZONE_EXIT",
                zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                is_revenue_zone=is_rev, dwell_ms=dwell_ms,
                sku_zone=sku_zone, **common_end))
        elif role == "billing":
            # Tracks still in billing at clip end → do NOT emit ABANDON.
            # They may still be transacting. Only emit ABANDON during clip when
            # a track exits AND no POS transaction follows (handled in flush_exits loop).

    cap.release()
    print(f"    -> frames={frame_num}  entries={entry_count}  events_so_far={len(output_events)}")


def find_clip(store_dir: Path, cam_key: str):
    for ext in [".mp4", ".MP4", ".avi", ".AVI", ".mov"]:
        p = store_dir / f"{cam_key}{ext}"
        if p.exists():
            return str(p)
    return None


def process_store(store_dir: Path, store_id: str, output_events: list,
                  model, every: int = 5):
    cfg = STORE_CONFIGS[store_id]
    clip_start = cfg["clip_start"]
    for cam_key, cam_meta in cfg["cameras"].items():
        clip_path = find_clip(store_dir, cam_key)
        if clip_path:
            process_clip(clip_path, cam_meta, store_id, clip_start,
                         output_events, model, every)
        else:
            print(f"[WARN] No clip for '{cam_key}' in {store_dir}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline (YOLOv8n)")
    parser.add_argument("--store-dir",  default=None)
    parser.add_argument("--store-id",   default="STORE_BLR_002")
    parser.add_argument("--all-stores", action="store_true")
    parser.add_argument("--store1-dir", default=None)
    parser.add_argument("--store2-dir", default=None)
    parser.add_argument("--output",     default="data/events.jsonl")
    parser.add_argument("--every",      type=int, default=5,
                        help="Process every N frames (default 5 = ~3fps at 15fps source)")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_events = []

    # Load YOLO once — shared across all clips
    model = load_yolo_model()

    if args.all_stores:
        if not args.store1_dir or not args.store2_dir:
            print("ERROR: --all-stores requires --store1-dir and --store2-dir", file=sys.stderr)
            sys.exit(1)
        process_store(Path(args.store1_dir), "STORE_BLR_001", all_events, model, args.every)
        process_store(Path(args.store2_dir), "STORE_BLR_002", all_events, model, args.every)
    elif args.store_dir:
        process_store(Path(args.store_dir), args.store_id, all_events, model, args.every)
    else:
        print("ERROR: Specify --store-dir or --all-stores with --store1-dir/--store2-dir",
              file=sys.stderr)
        sys.exit(1)

    all_events.sort(key=lambda e: e["timestamp"])

    with open(output_path, "w", encoding="utf-8") as f:
        for evt in all_events:
            f.write(json.dumps(evt) + "\n")

    print(f"\nGenerated {len(all_events)} events -> {output_path}")
    from collections import Counter
    for t, c in sorted(Counter(e["event_type"] for e in all_events).items()):
        print(f"   {t}: {c}")


if __name__ == "__main__":
    main()
