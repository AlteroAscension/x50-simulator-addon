"""A partial HA upload must be readable without pretending it is complete."""

import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from server import SimulationEngine, preview_gzip_jsonl


class DiagnosticsPreviewTest(unittest.TestCase):
    def test_partial_gzip_yields_only_complete_jsonl_records(self):
        rows = [
            {"seq": 1, "type": "vehicle_sample", "time_ms": 1000, "data": {}},
            {"seq": 2, "type": "coordinate_decision", "time_ms": 1100, "data": {}},
        ]
        payload = b"\n".join(json.dumps(row).encode() for row in rows) + b"\n"
        compressed = gzip.compress(payload)
        partial = preview_gzip_jsonl(compressed[:-8])
        self.assertFalse(partial["gzip_complete"])
        self.assertEqual(2, partial["record_count"])
        self.assertEqual(["vehicle_sample", "coordinate_decision"],
                         [row["type"] for row in partial["records"]])
        self.assertTrue(preview_gzip_jsonl(compressed)["gzip_complete"])

    def test_catalog_preview_and_export_use_original_sources(self):
        journal_id = "20260928-133647-deadbeef"
        archive = gzip.compress(b'{"type":"vehicle_sample","time_ms":1000}\n')[:-8]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trip_log = root / "trip_demo.jsonl"
            summary = root / "trip_demo.json"
            trip_log.write_text('{"type":"sample","time_ms":2000}\n', encoding="utf-8")
            summary.write_text('{"id":"demo"}', encoding="utf-8")
            engine = SimulationEngine.__new__(SimulationEngine)
            engine.ha_url, engine.ha_token = "http://ha.test", "token"
            engine.trip_store = SimpleNamespace(
                list=lambda: {"trips": [{"id": "demo"}]},
                _paths=lambda item_id: (trip_log, summary),
            )

            def fake_ha_request(endpoint, method, **_kwargs):
                if endpoint.startswith("belgee_x50/trip-journals"):
                    return {"journals": [{"id": journal_id, "complete": False}]}, 200
                return {"sources": [{"source": "gateway", "installation_id": "car",
                                     "lines": ["ready"]}]}, 200

            with patch("server.ha_request", side_effect=fake_ha_request), \
                    patch("server.ha_journal_download", return_value=archive):
                catalog = engine.diagnostics_catalog()
                self.assertEqual(journal_id, catalog["journals"][0]["id"])
                self.assertEqual("gateway", catalog["log_sources"][0]["source"])
                preview, status = engine.diagnostics_preview("journal", journal_id, partial=True)
                self.assertEqual(200, status)
                self.assertEqual(1, preview["record_count"])
                self.assertFalse(preview["gzip_complete"])
                filename, _mime, content = engine.diagnostics_download(
                    "journal", journal_id, partial=True)
                self.assertEqual(journal_id + ".jsonl.gz.part", filename)
                self.assertEqual(archive, content)
                filename, _mime, content = engine.diagnostics_download("trip", "demo")
                self.assertEqual(trip_log, content)


if __name__ == "__main__":
    unittest.main()
