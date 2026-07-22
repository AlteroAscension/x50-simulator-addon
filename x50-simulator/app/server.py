#!/usr/bin/env python3
"""Async browser controller and time-synchronised navigation simulator."""

from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from collections import deque
import argparse
import json
import math
import os
import socket
import subprocess
import threading
import time
import uuid


ROOT = Path(__file__).parent
GATEWAY = os.environ.get("X50_GATEWAY_URL", "http://127.0.0.1:8080")
STATIC_FILES = {"/": "index.html", "/index.html": "index.html",
                "/app.js": "app.js", "/styles.css": "styles.css"}


def gateway_request(path: str, method: str = "GET", data=None, token="x50test", base_url=None):
    target_base = (base_url or GATEWAY).rstrip('/')
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-X50-Token"] = token
    request = Request(target_base + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=4) as response:
            payload = response.read()
            return json.loads(payload), response.status
    except HTTPError as error:
        payload = error.read()
        try:
            return json.loads(payload), error.code
        except ValueError:
            return {"ok": False, "error": "gateway_http", "detail": payload.decode(errors="replace")}, error.code
    except Exception as error:
        return {"ok": False, "error": "gateway_unreachable", "detail": str(error)}, 502


def ha_request(endpoint: str, method: str = "POST", data=None, ha_url=None, ha_token=None):
    base = (ha_url or os.environ.get("HA_URL", "http://supervisor/core")).rstrip('/')
    token = ha_token or os.environ.get("SUPERVISOR_TOKEN", "")
    body = None if data is None else json.dumps(data).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"{base}/api/{endpoint.lstrip('/')}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=5) as response:
            payload = response.read()
            return json.loads(payload) if payload else {"ok": True}, response.status
    except Exception as error:
        return {"ok": False, "error": "ha_unreachable", "detail": str(error)}, 502


def clamp(value, low, high):
    return max(low, min(high, float(value)))


