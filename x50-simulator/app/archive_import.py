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
ARCHIVE_SAMPLE_KEYS = (
    'enabled', 'mode', 'reason', 'vehicle_speed_kmh',
    'corrected_speed_kmh', 'speed_factor', 'odometer_km', 'odometer_delta_m',
    'corrected_delta_m', 'distance_factor', 'distance_calibration_count', 'distance_calibration_ratio',
    'distance_calibration_window_m', 'distance_calibration_correction_bias', 'calibration_mode', 'calibration_mode_code',
    'calibration_accepted_windows', 'calibration_rejected_windows', 'calibration_distance_m', 'calibration_trip_count',
    'distance_candidate_factor', 'distance_calibration_confidence', 'distance_calibration_mad', 'distance_calibration_samples',
    'speed_candidate_factor', 'speed_calibration_confidence', 'speed_calibration_mad', 'speed_calibration_samples',
    'calibration_correction_bias_median', 'progress_source', 'route_length_m', 'progress_m',
    'route_match_progress_m', 'route_match_distance_m', 'last_progress_correction_m', 'last_correction_weight',
    'correction_target_progress_m', 'correction_prediction_m', 'correction_raw_delta_m', 'correction_target_delta_m',
    'correction_verified_recovery', 'correction_fix_age_ms', 'correction_fix_time_ms', 'correction_mode',
    'correction_total_m', 'correction_abs_total_m', 'recovery_correction_count', 'gps_recovery_pending',
    'gps_recovery_candidate_fixes', 'gps_outage_age_ms', 'last_gps_recovery_outage_ms', 'real_gps_age_ms',
    'real_gps_received_age_ms', 'real_gps_fix_time_ms', 'real_gps_quality_good', 'carlinkit_fix_age_ms',
    'carlinkit_lat', 'carlinkit_lon', 'carlinkit_accuracy_m', 'carlinkit_speed_kmh',
    'carlinkit_bearing', 'gps_gap_m', 'correction_count', 'rejected_corrections',
    'injected_count', 'tick_raw_dt_ms', 'tick_max_raw_dt_ms', 'tick_discarded_time_ms',
    'fake_lat', 'fake_lon', 'off_route_passthrough', 'off_route_distance_m',
    'off_route_candidate_fixes', 'off_route_recovery_fixes', 'off_route_started_ms', 'off_route_elapsed_ms',
    'off_route_started_route_generation', 'gps_vehicle_speed_difference_kmh', 'route_generation', 'route_activation_count',
    'route_activated_at_ms', 'route_identity', 'route_source', 'exact_route_fresh',
    'exact_route_available', 'exact_route_id', 'exact_route_captured_ms', 'exact_route_producer',
    'fake_provider_enabled', 'route_reanchor_pending', 'steering_angle_deg', 'steering_fresh',
    'steering_age_ms', 'gear_code', 'motion_age_ms', 'motion_steering_skew_ms',
    'virtual_trajectory', 'compass',
)


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
            ready = item["path"].with_suffix(".ready")
            item["path"].replace(ready)
            self.sessions.pop(key)
            return ready, item["target"]

    def cancel(self, key):
        with self.lock:
            item = self.sessions.pop(key, None)
            if item:
                item["path"].unlink(missing_ok=True)
            return {"ok": True}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def parse_archive(payload, *, multipart=False):
    path = payload if isinstance(payload, Path) else None
    size = path.stat().st_size if path else len(payload)
    if not size or size > (MAX_EXPANDED if multipart else MAX_UPLOAD):
        raise ValueError("Архив пустой или превышает 128 МБ")
    samples, routes, switches, events = {}, {}, [], []
    start = end = journal_id = None
    ended_complete = False
    diagnostic, current_route, device = {}, None, "head_unit"
    expanded = rows = 0
    parts, last_stamp = [], None
    try:
        with (gzip.open(path, "rb") if path else gzip.GzipFile(fileobj=io.BytesIO(payload))) as stream:
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
                    identity = data.get("trip_id")
                    if not isinstance(identity, str) or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}", identity):
                        raise ValueError("Invalid Navigation trip identity")
                    index = data.get("part_index")
                    physical = data.get("archive_id")
                    if index is not None:
                        if not isinstance(index,int) or isinstance(index,bool) or not 0<=index<10000:
                            raise ValueError("Invalid trip part index")
                        if not isinstance(physical,str) or not re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{8}",physical):
                            raise ValueError("Invalid trip archive identity")
                        if parts and (index<=parts[-1]["part_index"] or data.get("trip_started_ms")!=parts[0]["trip_started_ms"]):
                            raise ValueError("Conflicting trip parts")
                        if parts and index==parts[-1]["part_index"]+1 and data.get("previous_archive_id")!=parts[-1]["archive_id"]:
                            raise ValueError("Broken trip part chain")
                        parts.append(dict(part_index=index,archive_id=physical,previous_archive_id=data.get("previous_archive_id"),
                                          trip_started_ms=data.get("trip_started_ms")))
                    if start is not None:
                        if identity!=journal_id or index is None or len(parts)<2 or stamp<(last_stamp or start):
                            raise ValueError("Archive contains unrelated or unordered trips")
                    else:
                        start, journal_id = stamp, identity
                elif kind == "trip_end":
                    end = stamp
                    ended_complete = not data.get("truncated", False) and not data.get("continuation",False)
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
                    # Retain the same measurements as the live trip journal;
                    # full diagnostics remain in the original gzip on disk.
                    diagnostic = {k: data[k] for k in ARCHIVE_SAMPLE_KEYS if k in data}
                    inertial = data.get("inertial_trajectory")
                    if isinstance(inertial, dict) and isinstance(inertial.get("last_step"), dict):
                        diagnostic["inertial_step"] = inertial["last_step"]
                    device = "avd" if data.get("is_emulator") or data.get("device_kind") == "avd" else device
                    if data.get("route_available") is False and current_route:
                        switches.append(dict(kind="route_switch", time_ms=stamp,
                            from_snapshot_id=current_route, to_snapshot_id=None))
                        current_route = None
                    sample = dict(diagnostic, kind="sample", time_ms=stamp, route_snapshot_id=current_route)
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
                elif kind.startswith("steering_"):
                    events.append(dict(kind="event", event=kind, time_ms=stamp, data=data))
                last_stamp=stamp
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
    if parts:
        ended_complete=ended_complete and [p["part_index"] for p in parts]==list(range(parts[-1]["part_index"]+1))
    summary = dict(id="archive_"+journal_id, active=False, started_ms=start, ended_ms=finish,
        duration_s=(finish-start)/1000, device_kind=device, journal_source="uploaded_navigation_archive",
        source_journal_id=journal_id, archive_sha256=archive_hash(payload),
        archive_complete=ended_complete, samples=len(samples), route_snapshots=len(routes),
        route_switches=len(switches), route_ids=list(dict.fromkeys(r["route_id"] for r in routes.values())),
        start_odometer_km=odo[0] if odo else None, end_odometer_km=odo[-1] if odo else None,
        distance_odometer_m=max(0, (odo[-1]-odo[0])*1000) if odo else integrated,
        distance_integrated_m=integrated, max_speed_kmh=max(number(s.get("vehicle_speed_kmh")) or 0 for s in samples),
        correction_events=0, gps_outages=0, correction_total_m=0, correction_abs_total_m=0,
        finish_reason="archive_import", archive_parts=parts)
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


