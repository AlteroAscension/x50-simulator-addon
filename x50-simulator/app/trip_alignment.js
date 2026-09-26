/* Dense route-constrained steering points. The sensor x/y trace remains raw. */
function isNavigationTrajectory(trajectory) {
  return !!trajectory && trajectory.source !== 'experimental_steering_calibration'
    && (trajectory.source === 'ha_full_trip_journal'
      || trajectory.trajectory_schema === 'x50.virtual-trajectory.v2');
}

function alignJournalPoints(points, samples, routes = []) {
  const valid = (lat, lon) => lat != null && lon != null
    && Number.isFinite(Number(lat)) && Number.isFinite(Number(lon))
    && Math.abs(Number(lat)) <= 90 && Math.abs(Number(lon)) <= 180;
  const metres = (a, b) => Math.hypot((a[0] - b[0]) * 111132,
    (a[1] - b[1]) * 111320 * Math.cos(a[0] * Math.PI / 180));
  const routeById = new Map((routes || []).map(route => [route.snapshot_id, route]));
  const routeCache = new Map();
  const onRoute = (snapshotId, progress) => {
    const route = routeById.get(snapshotId);
    if (!route || !Number.isFinite(Number(progress))) return null;
    let prepared = routeCache.get(snapshotId);
    if (!prepared) {
      const positions = (route.points || []).filter(p => Array.isArray(p)
        && valid(p[0], p[1])).map(p => [Number(p[0]), Number(p[1])]);
      if (positions.length < 2) return null;
      const cumulative = [0];
      for (let i = 1; i < positions.length; i++)
        cumulative.push(cumulative[i - 1] + metres(positions[i - 1], positions[i]));
      prepared = {positions, cumulative, length: cumulative[cumulative.length - 1]};
      routeCache.set(snapshotId, prepared);
    }
    const total = Number(route.length_m);
    const target = Math.max(0, Math.min(prepared.length,
      Number(progress) * prepared.length / (total > 0 ? total : prepared.length)));
    let index = 1;
    while (index < prepared.cumulative.length - 1 && prepared.cumulative[index] < target) index++;
    const before = prepared.cumulative[index - 1], after = prepared.cumulative[index];
    const fraction = after > before ? (target - before) / (after - before) : 0;
    const a = prepared.positions[index - 1], b = prepared.positions[index];
    return [a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])];
  };
  const anchors = (samples || []).filter(sample => sample.mode === 'fake'
    && sample.fake_provider_enabled && valid(sample.fake_lat, sample.fake_lon)
    && Number.isFinite(Number(sample.time_ms))
    && sample.route_generation != null
    && Number.isFinite(Number(sample.route_generation)))
    .sort((a, b) => Number(a.time_ms) - Number(b.time_ms));
  let cursor = 0;
  return (points || []).map(pt => {
    if (pt.alignment_source === 'fakegps_route'
        && valid(pt.aligned_lat, pt.aligned_lon)) {
      return {lat: Number(pt.aligned_lat), lon: Number(pt.aligned_lon), pt,
        routeGeneration: Number(pt.alignment_route_generation), source: 'navigation'};
    }
    const time = Number(pt.t_ms);
    if (!Number.isFinite(time) || !anchors.length) return null;
    while (cursor + 1 < anchors.length && Number(anchors[cursor + 1].time_ms) <= time) cursor++;
    const before = anchors[cursor], after = anchors[cursor + 1];
    const left = Number(before.time_ms), right = after && Number(after.time_ms);
    if (after && time >= left && time <= right && right - left <= 4000
        && Number(before.route_generation) === Number(after.route_generation)) {
      const fraction = right === left ? 0 : (time - left) / (right - left);
      if (before.route_snapshot_id && before.route_snapshot_id === after.route_snapshot_id) {
        const start = onRoute(before.route_snapshot_id, before.progress_m);
        const end = onRoute(after.route_snapshot_id, after.progress_m);
        if (start && end
            && metres(start, [Number(before.fake_lat), Number(before.fake_lon)]) <= 20
            && metres(end, [Number(after.fake_lat), Number(after.fake_lon)]) <= 20) {
          const position = onRoute(before.route_snapshot_id,
            Number(before.progress_m) + fraction * (Number(after.progress_m) - Number(before.progress_m)));
          if (position) return {lat: position[0], lon: position[1], pt,
            routeGeneration: Number(before.route_generation), source: 'ha_route_progress'};
        }
        if (routeById.has(before.route_snapshot_id)) return null;
      }
      return {lat: Number(before.fake_lat) + fraction * (Number(after.fake_lat) - Number(before.fake_lat)),
        lon: Number(before.fake_lon) + fraction * (Number(after.fake_lon) - Number(before.fake_lon)),
        pt, routeGeneration: Number(before.route_generation), source: 'ha_fakegps'};
    }
    if (after && time > left && time < right) return null;
    // Do not extend a line across a route change or a stale FakeGPS period.
    const nearest = [before, after].filter(Boolean)
      .sort((a, b) => Math.abs(Number(a.time_ms) - time) - Math.abs(Number(b.time_ms) - time))[0];
    if (!nearest || Math.abs(Number(nearest.time_ms) - time) > 1000) return null;
    const routePosition = nearest.route_snapshot_id
      && onRoute(nearest.route_snapshot_id, nearest.progress_m);
    if (routePosition && metres(routePosition,
        [Number(nearest.fake_lat), Number(nearest.fake_lon)]) <= 20)
      return {lat: routePosition[0], lon: routePosition[1], pt,
        routeGeneration: Number(nearest.route_generation), source: 'ha_route_progress'};
    if (routeById.has(nearest.route_snapshot_id)) return null;
    return {lat: Number(nearest.fake_lat), lon: Number(nearest.fake_lon), pt,
      routeGeneration: Number(nearest.route_generation), source: 'ha_fakegps'};
  });
}

function splitAlignedJournalSegments(aligned, distanceMetres) {
  const segments = [];
  let current = [], previous = null;
  const flush = () => { if (current.length > 1) segments.push(current); current = []; };
  for (const point of aligned) {
    if (!point) { flush(); previous = null; continue; }
    const stamp = Number(point.pt.t_ms);
    const segmentId = Number(point.pt.segment_id ?? 0);
    if (previous && (point.routeGeneration !== previous.routeGeneration
        || segmentId !== Number(previous.pt.segment_id ?? 0)
        || stamp - Number(previous.pt.t_ms) > 3000
        || distanceMetres(point, previous) > 35)) flush();
    current.push(point);
    previous = point;
  }
  flush();
  return segments;
}

if (typeof module !== 'undefined') module.exports = {
  alignJournalPoints, splitAlignedJournalSegments, isNavigationTrajectory
};