def haversine_m(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * math.atan2(math.sqrt(h), math.sqrt(max(0.0, 1 - h)))


def bearing_deg(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def finite_number(value, default=None):
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


class TripLogStore:
    """Persistent 1 Hz trip journal with explicit GPS correction events."""

    SAMPLE_INTERVAL_S = 1.0
    STOP_TIMEOUT_S = 180.0

    def __init__(self, root=None):
        requested = Path(root or os.environ.get("X50_TRIP_DIR", "/data/x50-trips"))
        try:
            requested.mkdir(parents=True, exist_ok=True)
            self.root = requested
        except OSError:
            self.root = ROOT / ".x50-trips"
            self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.last_sample_monotonic = 0.0
        self.last_observe_monotonic = None
        self.last_moving_monotonic = None
        self.last_correction_count = None
        self.last_correction_total_m = None
        self.last_correction_abs_total_m = None
        self.last_recovery_correction_count = None
        self.last_gps_good = None
        self.outage = None

    @staticmethod
    def _gps_age(data):
        return finite_number(data.get("real_gps_age_ms"), finite_number(data.get("carlinkit_fix_age_ms")))

    @classmethod
    def _gps_good(cls, data):
        if data.get("real_gps_quality_good") is not None:
            return bool(data.get("real_gps_quality_good"))
        age = cls._gps_age(data)
        accuracy = finite_number(data.get("carlinkit_accuracy_m"))
        return (age is not None and age <= 3000
                and accuracy is not None and 0 < accuracy <= 35
                and data.get("carlinkit_lat") is not None)

    def _paths(self, trip_id):
        return self.root / (trip_id + ".jsonl"), self.root / (trip_id + ".summary.json")

    @staticmethod
    def _atomic_json(path, payload):
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(path)

    def _append(self, trip_id, record):
        log_path, _ = self._paths(trip_id)
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _start(self, now_ms, data, context):
        trip_id = time.strftime("%Y%m%d-%H%M%S", time.localtime(now_ms / 1000)) + "-" + uuid.uuid4().hex[:6]
        odometer = finite_number(data.get("odometer_km"), finite_number(context.get("odometer_km")))
        progress = finite_number(data.get("progress_m"), finite_number(context.get("route_progress_m")))
        self.active = {
            "id": trip_id, "active": True, "started_ms": now_ms, "ended_ms": None,
            "duration_s": 0.0, "start_odometer_km": odometer, "end_odometer_km": odometer,
            "distance_odometer_m": 0.0, "distance_integrated_m": 0.0,
            "start_progress_m": progress, "end_progress_m": progress,
            "route_id": data.get("exact_route_id") or context.get("exact_route_id") or "",
            "route_source": data.get("route_source") or context.get("route_source") or "none",
            "journal_source": context.get("journal_source", "gateway_direct"),
            "samples": 0, "correction_events": 0, "gps_outages": 0,
            "correction_operations": 0, "recovery_corrections": 0,
            "correction_total_m": 0.0, "correction_abs_total_m": 0.0,
            "max_forward_correction_m": 0.0, "max_backward_correction_m": 0.0,
            "max_speed_kmh": 0.0, "finish_reason": None,
        }
        self.last_correction_count = int(finite_number(data.get("correction_count"), 0) or 0)
        self.last_correction_total_m = finite_number(data.get("correction_total_m"))
        self.last_correction_abs_total_m = finite_number(data.get("correction_abs_total_m"))
        self.last_recovery_correction_count = int(
            finite_number(data.get("recovery_correction_count"), 0) or 0)
        self.last_gps_good = self._gps_good(data)
        self.outage = None
        if not self.last_gps_good:
            self._begin_outage(now_ms, data, context)
        self._append(trip_id, {"kind": "trip_start", "time_ms": now_ms,
                               "route_id": self.active["route_id"], "route_source": self.active["route_source"]})
        self._save_summary()

    def _save_summary(self):
        if self.active:
            _, summary_path = self._paths(self.active["id"])
            self._atomic_json(summary_path, self.active)

    def _begin_outage(self, now_ms, data, context):
        self.outage = {
            "started_ms": now_ms,
            "odometer_km": finite_number(data.get("odometer_km"), finite_number(context.get("odometer_km"))),
            "progress_m": finite_number(data.get("progress_m"), finite_number(context.get("route_progress_m"))),
            "route_match_progress_m": finite_number(data.get("route_match_progress_m")),
            "integrated_m": 0.0,
            "correction_total_m": finite_number(data.get("correction_total_m")),
        }

    def _event_base(self, now_ms, data, context):
        fields = (
            "vehicle_speed_kmh", "corrected_speed_kmh", "carlinkit_speed_kmh", "odometer_km",
            "odometer_delta_m", "corrected_delta_m", "distance_factor", "speed_factor",
            "progress_m", "route_match_progress_m", "route_match_distance_m", "gps_gap_m",
            "real_gps_age_ms", "carlinkit_fix_age_ms", "carlinkit_accuracy_m",
            "real_gps_received_age_ms", "real_gps_fix_time_ms", "real_gps_quality_good",
            "last_progress_correction_m", "last_correction_weight", "correction_count",
            "correction_target_progress_m", "correction_prediction_m", "correction_fix_age_ms",
            "correction_fix_time_ms", "correction_mode", "correction_total_m",
            "correction_abs_total_m", "recovery_correction_count", "gps_recovery_pending",
            "gps_recovery_candidate_fixes", "gps_outage_age_ms", "last_gps_recovery_outage_ms",
            "tick_raw_dt_ms", "tick_max_raw_dt_ms", "tick_discarded_time_ms",
            "rejected_corrections", "progress_source", "carlinkit_lat", "carlinkit_lon",
            "fake_lat", "fake_lon")
        event = {"kind": "event", "time_ms": now_ms}
        for name in fields:
            if name in data:
                event[name] = data.get(name)
        event.update({
            "simulator_running": bool(context.get("running")),
            "target_speed_kmh": context.get("target_speed_kmh"),
            "simulator_odometer_km": context.get("odometer_km"),
            "measured_gps_speed_kmh": context.get("measured_gps_speed_kmh"),
            "gateway_output_progress_m": context.get("gateway_output_progress_m"),
            "gateway_output_gap_m": context.get("gateway_output_gap_m"),
        })
        return event

    def _record_correction(self, now_ms, data, context, previous_count, current_count):
        batch = max(1, current_count - previous_count)
        current_total = finite_number(data.get("correction_total_m"))
        current_abs_total = finite_number(data.get("correction_abs_total_m"))
        last_single = finite_number(data.get("last_progress_correction_m"), 0.0) or 0.0
        correction = last_single
        correction_abs = abs(correction)
        if current_total is not None and self.last_correction_total_m is not None:
            correction = current_total - self.last_correction_total_m
        if current_abs_total is not None and self.last_correction_abs_total_m is not None:
            correction_abs = max(0.0, current_abs_total - self.last_correction_abs_total_m)
        event = self._event_base(now_ms, data, context)
        event.update({"event": "gps_progress_correction", "correction_m": correction,
                      "correction_abs_m": correction_abs,
                      "last_single_correction_m": last_single,
                      "corrections_since_sample": batch})
        self._append(self.active["id"], event)
        self.active["correction_events"] += 1
        self.active["correction_operations"] += batch
        current_recovery_count = int(
            finite_number(data.get("recovery_correction_count"),
                          self.last_recovery_correction_count or 0) or 0)
        if self.last_recovery_correction_count is not None:
            self.active["recovery_corrections"] += max(
                0, current_recovery_count - self.last_recovery_correction_count)
        self.active["correction_total_m"] += correction
        self.active["correction_abs_total_m"] += correction_abs
        self.active["max_forward_correction_m"] = max(self.active["max_forward_correction_m"], last_single)
        self.active["max_backward_correction_m"] = min(self.active["max_backward_correction_m"], last_single)
        self.last_correction_total_m = current_total
        self.last_correction_abs_total_m = current_abs_total
        self.last_recovery_correction_count = current_recovery_count

    def _finish_outage(self, now_ms, data, context):
        if not self.outage:
            return
        event = self._event_base(now_ms, data, context)
        current_odo = finite_number(data.get("odometer_km"), finite_number(context.get("odometer_km")))
        current_progress = finite_number(data.get("progress_m"), finite_number(context.get("route_progress_m")))
        match_progress = finite_number(data.get("route_match_progress_m"))
        start_odo = self.outage.get("odometer_km")
        start_progress = self.outage.get("progress_m")
        current_correction_total = finite_number(data.get("correction_total_m"))
        outage_correction_total = self.outage.get("correction_total_m")
        correction = (None if current_correction_total is None or outage_correction_total is None
                      else current_correction_total - outage_correction_total)
        target_progress = finite_number(data.get("correction_target_progress_m"), match_progress)
        residual = (None if target_progress is None or current_progress is None
                    else target_progress - current_progress)
        event.update({
            "event": "gps_reacquired", "outage_started_ms": self.outage["started_ms"],
            "outage_duration_s": round((now_ms - self.outage["started_ms"]) / 1000.0, 3),
            "odometer_before_outage_km": start_odo,
            "odometer_at_reacquisition_km": current_odo,
            "distance_by_odometer_m": (None if current_odo is None or start_odo is None
                                        else round((current_odo - start_odo) * 1000.0, 3)),
            "distance_by_speed_integral_m": round(self.outage["integrated_m"], 3),
            "progress_before_outage_m": start_progress,
            "progress_at_reacquisition_m": current_progress,
            "progress_during_outage_m": (None if current_progress is None or start_progress is None
                                          else round(current_progress - start_progress, 3)),
            "gps_catch_up_m": None if correction is None else round(correction, 3),
            "gps_residual_m": None if residual is None else round(residual, 3),
            "gps_recovery_mode": data.get("correction_mode"),
        })
        self._append(self.active["id"], event)
        self.active["gps_outages"] += 1
        self.outage = None

    def _sample(self, now_ms, data, context):
        keys = (
            "enabled", "mode", "reason", "vehicle_speed_kmh", "corrected_speed_kmh", "speed_factor",
            "odometer_km", "odometer_delta_m", "corrected_delta_m", "distance_factor",
            "distance_calibration_count", "progress_source", "route_length_m", "progress_m",
            "route_match_progress_m", "route_match_distance_m", "last_progress_correction_m",
            "last_correction_weight", "correction_target_progress_m", "correction_prediction_m",
            "correction_fix_age_ms", "correction_fix_time_ms", "correction_mode",
            "correction_total_m", "correction_abs_total_m", "recovery_correction_count",
            "gps_recovery_pending", "gps_recovery_candidate_fixes", "gps_outage_age_ms",
            "last_gps_recovery_outage_ms", "real_gps_age_ms", "real_gps_received_age_ms",
            "real_gps_fix_time_ms", "real_gps_quality_good", "carlinkit_fix_age_ms", "carlinkit_lat",
            "carlinkit_lon", "carlinkit_accuracy_m", "carlinkit_speed_kmh", "carlinkit_bearing",
            "gps_gap_m", "correction_count", "rejected_corrections", "injected_count",
            "tick_raw_dt_ms", "tick_max_raw_dt_ms", "tick_discarded_time_ms",
            "fake_lat", "fake_lon", "exact_route_id", "route_source")
        sample = {"kind": "sample", "time_ms": now_ms, "gps_good": self._gps_good(data)}
        for name in keys:
            if name in data:
                sample[name] = data.get(name)
        sample["simulator"] = {name: context.get(name) for name in (
            "running", "target_speed_kmh", "gps_speed_kmh", "vehicle_speed_kmh",
            "measured_gps_speed_kmh", "odometer_km", "route_progress_m",
            "gateway_output_progress_m", "gateway_output_gap_m")}
        self._append(self.active["id"], sample)
        self.active["samples"] += 1

    def observe(self, data, context):
        if not isinstance(data, dict) or not data.get("ok", True):
            return
        now_mono = time.monotonic()
        now_ms = int(time.time() * 1000)
        speed = finite_number(data.get("vehicle_speed_kmh"),
                              finite_number(context.get("vehicle_speed_kmh"), 0.0)) or 0.0
        moving = speed >= 1.0 or (bool(context.get("running")) and
                                  (finite_number(context.get("target_speed_kmh"), 0.0) or 0.0) >= 1.0)
        with self.lock:
            if self.active is None:
                if not moving:
                    self.last_observe_monotonic = now_mono
                    self.last_gps_good = self._gps_good(data)
                    return
                self._start(now_ms, data, context)
            dt = 0.0 if self.last_observe_monotonic is None else max(0.0, min(5.0, now_mono - self.last_observe_monotonic))
            self.last_observe_monotonic = now_mono
            if moving:
                self.last_moving_monotonic = now_mono
            elif self.last_moving_monotonic is not None and now_mono - self.last_moving_monotonic >= self.STOP_TIMEOUT_S:
                self.finish("stationary_timeout")
                return
            integrated = speed / 3.6 * dt
            self.active["distance_integrated_m"] += integrated
            if self.outage is not None:
                self.outage["integrated_m"] += integrated
            gps_good = self._gps_good(data)
            if self.last_gps_good is True and not gps_good:
                self._begin_outage(now_ms, data, context)
            elif self.last_gps_good is False and gps_good:
                self._finish_outage(now_ms, data, context)
            self.last_gps_good = gps_good
            correction_count = int(finite_number(data.get("correction_count"), self.last_correction_count or 0) or 0)
            if self.last_correction_count is not None and correction_count > self.last_correction_count:
                self._record_correction(now_ms, data, context, self.last_correction_count, correction_count)
            elif self.last_correction_count is not None and correction_count < self.last_correction_count:
                self.last_correction_total_m = finite_number(data.get("correction_total_m"))
                self.last_correction_abs_total_m = finite_number(data.get("correction_abs_total_m"))
                self.last_recovery_correction_count = int(
                    finite_number(data.get("recovery_correction_count"), 0) or 0)
            self.last_correction_count = correction_count
            odometer = finite_number(data.get("odometer_km"), finite_number(context.get("odometer_km")))
            progress = finite_number(data.get("progress_m"), finite_number(context.get("route_progress_m")))
            self.active["ended_ms"] = now_ms
            self.active["duration_s"] = round((now_ms - self.active["started_ms"]) / 1000.0, 1)
            self.active["end_odometer_km"] = odometer
            self.active["end_progress_m"] = progress
            if odometer is not None and self.active["start_odometer_km"] is not None:
                self.active["distance_odometer_m"] = round((odometer - self.active["start_odometer_km"]) * 1000.0, 3)
            self.active["distance_integrated_m"] = round(self.active["distance_integrated_m"], 3)
            self.active["max_speed_kmh"] = max(self.active["max_speed_kmh"], speed)
            if now_mono - self.last_sample_monotonic >= self.SAMPLE_INTERVAL_S:
                self.last_sample_monotonic = now_mono
                self._sample(now_ms, data, context)
                self._save_summary()

    def finish(self, reason="manual"):
        with self.lock:
            if not self.active:
                return {"ok": True, "trip": None}
            now_ms = int(time.time() * 1000)
            self.active.update({"active": False, "ended_ms": now_ms,
                                "duration_s": round((now_ms - self.active["started_ms"]) / 1000.0, 1),
                                "finish_reason": reason})
            self._append(self.active["id"], {"kind": "trip_end", "time_ms": now_ms, "reason": reason})
            self._save_summary()
            finished = dict(self.active)
            self.active = None
            self.outage = None
            self.last_moving_monotonic = None
            return {"ok": True, "trip": finished}

    def list(self):
        with self.lock:
            trips = []
            for path in self.root.glob("*.summary.json"):
                try:
                    trips.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
            trips.sort(key=lambda item: item.get("started_ms", 0), reverse=True)
            return {"ok": True, "active_trip_id": self.active.get("id") if self.active else None,
                    "storage_path": str(self.root), "trips": trips}

    def detail(self, trip_id):
        if not trip_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in trip_id):
            return {"ok": False, "error": "invalid_trip_id"}, 400
        log_path, summary_path = self._paths(trip_id)
        if not summary_path.exists():
            return {"ok": False, "error": "trip_not_found"}, 404
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        samples, events = [], []
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("kind") == "sample":
                    samples.append(record)
                elif record.get("kind") == "event":
                    events.append(record)
        return {"ok": True, "summary": summary, "samples": samples, "events": events}, 200


class LiveRouteHook:
    """Keeps the read-only MapKit capture agent attached to Navigator."""

    def __init__(self, adb: str, device: str, agent_path: str | None):
        self.adb = adb
        self.device = device
        self.agent_path = Path(agent_path) if agent_path else None
        self.lock = threading.RLock()
        self.closed = False
        self.online = False
        self.pid = None
        self.last_error = None
        self.attach_count = 0
        self.thread = None
        if self.agent_path and self.agent_path.exists():
            self.thread = threading.Thread(target=self._run, name="X50-LiveRouteHook", daemon=True)
            self.thread.start()
        else:
            self.last_error = "compiled_route_agent_missing"

    def close(self):
        self.closed = True

    def state(self):
        with self.lock:
            return {
                "enabled": self.agent_path is not None,
                "online": self.online,
                "pid": self.pid,
                "attach_count": self.attach_count,
                "last_error": self.last_error,
            }

    def _navigator_pid(self):
        result = subprocess.run(
            [self.adb, "-s", self.device, "shell", "pidof", "ru.yandex.yandexnavi"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        value = result.stdout.strip().split()
        return int(value[0]) if value else None

    def _run(self):
        session = None
        script = None
        while not self.closed:
            detached = threading.Event()
            try:
                import frida

                subprocess.run(
                    [self.adb, "-s", self.device, "forward", "tcp:27042", "tcp:27042"],
                    capture_output=True, timeout=3, check=True,
                )
                pid = self._navigator_pid()
                if pid is None:
                    raise RuntimeError("navigator_not_running")
                remote = frida.get_device_manager().add_remote_device("127.0.0.1:27042")
                session = remote.attach(pid)
                session.on("detached", lambda *_args: detached.set())
                script = session.create_script(self.agent_path.read_text(encoding="utf-8"))
                script.on("message", self._on_message)
                script.load()
                with self.lock:
                    self.online = True
                    self.pid = pid
                    self.attach_count += 1
                    self.last_error = None
                while not self.closed and not detached.wait(1.0):
                    if self._navigator_pid() != pid:
                        break
            except Exception as error:
                with self.lock:
                    self.online = False
                    self.pid = None
                    self.last_error = str(error)
                time.sleep(2.0)
            finally:
                try:
                    if script is not None:
                        script.unload()
                except Exception:
                    pass
                try:
                    if session is not None:
                        session.detach()
                except Exception:
                    pass
                script = None
                session = None
                with self.lock:
                    self.online = False
                    self.pid = None

    def _on_message(self, message, _data):
        if message.get("type") != "send":
            return
        payload = message.get("payload")
        if isinstance(payload, dict) and payload.get("event") == "capture_error":
            with self.lock:
                self.last_error = payload.get("error", "capture_error")


class EmulatorGeoConsole:
    """Persistent emulator console connection; avoids adb process jitter per fix."""

    def __init__(self, adb: str, device: str, transport: str = "console"):
        self.adb = adb
        self.device = device
        self.transport = transport
        self.port = int(device.rsplit("-", 1)[-1]) if device.startswith("emulator-") else None
        self.connection = None

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            except OSError:
                pass
        self.connection = None

    def _read_response(self):
        chunks = bytearray()
        while len(chunks) < 65536:
            part = self.connection.recv(4096)
            if not part:
                raise OSError("emulator console closed")
            chunks.extend(part)
            if chunks.endswith(b"OK\r\n") or b"\r\nKO:" in chunks:
                break
        response = chunks.decode("utf-8", errors="replace")
        if "KO:" in response:
            raise OSError(response.strip())
        return response

    def _connect(self):
        if self.port is None:
            raise OSError("not an emulator device")
        self.connection = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        self.connection.settimeout(2)
        greeting = self._read_response()
        if "Authentication required" in greeting:
            token_path = Path.home() / ".emulator_console_auth_token"
            token = token_path.read_text(encoding="utf-8").strip()
            self.connection.sendall(("auth " + token + "\r\n").encode("ascii"))
            self._read_response()

    def fix(self, longitude: float, latitude: float):
        if self.transport == "http":
            bridge_url = os.environ.get("X50_GEO_BRIDGE_URL", "").rstrip("/")
            if not bridge_url:
                raise OSError("X50_GEO_BRIDGE_URL is not configured")
            body = json.dumps({"latitude": latitude, "longitude": longitude}).encode("utf-8")
            request = Request(
                bridge_url + "/geo", data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "X-X50-Token": os.environ.get("X50_GEO_BRIDGE_TOKEN", "x50test")})
            sent_at = time.monotonic()
            try:
                with urlopen(request, timeout=3) as response:
                    result = json.loads(response.read() or b"{}")
            except HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise OSError("GPS bridge HTTP {}: {}".format(error.code, detail))
            except Exception as error:
                raise OSError("GPS bridge unavailable: {}".format(error))
            if not result.get("ok"):
                raise OSError(result.get("detail") or result.get("error") or "GPS bridge rejected fix")
            return sent_at
        if self.transport == "adb":
            sent_at = time.monotonic()
            result = subprocess.run(
                [self.adb, "-s", self.device, "emu", "geo", "fix",
                 "{:.8f}".format(longitude), "{:.8f}".format(latitude)],
                capture_output=True, text=True, timeout=3, check=False)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "adb emu geo fix failed").strip()
                raise OSError(detail)
            return sent_at
        last_error = None
        for _ in range(2):
            try:
                if self.connection is None:
                    self._connect()
                command = "geo fix {:.8f} {:.8f}\r\n".format(longitude, latitude)
                self.connection.sendall(command.encode("ascii"))
                sent_at = time.monotonic()
                self._read_response()
                return sent_at
            except (OSError, ValueError) as error:
                last_error = error
                self.close()
        raise OSError(str(last_error))


class SimulationEngine:
    def __init__(self, adb: str, device: str):
        self.adb = adb
        self.device = device
        self.geo_console = EmulatorGeoConsole(
            adb, device, os.environ.get("X50_GEO_TRANSPORT", "console"))
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.closed = False
        self.running = False
        self.gps_mode = "fixed"
        self.target_speed_kmh = 0.0
        self.vehicle_speed_scale = 1.025
        self.odometer_scale = 1.025
        self.gps_speed_scale = 1.0
        self.gps_hz = 5.0
        self.odometer_km = 0.0
        self.selected = None
        self.route_points = []
        self.display_route_points = []
        self.raw_route_points = []
        self.guidance_route_points = []
        self.history_route_points = []
        self.stale_history_route_points = []
        self.exact_route_points = []
        self.route_source = "none"
        self.exact_route_fresh = False
        self.exact_route_available = False
        self.exact_route_id = ""
        self.exact_route_phase = ""
        self.mapkit_route = {}
        self.route_source_revision = ""
        self.history_fresh = True
        self.history_time_fresh = True
        self.history_match_ratio = 0.0
        self.guidance_source_modified_ms = 0
        self.history_source_modified_ms = 0
        self.route_distances = []
        self.route_length_m = 0.0
        self.route_revision = 0
        self.route_imported = False
        self.route_progress_m = 0.0
        self.route_changed_at = 0
        self.fake_nav = {}
        self.gateway_online = False
        self.last_error = None
        self.last_sent = None
        self.last_raw = None
        self.last_send_epoch_ms = None
        self.last_send_monotonic = None
        self.last_measured_speed_kmh = 0.0
        self.last_latency_ms = None
        self.latency_ema_ms = 45.0
        self.effective_hz = 0.0
        self.sent_count = 0
        self.failed_count = 0
        self.pending_send = False
        self.gateway_fake_point = None
        self.gateway_fake_anchor_progress = 0.0
        self.gateway_fake_anchor_time = None
        self.gateway_output_progress = None
        self.gateway_output_time = None
        self.gateway_output_gap_m = None
        self.gateway_hard_reanchor_count = 0
        self.gateway_last_reanchor_reason = None
        self.last_gateway_fake_active = False
        self.speed_window = deque()
        self.trace = deque(maxlen=600)
        self.token = os.environ.get("X50_TOKEN", "x50test")
        self.gateway_url = GATEWAY
        self.gateway_mode = os.environ.get("X50_GATEWAY_MODE", "direct")
        self.ha_url = os.environ.get("HA_URL", "http://supervisor/core")
        self.ha_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.settings_path = Path(os.environ.get(
            "X50_SETTINGS_PATH", "/data/x50-controller-settings.json"))
        self._load_settings()
        self.force_route_anchor_pending = False
        self.route_hook = LiveRouteHook(adb, device, os.environ.get("X50_ROUTE_AGENT"))
        self.trip_store = TripLogStore()
        self._last_integrate = time.monotonic()
        self._next_send = self._last_integrate
        self._next_route_poll = 0.0
        self._next_status_poll = 0.0
        self.thread = threading.Thread(target=self._run, name="X50-SimulationEngine", daemon=True)
        self.poll_thread = threading.Thread(target=self._poll_run, name="X50-GatewayPoll", daemon=True)
        self.thread.start()
        self.poll_thread.start()

    def close(self):
        self.closed = True
        self.geo_console.close()
        self.route_hook.close()
        self.trip_store.finish("addon_shutdown")
        self.wake.set()

    def update(self, data):
        with self.lock:
            settings_changed = False
            if "running" in data:
                self.running = bool(data["running"])
                self.pending_send = True
            if data.get("send_now"):
                self.pending_send = True
            if "gps_mode" in data and data["gps_mode"] in ("fixed", "route"):
                self.gps_mode = data["gps_mode"]
                self.pending_send = True
            if "target_speed_kmh" in data:
                self.target_speed_kmh = clamp(data["target_speed_kmh"], 0, 240)
            if "vehicle_speed_scale" in data:
                self.vehicle_speed_scale = clamp(data["vehicle_speed_scale"], 0.5, 1.5)
            if "odometer_scale" in data:
                self.odometer_scale = clamp(data["odometer_scale"], 0.5, 1.5)
            if "gps_speed_scale" in data:
                self.gps_speed_scale = clamp(data["gps_speed_scale"], 0.5, 1.5)
            if "gps_hz" in data:
                self.gps_hz = clamp(data["gps_hz"], 1, 10)
            if "odometer_km" in data:
                self.odometer_km = clamp(data["odometer_km"], 0, 9999999)
            if "route_progress_m" in data:
                self.route_progress_m = clamp(data["route_progress_m"], 0, max(0, self.route_length_m))
                self.pending_send = True
            if "token" in data and str(data["token"]).strip():
                self.token = str(data["token"]).strip()
                settings_changed = True
            if "gateway_url" in data and str(data["gateway_url"]).strip():
                url = str(data["gateway_url"]).strip().rstrip('/')
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "http://" + url
                self.gateway_url = url
                settings_changed = True
            if "gateway_mode" in data and str(data["gateway_mode"]) in ("direct", "ha"):
                self.gateway_mode = str(data["gateway_mode"])
                settings_changed = True
            if "ha_url" in data and str(data["ha_url"]).strip():
                url = str(data["ha_url"]).strip().rstrip('/')
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "http://" + url
                self.ha_url = url
                settings_changed = True
            # The Supervisor token is injected by Home Assistant and must
            # never be accepted from or returned to the browser.
            if "latitude" in data and "longitude" in data:
                lat = clamp(data["latitude"], -90, 90)
                lon = clamp(data["longitude"], -180, 180)
                self.selected = (lat, lon)
                self.last_sent = None
                self.last_send_monotonic = None
                if self.route_points:
                    self.route_progress_m = self._project_progress(self.selected)
                # A map click intentionally teleports the AVD. The next GPS
                # request must reset Gateway's physical jump filter and
                # re-anchor fake progress to the selected route position.
                self.force_route_anchor_pending = True
                self.pending_send = True
            self._next_send = min(self._next_send, time.monotonic())
            if settings_changed:
                self._save_settings_locked()
        self.wake.set()
        return self.state()

    def state(self):
        with self.lock:
            actual_speed = self.target_speed_kmh * self.gps_speed_scale if self.running else 0.0
            vehicle_speed = self.target_speed_kmh * self.vehicle_speed_scale if self.running else 0.0
            return {
                "ok": True,
                "running": self.running,
                "gps_mode": self.gps_mode,
                "target_speed_kmh": round(self.target_speed_kmh, 3),
                "gps_speed_kmh": round(actual_speed, 3),
                "vehicle_speed_kmh": round(vehicle_speed, 3),
                "measured_gps_speed_kmh": round(self.last_measured_speed_kmh, 3),
                "speed_error_kmh": round(self.last_measured_speed_kmh - actual_speed, 3),
                "vehicle_speed_scale": self.vehicle_speed_scale,
                "odometer_scale": self.odometer_scale,
                "gps_speed_scale": self.gps_speed_scale,
                "gps_hz": self.gps_hz,
                "effective_hz": round(self.effective_hz, 2),
                "odometer_km": round(self.odometer_km, 6),
                "selected": None if self.selected is None else {"lat": self.selected[0], "lon": self.selected[1]},
                "last_raw": None if self.last_raw is None else {"lat": self.last_raw[0], "lon": self.last_raw[1]},
                "last_sent": None if self.last_sent is None else {"lat": self.last_sent[0], "lon": self.last_sent[1]},
                "last_send_epoch_ms": self.last_send_epoch_ms,
                "latency_ms": self.last_latency_ms,
                "latency_ema_ms": round(self.latency_ema_ms, 1),
                "sent_count": self.sent_count,
                "failed_count": self.failed_count,
                "gateway_online": self.gateway_online,
                "gateway_url": self.gateway_url,
                "gateway_mode": self.gateway_mode,
                "ha_url": self.ha_url,
                "last_error": self.last_error,
                "route_available": bool(self.route_points),
                "route_revision": self.route_revision,
                "route_length_m": round(self.route_length_m, 1),
                "route_progress_m": round(self.route_progress_m, 1),
                "route_imported": self.route_imported,
                "route_source": self.route_source,
                "exact_route_fresh": self.exact_route_fresh,
                "exact_route_available": self.exact_route_available,
                "exact_route_id": self.exact_route_id,
                "exact_route_phase": self.exact_route_phase,
                "gateway_output_progress_m": (None if self.gateway_output_progress is None
                                              else round(self.gateway_output_progress, 1)),
                "gateway_output_gap_m": (None if self.gateway_output_gap_m is None
                                         else round(self.gateway_output_gap_m, 1)),
                "gateway_hard_reanchor_count": self.gateway_hard_reanchor_count,
                "gateway_last_reanchor_reason": self.gateway_last_reanchor_reason,
                "route_hook": self.route_hook.state(),
                "fake_nav": self.fake_nav,
            }

    def _load_settings(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        token = str(data.get("token", "")).strip()
        gateway_url = str(data.get("gateway_url", "")).strip()
        gateway_mode = str(data.get("gateway_mode", "")).strip()
        ha_url = str(data.get("ha_url", "")).strip()
        if token:
            self.token = token
        if gateway_url.startswith(("http://", "https://")):
            self.gateway_url = gateway_url.rstrip("/")
        if gateway_mode in ("direct", "ha"):
            self.gateway_mode = gateway_mode
        if ha_url.startswith(("http://", "https://")):
            self.ha_url = ha_url.rstrip("/")

    def _save_settings_locked(self):
        payload = {
            "token": self.token,
            "gateway_url": self.gateway_url,
            "gateway_mode": self.gateway_mode,
            "ha_url": self.ha_url,
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            temporary.replace(self.settings_path)
        except OSError as error:
            self.last_error = "settings_save_failed: " + str(error)

    def route_state(self):
        with self.lock:
            return {"ok": True, "available": bool(self.route_points),
                    "revision": self.route_revision, "length_m": round(self.route_length_m, 1),
                    "progress_m": round(self.route_progress_m, 1),
                    "points": self.display_route_points or self.route_points,
                    "raw_points": self.raw_route_points,
                    "guidance_points": self.guidance_route_points,
                    "history_points": self.history_route_points,
                    "stale_history_points": self.stale_history_route_points,
                    "exact_points": self.exact_route_points,
                    "exact_point_count": len(self.exact_route_points),
                    "route_source": self.route_source,
                    "exact_fresh": self.exact_route_fresh,
                    "exact_available": self.exact_route_available,
                    "exact_route_id": self.exact_route_id,
                    "exact_phase": self.exact_route_phase,
                    "mapkit_route": self.mapkit_route,
                    "history_fresh": self.history_fresh,
                    "history_time_fresh": self.history_time_fresh,
                    "history_match_ratio": self.history_match_ratio,
                    "guidance_source_modified_ms": self.guidance_source_modified_ms,
                    "history_source_modified_ms": self.history_source_modified_ms,
                    "source_revision": self.route_source_revision}

    def trace_state(self):
        with self.lock:
            return {"ok": True, "samples": list(self.trace)}

    def _trip_context(self):
        with self.lock:
            return {
                "running": self.running,
                "target_speed_kmh": round(self.target_speed_kmh, 3),
                "gps_speed_kmh": round(self.target_speed_kmh * self.gps_speed_scale if self.running else 0.0, 3),
                "vehicle_speed_kmh": round(self.target_speed_kmh * self.vehicle_speed_scale if self.running else 0.0, 3),
                "measured_gps_speed_kmh": round(self.last_measured_speed_kmh, 3),
                "odometer_km": round(self.odometer_km, 6),
                "route_progress_m": round(self.route_progress_m, 3),
                "gateway_output_progress_m": self.gateway_output_progress,
                "gateway_output_gap_m": self.gateway_output_gap_m,
                "route_source": self.route_source,
                "exact_route_id": self.exact_route_id,
            }

    def set_gateway_fake(self, enabled):
        with self.lock:
            token = self.token
            base_url = self.gateway_url
            mode = self.gateway_mode
            ha_url = self.ha_url
            ha_token = self.ha_token
        if mode == "ha":
            cmd_payload = {"id": str(int(time.time() * 1000)), "action": "fake_nav", "value": {"enabled": bool(enabled)}}
            result, status = ha_request("services/input_text/set_value", "POST",
                                        {"entity_id": "input_text.x50_pending_command", "value": json.dumps(cmd_payload)},
                                        ha_url=ha_url, ha_token=ha_token)
            if status < 300:
                result = {"enabled": bool(enabled), "mode": "queued_via_ha"}
        else:
            result, status = gateway_request("/api/fake_nav", "POST", {"enabled": bool(enabled)}, token, base_url=base_url)
        with self.lock:
            self.fake_nav = result if isinstance(result, dict) else {}
            self.gateway_online = status < 500
            if status >= 300:
                self.last_error = result.get("error", "fake_nav_failed")
        self.wake.set()
        return result, status

    def reload_route(self, requested_source="all"):
        if requested_source not in ("all", "exact", "guidance", "history"):
            return {"ok": False, "error": "invalid_route_source"}, 400
        with self.lock:
            token = self.token
            base_url = self.gateway_url
        # Root-side staging runs every two seconds. Waiting for one complete
        # cycle makes the button read Navigator's current private files instead
        # of immediately re-decoding a potentially older staged copy.
        time.sleep(2.2)
        result, status = gateway_request("/api/fake_nav/reload", "POST", {}, token, base_url=base_url)
        self._next_route_poll = 0.0
        self.wake.set()
        if status < 300:
            with self.lock:
                self.route_source_revision = ""
            self._poll_route()
            result = dict(result) if isinstance(result, dict) else {"ok": True}
            result["requested_source"] = requested_source
            result["route"] = self.route_state()
        return result, status

    def _run(self):
        while not self.closed:
            now = time.monotonic()
            self._integrate(now)
            with self.lock:
                forced_send = self.pending_send
                should_send = forced_send or (self.running and now >= self._next_send)
                interval = 1.0 / self.gps_hz
                if should_send:
                    self.pending_send = False
                    if forced_send:
                        self._next_send = now + interval
                    else:
                        # Preserve the fixed cadence instead of adding loop or
                        # transport jitter to every following deadline.
                        self._next_send += interval
                        if self._next_send <= now:
                            self._next_send = now + interval
            if should_send:
                self._send_sample()
            self.wake.wait(0.02)
            self.wake.clear()

    def _poll_run(self):
        while not self.closed:
            now = time.monotonic()
            if now >= self._next_route_poll:
                self._poll_route()
                self._next_route_poll = time.monotonic() + 1.0
            if now >= self._next_status_poll:
                self._poll_status()
                self._next_status_poll = time.monotonic() + 0.75
            time.sleep(0.08)

    def _integrate(self, now):
        with self.lock:
            dt = max(0.0, min(2.0, now - self._last_integrate))
            self._last_integrate = now
            if not self.running:
                return
            base_distance_m = self.target_speed_kmh / 3.6 * dt
            self.odometer_km += base_distance_m * self.odometer_scale / 1000.0
            if self.gps_mode == "route" and self.route_points:
                self.route_progress_m += base_distance_m * self.gps_speed_scale
                if self.route_progress_m >= self.route_length_m:
                    self.route_progress_m = self.route_length_m
                    self.running = False
                    self.pending_send = True

    def _poll_route(self):
        with self.lock:
            token = self.token
            base_url = self.gateway_url
        result, status = gateway_request("/api/fake_nav/route", token=token, base_url=base_url)
        with self.lock:
            self.gateway_online = status < 500
            if status >= 300:
                return
            if not result.get("available"):
                self._reset_gateway_output_locked("route unavailable")
                self.route_points = []
                self.display_route_points = []
                self.raw_route_points = []
                self.guidance_route_points = []
                self.history_route_points = []
                self.stale_history_route_points = []
                self.exact_route_points = []
                self.route_distances = []
                self.route_length_m = 0.0
                self.route_progress_m = 0.0
                self.route_source = str(result.get("route_source", "none"))
                self.exact_route_fresh = bool(result.get("exact_fresh", False))
                self.exact_route_available = bool(result.get("exact_available", False))
                self.exact_route_id = str(result.get("exact_route_id", ""))
                self.exact_route_phase = str(result.get("exact_phase", ""))
                self.mapkit_route = dict(result.get("mapkit_route") or {})
                self.route_source_revision = str(result.get("source_revision", "unavailable"))
                return
            points = []
            for item in result.get("points", []):
                if isinstance(item, list) and len(item) >= 2:
                    points.append((float(item[0]), float(item[1])))
            raw_points = []
            for item in result.get("raw_points", []):
                if isinstance(item, list) and len(item) >= 2:
                    raw_points.append((float(item[0]), float(item[1])))
            def parse_points(name):
                parsed = []
                for item in result.get(name, []):
                    if isinstance(item, list) and len(item) >= 2:
                        parsed.append((float(item[0]), float(item[1])))
                return parsed
            guidance_points = parse_points("guidance_points")
            history_points = parse_points("history_points")
            stale_history_points = parse_points("stale_history_points")
            exact_points = parse_points("exact_points")
            self.mapkit_route = dict(result.get("mapkit_route") or {})
            self.exact_route_fresh = bool(result.get("exact_fresh", False))
            self.exact_route_available = bool(result.get("exact_available", False))
            self.exact_route_id = str(result.get("exact_route_id", ""))
            self.exact_route_phase = str(result.get("exact_phase", ""))
            revision = result.get("revision", 0)
            source_revision = str(result.get("source_revision", revision))
            if len(points) >= 2 and (revision != self.route_revision
                                     or source_revision != self.route_source_revision
                                     or len(points) != len(self.route_points)):
                old_selected = self.selected
                self._reset_gateway_output_locked("route geometry changed")
                self.route_source = str(result.get("route_source", "unknown"))
                self.exact_route_points = exact_points
                self.display_route_points = exact_points or points
                self.route_points = list(points) if self.route_source == "exact" else self._smooth_route(points)
                self.raw_route_points = raw_points or list(points)
                self.guidance_route_points = guidance_points or list(points)
                self.history_route_points = history_points
                self.stale_history_route_points = stale_history_points
                self.route_source_revision = source_revision
                self.history_fresh = bool(result.get("history_fresh", True))
                self.history_time_fresh = bool(result.get("history_time_fresh", True))
                self.history_match_ratio = float(result.get("history_match_ratio", 0.0) or 0.0)
                self.guidance_source_modified_ms = int(result.get("guidance_source_modified_ms", 0) or 0)
                self.history_source_modified_ms = int(result.get("history_source_modified_ms", 0) or 0)
                self.route_revision = revision
                self.route_imported = bool(result.get("imported"))
                self._prepare_route()
                self.route_progress_m = self._project_progress(old_selected) if old_selected else 0.0
                self.route_changed_at = int(time.time() * 1000)

    def _reset_gateway_output_locked(self, reason):
        self.gateway_fake_point = None
        self.gateway_fake_anchor_progress = 0.0
        self.gateway_fake_anchor_time = None
        self.gateway_output_progress = None
        self.gateway_output_time = None
        self.gateway_output_gap_m = None
        self.gateway_last_reanchor_reason = reason
        self.last_sent = None
        self.last_send_monotonic = None
        self.last_measured_speed_kmh = 0.0
        self.speed_window.clear()
        self.trace.clear()

    def _ha_trip_diagnostics(self, ha_url, ha_token):
        ha_state, status = ha_request("states/sensor.x50_trip_diagnostics", "GET",
                                      ha_url=ha_url, ha_token=ha_token)
        attributes = ha_state.get("attributes", {}) if isinstance(ha_state, dict) else {}
        result = dict(attributes.get("fake_nav") or {})
        if status < 300:
            result["ok"] = True
            if attributes.get("vehicle_speed_kmh") is not None:
                result["vehicle_speed_kmh"] = attributes.get("vehicle_speed_kmh")
            if attributes.get("odometer_km") is not None:
                result["odometer_km"] = attributes.get("odometer_km")
            result["ha_sample_timestamp_ms"] = attributes.get("sample_timestamp_ms")
        timestamp = finite_number(attributes.get("sample_timestamp_ms"))
        fresh = timestamp is not None and abs(time.time() * 1000 - timestamp) <= 20000
        return result, status, fresh

    def _poll_status(self):
        with self.lock:
            token = self.token
            base_url = self.gateway_url
            mode = self.gateway_mode
            ha_url = self.ha_url
            ha_token = self.ha_token
        if mode == "ha":
            result, status, ha_fresh = self._ha_trip_diagnostics(ha_url, ha_token)
            trip_result = result if ha_fresh else None
            trip_source = "ha_relay"
        else:
            result, status = gateway_request("/api/fake_nav", token=token, base_url=base_url)
            # Route/control traffic can use the direct VPN address while the
            # real-trip journal independently follows Relay telemetry in HA.
            ha_result, ha_status, ha_fresh = self._ha_trip_diagnostics(ha_url, ha_token)
            trip_result = ha_result if ha_status < 300 and ha_fresh else (
                result if status < 300 else None)
            trip_source = "ha_relay" if ha_status < 300 and ha_fresh else "gateway_direct"
        with self.lock:
            self.gateway_online = status < 500
            if status < 300:
                self.fake_nav = result
        if trip_result is not None:
            context = self._trip_context()
            context["journal_source"] = trip_source
            self.trip_store.observe(trip_result, context)

    def _prepare_route(self):
        self.route_distances = [0.0]
        total = 0.0
        for previous, current in zip(self.route_points, self.route_points[1:]):
            total += haversine_m(previous, current)
            self.route_distances.append(total)
        self.route_length_m = total

    @staticmethod
    def _smooth_route(points, passes=2):
        """Chaikin corner cutting for plausible high-frequency GPS motion.

        The captured polyline remains available unchanged for diagnostics; this
        geometry is used only for generated AVD fixes.
        """
        smoothed = list(points)
        for _ in range(passes):
            if len(smoothed) < 3:
                break
            result = [smoothed[0]]
            for a, b in zip(smoothed, smoothed[1:]):
                result.append((a[0] * 0.75 + b[0] * 0.25, a[1] * 0.75 + b[1] * 0.25))
                result.append((a[0] * 0.25 + b[0] * 0.75, a[1] * 0.25 + b[1] * 0.75))
            result.append(smoothed[-1])
            smoothed = result
        return smoothed

    def _project_progress(self, point, near_progress=None):
        if point is None or len(self.route_points) < 2:
            return 0.0
        ref_lat = math.radians(point[0])
        lon_scale = max(0.1, math.cos(ref_lat))
        px, py = point[1] * lon_scale, point[0]
        best_distance, best_progress = float("inf"), 0.0
        for index, (a, b) in enumerate(zip(self.route_points, self.route_points[1:])):
            if near_progress is not None:
                segment_mid = (self.route_distances[index] + self.route_distances[index + 1]) / 2.0
                if abs(segment_mid - near_progress) > 500.0:
                    continue
            ax, ay = a[1] * lon_scale, a[0]
            bx, by = b[1] * lon_scale, b[0]
            dx, dy = bx - ax, by - ay
            span = dx * dx + dy * dy
            t = 0.0 if span == 0 else clamp(((px - ax) * dx + (py - ay) * dy) / span, 0, 1)
            candidate = (ay + dy * t, (ax + dx * t) / lon_scale)
            distance = haversine_m(point, candidate)
            if distance < best_distance:
                segment = self.route_distances[index + 1] - self.route_distances[index]
                best_distance = distance
                best_progress = self.route_distances[index] + segment * t
        return best_progress

    def _route_coordinate(self, progress):
        if not self.route_points:
            return self.selected, 0.0
        if progress <= 0:
            return self.route_points[0], bearing_deg(self.route_points[0], self.route_points[1])
        for index in range(1, len(self.route_points)):
            if progress <= self.route_distances[index] or index == len(self.route_points) - 1:
                start_d, end_d = self.route_distances[index - 1], self.route_distances[index]
                t = clamp((progress - start_d) / max(0.01, end_d - start_d), 0, 1)
                a, b = self.route_points[index - 1], self.route_points[index]
                return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), bearing_deg(a, b)
        return self.route_points[-1], 0.0

    def _send_sample(self):
        with self.lock:
            delivery_ahead = self.latency_ema_ms / 1000.0
            running = self.running
            target = self.target_speed_kmh
            vehicle_speed = target * self.vehicle_speed_scale if running else 0.0
            gps_speed = target * self.gps_speed_scale if running else 0.0
            odometer = self.odometer_km
            token = self.token
            base_url = self.gateway_url
            mode = self.gateway_mode
            ha_url = self.ha_url
            ha_token = self.ha_token
            base_route_progress = self.route_progress_m
            projected_progress = self.route_progress_m + (
                target * self.gps_speed_scale / 3.6 * delivery_ahead if running else 0.0)
            coordinate, bearing = ((self.selected, 0.0) if self.gps_mode == "fixed"
                                   else self._route_coordinate(projected_progress))
            force_route_anchor = self.force_route_anchor_pending
        started = time.monotonic()
        if mode == "ha":
            vehicle_result, vehicle_status = {"ok": True}, 200
        else:
            vehicle_result, vehicle_status = gateway_request(
                "/api/simulator/vehicle", "POST",
                {"enabled": running, "speed_kmh": vehicle_speed, "odometer_km": odometer}, token, base_url=base_url)
        if coordinate is None:
            with self.lock:
                self.gateway_online = vehicle_status < 500
                self.last_error = None if vehicle_status < 300 else vehicle_result.get("error")
            return
        epoch_ms = int(time.time() * 1000)
        location = {"latitude": coordinate[0], "longitude": coordinate[1], "accuracy": 5,
                    "altitude": 160, "speed": gps_speed / 3.6, "bearing": bearing,
                    "satellites": 12, "time_ms": epoch_ms, "emulator_native": True}
        if force_route_anchor:
            location["force_route_anchor"] = True
        if mode == "ha":
            cmd_payload = {"id": str(epoch_ms), "action": "location", "value": location}
            location_result, location_status = ha_request(
                "services/input_text/set_value", "POST",
                {"entity_id": "input_text.x50_pending_command", "value": json.dumps(cmd_payload)},
                ha_url=ha_url, ha_token=ha_token)
            if location_status < 300:
                location_result = {"ok": True, "fake_navigation_active": True}
        else:
            location_result, location_status = gateway_request("/api/location", "POST", location, token, base_url=base_url)
        final_coordinate = coordinate
        sample_progress = None
        phase_error_m = None
        phase_correction_kmh = None
        hard_reanchor = False
        hard_reanchor_reason = None
        gateway_fake_active = bool(location_result.get("fake_navigation_active"))
        if gateway_fake_active:
            fake_lat, fake_lon = location_result.get("fake_lat"), location_result.get("fake_lon")
            if fake_lat is not None and fake_lon is not None:
                fake_point = (float(fake_lat), float(fake_lon))
                with self.lock:
                    delivery_time = time.monotonic()
                    base_mps = target * self.odometer_scale / 3.6 if running else 0.0
                    fake_age = clamp(location_result.get("fake_gps_age_ms") or 0, 0, 2000) / 1000.0
                    changed = (self.gateway_fake_point is None
                               or haversine_m(self.gateway_fake_point, fake_point) > 0.15)
                    if self.gateway_output_progress is None:
                        hard_reanchor_reason = self.gateway_last_reanchor_reason or "initial fake anchor"
                    else:
                        current_output, _ = self._route_coordinate(self.gateway_output_progress)
                        output_gap = haversine_m(current_output, fake_point)
                        self.gateway_output_gap_m = output_gap
                        if output_gap > 80.0:
                            hard_reanchor_reason = "output/fake spatial gap {:.1f} m".format(output_gap)
                    if changed or hard_reanchor_reason:
                        self.gateway_fake_point = fake_point
                        self.gateway_fake_anchor_progress = self._project_progress(
                            fake_point, None if hard_reanchor_reason else self.gateway_output_progress)
                        self.gateway_fake_anchor_time = delivery_time - fake_age
                    # Generate the coordinate for the actual console-send moment.
                    # HTTP response time is variable and must not alter distance/time.
                    anchor_age = max(0.0, delivery_time - (self.gateway_fake_anchor_time or delivery_time))
                    expected_progress = self.gateway_fake_anchor_progress + base_mps * anchor_age
                    if hard_reanchor_reason:
                        self.gateway_output_progress = clamp(
                            expected_progress, 0, max(0, self.route_length_m))
                        self.gateway_output_time = delivery_time
                        phase_error_m = 0.0
                        phase_correction_kmh = 0.0
                        hard_reanchor = True
                        self.gateway_hard_reanchor_count += 1
                        self.gateway_last_reanchor_reason = hard_reanchor_reason
                        self.last_sent = None
                        self.last_send_monotonic = None
                        self.last_measured_speed_kmh = 0.0
                        self.speed_window.clear()
                    else:
                        dt = max(0.0, delivery_time - (self.gateway_output_time or delivery_time))
                        error_m = expected_progress - self.gateway_output_progress
                        # Small transport timing errors are phase-locked gradually.
                        # Geometric discontinuities are handled by the hard anchor.
                        correction_limit = max(0.12, abs(base_mps) * 0.04) if running else 0.0
                        correction_mps = clamp(error_m * 0.22, -correction_limit, correction_limit)
                        self.gateway_output_progress = clamp(
                            self.gateway_output_progress + (base_mps + correction_mps) * dt,
                            0, max(0, self.route_length_m))
                        self.gateway_output_time = delivery_time
                        phase_error_m = error_m
                        phase_correction_kmh = correction_mps * 3.6
                    sample_progress = self.gateway_output_progress
                    final_coordinate, _ = self._route_coordinate(self.gateway_output_progress)
                    self.gateway_output_gap_m = haversine_m(final_coordinate, fake_point)
        elif self.gps_mode == "route":
            with self.lock:
                delivery_progress = base_route_progress + (
                    target * self.gps_speed_scale / 3.6 * max(0.0, time.monotonic() - started)
                    if running else 0.0)
                final_coordinate, _ = self._route_coordinate(delivery_progress)
        error = None
        delivered = None
        if location_status < 300:
            try:
                delivered = self.geo_console.fix(final_coordinate[1], final_coordinate[0])
            except OSError as exc:
                error = "emulator_geo_fix_failed: " + str(exc)
        else:
            error = location_result.get("error", "location_failed")
        completed = time.monotonic()
        delivered = delivered or completed
        with self.lock:
            if force_route_anchor and location_status < 300:
                self.force_route_anchor_pending = False
            if gateway_fake_active != self.last_gateway_fake_active:
                self.last_sent = None
                self.last_send_monotonic = None
                self.last_measured_speed_kmh = 0.0
                self.speed_window.clear()
            self.last_gateway_fake_active = gateway_fake_active
            if not gateway_fake_active:
                self._reset_gateway_output_locked("fake disabled")
            if self.last_sent is not None and self.last_send_monotonic is not None:
                # The emulator observes delivery time, not generation time.
                elapsed = delivered - self.last_send_monotonic
                if elapsed > 0.01:
                    instant_speed = haversine_m(self.last_sent, final_coordinate) / elapsed * 3.6
                    self.speed_window.append((delivered, final_coordinate))
                    while len(self.speed_window) > 2 and self.speed_window[0][0] < delivered - 1.6:
                        self.speed_window.popleft()
                    if len(self.speed_window) >= 2:
                        distance = sum(haversine_m(a[1], b[1]) for a, b in zip(self.speed_window, list(self.speed_window)[1:]))
                        duration = self.speed_window[-1][0] - self.speed_window[0][0]
                        self.last_measured_speed_kmh = distance / max(0.01, duration) * 3.6
                    instant_hz = 1.0 / elapsed
                    self.effective_hz = instant_hz if self.effective_hz == 0 else self.effective_hz * 0.8 + instant_hz * 0.2
                    self.trace.append({"time_ms": epoch_ms, "dt_ms": round(elapsed * 1000, 1),
                                       "distance_m": round(haversine_m(self.last_sent, final_coordinate), 3),
                                       "instant_kmh": round(instant_speed, 2),
                                       "filtered_kmh": round(self.last_measured_speed_kmh, 2),
                                       "progress_m": None if sample_progress is None else round(sample_progress, 3),
                                       "phase_error_m": None if phase_error_m is None else round(phase_error_m, 3),
                                       "correction_kmh": None if phase_correction_kmh is None else round(phase_correction_kmh, 3),
                                       "output_gap_m": (None if self.gateway_output_gap_m is None
                                                        else round(self.gateway_output_gap_m, 3)),
                                       "hard_reanchor": hard_reanchor,
                                       "reanchor_reason": hard_reanchor_reason,
                                       "fake": gateway_fake_active})
            self.last_raw = coordinate
            self.last_sent = final_coordinate
            self.last_send_monotonic = delivered
            self.last_send_epoch_ms = epoch_ms
            self.last_latency_ms = round((delivered - started) * 1000, 1)
            self.latency_ema_ms = self.latency_ema_ms * 0.85 + self.last_latency_ms * 0.15
            self.gateway_online = vehicle_status < 500 and location_status < 500
            self.last_error = error
            if error:
                self.failed_count += 1
            else:
                self.sent_count += 1


class Handler(SimpleHTTPRequestHandler):
    engine: SimulationEngine = None

    def translate_path(self, path):
        clean = urlsplit(path).path
        filename = STATIC_FILES.get(clean, "")
        return str(ROOT / filename) if filename else str(ROOT / "__not_found__")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/controller/state":
            self.reply_json(self.engine.state())
        elif path == "/api/controller/route":
            self.reply_json(self.engine.route_state())
        elif path == "/api/controller/trace":
            self.reply_json(self.engine.trace_state())
        elif path == "/api/controller/trips":
            self.reply_json(self.engine.trip_store.list())
        elif path.startswith("/api/controller/trips/"):
            trip_id = path.rsplit("/", 1)[-1]
            payload, status = self.engine.trip_store.detail(trip_id)
            self.reply_json(payload, status)
        elif path.startswith("/api/"):
            self.proxy()
        else:
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/api/controller/control":
            try:
                self.reply_json(self.engine.update(self.read_json()))
            except (ValueError, TypeError, KeyError) as error:
                self.reply_json({"ok": False, "error": "invalid_control", "detail": str(error)}, 400)
        elif path == "/api/controller/fake-nav":
            data = self.read_json()
            payload, status = self.engine.set_gateway_fake(bool(data.get("enabled")))
            self.reply_json(payload, status)
        elif path == "/api/controller/reload-route":
            data = self.read_json()
            payload, status = self.engine.reload_route(str(data.get("source", "all")))
            self.reply_json(payload, status)
        elif path == "/api/controller/trips/finish":
            self.reply_json(self.engine.trip_store.finish("manual"))
        elif path == "/api/location":
            data = self.read_json()
            data["emulator_native"] = True
            payload, status = gateway_request("/api/location", "POST", data,
                                              self.headers.get("X-X50-Token", "x50test"),
                                              base_url=self.engine.gateway_url)
            self.reply_json(payload, status)
        else:
            self.proxy()

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def proxy(self):
        path = self.path
        data = self.read_json() if self.command == "POST" else None
        payload, status = gateway_request(path, self.command, data,
                                          self.headers.get("X-X50-Token", "x50test"),
                                          base_url=self.engine.gateway_url)
        self.reply_json(payload, status)

    def reply_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[controller] " + fmt % args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    engine = SimulationEngine(os.environ.get("X50_ADB", "adb"),
                              os.environ.get("X50_DEVICE", "emulator-5554"))
    Handler.engine = engine
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("X50 simulator: http://0.0.0.0:%d" % args.port)
    try:
        server.serve_forever()
    finally:
        engine.close()
        server.server_close()


if __name__ == "__main__":
    main()
