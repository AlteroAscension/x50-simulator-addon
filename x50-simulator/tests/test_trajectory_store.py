import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from server import SimulationEngine, TrajectoryStore


class TrajectoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TrajectoryStore(root=Path(self.temp_dir.name))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_list_trajectory(self):
        sample = {
            "trajectory_id": "test_traj_001",
            "started_at_ms": 1700000000000,
            "ended_at_ms": 1700000010000,
            "duration_s": 10.0,
            "distance_m": 45.2,
            "vehicle_params": {"wheelbase_m": 2.6, "steering_ratio": 15.5},
            "anchor": {
                "has_anchor": True,
                "start_latitude": 55.751244,
                "start_longitude": 37.618423,
                "start_bearing_deg": 45.0,
            },
            "points": [
                {"t_ms": 1700000000000, "dt_s": 0.1, "x_m": 0.0, "y_m": 0.0, "heading_deg": 0.0, "speed_kmh": 20.0, "steer_deg": 0.0, "dist_m": 0.0},
                {"t_ms": 1700000000100, "dt_s": 0.1, "x_m": 0.55, "y_m": 0.02, "heading_deg": 1.2, "speed_kmh": 20.0, "steer_deg": 15.0, "dist_m": 0.55}
            ]
        }
        res, code = self.store.save(sample)
        self.assertEqual(200, code)
        self.assertTrue(res["ok"])
        self.assertEqual("test_traj_001", res["id"])

        listed = self.store.list()
        self.assertTrue(listed["ok"])
        self.assertEqual(1, len(listed["trajectories"]))
        item = listed["trajectories"][0]
        self.assertEqual("test_traj_001", item["id"])
        self.assertEqual(2, item["point_count"])
        self.assertTrue(item["has_anchor"])

    def test_live_snapshot_is_updated_and_matched_to_overlapping_trip(self):
        sample = {
            "trajectory_id": "trip_trace",
            "started_at_ms": 1700000000000,
            "ended_at_ms": 1700000010000,
            "complete": False,
            "observed_at_ms": 1700000005000,
            "points": [{"x_m": 0, "y_m": 0}],
        }
        self.assertEqual(200, self.store.save(sample)[1])
        index_item = {
            "snapshot_id": "trip_trace",
            "point_count": 2,
            "complete": True,
            "observed_at_ms": 1700000011000,
        }
        self.assertTrue(self.store.needs_sync(index_item))
        sample.update({
            "ended_at_ms": 1700000010000,
            "complete": True,
            "observed_at_ms": 1700000011000,
            "points": [{"x_m": 0, "y_m": 0}, {"x_m": 1, "y_m": 1}],
        })
        self.assertEqual(200, self.store.save(sample)[1])
        self.assertFalse(self.store.needs_sync(index_item))
        matched = self.store.overlapping(1700000001000, 1700000009000)
        self.assertEqual(["trip_trace"], [item["trajectory_id"] for item in matched])

    def test_non_overlapping_trajectory_is_not_attached_to_trip(self):
        self.store.save({
            "trajectory_id": "other_trip",
            "started_at_ms": 1700001000000,
            "ended_at_ms": 1700001010000,
            "points": [{"x_m": 0, "y_m": 0}],
        })
        self.assertEqual([], self.store.overlapping(1700000000000, 1700000010000))

    def test_detail_and_delete(self):
        sample = {
            "trajectory_id": "test_traj_002",
            "points": [{"x_m": 1.0, "y_m": 1.0}]
        }
        self.store.save(sample)
        detail, status = self.store.detail("test_traj_002")
        self.assertEqual(200, status)
        self.assertEqual("test_traj_002", detail["trajectory"]["trajectory_id"])

        del_res, del_status = self.store.delete("test_traj_002")
        self.assertEqual(200, del_status)
        self.assertTrue(del_res["ok"])

        detail2, status2 = self.store.detail("test_traj_002")
        self.assertEqual(404, status2)

    def test_invalid_payload_rejected(self):
        res, code = self.store.save({"invalid": "data"})
        self.assertEqual(400, code)
        self.assertFalse(res["ok"])

    def test_complete_native_trace_takes_precedence_over_journal_fallback(self):
        self.store.save({"trajectory_id": "journal_copy", "source": "ha_full_trip_journal",
                         "started_at_ms": 1000, "ended_at_ms": 2000, "complete": True,
                         "points": [{"t_ms": 1000}, {"t_ms": 2000}]})
        self.store.save({"trajectory_id": "native_copy", "started_at_ms": 950,
                         "ended_at_ms": 2050, "complete": True,
                         "points": [{"t_ms": 950}, {"t_ms": 2050}]})
        class TripStore:
            def detail(self, _trip_id):
                return {"ok": True, "summary": {"started_ms": 1000, "ended_ms": 2000}}, 200
        engine = SimulationEngine.__new__(SimulationEngine)
        engine.trip_store = TripStore()
        engine.trajectory_store = self.store
        payload, status = engine.trip_detail("trip")
        self.assertEqual(200, status)
        self.assertEqual(["native_copy"], [item["trajectory_id"] for item in payload["trajectories"]])
