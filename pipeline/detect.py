"""
Main detection + tracking script.
Processes CCTV clips using OpenCV MOG2 background subtraction.
Emits structured events via emit.py.

Usage:
    python detect.py --clips-dir /path/to/cctv/ --output events.jsonl
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
from emit import make_event, ts_from_clip, STORE_ID

# Camera mapping: filename prefix → camera metadata
CAMERA_MAP = {
    "CAM 1": {"camera_id": "CAM_ENTRY_01",   "role": "entry_exit"},
    "CAM 2": {"camera_id": "CAM_FLOOR_02",   "role": "main_floor",  "zone": "SKINCARE",  "sku_zone": "SKINCARE"},
    "CAM 3": {"camera_id": "CAM_BILLING_03", "role": "billing",     "zone": "BILLING",   "sku_zone": None},
    "CAM 4": {"camera_id": "CAM_ZONE_04",    "role": "zone",        "zone": "MAKEUP",    "sku_zone": "MAKEUP"},
    "CAM 5": {"camera_id": "CAM_ZONE_05",    "role": "zone",        "zone": "EB_KOREAN", "sku_zone": "SKINCARE"},
}

# Clip start times (Brigade Bangalore, April 10 2026)
CLIP_START_TIMES = {
    "CAM_ENTRY_01":   "2026-04-10T12:00:00Z",
    "CAM_FLOOR_02":   "2026-04-10T12:00:00Z",
    "CAM_BILLING_03": "2026-04-10T12:00:00Z",
    "CAM_ZONE_04":    "2026-04-10T12:00:00Z",
    "CAM_ZONE_05":    "2026-04-10T12:00:00Z",
}

DWELL_THRESHOLD_FRAMES = 450   # 30 seconds at 15fps
MIN_CONTOUR_AREA = 800
STAFF_AREA_THRESHOLD = 12000   # large consistent bounding box → likely staff
CONFIDENCE_BASE = 0.85


def classify_staff(area: float, frame_count_in_scene: int) -> bool:
    """
    Heuristic: staff tend to have larger bounding boxes (uniform) and
    appear consistently across many frames.
    """
    return area > STAFF_AREA_THRESHOLD and frame_count_in_scene > 150


def detect_blobs(frame, bg_subtractor, min_area=MIN_CONTOUR_AREA):
    """Run MOG2 background subtraction and return contour bounding boxes."""
    fg_mask = bg_subtractor.apply(frame)
    # Morphological clean-up
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
        # Aspect ratio filter: people are taller than wide
        aspect = h / w if w > 0 else 1
        if aspect < 0.8:
            continue
        blobs.append({"bbox": (x, y, w, h), "area": area, "confidence": min(0.98, CONFIDENCE_BASE + area / 50000)})
    return blobs


def split_group_blobs(blobs):
    """
    Split large blobs that likely contain multiple people.
    A blob wider than 2× average width is split into sub-persons.
    """
    if not blobs:
        return blobs
    if len(blobs) == 1:
        single = blobs[0]
        x, y, w, h = single["bbox"]
        if w > 200:  # wide blob = likely 2 people
            half = w // 2
            return [
                {"bbox": (x, y, half, h), "area": single["area"] / 2, "confidence": single["confidence"] * 0.9},
                {"bbox": (x + half, y, half, h), "area": single["area"] / 2, "confidence": single["confidence"] * 0.9},
            ]
    return blobs


def is_entry_direction(track_result, frame_height) -> bool:
    """Entry = movement from top of frame (door) downward into store."""
    return track_result["cy"] < frame_height * 0.5


def process_clip(clip_path: str, cam_meta: dict, output_events: list, process_every_n: int = 5):
    """Process a single CCTV clip and append events to output_events."""
    camera_id = cam_meta["camera_id"]
    role = cam_meta["role"]
    clip_start = CLIP_START_TIMES[camera_id]
    fps = 15.0

    cap = cv2.VideoCapture(clip_path)
    if not cap.isOpened():
        print(f"[WARN] Cannot open {clip_path}", file=sys.stderr)
        return

    bg_sub = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=50, detectShadows=False)
    tracker = Tracker()
    tracker._camera_prefix = camera_id.replace("_", "")  # e.g. CAMENTRYOL, CAMFLOORL02

    frame_num = 0
    track_frame_counts: dict = {}   # track_id → frames seen (for staff classification)
    zone_enter_frames: dict = {}    # visitor_id → frame when they entered zone
    queue_depth = 0
    entry_count = 0

    print(f"  Processing {Path(clip_path).name} [{camera_id}] role={role}")

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
            area = blob["area"]
            detections.append({
                "bbox": (x, y, w, h),
                "confidence": blob["confidence"],
                "is_staff": False,  # updated after tracking
            })

        track_results = tracker.update(detections, frame_num)
        ts = ts_from_clip(clip_start, frame_num, fps)

        for tr in track_results:
            tid = tr["track_id"]
            vid = tr["visitor_id"]
            conf = tr["confidence"]
            seq = tr["session_seq"]

            # Update frame count for staff classification
            track_frame_counts[tid] = track_frame_counts.get(tid, 0) + 1
            is_staff = classify_staff(tr["area"], track_frame_counts[tid])
            tr["is_staff"] = is_staff

            if role == "entry_exit":
                if tr["is_new"]:
                    entry_count += 1
                    evt_type = "ENTRY"
                    if not is_entry_direction(tr, frame_h):
                        evt_type = "EXIT"
                    output_events.append(make_event(
                        camera_id=camera_id, visitor_id=vid, event_type=evt_type,
                        timestamp=ts, zone_id=None, dwell_ms=0,
                        is_staff=is_staff, confidence=conf,
                        sku_zone=None, session_seq=seq,
                    ))
                elif tr["is_reentry"]:
                    output_events.append(make_event(
                        camera_id=camera_id, visitor_id=vid, event_type="REENTRY",
                        timestamp=ts, zone_id=None, dwell_ms=0,
                        is_staff=is_staff, confidence=conf,
                        sku_zone=None, session_seq=seq,
                    ))

            elif role in ("main_floor", "zone"):
                zone_id = cam_meta.get("zone", "UNKNOWN")
                sku_zone = cam_meta.get("sku_zone")

                if tr["is_new"] or tr["is_reentry"]:
                    zone_enter_frames[vid] = frame_num
                    output_events.append(make_event(
                        camera_id=camera_id, visitor_id=vid, event_type="ZONE_ENTER",
                        timestamp=ts, zone_id=zone_id, dwell_ms=0,
                        is_staff=is_staff, confidence=conf,
                        sku_zone=sku_zone, session_seq=seq,
                    ))
                else:
                    # Check dwell threshold
                    enter_f = zone_enter_frames.get(vid, frame_num)
                    dwell_frames = frame_num - enter_f
                    dwell_ms = int(dwell_frames / fps * 1000)
                    if dwell_frames > 0 and dwell_frames % DWELL_THRESHOLD_FRAMES < process_every_n:
                        output_events.append(make_event(
                            camera_id=camera_id, visitor_id=vid, event_type="ZONE_DWELL",
                            timestamp=ts, zone_id=zone_id, dwell_ms=dwell_ms,
                            is_staff=is_staff, confidence=conf,
                            sku_zone=sku_zone, session_seq=seq,
                        ))

            elif role == "billing":
                zone_id = "BILLING"
                if tr["is_new"] or tr["is_reentry"]:
                    # queue_depth = people currently in billing area (excluding the person just joining)
                    # Cap at 10 — physically impossible to have more in a small beauty store
                    queue_depth = min(10, max(0, len([t for t in tracker.get_active_tracks() if not t.is_staff and t.track_id != tid]) ))
                    if queue_depth > 0:
                        output_events.append(make_event(
                            camera_id=camera_id, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                            timestamp=ts, zone_id=zone_id, dwell_ms=0,
                            is_staff=is_staff, confidence=conf,
                            queue_depth=queue_depth, session_seq=seq,
                        ))
                    else:
                        output_events.append(make_event(
                            camera_id=camera_id, visitor_id=vid, event_type="ZONE_ENTER",
                            timestamp=ts, zone_id=zone_id, dwell_ms=0,
                            is_staff=is_staff, confidence=conf,
                            sku_zone=None, session_seq=seq,
                        ))

        # Emit EXIT events for lost tracks
        exits = tracker.flush_exits(frame_num)
        for vid in exits:
            enter_f = zone_enter_frames.pop(vid, frame_num)
            dwell_ms = int((frame_num - enter_f) / fps * 1000)

            if role == "entry_exit":
                output_events.append(make_event(
                    camera_id=camera_id, visitor_id=vid, event_type="EXIT",
                    timestamp=ts, zone_id=None, dwell_ms=dwell_ms,
                    is_staff=False, confidence=0.8, session_seq=1,
                ))
            elif role in ("main_floor", "zone"):
                zone_id = cam_meta.get("zone", "UNKNOWN")
                output_events.append(make_event(
                    camera_id=camera_id, visitor_id=vid, event_type="ZONE_EXIT",
                    timestamp=ts, zone_id=zone_id, dwell_ms=dwell_ms,
                    is_staff=False, confidence=0.8,
                    sku_zone=cam_meta.get("sku_zone"), session_seq=1,
                ))
            elif role == "billing":
                # Check for billing queue abandonment (simplified: any exit without purchase)
                output_events.append(make_event(
                    camera_id=camera_id, visitor_id=vid, event_type="BILLING_QUEUE_ABANDON",
                    timestamp=ts, zone_id="BILLING", dwell_ms=dwell_ms,
                    is_staff=False, confidence=0.8, session_seq=1,
                ))

    # Flush all remaining active tracks at clip end
    # This generates ZONE_EXIT and BILLING_QUEUE_ABANDON for anyone still in frame
    for tid, track in list(tracker._tracks.items()):
        vid = track.visitor_id
        enter_f = zone_enter_frames.pop(vid, frame_num)
        dwell_ms = int((frame_num - enter_f) / fps * 1000)
        if role in ("main_floor", "zone"):
            zone_id = cam_meta.get("zone", "UNKNOWN")
            output_events.append(make_event(
                camera_id=camera_id, visitor_id=vid, event_type="ZONE_EXIT",
                timestamp=ts, zone_id=zone_id, dwell_ms=dwell_ms,
                is_staff=track.is_staff, confidence=0.8,
                sku_zone=cam_meta.get("sku_zone"), session_seq=1,
            ))
        elif role == "billing":
            if dwell_ms < 30000:  # < 30 seconds = abandoned (didn't wait to purchase)
                output_events.append(make_event(
                    camera_id=camera_id, visitor_id=vid, event_type="BILLING_QUEUE_ABANDON",
                    timestamp=ts, zone_id="BILLING", dwell_ms=dwell_ms,
                    is_staff=track.is_staff, confidence=0.8, session_seq=1,
                ))

    cap.release()
    print(f"    -> {frame_num} frames processed, {entry_count} entries, {len(output_events)} events so far")


def main():
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--clips-dir", default="C:/Users/Purvi/Downloads/cctv", help="Directory with CAM video files")
    parser.add_argument("--output", default="data/events.jsonl", help="Output JSONL file")
    parser.add_argument("--every", type=int, default=5, help="Process every N frames (default 5)")
    args = parser.parse_args()

    clips_dir = Path(args.clips_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_events = []

    for cam_key, cam_meta in CAMERA_MAP.items():
        # Try both .mp4 and .MP4 extensions
        for ext in [".mp4", ".MP4", ".avi"]:
            clip_path = clips_dir / f"{cam_key}{ext}"
            if clip_path.exists():
                process_clip(str(clip_path), cam_meta, all_events, process_every_n=args.every)
                break
        else:
            print(f"[WARN] No clip found for {cam_key} in {clips_dir}", file=sys.stderr)

    # Sort by timestamp
    all_events.sort(key=lambda e: e["timestamp"])

    with open(output_path, "w", encoding="utf-8") as f:
        for evt in all_events:
            f.write(json.dumps(evt) + "\n")

    print(f"\nGenerated {len(all_events)} events -> {output_path}")

    # Summary
    from collections import Counter
    types = Counter(e["event_type"] for e in all_events)
    for t, c in sorted(types.items()):
        print(f"   {t}: {c}")


if __name__ == "__main__":
    main()
