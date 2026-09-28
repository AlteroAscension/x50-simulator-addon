import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from server import SimulationEngine, TrajectoryStore, TripLogStore


class SteeringTripEventsTest(unittest.TestCase):
    def test_live_inertial_inputs_are_saved_with_gps_sample(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TripLogStore(root=Path(directory), device_kind="head_unit")
            step = {"schema": "x50.inertial-step.v1", "compass_raw_deg": 91.2,
                    "compass_valid": True, "compass_state": "rejected",
                    "compass_residual_deg": 24.0, "compass_allowed_deg": 5.0,
                    "steer_deg": 12.0, "step_m": 0.8}
            store.observe({"ok": True, "vehicle_speed_kmh": 25,
                           "carlinkit_lat": 55.7, "carlinkit_lon": 37.5,
                           "compass": {"valid": True, "raw_deg": 91.2},
                           "inertial_trajectory": {"anchored": True,
                                                   "last_step": step}},
                          {"device_kind": "head_unit"})
            sample = store.detail(store.active["id"])[0]["samples"][-1]
            self.assertEqual(55.7, sample["carlinkit_lat"])
            self.assertEqual(91.2, sample["compass"]["raw_deg"])
            self.assertEqual(step, sample["inertial_step"])

    def test_journal_events_survive_native_trajectory_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            trajectories = TrajectoryStore(root=Path(directory))
            trajectories.save({"trajectory_id": "native", "complete": True,
                               "started_at_ms": 1000, "ended_at_ms": 2000,
                               "points": [{"t_ms": 1000}, {"t_ms": 2000}]})
            trajectories.save({"trajectory_id": "journal", "complete": True,
                               "source": "ha_full_trip_journal",
                               "started_at_ms": 1000, "ended_at_ms": 2000,
                               "points": [{"t_ms": 1000}, {"t_ms": 2000}],
                               "events": [{"event": "steering_overlay_divergence_started",
                                           "time_ms": 1500, "data": {}}]})
            class TripStore:
                def detail(self, _trip_id):
                    return {"ok": True, "summary": {"started_ms": 1000,
                                                       "ended_ms": 2000}}, 200
            engine = SimulationEngine.__new__(SimulationEngine)
            engine.trip_store = TripStore()
            engine.trajectory_store = trajectories
            result, status = engine.trip_detail("trip")
            self.assertEqual(200, status)
            self.assertEqual(["native"], [item["trajectory_id"]
                                          for item in result["trajectories"]])
            self.assertEqual("steering_overlay_divergence_started",
                             result["trajectory_event_overlays"][0]["events"][0]["event"])

    def test_live_steering_correction_and_departure_keep_their_source(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TripLogStore(root=Path(directory), device_kind="head_unit")
            context = {"device_kind": "head_unit"}
            base = {"ok": True, "vehicle_speed_kmh": 20,
                    "correction_count": 0, "correction_total_m": 0,
                    "correction_abs_total_m": 0, "mode": "route"}
            store.observe(base, context)
            trip_id = store.active["id"]
            store.observe({**base, "correction_count": 1,
                           "correction_total_m": 0.6,
                           "correction_abs_total_m": 0.6,
                           "correction_mode": "steering"}, context)
            store.observe({**base, "correction_count": 1,
                           "correction_total_m": 0.6,
                           "correction_abs_total_m": 0.6,
                           "mode": "off_route_steering",
                           "fake_lat": 55.0, "fake_lon": 37.0}, context)
            events = store.detail(trip_id)[0]["events"]
            self.assertEqual(["steering_progress_correction",
                              "steering_overlay_divergence_started"],
                             [event["event"] for event in events])
            self.assertEqual(0.6, events[0]["correction_m"])
            self.assertEqual(55.0, events[1]["fake_lat"])
