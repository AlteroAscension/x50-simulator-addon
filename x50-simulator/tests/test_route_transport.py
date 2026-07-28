import base64
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path


APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
import server


class RouteTransportTest(unittest.TestCase):
    def transport(self, available=True):
        route = {
            "available": True,
            "exact_route_id": "route-1",
            "route_source": "mapkit",
            "route_generation": 4,
            "route_activation_count": 7,
            "route_activated_at_ms": 1700000000100,
            "route_identity": "identity-1",
            "length_m": 120.5,
            "exact_points": [[55.7, 37.5], [55.7005, 37.501]],
            "mapkit_route": {
                "schema": "x50.exact-route.v2",
                "captured_at_ms": 1700000000000,
                "events": [{"tags": ["POLICE"]}],
            },
        }
        raw = json.dumps(route, separators=(",", ":")).encode()
        return {
            "snapshot_id": "identity-1:7:4",
            "available": available,
            "published_at_ms": 1700000000200,
            "codec": "gzip+base64",
            "payload_b64": base64.b64encode(gzip.compress(raw)).decode(),
        }

    def test_decodes_complete_mapkit_snapshot(self):
        snapshot = server.decode_route_transport(self.transport())
        self.assertTrue(snapshot["available"])
        self.assertEqual("head_unit", snapshot["device_kind"])
        self.assertEqual("route-1", snapshot["route_id"])
        self.assertEqual(2, snapshot["point_count"])
        self.assertEqual("POLICE",
                         snapshot["mapkit_route"]["events"][0]["tags"][0])

    def test_unavailable_transport_clears_without_geometry(self):
        snapshot = server.decode_route_transport(self.transport(False))
        self.assertEqual({
            "snapshot_id": "identity-1:7:4",
            "available": False,
            "device_kind": "head_unit",
            "observed_at_ms": 1700000000200,
        }, snapshot)

    def test_registry_never_attaches_head_unit_route_to_avd(self):
        with tempfile.TemporaryDirectory() as root:
            registry = server.TripLogRegistry(root=root)
            snapshot = server.decode_route_transport(self.transport())
            registry.observe_route(snapshot, "head_unit")
            self.assertEqual(snapshot["snapshot_id"],
                             registry.stores["head_unit"].latest_route_snapshot[
                                 "snapshot_id"])
            self.assertIsNone(registry.stores["avd"].latest_route_snapshot)

    def test_head_unit_trip_contains_transported_route(self):
        with tempfile.TemporaryDirectory() as root:
            registry = server.TripLogRegistry(root=root)
            snapshot = server.decode_route_transport(self.transport())
            registry.observe_route(snapshot, "head_unit")
            registry.observe("head_unit", {
                "ok": True,
                "vehicle_speed_kmh": 12.0,
                "odometer_km": 100.0,
                "route_source": "mapkit",
                "exact_route_id": "route-1",
            }, {"journal_source": "ha_relay"})
            trips = registry.list()["trips"]
            self.assertEqual(1, len(trips))
            self.assertEqual("head_unit", trips[0]["device_kind"])
            self.assertEqual(1, trips[0]["route_snapshots"])
            detail, status = registry.detail(trips[0]["id"])
            self.assertEqual(200, status)
            self.assertEqual("identity-1:7:4",
                             detail["routes"][0]["snapshot_id"])
            self.assertEqual(2, len(detail["routes"][0]["points"]))


if __name__ == "__main__":
    unittest.main()
