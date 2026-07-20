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

    def __init__(self, device: str):
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
        self.geo_console = EmulatorGeoConsole(device)
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
        self.token = "x50test"
        self.gateway_url = GATEWAY
        self.gateway_mode = os.environ.get("X50_GATEWAY_MODE", "direct")
        self.ha_url = os.environ.get("HA_URL", "http://supervisor/core")
        self.ha_token = os.environ.get("SUPERVISOR_TOKEN", "")
        self.force_route_anchor_pending = False
        self.route_hook = LiveRouteHook(adb, device, os.environ.get("X50_ROUTE_AGENT"))
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
        self.wake.set()

    def update(self, data):
        with self.lock:
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
            if "gateway_url" in data and str(data["gateway_url"]).strip():
                url = str(data["gateway_url"]).strip().rstrip('/')
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "http://" + url
                self.gateway_url = url
            if "gateway_mode" in data and str(data["gateway_mode"]) in ("direct", "ha"):
                self.gateway_mode = str(data["gateway_mode"])
            if "ha_url" in data and str(data["ha_url"]).strip():
                url = str(data["ha_url"]).strip().rstrip('/')
                if not url.startswith("http://") and not url.startswith("https://"):
                    url = "http://" + url
                self.ha_url = url
            if "ha_token" in data:
                self.ha_token = str(data["ha_token"]).strip()
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
                "ha_token": self.ha_token,
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
                    "history_fresh": self.history_fresh,
                    "history_time_fresh": self.history_time_fresh,
                    "history_match_ratio": self.history_match_ratio,
                    "guidance_source_modified_ms": self.guidance_source_modified_ms,
                    "history_source_modified_ms": self.history_source_modified_ms,
                    "source_revision": self.route_source_revision}

    def trace_state(self):
        with self.lock:
            return {"ok": True, "samples": list(self.trace)}

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
            revision = result.get("revision", 0)
            source_revision = str(result.get("source_revision", revision))
            if len(points) >= 2 and (revision != self.route_revision
                                     or source_revision != self.route_source_revision
                                     or len(points) != len(self.route_points)):
                old_selected = self.selected
                self._reset_gateway_output_locked("route geometry changed")
                self.route_source = str(result.get("route_source", "unknown"))
                self.exact_route_points = exact_points
                self.exact_route_fresh = bool(result.get("exact_fresh", False))
                self.exact_route_available = bool(result.get("exact_available", False))
                self.exact_route_id = str(result.get("exact_route_id", ""))
                self.exact_route_phase = str(result.get("exact_phase", ""))
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

    def _poll_status(self):
        with self.lock:
            token = self.token
            base_url = self.gateway_url
        result, status = gateway_request("/api/fake_nav", token=token, base_url=base_url)
        with self.lock:
            self.gateway_online = status < 500
            if status < 300:
                self.fake_nav = result

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
