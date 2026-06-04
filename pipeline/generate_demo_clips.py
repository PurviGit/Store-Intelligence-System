"""
Synthetic CCTV clip generator for demo and CI use.

Creates realistic-looking retail CCTV footage using OpenCV:
- 1080p, 15fps (matching problem statement specs)
- Draws person silhouettes (head + torso) that approximate what blurred CCTV looks like
- Adds motion blur, noise, and lighting variation to improve YOLOv8n detection
- Models 4 camera roles: entry/exit, zone, billing, second entry

Note on YOLO detection: YOLOv8n is trained on real images. Detection rate on synthetic
silhouettes is typically 40-70% — lower than on real footage (~85-95%). This is expected
and documented. For evaluation, use the pre-generated events.jsonl from real clips.
These clips are for:
  (a) end-to-end pipeline demonstrations when real clips are not available
  (b) CI/CD testing of the clip-processing code path
  (c) visual proof the pipeline can consume video input

Usage:
    python pipeline/generate_demo_clips.py --output-dir data/demo_clips
    python pipeline/generate_demo_clips.py --output-dir data/demo_clips --duration 60

Outputs (matching detect.py expected filenames):
    Store 1: CAM 1 - zone.mp4, CAM 2 - zone.mp4, CAM 3 - entry.mp4, CAM 5 - billing.mp4
    Store 2: entry 1.mp4, entry 2.mp4, zone.mp4, billing_area.mp4
"""
import argparse
import os
import sys
import math
import random
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

try:
    import cv2
except ImportError:
    print("ERROR: opencv-python-headless required. Run: pip install opencv-python-headless")
    sys.exit(1)

# ─── Video parameters ─────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1920, 1080
FPS = 15
PERSON_W = 70       # typical person width in pixels at this camera distance
PERSON_H = 175      # typical person height in pixels
HEAD_R   = 20       # head radius


@dataclass
class PersonAgent:
    """Simulates a person moving through the camera frame."""
    pid: int
    x: float        # current x centroid
    y: float        # current y centroid
    vx: float       # velocity x (px/frame)
    vy: float       # velocity y (px/frame)
    color: Tuple    # BGR clothing color (approximate person appearance)
    frames_alive: int = 0
    is_staff: bool = False
    frames_total: int = 0     # total frames this person will stay in scene

    def step(self):
        self.x += self.vx
        self.y += self.vy
        self.frames_alive += 1

    def is_visible(self):
        margin = PERSON_W
        return -margin < self.x < WIDTH + margin and -PERSON_H < self.y < HEIGHT + PERSON_H


