"""Recover steering traces from completed Navigation diagnostic journals."""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path


def journal_points(path: Path) -> list[dict]:
    """Read retained engine points; 10 Hz journal rows repeat the latest point."""
    points = []
    seen = set()
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("type") != "vehicle_sample":
                continue
            point = row.get("data", {}).get("trajectory_point")
            if not isinstance(point, dict):
                continue
            stamp = point.get("t_ms")
            if not isinstance(stamp, (int, float)) or stamp in seen:
                continue
            seen.add(stamp)
            points.append(point)
    points.sort(key=lambda item: item["t_ms"])
    return points


def journal_inertial_points(path: Path) -> list[dict]:
    """Use the final revision of each recorded pose, without joining segments."""
    revised, causal, last_revision = {}, {}, {}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            data = row.get("data") or {}
            wall, elapsed = row.get("time_ms"), row.get("elapsed_ms")
            if not all(isinstance(t, (int, float)) and math.isfinite(t) for t in (wall, elapsed)):
                continue
            if row.get("type") == "inertial_fusion_revision":
                for source in data.get("points") or []:
                    at = source.get("elapsed_ms")
                    if not isinstance(at, (int, float)) or not math.isfinite(at):
                        continue
                    position = _inertial_position(source)
                    if position is None:
                        continue
                    segment = source.get("segment_id", data.get("segment_id", 0))
                    point = dict(source, t_ms=wall-elapsed+at, segment_id=segment)
                    revised[(segment, at)] = point
                    last_revision[segment] = max(last_revision.get(segment, at), at)
            elif row.get("type") == "vehicle_sample":
                step = data.get("inertial_step") or {}
                fusion = step.get("fusion") or {}
                source = fusion if fusion.get("anchored") else {
                    "lat": step.get("after_lat"), "lon": step.get("after_lon"),
                    "heading_deg": step.get("after_heading_deg"), "segment_id": step.get("segment_id", 0)}
                if _inertial_position(source) is None:
                    continue
                at = step.get("elapsed_ms", elapsed)
                if not isinstance(at, (int, float)) or not math.isfinite(at):
                    continue
                segment = source.get("segment_id", 0)
                causal[(segment, int(at//1000))] = dict(source, elapsed_ms=at,
                    t_ms=wall-elapsed+at, segment_id=segment)
    # Dense causal points preceding a revision would redraw its old, uncorrected
    # curve. Only append the unsolved end or segments with no revision at all.
    points = list(revised.values())
    points.extend(p for (segment, _), p in causal.items()
                  if segment not in last_revision or p["elapsed_ms"] > last_revision[segment])
    return sorted(points, key=lambda p: p["t_ms"])


def _inertial_position(point):
    try:
        lat, lon = float(point["lat"]), float(point["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    return (lat, lon) if math.isfinite(lat) and math.isfinite(lon) and abs(lat)<=90 and abs(lon)<=180 else None


STEERING_EVENTS = frozenset({
    "steering_overlay_fit", "steering_overlay_correction",
    "steering_overlay_applied",
    "steering_overlay_divergence_started", "steering_overlay_divergence_finished",
    "steering_route_rebuild_handoff",
})


def journal_steering_events(path: Path) -> list[dict]:
    """Retain only Navigation steering decisions with their original wall time."""
    events = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            kind = row.get("type")
            stamp = row.get("time_ms")
            if kind not in STEERING_EVENTS or not isinstance(stamp, (int, float)):
                continue
            data = row.get("data")
            if not isinstance(data, dict):
                data = {}
            events.append({"event": kind, "time_ms": stamp, "seq": row.get("seq"),
                           "data": data})
    # Navigation may apply small progress steps on every drive tick. Keep the
    # precise signed total while limiting map markers to one per second.
    grouped = {}
    result = []
    for event in events:
        if event["event"] != "steering_overlay_applied":
            result.append(event)
            continue
        key = int(event["time_ms"] // 1000)
        if key not in grouped:
            grouped[key] = dict(event, data=dict(event["data"]))
            grouped[key]["data"]["operations"] = 0
            grouped[key]["data"]["applied_m"] = 0.0
            result.append(grouped[key])
        grouped[key]["time_ms"] = event["time_ms"]
        grouped[key]["data"]["operations"] += 1
        amount = event["data"].get("applied_m")
        if isinstance(amount, (int, float)) and math.isfinite(amount):
            grouped[key]["data"]["applied_m"] += amount
    return sorted(result, key=lambda item: item["time_ms"])


def _position(sample, prefix):
    try:
        lat = float(sample[prefix + "_lat"])
        lon = float(sample[prefix + "_lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if math.isfinite(lat) and math.isfinite(lon) and abs(lat) <= 90 and abs(lon) <= 180:
        return lat, lon
    return None


def _bearing(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _distance_m(a, b):
    lat = math.radians((a[0] + b[0]) / 2)
    north = (b[0] - a[0]) * 111_132
    east = (b[1] - a[1]) * 111_320 * math.cos(lat)
    return math.hypot(north, east)


def _anchor(trip, started_ms):
    samples = sorted(trip.get("samples", []), key=lambda item: abs(item.get("time_ms", 0) - started_ms))
    for sample in samples:
        if abs(sample.get("time_ms", 0) - started_ms) > 30_000:
            break
        position = _position(sample, "carlinkit")
        if position and sample.get("gps_good"):
            bearing = sample.get("carlinkit_bearing")
            # At trip start a repeated GPS fix can carry the preceding parking
            # heading. Prefer the first actual displacement along the drive.
            for later in sorted(trip.get("samples", []), key=lambda item: item.get("time_ms", 0)):
                if later.get("time_ms", 0) < started_ms or later.get("time_ms", 0) > started_ms + 120_000:
                    continue
                moved = _position(later, "carlinkit")
                if moved and later.get("gps_good") and 10 <= _distance_m(position, moved) <= 100:
                    reported = later.get("carlinkit_bearing")
                    bearing = reported if isinstance(reported, (int, float)) and math.isfinite(reported) else _bearing(position, moved)
                    break
            if isinstance(bearing, (int, float)) and math.isfinite(bearing):
                return {"has_anchor": True, "start_latitude": position[0],
                        "start_longitude": position[1], "start_bearing_deg": bearing,
                        "source": "nearest_trip_gps"}
    for sample in samples:
        if abs(sample.get("time_ms", 0) - started_ms) > 30_000:
            break
        position = _position(sample, "fake")
        if position:
            bearing = sample.get("carlinkit_bearing")
            return {"has_anchor": True, "start_latitude": position[0],
                    "start_longitude": position[1],
                    "start_bearing_deg": bearing if isinstance(bearing, (int, float)) and math.isfinite(bearing) else 0,
                    "source": "nearest_trip_fake"}
    for route in trip.get("routes", []):
        positions = route.get("points") or []
        if len(positions) >= 2:
            first, second = positions[:2]
            return {"has_anchor": True, "start_latitude": first[0],
                    "start_longitude": first[1], "start_bearing_deg": _bearing(first, second),
                    "source": "route_start_fallback"}
    return {"has_anchor": False}


def trajectory_for_trip(points: list[dict], journal_id: str, trip: dict,
                        events: list[dict] | None = None,
                        inertial_points: list[dict] | None = None) -> dict | None:
    summary = trip.get("summary", {})
    trip_id = str(summary.get("id") or "")
    start = summary.get("started_ms")
    end = summary.get("ended_ms")
    if not trip_id or not isinstance(start, (int, float)):
        return None
    if not isinstance(end, (int, float)):
        end = float("inf")
    selected = [point for point in points if start <= point["t_ms"] <= end]
    if len(selected) < 2:
        return None
    origin = selected[0]
    heading0 = math.radians(float(origin["heading_deg"]))
    cos_h, sin_h = math.cos(heading0), math.sin(heading0)
    x0, y0 = float(origin["x_m"]), float(origin["y_m"])
    first_segment = int(origin.get("segment_id") or 0)
    normalized = []
    for source in selected:
        dx, dy = float(source["x_m"]) - x0, float(source["y_m"]) - y0
        point = dict(source)
        point["x_m"] = round(dx * cos_h + dy * sin_h, 2)
        point["y_m"] = round(-dx * sin_h + dy * cos_h, 2)
        point["heading_deg"] = round(float(source["heading_deg"]) - float(origin["heading_deg"]), 1)
        point["segment_id"] = int(source.get("segment_id") or 0) - first_segment
        normalized.append(point)
    result = {
        "trajectory_schema": "x50.virtual-trajectory.v2",
        "trajectory_id": f"journal_{journal_id}_{trip_id}",
        "source": "ha_full_trip_journal",
        "source_journal_id": journal_id,
        "matched_trip_id": trip_id,
        "started_at_ms": normalized[0]["t_ms"],
        "ended_at_ms": normalized[-1]["t_ms"],
        "duration_s": round((normalized[-1]["t_ms"] - normalized[0]["t_ms"]) / 1000, 3),
        "distance_m": round(float(normalized[-1]["dist_m"]) - float(normalized[0]["dist_m"]), 1),
        "point_count": len(normalized),
        "segment_count": len({point["segment_id"] for point in normalized}),
        "complete": True,
        "anchor": _anchor(trip, normalized[0]["t_ms"]),
        "points": normalized,
        "events": [event for event in (events or [])
                   if start <= event["time_ms"] <= end],
        "journal_import_version": 2,
    }
    selected_inertial = [dict(p) for p in (inertial_points or []) if start <= p["t_ms"] <= end]
    if selected_inertial:
        result["inertial"] = {"schema": "x50.inertial-trajectory.v1",
                              "source": "recorded_journal_revisions",
                              "point_count": len(selected_inertial), "points": selected_inertial}
    return result
