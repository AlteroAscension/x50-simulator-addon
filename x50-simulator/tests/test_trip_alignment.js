const assert = require('node:assert/strict');
const {alignJournalPoints, splitAlignedJournalSegments, isNavigationTrajectory} = require('../app/trip_alignment.js');

assert.equal(isNavigationTrajectory({trajectory_schema: 'x50.virtual-trajectory.v2'}), true);
assert.equal(isNavigationTrajectory({source: 'ha_full_trip_journal'}), true);
assert.equal(isNavigationTrajectory({source: 'experimental_steering_calibration',
  trajectory_schema: 'x50.virtual-trajectory.v2'}), false);

const samples = [
  {time_ms: 1000, mode: 'fake', fake_provider_enabled: true,
    route_generation: 1, fake_lat: 55.0, fake_lon: 37.0},
  {time_ms: 2000, mode: 'fake', fake_provider_enabled: true,
    route_generation: 1, fake_lat: 55.001, fake_lon: 37.0},
  {time_ms: 3000, mode: 'off_route_passthrough', fake_provider_enabled: false,
    route_generation: 1, fake_lat: 55.002, fake_lon: 37.0},
  {time_ms: 5000, mode: 'fake', fake_provider_enabled: true,
    route_generation: 2, fake_lat: 56.0, fake_lon: 38.0},
];
const points = [
  {t_ms: 1500, x_m: 999, y_m: 999, segment_id: 0},
  {t_ms: 2500, x_m: 1000, y_m: 1000, segment_id: 0},
  {t_ms: 4000, x_m: 1001, y_m: 1001, segment_id: 0},
  {t_ms: 5000, x_m: 1002, y_m: 1002, segment_id: 1},
  {t_ms: 5500, aligned_lat: 56.0001, aligned_lon: 38.0001,
    alignment_source: 'fakegps_route', alignment_route_generation: 2},
];
const aligned = alignJournalPoints(points, samples);
assert.equal(aligned[0].lat, 55.0005);
assert.equal(aligned[0].source, 'ha_fakegps');
assert.equal(aligned[1], null); // no interpolation across missing FakeGPS
assert.equal(aligned[2], null); // no bridge between route generations
assert.equal(aligned[3].routeGeneration, 2);
assert.equal(aligned[4].lat, 56.0001); // owner-published point wins
assert.equal(aligned[4].source, 'navigation');
const curved = alignJournalPoints([{t_ms: 1500, segment_id: 0}], [
  {time_ms: 1000, mode: 'fake', fake_provider_enabled: true, route_generation: 7,
    route_snapshot_id: 'bend', progress_m: 0, fake_lat: 55, fake_lon: 37},
  {time_ms: 2000, mode: 'fake', fake_provider_enabled: true, route_generation: 7,
    route_snapshot_id: 'bend', progress_m: 175, fake_lat: 55.001, fake_lon: 37.001},
], [{snapshot_id: 'bend', length_m: 175,
  points: [[55, 37], [55.001, 37], [55.001, 37.001]]}]);
assert.equal(curved[0].source, 'ha_route_progress');
assert.ok(Math.abs(curved[0].lon - 37) < 0.000001);
const wrongRoute = alignJournalPoints([{t_ms: 1500}], [
  {time_ms: 1000, mode: 'fake', fake_provider_enabled: true, route_generation: 7,
    route_snapshot_id: 'bad', progress_m: 0, fake_lat: 55, fake_lon: 37},
  {time_ms: 2000, mode: 'fake', fake_provider_enabled: true, route_generation: 7,
    route_snapshot_id: 'bad', progress_m: 100, fake_lat: 55.001, fake_lon: 37},
], [{snapshot_id: 'bad', length_m: 100,
  points: [[56, 38], [56.001, 38]]}]);
assert.equal(wrongRoute[0], null); // captured route belongs somewhere else
const fragments = splitAlignedJournalSegments([
  aligned[0], {...aligned[0], pt: {t_ms: 1600, segment_id: 0}},
  null, aligned[3], {...aligned[3], pt: {t_ms: 5100, segment_id: 1}},
], () => 1);
assert.equal(fragments.length, 2); // no bridge through a GPS/route gap
console.log('Trip FakeGPS alignment checks passed');
