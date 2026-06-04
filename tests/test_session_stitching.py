# PROMPT: "Write tests for cross-camera session stitching, appearance-based Re-ID,
# and tracker direction detection. Cross-camera: visitor on entry camera (VIS_ENTRY_001)
# and zone camera (VIS_ZONE_007) counted as ONE session when timestamps overlap.
# Test both direct ID match (scoring harness) and time-window match (real pipeline).
# Appearance Re-ID: compute_color_histogram returns float32 array, histogram_similarity
# returns float in [-1,1], re-entry with matching appearance → same visitor_id.
# Test that a geometrically-close detection with very different appearance (different
# coloured clothing) is NOT matched as re-entry. Direction: y-slope positive → ENTRY,
# negative → EXIT."
#
# CHANGES MADE:
# - Added time-window tolerance test (events within session window are matched even
#   with different visitor_ids across cameras — the key cross-camera Re-ID test)
# - Added expired-session test (zone visit >90min after entry should NOT be counted)
# - Added concurrent-sessions test (two visitors, two sessions, each tracked separately)
# - Tracker direction tests use hand-crafted centroid histories to avoid real video
# - Re-ID test injects EXIT then ENTRY 3 seconds later to simulate re-entry

import uuid
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

DATE = "2026-04-10"
STORE = "STORE_BLR_002"


def make_event(store_id=STORE, **overrides):
    base = {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  "CAM_ENTRY_01",
        "visitor_id": "VIS_00001",
        "event_type": "ENTRY",
        "timestamp":  f"{DATE}T12:00:00Z",
        "zone_id":    None,
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.90,
        "metadata":   {"queue_depth": None, "sku_zone": None, "session_seq": 1},
    }
    base.update(overrides)
    return base


# ── Cross-Camera Funnel Session Stitching ─────────────────────────────────────

