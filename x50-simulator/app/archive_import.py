"""Bounded Navigation journal import; independent of live vehicle state."""
import gzip
import hashlib
import io
import json
import math
import re
import threading
import time
import uuid
from pathlib import Path

MAX_UPLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 1024 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class ArchiveUploads:
    """Short-lived bounded disk staging for uploads through HA Ingress."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions = {}
        # Sessions cannot survive a service restart.
        for path in self.root.glob("*.part"):
            path.unlink()

    def _expire(self):
        for key, item in list(self.sessions.items()):
            if time.monotonic() - item["touched"] > 900:
                self.cancel(key)

    def start(self, total, target=None):
        if not isinstance(total, int) or isinstance(total, bool) or not 0 < total <= MAX_UPLOAD:
            raise ValueError("Архив должен быть не больше 128 МБ")
        with self.lock:
            self._expire()
            if len(self.sessions) >= 2:
                raise ValueError("Уже выполняются две загрузки архивов")
            key = uuid.uuid4().hex
            path = self.root / (key + ".part")
            path.touch()
            self.sessions[key] = dict(path=path, total=total, target=target, touched=time.monotonic())
            return {"ok": True, "upload_id": key, "chunk_size": CHUNK_SIZE}

    def append(self, key, offset, data):
        with self.lock:
            self._expire()
            item = self.sessions.get(key)
            if item is None:
                raise ValueError("Загрузка не найдена или истекла")
            size = item["path"].stat().st_size
            if not data or len(data) > CHUNK_SIZE or offset < 0 or offset + len(data) > item["total"]:
                raise ValueError("Некорректный размер части архива")
            if offset < size:
                with item["path"].open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(data)) != data:
                        raise ValueError("Повторная часть отличается от загруженной")
            elif offset == size:
                with item["path"].open("ab") as stream:
                    stream.write(data)
            else:
                raise ValueError("Нарушен порядок частей архива")
            item["touched"] = time.monotonic()
            return {"ok": True, "received_bytes": item["path"].stat().st_size}

    def finish(self, key):
        with self.lock:
            self._expire()
            item = self.sessions.get(key)
            if item is None or item["path"].stat().st_size != item["total"]:
                raise ValueError("Архив загружен не полностью")
            try:
                return item["path"].read_bytes(), item["target"]
            finally:
                self.cancel(key)

    def cancel(self, key):
        with self.lock:
            item = self.sessions.pop(key, None)
            if item:
                item["path"].unlink(missing_ok=True)
            return {"ok": True}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def parse_archive(payload):
    if not payload or len(payload) > MAX_UPLOAD:
        raise ValueError("Архив пустой или превышает 128 МБ")
    samples, routes, switches, events = {}, {}, [], []
    start = end = journal_id = None
    ended_complete = False
    diagnostic, current_route, device = {}, None, "head_unit"
    expanded = rows = 0
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
            while True:
                line = stream.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                expanded += len(line)
                rows += 1
                if len(line) > 2 * 1024 * 1024 or expanded > MAX_EXPANDED or rows > 1_000_000:
                    raise ValueError("Слишком большой распакованный журнал")
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Некорректная запись журнала")
                stamp, kind, data = number(row.get("time_ms")), row.get("type"), row.get("data")
                if stamp is None or not isinstance(data, dict):
                    raise ValueError("В журнале отсутствуют время или данные записи")
                if kind == "trip_start":
                    if start is not None:
                        raise ValueError("Архив содержит несколько поездок")
                    start, journal_id = stamp, data.get("trip_id")
                    if not isinstance(journal_id, str) or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", journal_id):
                        raise ValueError("Неизвестный формат идентификатора поездки")
                elif kind == "trip_end":
                    end = stamp
                    ended_complete = not data.get("truncated", False)
                elif kind == "route_snapshot" and data.get("available"):
                    device = "avd" if data.get("is_emulator") or data.get("device_kind") == "avd" else device
                    points = data.get("points") or data.get("exact_points") or []
                    if not isinstance(points, list) or len(points) < 2:
                        continue
                    identity = json.dumps([data.get("revision"), data.get("route_generation"),
                                           data.get("route_activation_count"), points], separators=(",", ":"))
                    sid = "archive_" + hashlib.sha256(identity.encode()).hexdigest()[:24]
                    routes.setdefault(sid, dict(data, kind="route_snapshot", snapshot_id=sid,
                        time_ms=stamp, observed_at_ms=stamp, points=points,
                        route_id=data.get("exact_route_id") or data.get("route_identity") or sid))
                    if sid != current_route:
                        switches.append(dict(kind="route_switch", time_ms=stamp,
                            from_snapshot_id=current_route, to_snapshot_id=sid,
                            route_id=routes[sid]["route_id"], route_source=data.get("route_source")))
                        current_route = sid
                elif kind == "diagnostic_tick":
                    diagnostic = data
                    device = "avd" if data.get("is_emulator") or data.get("device_kind") == "avd" else device
                    if data.get("route_available") is False and current_route:
                        switches.append(dict(kind="route_switch", time_ms=stamp,
                            from_snapshot_id=current_route, to_snapshot_id=None))
                        current_route = None
                    sample = dict(data, kind="sample", time_ms=stamp, route_snapshot_id=current_route)
                    sample["gps_good"] = bool(data.get("real_gps_quality_good"))
                    samples[int(stamp // 1000)] = sample
                elif kind == "vehicle_sample":
                    bucket = int(stamp // 1000)
                    sample = dict(samples.get(bucket) or diagnostic, kind="sample", time_ms=stamp,
                                  route_snapshot_id=current_route)
                    for key in ("odometer_km", "compass", "gear_code", "inertial_step"):
                        if key in data:
                            sample[key] = data[key]
                    sample["vehicle_speed_kmh"] = data.get("speed_kmh")
                    sample["steering_angle_deg"] = data.get("steer_angle_deg")
                    sample["gps_good"] = bool(sample.get("real_gps_quality_good"))
                    samples[bucket] = sample
                elif kind.startswith("steering_") or kind == "coordinate_decision":
                    events.append(dict(kind="event", event=kind, time_ms=stamp, data=data))
    except (OSError, EOFError, UnicodeError, json.JSONDecodeError, TypeError, AttributeError) as error:
        raise ValueError("Повреждённый gzip/JSONL архив Navigation") from error
    if start is None or not samples:
        raise ValueError("Это не архив поездки Navigation: нет начала или измерений")
    samples = sorted(samples.values(), key=lambda item: item["time_ms"])
    finish = end if end is not None else samples[-1]["time_ms"]
    if finish <= start or samples[0]["time_ms"] < start - 5000 or samples[-1]["time_ms"] > finish + 5000:
        raise ValueError("Некорректные границы времени поездки")
    for sample in samples:
        sample.update(device_kind=device, journal_source="uploaded_navigation_archive")
    odo = [s["odometer_km"] for s in samples if number(s.get("odometer_km")) is not None]
    integrated = sum(max(0, number(a.get("vehicle_speed_kmh")) or 0) / 3.6 *
                     min(2, max(0, (b["time_ms"]-a["time_ms"])/1000)) for a, b in zip(samples, samples[1:]))
    summary = dict(id="archive_"+journal_id, active=False, started_ms=start, ended_ms=finish,
        duration_s=(finish-start)/1000, device_kind=device, journal_source="uploaded_navigation_archive",
        source_journal_id=journal_id, archive_sha256=hashlib.sha256(payload).hexdigest(),
        archive_complete=ended_complete, samples=len(samples), route_snapshots=len(routes),
        route_switches=len(switches), route_ids=list(dict.fromkeys(r["route_id"] for r in routes.values())),
        start_odometer_km=odo[0] if odo else None, end_odometer_km=odo[-1] if odo else None,
        distance_odometer_m=max(0, (odo[-1]-odo[0])*1000) if odo else integrated,
        distance_integrated_m=integrated, max_speed_kmh=max(number(s.get("vehicle_speed_kmh")) or 0 for s in samples),
        correction_events=0, gps_outages=0, correction_total_m=0, correction_abs_total_m=0,
        finish_reason="archive_import")
    first, last = samples[0], samples[-1]
    summary.update(route_id=routes[current_route]["route_id"] if current_route in routes else "",
                   route_source=last.get("route_source", "none"),
                   start_progress_m=first.get("progress_m"), end_progress_m=last.get("progress_m"))
    for key in ("correction_total_m", "correction_abs_total_m", "correction_count", "recovery_correction_count"):
        values = [s[key] for s in samples if number(s.get(key)) is not None]
        if values:
            name = {"correction_count": "correction_operations", "recovery_correction_count": "recovery_corrections"}.get(key, key)
            summary[name] = max(0, values[-1]-values[0]) if key != "correction_total_m" else values[-1]-values[0]
    summary["gps_outages"] = sum(not s["gps_good"] and (i == 0 or samples[i-1]["gps_good"])
                                for i, s in enumerate(samples))
    return dict(ok=True, summary=summary, samples=samples, events=events,
                routes=list(routes.values()), route_switches=switches)


def validate_match(existing, imported):
    a, b = existing["summary"], imported["summary"]
    if a.get("active"):
        raise ValueError("Сначала завершите выбранную поездку")
    if a.get("device_kind", "head_unit") != b["device_kind"]:
        raise ValueError("Архив записан на другом типе устройства")
    start, end = number(a.get("started_ms")), number(a.get("ended_ms"))
    if start is None or end is None:
        raise ValueError("У выбранной поездки нет границ времени")
    overlap = min(end, b["ended_ms"]) - max(start, b["started_ms"])
    if overlap <= 0 or overlap < .7 * min(end-start, b["ended_ms"]-b["started_ms"]) or abs(start-b["started_ms"]) > 300_000:
        raise ValueError("Архив не соответствует времени выбранной поездки")
    # Compare at the same time, rather than different trip start odometers.
    target = [s for s in existing.get("samples", []) if number(s.get("odometer_km")) is not None]
    source = [s for s in imported["samples"] if number(s.get("odometer_km")) is not None]
    if target and source:
        t = target[len(target)//2]
        nearest = min(source, key=lambda s: abs(s["time_ms"]-t["time_ms"]))
        if abs(nearest["time_ms"]-t["time_ms"]) <= 30_000 and abs(nearest["odometer_km"]-t["odometer_km"]) > .3:
            raise ValueError("Одометр архива не соответствует выбранной поездке")
    # Independently reject coincident recordings from different vehicles when
    # both carry a usable real GPS fix. Do not compare FakeGPS drift here.
    for t in existing.get("samples", []):
        if not t.get("gps_good") or any(number(t.get(k)) is None for k in ("carlinkit_lat", "carlinkit_lon")):
            continue
        nearby = [s for s in imported["samples"] if s.get("gps_good")
                  and abs(s["time_ms"]-t["time_ms"]) <= 5000
                  and all(number(s.get(k)) is not None for k in ("carlinkit_lat", "carlinkit_lon"))]
        if nearby:
            s = min(nearby, key=lambda s: abs(s["time_ms"]-t["time_ms"]))
            north = (s["carlinkit_lat"]-t["carlinkit_lat"])*111132
            east = (s["carlinkit_lon"]-t["carlinkit_lon"])*111320*math.cos(math.radians(t["carlinkit_lat"]))
            if math.hypot(north, east) > 300:
                raise ValueError("GPS архива не соответствует выбранной поездке")
            break
