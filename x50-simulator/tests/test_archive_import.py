import gzip
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import server
from archive_import import parse_archive, validate_match
from archive_import import ArchiveUploads, CHUNK_SIZE

START = 1_790_000_000_000


def archive(start=START, odo=100, end=True, steering=True):
    rows = [dict(type="trip_start", time_ms=start, elapsed_ms=0,
                 data={"trip_id": "20261007-120000-0123abcd"})]
    def row(kind, at, data):
        rows.append(dict(type=kind, time_ms=start+at, elapsed_ms=at, data=data))
    row("route_snapshot", 0, dict(available=True, points=[[1, 2], [1.001, 2.001]],
        revision="one", exact_route_id="one", route_source="mapkit", mapkit_route={"events": []}))
    for i in range(4):
        row("diagnostic_tick", i*1000, dict(real_gps_quality_good=True, carlinkit_lat=1+i*.0001,
            carlinkit_lon=2, fake_lat=1+i*.0001, fake_lon=2, vehicle_speed_kmh=36,
            odometer_km=odo+i*.01, route_available=True))
        data = dict(speed_kmh=36, odometer_km=odo+i*.01)
        if steering:
            data["trajectory_point"] = dict(t_ms=start+i*1000, x_m=0, y_m=i*10,
                heading_deg=0, dist_m=i*10, segment_id=0)
        row("vehicle_sample", i*1000, data)
    row("inertial_fusion_revision", 3000, dict(segment_id=1, points=[
        dict(elapsed_ms=1000, lat=1.0001, lon=2, segment_id=1),
        dict(elapsed_ms=2000, lat=1.0002, lon=2, segment_id=1)]))
    if end:
        row("trip_end", 4000, {})
    return gzip.compress(("\n".join(json.dumps(r) for r in rows)+"\n").encode())


class ArchiveImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.engine = server.SimulationEngine.__new__(server.SimulationEngine)
        self.engine.trip_store = server.TripLogRegistry(root / "trips")
        self.engine.trajectory_store = server.TrajectoryStore(root / "traces")
        self.engine.journal_dir = root / "journals"
        self.engine.journal_dir.mkdir()
        self.engine.archive_uploads = ArchiveUploads(root / "uploads")

    def tearDown(self):
        self.temp.cleanup()

    def test_create_restart_layers_and_duplicate(self):
        result, status = self.engine.import_trip_archive(archive())
        self.assertEqual(status, 200)
        self.engine.trip_store = server.TripLogRegistry(Path(self.temp.name) / "trips")
        detail, status = self.engine.trip_detail(result["trip_id"])
        self.assertEqual(status, 200)
        self.assertEqual(len(detail["routes"]), 1)
        self.assertEqual(len(detail["samples"]), 4)
        trace = detail["trajectories"][0]
        self.assertEqual(trace["point_count"], 4)
        self.assertEqual(trace["inertial"]["points"][0]["segment_id"], 1)
        repeat, _ = self.engine.import_trip_archive(archive())
        self.assertTrue(repeat["already_imported"])
        self.assertEqual(len(self.engine.trip_store.list()["trips"]), 1)

    def existing(self, start=START, odo=100, device="head_unit", active=False):
        store = self.engine.trip_store.stores["head_unit"]
        summary = dict(id="existing", started_ms=start, ended_ms=start+4000, active=active,
                       device_kind=device, samples=1)
        store._atomic_json(store._paths("existing")[1], summary)
        store._append("existing", dict(kind="sample", time_ms=start+1000, odometer_km=odo+.01))
        return store._paths("existing")[0]

    def test_attach_keeps_original_and_create_finds_attached(self):
        log = self.existing()
        before = log.read_bytes()
        result, status = self.engine.import_trip_archive(archive(), "existing")
        self.assertEqual(status, 200)
        self.assertEqual(log.read_bytes(), before)
        self.assertEqual(result["trip_id"], "existing")
        result, status = self.engine.import_trip_archive(archive())
        self.assertEqual(result["trip_id"], "existing")
        self.assertEqual(len(self.engine.trip_store.list()["trips"]), 1)

    def test_mismatches_do_not_write(self):
        for kwargs in [dict(start=START+900000), dict(odo=200), dict(device="avd"), dict(active=True)]:
            with self.subTest(kwargs=kwargs):
                log = self.existing(**kwargs)
                before = log.read_bytes()
                result, status = self.engine.import_trip_archive(archive(), "existing")
                self.assertEqual(status, 400, result)
                self.assertEqual(log.read_bytes(), before)
                self.assertFalse(list(self.engine.trajectory_store.root.glob("*.json")))

    def test_corruption_and_expansion_limit(self):
        for raw in [b"bad", archive()[:-5], gzip.compress(b'{}\n')]:
            self.assertEqual(self.engine.import_trip_archive(raw)[1], 400)
        with patch("archive_import.MAX_EXPANDED", 20):
            self.assertEqual(self.engine.import_trip_archive(archive())[1], 400)
        self.assertFalse(self.engine.trip_store.list()["trips"])

    def test_gps_mismatch_and_complete_cannot_be_downgraded(self):
        existing = parse_archive(archive())
        existing["samples"][0]["carlinkit_lat"] = 10
        with self.assertRaisesRegex(ValueError, "GPS"):
            validate_match(existing, parse_archive(archive()))
        result, status = self.engine.import_trip_archive(archive())
        self.assertEqual(status, 200)
        self.assertEqual(self.engine.import_trip_archive(archive(end=False))[1], 400)
        detail, _ = self.engine.trip_detail(result["trip_id"])
        self.assertTrue(detail["summary"]["archive_complete"])

    def test_inertial_without_steering_and_partial(self):
        result, status = self.engine.import_trip_archive(archive(end=False, steering=False))
        self.assertEqual(status, 200)
        self.assertFalse(result["complete"])
        detail, _ = self.engine.trip_detail(result["trip_id"])
        self.assertEqual(detail["trajectories"][0]["inertial"]["point_count"], 2)

    def test_http_binary_upload_and_selected_trip_query(self):
        self.existing()
        class Handler(server.Handler):
            engine = self.engine
            def log_message(self, *_): pass
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{http.server_port}/api/controller/trips/import?trip_id=existing"
            with urlopen(Request(url, data=archive(), headers={"Content-Type": "application/gzip"})) as response:
                self.assertEqual(json.load(response)["trip_id"], "existing")
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_http_archive_larger_than_37_mib(self):
        # Uncompressed gzip blocks make the request genuinely large, without
        # storing private trip data or millions of samples in the test suite.
        padding = json.dumps(dict(type="application_log", time_ms=START,
                                  data={"message": "x" * (1024 * 1024)})).encode() + b"\n"
        raw = gzip.compress(gzip.decompress(archive()) + padding * 37, compresslevel=0)
        self.assertGreater(len(raw), 37 * 1024 * 1024)
        class Handler(server.Handler):
            engine = self.engine
            def log_message(self, *_): pass
            def do_POST(self):
                # Model the actual HA proxy body limit, rather than testing
                # only the add-on's much larger direct HTTP allowance.
                if int(self.headers.get("Content-Length", "0")) > 16 * 1024 * 1024:
                    self.reply_json({"error": "proxy body limit"}, 413)
                    return
                super().do_POST()
        http = server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{http.server_port}/api/controller/trips/import"
            def post(suffix, data):
                with urlopen(Request(url+suffix, data=data), timeout=30) as response:
                    return json.load(response)
            session = post("/start", json.dumps({"total_bytes": len(raw)}).encode())
            key = session["upload_id"]
            for offset in range(0, len(raw), session["chunk_size"]):
                data = raw[offset:offset+session["chunk_size"]]
                result = post(f"/chunk?upload_id={key}&offset={offset}", data)
                self.assertEqual(result["received_bytes"], offset+len(data))
                if offset == 0:  # Retrying a lost HTTP response must be safe.
                    self.assertEqual(post(f"/chunk?upload_id={key}&offset=0", data), result)
            result = post("/finish", json.dumps({"upload_id": key}).encode())
            self.assertTrue(result["ok"])
            self.assertEqual(result["samples"], 4)
            self.assertEqual(result["inertial_points"], 2)
            self.assertFalse(list(self.engine.archive_uploads.root.glob("*.part")))
        finally:
            http.shutdown(); http.server_close(); thread.join()

    def test_upload_order_limits_expiry_and_cleanup(self):
        uploads = self.engine.archive_uploads
        key = uploads.start(4)["upload_id"]
        with self.assertRaises(ValueError): uploads.append(key, 2, b"ab")
        uploads.append(key, 0, b"ab")
        with self.assertRaises(ValueError): uploads.append(key, 0, b"xx")
        with self.assertRaises(ValueError): uploads.finish(key)
        with self.assertRaises(ValueError): uploads.append(key, 2, b"x"*(CHUNK_SIZE+1))
        uploads.append(key, 2, b"cd")
        ready, target = uploads.finish(key)
        self.assertEqual(ready.read_bytes(), b"abcd")
        self.assertIsNone(target)
        # Complete uploads survive a restart until their original is retained.
        ArchiveUploads(uploads.root)
        self.assertTrue(ready.exists())
        ready.unlink()
        key = uploads.start(4)["upload_id"]
        uploads.sessions[key]["touched"] -= 901
        with self.assertRaises(ValueError): uploads.append(key, 0, b"ab")
        self.assertFalse(list(uploads.root.glob("*.part")))
        keys = [uploads.start(4)["upload_id"] for _ in range(2)]
        with self.assertRaises(ValueError): uploads.start(4)
        for key in keys: uploads.cancel(key)
        self.assertFalse(list(uploads.root.glob("*.part")))

    def test_original_is_retained_if_processing_fails(self):
        raw = archive()
        with patch("server.parse_archive", side_effect=ValueError("processing failed")):
            self.assertEqual(self.engine.import_trip_archive(raw)[1], 400)
        copies = list(self.engine.journal_dir.glob("uploaded-*.jsonl.gz"))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_bytes(), raw)
        result, status = self.engine.import_trip_archive(copies[0])
        self.assertEqual(status, 200)
        self.assertEqual(result["samples"], 4)
        self.assertFalse(list(self.engine.journal_dir.glob("uploaded-*.jsonl.gz")))

    def test_samples_do_not_duplicate_diagnostic_logs(self):
        rows = gzip.decompress(archive()).decode().splitlines()
        for i, line in enumerate(rows):
            record = json.loads(line)
            if record["type"] == "diagnostic_tick":
                record["data"]["audit_recent_events"] = [{"message": "diagnostic-only"}]
                record["data"]["inertial_trajectory"] = {"last_step": {"after_lat": 1, "after_lon": 2}, "legacy": {"large": "diagnostic"}}
                rows[i] = json.dumps(record)
        imported = parse_archive(gzip.compress(("\n".join(rows)+"\n").encode()))
        sample = imported["samples"][0]
        self.assertNotIn("audit_recent_events", sample)
        self.assertNotIn("inertial_trajectory", sample)
        self.assertEqual(sample["inertial_step"]["after_lat"], 1)
        self.assertEqual(sample["carlinkit_lat"], 1)


if __name__ == "__main__":
    unittest.main()