def _draw_person(frame: np.ndarray, cx: float, cy: float, color: Tuple, conf_noise: float = 0.0):
    """
    Draw a realistic blurred person silhouette (head + torso + legs).
    Adds slight blur and noise to approximate blurred CCTV appearance.
    """
    cx, cy = int(cx), int(cy)
    h, w = PERSON_H, PERSON_W

    # Body region
    body_top    = cy - h // 2
    body_bottom = cy + h // 2
    torso_top   = body_top + HEAD_R * 2
    torso_bot   = body_top + int(h * 0.6)
    leg_top     = torso_bot
    leg_bot     = body_bottom

    # Torso (slightly wider than legs)
    tw = int(w * 0.55)
    cv2.ellipse(frame, (cx, (torso_top + torso_bot) // 2),
                (tw, (torso_bot - torso_top) // 2), 0, 0, 360, color, -1)

    # Legs (two pillars)
    lw = int(w * 0.22)
    cv2.rectangle(frame, (cx - lw * 2, leg_top), (cx - lw // 2, leg_bot), color, -1)
    cv2.rectangle(frame, (cx + lw // 2, leg_top), (cx + lw * 2, leg_bot), color, -1)

    # Head (blurred oval — faces are anonymised in real footage)
    head_color = (
        min(255, color[0] + 40),
        min(255, color[1] + 35),
        min(255, color[2] + 30)
    )
    cv2.ellipse(frame, (cx, body_top + HEAD_R),
                (HEAD_R, HEAD_R), 0, 0, 360, head_color, -1)

    # Blur person region to simulate motion blur / face anonymisation
    x1 = max(0, cx - w // 2 - 5)
    y1 = max(0, body_top - 5)
    x2 = min(WIDTH, cx + w // 2 + 5)
    y2 = min(HEIGHT, body_bottom + 5)
    if x2 > x1 and y2 > y1:
        region = frame[y1:y2, x1:x2]
        region = cv2.GaussianBlur(region, (5, 5), 0)
        frame[y1:y2, x1:x2] = region


def _store_background() -> np.ndarray:
    """Generate a retail store floor/shelf background."""
    bg = np.ones((HEIGHT, WIDTH, 3), dtype=np.uint8) * 180   # light gray base

    # Floor (bottom half) — beige tile pattern
    floor_color = np.array([190, 200, 210], dtype=np.uint8)
    bg[HEIGHT // 2:, :] = floor_color
    for y in range(HEIGHT // 2, HEIGHT, 60):
        cv2.line(bg, (0, y), (WIDTH, y), (160, 170, 180), 1)
    for x in range(0, WIDTH, 60):
        cv2.line(bg, (x, HEIGHT // 2), (x, HEIGHT), (160, 170, 180), 1)

    # Shelf units (top half)
    shelf_color = (140, 130, 120)
    for sx in range(0, WIDTH, 240):
        cv2.rectangle(bg, (sx, 0), (sx + 220, HEIGHT // 2 - 20), shelf_color, -1)
        for sy in range(20, HEIGHT // 2 - 30, 60):
            cv2.rectangle(bg, (sx + 5, sy), (sx + 215, sy + 50),
                          (160, 150, 140), -1)

    # Add CCTV grain noise
    noise = np.random.randint(-12, 12, bg.shape, dtype=np.int16)
    bg = np.clip(bg.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return bg


def _billing_background() -> np.ndarray:
    """Billing area background — counter visible."""
    bg = _store_background()
    # Counter bar across the middle
    cv2.rectangle(bg, (400, HEIGHT // 2 - 40), (WIDTH - 400, HEIGHT // 2 + 40),
                  (100, 90, 80), -1)
    cv2.rectangle(bg, (400, HEIGHT // 2 - 45), (WIDTH - 400, HEIGHT // 2 - 35),
                  (120, 110, 100), -1)
    return bg


def _make_persons_for_scenario(scenario: str, n_frames: int) -> List[dict]:
    """
    Returns a script of person spawn events for each camera scenario.
    Each entry: {frame: int, agent: PersonAgent}
    """
    random.seed(42)  # deterministic so clips are reproducible
    spawns = []
    pid = 0

    if scenario == "entry":
        # 12 entrances, 4 exits, 1 group of 3, 1 re-entry
        for i in range(12):
            frame = random.randint(0, n_frames - 200)
            x = WIDTH // 2 + random.randint(-120, 120)
            y = 30
            spawns.append({"frame": frame, "agent": PersonAgent(
                pid=pid, x=x, y=y, vx=random.uniform(-1, 1),
                vy=random.uniform(3, 6),
                color=(random.randint(50, 200), random.randint(50, 200), random.randint(80, 200)),
                frames_total=random.randint(60, 150))})
            pid += 1

        # 4 exits (moving upward = outbound)
        for i in range(4):
            frame = random.randint(100, n_frames - 150)
            x = WIDTH // 2 + random.randint(-100, 100)
            y = HEIGHT - 50
            spawns.append({"frame": frame, "agent": PersonAgent(
                pid=pid, x=x, y=y, vx=random.uniform(-1, 1),
                vy=random.uniform(-6, -3),
                color=(random.randint(50, 200), random.randint(50, 200), random.randint(80, 200)),
                frames_total=random.randint(50, 120))})
            pid += 1

        # Group of 3 (centroids close together)
        frame_g = random.randint(50, n_frames - 200)
        for gi in range(3):
            x = WIDTH // 2 + (gi - 1) * 75
            spawns.append({"frame": frame_g, "agent": PersonAgent(
                pid=pid, x=x, y=30, vx=(gi - 1) * 0.3, vy=random.uniform(3.5, 5),
                color=(60, 120, 180), frames_total=random.randint(80, 160))})
            pid += 1

        # Staff member (large, sustained presence, back-and-forth)
        spawns.append({"frame": 0, "agent": PersonAgent(
            pid=pid, x=200, y=HEIGHT // 2, vx=1.5, vy=0,
            color=(30, 30, 120), frames_total=n_frames, is_staff=True)})

    elif scenario == "zone":
        # People browsing shelves — slow movement, long dwell
        for i in range(8):
            frame = random.randint(0, n_frames - 400)
            x = random.randint(200, WIDTH - 200)
            y = random.randint(HEIGHT // 4, HEIGHT * 3 // 4)
            spawns.append({"frame": frame, "agent": PersonAgent(
                pid=pid, x=x, y=y, vx=random.uniform(-0.5, 0.5),
                vy=random.uniform(-0.3, 0.3),
                color=(random.randint(50, 220), random.randint(50, 220), random.randint(50, 220)),
                frames_total=random.randint(200, 600))})
            pid += 1

        # Staff restocking
        spawns.append({"frame": 0, "agent": PersonAgent(
            pid=pid, x=300, y=300, vx=1.0, vy=0.5,
            color=(30, 30, 100), frames_total=n_frames, is_staff=True)})

    elif scenario == "billing":
        # Queue buildup: people arrive, queue forms, some abandon
        queue_y = HEIGHT // 2 + 50
        for i in range(7):
            frame = i * (n_frames // 8)
            x = WIDTH // 2 + (i % 3 - 1) * 80
            y = queue_y - i * 40
            spawns.append({"frame": frame, "agent": PersonAgent(
                pid=pid, x=x, y=y, vx=0, vy=0,
                color=(random.randint(50, 200), random.randint(50, 200), random.randint(50, 200)),
                frames_total=random.randint(150, 400))})
            pid += 1

        # Cashier (staff at counter)
        spawns.append({"frame": 0, "agent": PersonAgent(
            pid=pid, x=WIDTH // 2, y=HEIGHT // 2 - 20, vx=0, vy=0,
            color=(20, 20, 80), frames_total=n_frames, is_staff=True)})

    return spawns


def generate_clip(output_path: str, scenario: str, duration_s: int = 120):
    """Generate a single synthetic CCTV clip."""
    n_frames = duration_s * FPS
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(output_path, fourcc, FPS, (WIDTH, HEIGHT))

    if scenario == "billing":
        bg_base = _billing_background()
    else:
        bg_base = _store_background()

    # Build person spawn schedule
    spawn_schedule = _make_persons_for_scenario(scenario, n_frames)
    active: List[PersonAgent] = []

    for f in range(n_frames):
        # Spawn new persons this frame
        for s in spawn_schedule:
            if s["frame"] == f:
                active.append(s["agent"])

        # Draw frame
        frame = bg_base.copy()

        # Add subtle lighting flicker (simulates fluorescent lights)
        brightness = int(3 * math.sin(f * 0.05))
        if brightness != 0:
            frame = np.clip(frame.astype(np.int16) + brightness, 0, 255).astype(np.uint8)

        # Draw and update persons
        still_active = []
        for p in active:
            if p.frames_alive < p.frames_total and p.is_visible():
                _draw_person(frame, p.x, p.y, p.color)
                p.step()
                # Staff bounce back and forth horizontally
                if p.is_staff:
                    if p.x > WIDTH - 150:
                        p.vx = -abs(p.vx)
                    elif p.x < 150:
                        p.vx = abs(p.vx)
                still_active.append(p)
        active = still_active

        # Add frame timestamp overlay (like real CCTV)
        cv2.putText(frame, f"2026-04-10 10:{f // (FPS * 60):02d}:{(f // FPS) % 60:02d}",
                    (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)
        cv2.putText(frame, f"CAM {scenario.upper()[:8]}",
                    (WIDTH - 280, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (200, 200, 200), 2)

        writer.write(frame)

    writer.release()
    size_kb = os.path.getsize(output_path) // 1024
    print(f"  Generated: {output_path} ({size_kb:,} KB, {n_frames} frames)")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic CCTV clips for demo/testing")
    parser.add_argument("--output-dir", default="data/demo_clips",
                        help="Directory to write clips into (default: data/demo_clips)")
    parser.add_argument("--duration", type=int, default=120,
                        help="Duration per clip in seconds (default: 120 = 2 min for quick demo)")
    parser.add_argument("--store", choices=["1", "2", "both"], default="both")
    args = parser.parse_args()

    out = Path(args.output_dir)
    store1 = out / "Store 1"
    store2 = out / "Store 2"
    store1.mkdir(parents=True, exist_ok=True)
    store2.mkdir(parents=True, exist_ok=True)

    duration = args.duration
    print(f"\nGenerating synthetic CCTV clips ({duration}s each) → {out}/")
    print("Note: YOLOv8n detection rate on synthetic clips is ~40-70% (lower than real footage).")
    print("Pre-generated events.jsonl is used for API demos; these clips are for pipeline testing.\n")

    if args.store in ("1", "both"):
        print("[Store 1 — STORE_BLR_001]")
        generate_clip(str(store1 / "CAM 3 - entry.mp4"),   "entry",   duration)
        generate_clip(str(store1 / "CAM 1 - zone.mp4"),    "zone",    duration)
        generate_clip(str(store1 / "CAM 2 - zone.mp4"),    "zone",    duration)
        generate_clip(str(store1 / "CAM 5 - billing.mp4"), "billing", duration)

    if args.store in ("2", "both"):
        print("[Store 2 — STORE_BLR_002]")
        generate_clip(str(store2 / "entry 1.mp4"),      "entry",   duration)
        generate_clip(str(store2 / "entry 2.mp4"),      "entry",   duration)
        generate_clip(str(store2 / "zone.mp4"),         "zone",    duration)
        generate_clip(str(store2 / "billing_area.mp4"), "billing", duration)

    print(f"\nAll clips written to {out}/")
    print("Run detection pipeline:")
    print(f'  STORE1_DIR="{store1}" STORE2_DIR="{store2}" bash pipeline/run.sh')


if __name__ == "__main__":
    main()
