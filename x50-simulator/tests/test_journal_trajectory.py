import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from journal_trajectory import journal_points, journal_steering_events, trajectory_for_trip


class JournalTrajectoryTest(unittest.TestCase):
    def test_repeated_journal_samples_and_trip_clip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drive.jsonl.gz"
            points = [
                {"t_ms": 900, "x_m": 0, "y_m": 0, "heading_deg": 0, "dist_m": 0, "segment_id": 0},
                {"t_ms": 1000, "x_m": 10, "y_m": 5, "heading_deg": 90, "dist_m": 10, "segment_id": 1},
                {"t_ms": 1100, "x_m": 10, "y_m": 7, "heading_deg": 95, "dist_m": 12,
                 "segment_id": 1, "aligned_lat": 55.01, "aligned_lon": 37.01,
                 "alignment_source": "fakegps_route", "alignment_route_generation": 3},
            ]
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for point in (points[0], points[1], points[1], points[2]):
                    stream.write(json.dumps({"type": "vehicle_sample", "data": {"trajectory_point": point}}) + "\n")
            retained = journal_points(path)
            self.assertEqual(3, len(retained))
            trip = {"summary": {"id": "trip", "started_ms": 950, "ended_ms": 1150},
                    "samples": [{"time_ms": 1000, "gps_good": True,
                                 "carlinkit_lat": 55.0, "carlinkit_lon": 37.0,
                                 "carlinkit_bearing": 90.0}]}
            trajectory = trajectory_for_trip(retained, "journal", trip)
            self.assertEqual(2, trajectory["point_count"])
            self.assertEqual(2.0, trajectory["distance_m"])
            self.assertEqual(0, trajectory["points"][0]["segment_id"])
            self.assertEqual(2.0, trajectory["points"][1]["x_m"])
            self.assertEqual(0.0, trajectory["points"][1]["y_m"])
            self.assertEqual(90.0, trajectory["anchor"]["start_bearing_deg"])
            self.assertEqual((55.01, 37.01, 3),
                             (trajectory["points"][1]["aligned_lat"],
                              trajectory["points"][1]["aligned_lon"],
                              trajectory["points"][1]["alignment_route_generation"]))

    def test_no_map_anchor_keeps_sensor_shape(self):
        points = [
            {"t_ms": 1000, "x_m": 0, "y_m": 0, "heading_deg": 0, "dist_m": 0},
            {"t_ms": 1100, "x_m": 1, "y_m": 0, "heading_deg": 0, "dist_m": 1},
        ]
        trip = {"summary": {"id": "trip", "started_ms": 1000, "ended_ms": 1100}, "samples": []}
        trajectory = trajectory_for_trip(points, "journal", trip)
        self.assertFalse(trajectory["anchor"]["has_anchor"])
        self.assertEqual(1.0, trajectory["points"][1]["x_m"])

    def test_steering_events_keep_time_and_clip_to_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "drive.jsonl.gz"
            rows = [
                {"type": "steering_overlay_correction", "time_ms": 1050,
                 "seq": 8, "data": {"delta_m": 4.2}},
                {"type": "steering_overlay_divergence_started", "time_ms": 1080,
                 "seq": 9, "data": {"fake_lat": 55.1, "fake_lon": 37.1}},
                {"type": "steering_overlay_fit", "time_ms": 1200,
                 "seq": 10, "data": {"rms_m": 3.0, "aligned": True,
                                      "aligned_lat": 55.12, "aligned_lon": 37.13,
                                      "route_generation": 4}},
                {"type": "steering_overlay_applied", "time_ms": 1040,
                 "seq": 11, "data": {"applied_m": 0.2}},
                {"type": "steering_overlay_applied", "time_ms": 1060,
                 "seq": 12, "data": {"applied_m": 0.3}},
                {"type": "coordinate_decision", "time_ms": 1090, "data": {}},
            ]
            with gzip.open(path, "wt", encoding="utf-8") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            events = journal_steering_events(path)
            self.assertEqual(4, len(events))
            applied = next(event for event in events if event["event"] == "steering_overlay_applied")
            self.assertEqual(0.5, applied["data"]["applied_m"])
            self.assertEqual(2, applied["data"]["operations"])
            points = [
                {"t_ms": 1000, "x_m": 0, "y_m": 0, "heading_deg": 0,
                 "dist_m": 0, "segment_id": 0},
                {"t_ms": 1100, "x_m": 1, "y_m": 0, "heading_deg": 0,
                 "dist_m": 1, "segment_id": 0},
            ]
            trip = {"summary": {"id": "trip", "started_ms": 1000,
                                 "ended_ms": 1100}, "samples": []}
            trajectory = trajectory_for_trip(points, "journal", trip, events)
            self.assertEqual({8, 9, 11}, {event["seq"] for event in trajectory["events"]})
            divergence = next(event for event in trajectory["events"]
                              if event["event"] == "steering_overlay_divergence_started")
            self.assertEqual(55.1, divergence["data"]["fake_lat"])
            fit = next(event for event in events if event["event"] == "steering_overlay_fit")
            self.assertEqual((55.12, 37.13, 4),
                             (fit["data"]["aligned_lat"], fit["data"]["aligned_lon"],
                              fit["data"]["route_generation"]))