class TestCrossCameraFunnel:

    def test_consistent_visitor_id_counted_in_stage2(self, client):
        """
        Pass 1 (direct ID match): same visitor_id in ENTRY and ZONE_ENTER
        → session counted in Stage 2. This is the scoring harness scenario.
        """
        store = f"STORE_XC_{uuid.uuid4().hex[:6]}"
        events = [
            make_event(store_id=store, visitor_id="VIS_SAME", event_id=str(uuid.uuid4()),
                       event_type="ENTRY", timestamp=f"{DATE}T12:00:00Z"),
            make_event(store_id=store, visitor_id="VIS_SAME", event_id=str(uuid.uuid4()),
                       event_type="ZONE_ENTER", zone_id="SKINCARE",
                       camera_id="CAM_ZONE_02", timestamp=f"{DATE}T12:05:00Z",
                       metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2}),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        funnel = resp.json()["funnel"]
        assert funnel[0]["visitors"] == 1   # Stage 1: 1 entry
        assert funnel[1]["visitors"] == 1   # Stage 2: 1 zone session (same ID)

    def test_different_visitor_ids_stitched_by_time_window(self, client):
        """
        Pass 2 (time-window match): entry camera visitor_id differs from zone
        camera visitor_id. Zone event timestamp falls within entry session window
        → should be counted as 1 session in Stage 2, not 0.
        This is the real-pipeline scenario with camera-scoped visitor_ids.
        """
        store = f"STORE_XW_{uuid.uuid4().hex[:6]}"
        events = [
            # Entry camera visitor (different ID from zone camera)
            make_event(store_id=store, visitor_id="VIS_ENTRY_CAM_001",
                       event_id=str(uuid.uuid4()),
                       event_type="ENTRY", timestamp=f"{DATE}T13:00:00Z"),
            # Zone camera visitor (different ID — camera-scoped)
            make_event(store_id=store, visitor_id="VIS_ZONE_CAM_099",
                       event_id=str(uuid.uuid4()),
                       event_type="ZONE_ENTER", zone_id="SKINCARE",
                       camera_id="CAM_ZONE_02", timestamp=f"{DATE}T13:08:00Z",
                       metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 1}),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        funnel = resp.json()["funnel"]
        assert funnel[0]["visitors"] == 1   # Stage 1: 1 entry
        assert funnel[1]["visitors"] == 1   # Stage 2: time-window stitched → 1 session

    def test_zone_event_after_session_expiry_not_counted(self, client):
        """
        Zone event that occurs >90 minutes after entry (session expired)
        should NOT be counted in Stage 2 for that session.
        """
        store = f"STORE_EXP_{uuid.uuid4().hex[:6]}"
        events = [
            make_event(store_id=store, visitor_id="VIS_ENTRY_A",
                       event_id=str(uuid.uuid4()),
                       event_type="ENTRY", timestamp=f"{DATE}T10:00:00Z"),
            make_event(store_id=store, visitor_id="VIS_ENTRY_A",
                       event_id=str(uuid.uuid4()),
                       event_type="EXIT", timestamp=f"{DATE}T10:15:00Z"),
            # Zone event 2 hours after entry — different person, different camera ID
            make_event(store_id=store, visitor_id="VIS_ZONE_LATE",
                       event_id=str(uuid.uuid4()),
                       event_type="ZONE_ENTER", zone_id="HAIRCARE",
                       camera_id="CAM_ZONE_02", timestamp=f"{DATE}T12:20:00Z",
                       metadata={"queue_depth": None, "sku_zone": "HAIRCARE", "session_seq": 1}),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        funnel = resp.json()["funnel"]
        # Stage 1 = 1 (VIS_ENTRY_A). Stage 2 should be 0 because late zone event
        # is outside the session window (entry 10:00 exit 10:15, zone at 12:20).
        assert funnel[0]["visitors"] == 1
        assert funnel[1]["visitors"] == 0

    def test_two_concurrent_sessions_stitched_independently(self, client):
        """
        Two visitors enter at different times with camera-scoped IDs.
        Both visit zones. Both should be counted in Stage 2.
        """
        store = f"STORE_CONC_{uuid.uuid4().hex[:6]}"
        events = [
            # Visitor A: entry at 14:00
            make_event(store_id=store, visitor_id="VIS_ENTRY_A",
                       event_id=str(uuid.uuid4()),
                       event_type="ENTRY", timestamp=f"{DATE}T14:00:00Z"),
            # Visitor B: entry at 14:30
            make_event(store_id=store, visitor_id="VIS_ENTRY_B",
                       event_id=str(uuid.uuid4()),
                       event_type="ENTRY", timestamp=f"{DATE}T14:30:00Z"),
            # Zone camera visitor for A (at 14:10 — within A's window)
            make_event(store_id=store, visitor_id="VIS_ZONE_X",
                       event_id=str(uuid.uuid4()),
                       event_type="ZONE_ENTER", zone_id="SKINCARE",
                       camera_id="CAM_ZONE_02", timestamp=f"{DATE}T14:10:00Z",
                       metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 1}),
            # Zone camera visitor for B (at 14:40 — within B's window but not A's exit)
            make_event(store_id=store, visitor_id="VIS_ZONE_Y",
                       event_id=str(uuid.uuid4()),
                       event_type="ZONE_ENTER", zone_id="HAIRCARE",
                       camera_id="CAM_ZONE_02", timestamp=f"{DATE}T14:45:00Z",
                       metadata={"queue_depth": None, "sku_zone": "HAIRCARE", "session_seq": 1}),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        funnel = resp.json()["funnel"]
        assert funnel[0]["visitors"] == 2   # 2 entries
        assert funnel[1]["visitors"] == 2   # both stitched to zone sessions

    def test_reentry_not_double_counted_in_funnel(self, client):
        """REENTRY uses same visitor_id as original ENTRY → Stage 1 count stays 1."""
        store = f"STORE_RENTRYF_{uuid.uuid4().hex[:6]}"
        events = [
            make_event(store_id=store, visitor_id="VIS_RE", event_id=str(uuid.uuid4()),
                       event_type="ENTRY"),
            make_event(store_id=store, visitor_id="VIS_RE", event_id=str(uuid.uuid4()),
                       event_type="REENTRY", timestamp=f"{DATE}T13:00:00Z"),
        ]
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.json()["funnel"][0]["visitors"] == 1

    def test_funnel_four_stages_monotonically_decreasing(self, client):
        """Each stage visitors ≤ previous stage visitors (physical constraint)."""
        store = f"STORE_MONO_{uuid.uuid4().hex[:6]}"
        events = []
        for i in range(5):
            events.append(make_event(store_id=store, visitor_id=f"VIS_{i:03d}",
                                     event_id=str(uuid.uuid4())))
        for i in range(3):
            events.append(make_event(store_id=store, visitor_id=f"VIS_{i:03d}",
                                     event_type="ZONE_ENTER", zone_id="SKINCARE",
                                     camera_id="CAM_ZONE_02", event_id=str(uuid.uuid4()),
                                     timestamp=f"{DATE}T12:10:00Z",
                                     metadata={"queue_depth": None, "sku_zone": "SKINCARE", "session_seq": 2}))
        client.post("/events/ingest", json={"events": events})
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        stages = resp.json()["funnel"]
        for i in range(1, len(stages)):
            assert stages[i]["visitors"] <= stages[i-1]["visitors"], \
                f"Stage {i+1} ({stages[i]['visitors']}) > Stage {i} ({stages[i-1]['visitors']})"

    def test_empty_store_funnel_returns_zeros(self, client):
        store = f"STORE_EMPTY_FUNNEL_{uuid.uuid4().hex[:6]}"
        resp = client.get(f"/stores/{store}/funnel?date={DATE}")
        assert resp.status_code == 200
        for stage in resp.json()["funnel"]:
            assert stage["visitors"] == 0

    def test_funnel_has_cross_camera_reid_flag(self, client):
        """Funnel response must declare cross_camera_reid status."""
        resp = client.get(f"/stores/{STORE}/funnel?date={DATE}")
        data = resp.json()
        assert "cross_camera_reid" in data
        assert data["cross_camera_reid"] is True
        assert "reid_method" in data


# ── Tracker Direction Detection ──────────────────────────────────────────────

class TestTrackerDirection:

    def test_velocity_downward_is_entry(self):
        """
        Person moving downward (y increasing each frame) → entry direction.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker, Track
        from collections import deque

        t = Tracker()
        history = deque([(100, 100 + i * 5) for i in range(10)], maxlen=10)
        track = Track(
            track_id=1, visitor_id="VIS_001",
            cx=100, cy=150, initial_cx=100, initial_cy=100,
            area=2000, last_seen_frame=10,
            centroid_history=history,
        )
        direction = t._confirm_direction(track)
        assert direction == "entry", f"Expected 'entry', got '{direction}'"

    def test_velocity_upward_is_exit(self):
        """Person moving upward (y decreasing) → exit direction."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker, Track
        from collections import deque

        t = Tracker()
        history = deque([(100, 200 - i * 5) for i in range(10)], maxlen=10)
        track = Track(
            track_id=2, visitor_id="VIS_002",
            cx=100, cy=150, initial_cx=100, initial_cy=200,
            area=2000, last_seen_frame=10,
            centroid_history=history,
        )
        direction = t._confirm_direction(track)
        assert direction == "exit", f"Expected 'exit', got '{direction}'"

    def test_insufficient_history_returns_none(self):
        """Direction is None when fewer than DIRECTION_BUFFER_FRAMES frames buffered."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker, Track
        from collections import deque

        t = Tracker()
        history = deque([(100, 100), (100, 105)], maxlen=10)  # only 2 frames
        track = Track(
            track_id=3, visitor_id="VIS_003",
            cx=100, cy=105, initial_cx=100, initial_cy=100,
            area=2000, last_seen_frame=2,
            centroid_history=history,
        )
        direction = t._confirm_direction(track)
        assert direction is None

    def test_net_y_velocity_positive_for_downward(self):
        """_net_y_velocity returns positive float for downward movement."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker
        from collections import deque

        t = Tracker()
        history = deque([(100, 100 + i * 4) for i in range(8)], maxlen=10)
        vel = t._net_y_velocity(history)
        assert vel > 0, f"Expected positive velocity, got {vel}"

    def test_net_y_velocity_negative_for_upward(self):
        """_net_y_velocity returns negative float for upward movement."""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker
        from collections import deque

        t = Tracker()
        history = deque([(100, 200 - i * 4) for i in range(8)], maxlen=10)
        vel = t._net_y_velocity(history)
        assert vel < 0, f"Expected negative velocity, got {vel}"


# ── Tracker Re-ID ─────────────────────────────────────────────────────────────

class TestTrackerReID:

    def test_reentry_within_window_returns_same_visitor_id(self):
        """
        Person exits (track lost) and re-appears within re-entry window
        at the same position → same visitor_id should be returned via is_reentry.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker

        t = Tracker()
        t._camera_prefix = "TEST"

        det1 = [{"bbox": (400, 300, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result1 = t.update(det1, frame_num=1)
        assert len(result1) == 1
        original_vid = result1[0]["visitor_id"]

        # Simulate track loss (advance frames beyond MAX_FRAMES_LOST)
        t.flush_exits(frame_num=200)

        # Re-enter at same position within re-entry window
        det2 = [{"bbox": (405, 295, 58, 138), "confidence": 0.85,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result2 = t.update(det2, frame_num=210)
        assert len(result2) == 1
        assert result2[0]["is_reentry"] is True, "Should detect re-entry"
        assert result2[0]["visitor_id"] == original_vid, \
            f"Expected same visitor_id {original_vid}, got {result2[0]['visitor_id']}"

    def test_distant_redetection_is_new_visitor(self):
        """
        Person detected far from any exited visitor → new visitor_id (not re-entry).
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker

        t = Tracker()
        t._camera_prefix = "TEST2"

        det1 = [{"bbox": (100, 100, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result1 = t.update(det1, frame_num=1)
        original_vid = result1[0]["visitor_id"]
        t.flush_exits(frame_num=200)

        # Re-appear 800px away — different person
        det2 = [{"bbox": (900, 700, 60, 140), "confidence": 0.85,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result2 = t.update(det2, frame_num=210)
        assert result2[0]["is_reentry"] is False
        assert result2[0]["visitor_id"] != original_vid

    def test_reentry_after_window_expires_is_new_visitor(self):
        """
        Re-detection after the re-entry window (5min @ 15fps = 4500 frames)
        → treated as new visitor, not re-entry.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker

        t = Tracker()
        t._camera_prefix = "TEST3"

        det1 = [{"bbox": (400, 300, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result1 = t.update(det1, frame_num=1)
        original_vid = result1[0]["visitor_id"]
        t.flush_exits(frame_num=200)

        # Re-appear after re-entry window (> 4500 frames)
        det2 = [{"bbox": (405, 295, 60, 140), "confidence": 0.85,
                 "is_staff": False, "group_id": None, "group_size": None}]
        result2 = t.update(det2, frame_num=5000)
        assert result2[0]["is_reentry"] is False, "Window expired — should be new visitor"
        assert result2[0]["visitor_id"] != original_vid

    def test_group_detection_same_group_id(self):
        """
        Two detections close together → same group_id, group_size=2, separate visitor_ids.
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))
        from tracker import Tracker

        t = Tracker()
        t._camera_prefix = "GRP"
        det = [
            {"bbox": (400, 300, 60, 140), "confidence": 0.88,
             "is_staff": False, "group_id": "GRP_400_300", "group_size": 2},
            {"bbox": (460, 305, 62, 135), "confidence": 0.85,
             "is_staff": False, "group_id": "GRP_400_300", "group_size": 2},
        ]
        results = t.update(det, frame_num=1)
        assert len(results) == 2
        vids = {r["visitor_id"] for r in results}
        assert len(vids) == 2, "Each group member should have a unique visitor_id"
        group_ids = {r["group_id"] for r in results}
        assert "GRP_400_300" in group_ids


# ── Appearance Re-ID (Color Histogram) ───────────────────────────────────────
# CHANGES MADE:
# - AI suggested mocking cv2 to test histogram similarity — I used real numpy arrays
#   instead (simpler, no patching needed, tests the actual math)
# - Added explicit test that different-coloured clothing prevents false re-entry match
# - Added test that None histograms fall back to geometry-only (graceful degradation)

class TestAppearanceReID:

    def _path_setup(self):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'pipeline'))

    def test_compute_color_histogram_returns_array(self):
        """compute_color_histogram returns a float32 numpy array or None."""
        self._path_setup()
        from tracker import compute_color_histogram
        import numpy as np
        # Create a simple 100x100 BGR test frame (blue rectangle)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = (255, 0, 0)   # blue
        hist = compute_color_histogram(frame, (10, 10, 60, 60))
        assert hist is not None, "Should return histogram for valid frame"
        assert hist.dtype == np.float32
        assert len(hist) == 16 * 16   # HIST_BINS² = 256

    def test_histogram_similarity_identical_histograms(self):
        """Same histogram compared to itself → similarity near 1.0."""
        self._path_setup()
        from tracker import compute_color_histogram, histogram_similarity
        import numpy as np
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        frame[:, :] = (0, 128, 255)
        hist = compute_color_histogram(frame, (0, 0, 100, 100))
        sim = histogram_similarity(hist, hist)
        assert sim > 0.95, f"Identical histograms should have similarity > 0.95, got {sim}"

    def test_histogram_similarity_different_colours_low(self):
        """Blue vs red clothing → histogram similarity significantly below 1.0."""
        self._path_setup()
        from tracker import compute_color_histogram, histogram_similarity
        import numpy as np
        frame_blue = np.zeros((100, 100, 3), dtype=np.uint8)
        frame_blue[:, :] = (255, 0, 0)   # blue
        frame_red = np.zeros((100, 100, 3), dtype=np.uint8)
        frame_red[:, :] = (0, 0, 255)    # red
        hist_blue = compute_color_histogram(frame_blue, (0, 0, 100, 100))
        hist_red  = compute_color_histogram(frame_red,  (0, 0, 100, 100))
        sim = histogram_similarity(hist_blue, hist_red)
        assert sim < 0.8, f"Different colours should have low similarity, got {sim}"

    def test_histogram_none_returns_zero_similarity(self):
        """None histograms → similarity 0.0 (geometric-only fallback)."""
        self._path_setup()
        from tracker import histogram_similarity
        import numpy as np
        fake = np.ones(256, dtype=np.float32)
        assert histogram_similarity(None, fake) == 0.0
        assert histogram_similarity(fake, None) == 0.0
        assert histogram_similarity(None, None) == 0.0

    def test_appearance_reentry_with_matching_histogram(self):
        """
        Re-entry with matching appearance (same colour histogram) → is_reentry=True.
        When both geometry AND appearance match, Re-ID fires correctly.
        """
        self._path_setup()
        from tracker import Tracker
        import numpy as np

        t = Tracker()
        t._camera_prefix = "HIST"

        # Create a consistent colour histogram (simulates same clothing)
        blue_hist = np.ones(256, dtype=np.float32) * 0.1
        blue_hist[50] = 1.0   # dominant blue bin

        det1 = [{"bbox": (400, 300, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": blue_hist}]
        result1 = t.update(det1, frame_num=1)
        original_vid = result1[0]["visitor_id"]
        t.flush_exits(frame_num=200)

        # Same position, same histogram → re-entry
        det2 = [{"bbox": (405, 295, 58, 138), "confidence": 0.85,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": blue_hist}]
        result2 = t.update(det2, frame_num=210)
        assert result2[0]["is_reentry"] is True
        assert result2[0]["visitor_id"] == original_vid

    def test_appearance_mismatch_blocks_false_reentry(self):
        """
        Re-entry with DIFFERENT appearance (different clothing colour) → new visitor.
        This is the key edge case: two different people at the same door position.
        A person in blue exits; a person in red appears at the same spot 10s later.
        Without appearance features, this would be incorrectly matched as re-entry.
        With histogram Re-ID, the colour mismatch correctly blocks the match.
        """
        self._path_setup()
        from tracker import Tracker, HIST_SIMILARITY_THRESHOLD
        import numpy as np

        t = Tracker()
        t._camera_prefix = "HMIS"

        # Visitor 1: blue clothing
        blue_hist = np.zeros(256, dtype=np.float32)
        blue_hist[20] = 1.0   # strong blue-ish hue bin

        # Visitor 2: red clothing (very different hue)
        red_hist = np.zeros(256, dtype=np.float32)
        red_hist[200] = 1.0   # strong red-ish hue bin (opposite end of histogram)

        det1 = [{"bbox": (400, 300, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": blue_hist}]
        result1 = t.update(det1, frame_num=1)
        original_vid = result1[0]["visitor_id"]
        t.flush_exits(frame_num=200)

        # Different person (red) appears at same door position — should NOT be re-entry
        det2 = [{"bbox": (403, 298, 60, 140), "confidence": 0.87,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": red_hist}]
        result2 = t.update(det2, frame_num=210)
        assert result2[0]["is_reentry"] is False, \
            "Different clothing colour should block false re-entry match"
        assert result2[0]["visitor_id"] != original_vid, \
            "Different person should get a new visitor_id"

    def test_no_histogram_falls_back_to_geometry_only(self):
        """
        When no color_hist is provided (e.g., test harness events without video),
        Re-ID falls back to geometry-only matching. Re-entry still works.
        """
        self._path_setup()
        from tracker import Tracker

        t = Tracker()
        t._camera_prefix = "GEO"

        det1 = [{"bbox": (400, 300, 60, 140), "confidence": 0.88,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": None}]  # no histogram
        result1 = t.update(det1, frame_num=1)
        original_vid = result1[0]["visitor_id"]
        t.flush_exits(frame_num=200)

        det2 = [{"bbox": (402, 299, 60, 140), "confidence": 0.86,
                 "is_staff": False, "group_id": None, "group_size": None,
                 "color_hist": None}]  # no histogram
        result2 = t.update(det2, frame_num=210)
        # Geometry-only: same position → re-entry still detected
        assert result2[0]["is_reentry"] is True
        assert result2[0]["visitor_id"] == original_vid