def archive_hash(payload):
    if not isinstance(payload, Path):
        return hashlib.sha256(payload).hexdigest()
    digest = hashlib.sha256()
    with payload.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


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


def assemble_parts(root, raw, imported):
    """Retain verified physical parts and stream a sorted concatenated gzip to disk.

    gzip members remain original; no coordinate or timestamp rewriting occurs.
    """
    parts=imported["summary"].get("archive_parts") or []
    if not parts:
        return raw, imported
    if len(parts)!=1:
        raise ValueError("Upload one physical trip part at a time")
    part=parts[0];group=imported["summary"]["source_journal_id"]
    directory=Path(root)/"parts"/group;directory.mkdir(parents=True,exist_ok=True)
    path=directory/f"{part['part_index']:04d}-{part['archive_id']}.jsonl.gz"
    conflicting=list(directory.glob(f"{part['part_index']:04d}-*.jsonl.gz"))
    if conflicting and (conflicting[0]!=path or archive_hash(conflicting[0])!=archive_hash(raw)):
        raise ValueError("Conflicting content for the same trip part")
    combined=Path(root)/("group-"+group+".jsonl.gz")
    temporary=combined.with_suffix(".merge.tmp")
    import shutil
    try:
        sources=sorted(set(directory.glob("*.jsonl.gz")) | {path})
        with temporary.open("wb") as output:
            for source in sources:
                with (source if source.exists() else raw).open("rb") as stream:shutil.copyfileobj(stream,output,CHUNK_SIZE)
        aggregate=parse_archive(temporary,multipart=True)
        if not path.exists():
            staged=path.with_suffix(".tmp");shutil.copyfile(raw,staged);staged.replace(path)
        for item,source in zip(aggregate["summary"]["archive_parts"],sources,strict=True):
            item["sha256"]=archive_hash(source)
        temporary.replace(combined)
    finally:
        temporary.unlink(missing_ok=True)
    return combined,aggregate
