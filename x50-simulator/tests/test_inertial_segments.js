const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require.resolve('../app/app.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function splitTrajectorySegments('),
  source.indexOf('function getTrajectoryAnchor(')), context);
const split = points => JSON.parse(JSON.stringify(context.inertialTrajectorySegments(points)));
const points = [
  {lat:55,lon:37,segment_id:0}, {lat:55.001,lon:37,segment_id:0},
  {lat:56,lon:38,segment_id:1}, {lat:56.001,lon:38,segment_id:1},
];
assert.deepEqual(split(points), [[[55,37],[55.001,37]],[[56,38],[56.001,38]]]);
assert.equal(split(points.map(({lat,lon})=>({lat,lon}))).length, 1);
assert.deepEqual(split([{lat:null,lon:37},{lat:'bad',lon:37}]), []);
console.log('Inertial re-anchor segments and legacy recordings passed');
