import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from server import TrajectoryStore


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
