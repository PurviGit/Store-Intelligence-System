"""
Main detection + tracking script.
Processes CCTV clips using OpenCV MOG2 background subtraction.
Emits structured events via emit.py.

Handles Store 1 (STORE_BLR_001) and Store 2 (STORE_BLR_002).

Usage:
    python detect.py --store-dir "path/to/Store 1" --store-id STORE_BLR_001 --output data/events_store1.jsonl
    python detect.py --store-dir "path/to/Store 2" --store-id STORE_BLR_002 --output data/events_store2.jsonl
    python detect.py --all-stores --output data/events.jsonl   # process all known stores

# PROMPT used with Claude:
# "Design a detection pipeline for retail CCTV that handles entry/exit counting,
#  zone tracking, billing queue detection, staff exclusion, re-entry, and group entry.
#  Use OpenCV MOG2 for CPU-only operation. Output structured events matching the schema."
# CHANGES MADE: Added two-store support, hotspot coordinates, gender/age heuristics,
#  improved group blob splitting, billing queue depth tracking from active tracks.
"""
import argparse
import json
import os
import sys
import cv2
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from tracker import Tracker
from emit import make_event, ts_from_clip

# ─── Store configurations ─────────────────────────────────────────────────────

STORE_CONFIGS = {
    "STORE_BLR_001": {
        "clip_start": "2026-04-10T10:00:00Z",
        "cameras": {
            # Store 1 video filenames
            "CAM 1 - zone":    {"camera_id": "CAM_ZONE_01",    "role": "zone",      "zone_id": "MINIMALIST",  "zone_name": "Minimalist Zone",     "zone_type": "SHELF",   "is_revenue_zone": "Yes", "sku_zone": "SKINCARE",  "hotspot_x": 840,  "hotspot_y": 90},
            "CAM 2 - zone":    {"camera_id": "CAM_ZONE_02",    "role": "zone",      "zone_id": "SALM",        "zone_name": "Salm / TFS Zone",     "zone_type": "SHELF",   "is_revenue_zone": "Yes", "sku_zone": "SKINCARE",  "hotspot_x": 250,  "hotspot_y": 90},
            "CAM 3 - entry":   {"camera_id": "CAM_ENTRY_03",   "role": "entry_exit","zone_id": None,          "zone_name": None,                  "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,        "hotspot_x": 960,  "hotspot_y": 800},
            "CAM 5 - billing": {"camera_id": "CAM_BILLING_05", "role": "billing",   "zone_id": "BILLING",     "zone_name": "Billing Counter Queue","zone_type": "BILLING", "is_revenue_zone": "Yes", "sku_zone": None,        "hotspot_x": 1280, "hotspot_y": 370},
        }
    },
    "STORE_BLR_002": {
        "clip_start": "2026-04-10T10:00:00Z",
        "cameras": {
            # Store 2 video filenames
            "entry 1":       {"camera_id": "CAM_ENTRY_01",   "role": "entry_exit","zone_id": None,      "zone_name": None,                    "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,       "hotspot_x": 960,  "hotspot_y": 900},
            "entry 2":       {"camera_id": "CAM_ENTRY_01B",  "role": "entry_exit","zone_id": None,      "zone_name": None,                    "zone_type": "ENTRY",   "is_revenue_zone": "No",  "sku_zone": None,       "hotspot_x": 960,  "hotspot_y": 900},
            "zone":          {"camera_id": "CAM_ZONE_02",    "role": "zone",      "zone_id": "EB_KOREAN","zone_name": "EB Korean / DermDoc Zone","zone_type": "SHELF",  "is_revenue_zone": "Yes", "sku_zone": "SKINCARE", "hotspot_x": 200,  "hotspot_y": 400},
            "billing_area":  {"camera_id": "CAM_BILLING_03", "role": "billing",   "zone_id": "BILLING", "zone_name": "Billing Counter Queue",  "zone_type": "BILLING", "is_revenue_zone": "Yes", "sku_zone": None,       "hotspot_x": 1746, "hotspot_y": 450},
        }
    }
}

DWELL_THRESHOLD_FRAMES = 450   # 30 s at 15 fps
MIN_CONTOUR_AREA = 800
STAFF_AREA_THRESHOLD = 12000
CONFIDENCE_BASE = 0.85


def classify_staff(area: float, frame_count_in_scene: int) -> bool:
    return area > STAFF_AREA_THRESHOLD and frame_count_in_scene > 150


def detect_blobs(frame, bg_subtractor, min_area=MIN_CONTOUR_AREA):
    fg_mask = bg_subtractor.apply(frame)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
    fg_mask = cv2.dilate(fg_mask, kernel, iterations=2)
    contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        aspect = h / w if w > 0 else 1
        if aspect < 0.8:
            continue
        blobs.append({"bbox": (x, y, w, h), "area": area,
                      "confidence": min(0.98, CONFIDENCE_BASE + area / 50000)})
    return blobs


def split_group_blobs(blobs):
    """Split wide blobs likely containing 2–4 people entering together."""
    result = []
    for blob in blobs:
        x, y, w, h = blob["bbox"]
        if w > 240:
            n = max(2, w // 120)
            part_w = w // n
            for i in range(n):
                result.append({
                    "bbox": (x + i * part_w, y, part_w, h),
                    "area": blob["area"] / n,
                    "confidence": blob["confidence"] * 0.88,
                    "group_id": f"GRP_{x}_{y}",
                    "group_size": n,
                })
        else:
            blob.setdefault("group_id", None)
            blob.setdefault("group_size", None)
            result.append(blob)
    return result


def process_clip(clip_path: str, cam_meta: dict, store_id: str,
                 clip_start: str, output_events: list, process_every_n: int = 5):
    camera_id  = cam_meta["camera_id"]
    role       = cam_meta["role"]
    zone_id    = cam_meta.get("zone_id")
    zone_name  = cam_meta.get("zone_name")
    zone_type  = cam_meta.get("zone_type")
    is_rev     = cam_meta.get("is_revenue_zone", "No")
    sku_zone   = cam_meta.get("sku_zone")
    hotspot_x  = cam_meta.get("hotspot_x", 0.0)
    hotspot_y  = cam_meta.get("hotspot_y", 0.0)
    fps        = 15.0

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"[WARN] Cannot open {clip_path}", file=sys.stderr)
        return

    bg_sub  = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    tracker = Tracker()
    tracker._store_prefix = store_id.replace("_", "")

    frame_num = 0
    track_frame_counts: dict = {}
    zone_enter_frames:  dict = {}
    entry_count = 0

    print(f"  [{store_id}] {Path(clip_path).name} -> {camera_id} role={role}")

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_num += 1
        if frame_num % process_every_n != 0:
            continue

        frame_h, frame_w = frame.shape[:2]
        blobs = detect_blobs(frame, bg_sub)
        blobs = split_group_blobs(blobs)

        detections = []
        for blob in blobs:
            x, y, w, h = blob["bbox"]
            detections.append({
                "bbox": (x, y, w, h),
                "confidence": blob["confidence"],
                "is_staff":   False,
                "group_id":   blob.get("group_id"),
                "group_size": blob.get("group_size"),
            })

        track_results = tracker.update(detections, frame_num)
        ts = ts_from_clip(clip_start, frame_num, fps)

        for tr in track_results:
            tid  = tr["track_id"]
            vid  = tr["visitor_id"]
            conf = tr["confidence"]
            seq  = tr["session_seq"]
            group_id   = tr.get("group_id")
            group_size = tr.get("group_size")

            track_frame_counts[tid] = track_frame_counts.get(tid, 0) + 1
            is_staff = classify_staff(tr["area"], track_frame_counts[tid])
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
                    entry_count += 1
                    evt_type = "ENTRY" if tr["cy"] < frame_h * 0.5 else "EXIT"
                    output_events.append(make_event(event_type=evt_type,
                        zone_id=None, zone_name=None, zone_type="ENTRY",
                        is_revenue_zone="No", dwell_ms=0, session_seq=seq, **common))
                elif tr["is_reentry"]:
                    output_events.append(make_event(event_type="REENTRY",
                        zone_id=None, zone_name=None, zone_type="ENTRY",
                        is_revenue_zone="No", dwell_ms=0, session_seq=seq, **common))

            elif role in ("main_floor", "zone"):
                if tr["is_new"] or tr["is_reentry"]:
                    zone_enter_frames[vid] = frame_num
                    output_events.append(make_event(event_type="ZONE_ENTER",
                        zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                        is_revenue_zone=is_rev, dwell_ms=0, sku_zone=sku_zone,
                        session_seq=seq, **common))
                else:
                    enter_f    = zone_enter_frames.get(vid, frame_num)
                    dwell_frames = frame_num - enter_f
                    dwell_ms   = int(dwell_frames / fps * 1000)
                    if dwell_frames > 0 and dwell_frames % DWELL_THRESHOLD_FRAMES < process_every_n:
                        output_events.append(make_event(event_type="ZONE_DWELL",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=dwell_ms, sku_zone=sku_zone,
                            session_seq=seq, **common))

            elif role == "billing":
                if tr["is_new"] or tr["is_reentry"]:
                    active_non_staff = [
                        t for t in tracker.get_active_tracks()
                        if not t.is_staff and t.track_id != tid
                    ]
                    queue_depth = min(10, len(active_non_staff))
                    if queue_depth > 0:
                        output_events.append(make_event(event_type="BILLING_QUEUE_JOIN",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=0,
                            queue_depth=queue_depth, session_seq=seq, **common))
                    else:
                        output_events.append(make_event(event_type="ZONE_ENTER",
                            zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                            is_revenue_zone=is_rev, dwell_ms=0, session_seq=seq, **common))

        # Emit EXIT / ZONE_EXIT for lost tracks
        exits = tracker.flush_exits(frame_num)
        for vid in exits:
            enter_f  = zone_enter_frames.pop(vid, frame_num)
            dwell_ms = int((frame_num - enter_f) / fps * 1000)
            vis_seed = hash(vid) & 0xFFFFFF
            common_exit = dict(store_id=store_id, camera_id=camera_id,
                               visitor_id=vid, timestamp=ts,
                               is_staff=False, confidence=0.8,
                               hotspot_x=hotspot_x, hotspot_y=hotspot_y,
                               _vis_seed=vis_seed)
            if role == "entry_exit":
                output_events.append(make_event(event_type="EXIT",
                    zone_id=None, dwell_ms=dwell_ms, **common_exit))
            elif role in ("main_floor", "zone"):
                output_events.append(make_event(event_type="ZONE_EXIT",
                    zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                    is_revenue_zone=is_rev, dwell_ms=dwell_ms,
                    sku_zone=sku_zone, **common_exit))
            elif role == "billing":
                output_events.append(make_event(event_type="BILLING_QUEUE_ABANDON",
                    zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                    is_revenue_zone=is_rev, dwell_ms=dwell_ms, **common_exit))

    # Flush remaining active tracks
    for tid, track in list(tracker._tracks.items()):
        vid      = track.visitor_id
        enter_f  = zone_enter_frames.pop(vid, frame_num)
        dwell_ms = int((frame_num - enter_f) / fps * 1000)
        vis_seed = hash(vid) & 0xFFFFFF
        common_end = dict(store_id=store_id, camera_id=camera_id,
                          visitor_id=vid, timestamp=ts,
                          is_staff=track.is_staff, confidence=0.8,
                          hotspot_x=hotspot_x, hotspot_y=hotspot_y,
                          _vis_seed=vis_seed)
        if role in ("main_floor", "zone"):
            output_events.append(make_event(event_type="ZONE_EXIT",
                zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                is_revenue_zone=is_rev, dwell_ms=dwell_ms,
                sku_zone=sku_zone, **common_end))
        elif role == "billing" and dwell_ms < 30000:
            output_events.append(make_event(event_type="BILLING_QUEUE_ABANDON",
                zone_id=zone_id, zone_name=zone_name, zone_type=zone_type,
                is_revenue_zone=is_rev, dwell_ms=dwell_ms, **common_end))

    cap.release()
    print(f"    -> frames={frame_num}  entries={entry_count}  events_so_far={len(output_events)}")


def find_clip(store_dir: Path, cam_key: str):
    """Try common extensions for a given camera key filename."""
    for ext in [".mp4", ".MP4", ".avi", ".AVI", ".mov"]:
        p = store_dir / f"{cam_key}{ext}"
        if p.exists():
            return str(p)
    return None


def process_store(store_dir: Path, store_id: str, output_events: list, every: int = 5):
    cfg = STORE_CONFIGS[store_id]
    clip_start = cfg["clip_start"]
    for cam_key, cam_meta in cfg["cameras"].items():
        clip_path = find_clip(store_dir, cam_key)
        if clip_path:
            process_clip(clip_path, cam_meta, store_id, clip_start, output_events, every)
        else:
            print(f"[WARN] No clip for '{cam_key}' in {store_dir}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--store-dir",  default=None, help="Directory with one store's clips")
    parser.add_argument("--store-id",   default="STORE_BLR_002", help="Store ID for the clips")
    parser.add_argument("--all-stores", action="store_true", help="Process all configured stores")
    parser.add_argument("--store1-dir", default="C:/Users/Purvi/Downloads/Store 1-20260602T101818Z-3-001ec38db8/Store 1")
    parser.add_argument("--store2-dir", default="C:/Users/Purvi/Downloads/Store 2-20260602T101819Z-3-001099f208/Store 2")
    parser.add_argument("--output",     default="data/events.jsonl", help="Output JSONL file")
    parser.add_argument("--every",      type=int, default=5, help="Process every N frames")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_events = []

    if args.all_stores:
        process_store(Path(args.store1_dir), "STORE_BLR_001", all_events, args.every)
        process_store(Path(args.store2_dir), "STORE_BLR_002", all_events, args.every)
    elif args.store_dir:
        process_store(Path(args.store_dir), args.store_id, all_events, args.every)
    else:
        # Default: process Store 2 (Brigade Bangalore — acceptance gate store)
        process_store(Path(args.store2_dir), "STORE_BLR_002", all_events, args.every)

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
