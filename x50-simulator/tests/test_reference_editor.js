const assert=require('node:assert/strict');
const {rope,distance,destination,joinFragment,quality}=require('../app/reference_editor.js');
const origin={lat:55,lon:37};
const nodes=[{...origin,rest_m:0,break_before:true}];
for(let i=1;i<50;i++)nodes.push({...destination(nodes[i-1],i*7,10),rest_m:10,break_before:false,evidence:{}});
nodes[30].break_before=true;nodes[30].rest_m=0;
const untouched=JSON.stringify(nodes.slice(30));
rope(nodes,15,destination(nodes[15],90,80));
for(let i=1;i<30;i++)assert.ok(Math.abs(distance(nodes[i-1],nodes[i])-10)<1e-6);
assert.equal(JSON.stringify(nodes.slice(30)),untouched,'Rope must stop at a virtual break');
assert.ok(joinFragment(nodes,35));
assert.ok(distance(nodes[29],nodes[30])<1e-8);
assert.equal(nodes[30].break_before,true,'Joining must preserve the unmeasured evidence boundary');
for(let i=31;i<nodes.length;i++)assert.ok(Math.abs(distance(nodes[i-1],nodes[i])-10)<1e-6);
assert.equal(quality(nodes,10).color,'#94a3b8','Absent angular sensors cannot be green');
const line=[{...origin,break_before:true},{...destination(origin,90,10),rest_m:10,evidence:{step:{compass_valid:true,compass_age_ms:100,compass_sensor_calibrated_deg:0}}}];
assert.equal(quality(line,1).color,'#ff6277','Compass conflict must be visible');
const sensors={compass_valid:true,compass_age_ms:100,compass_sensor_calibrated_deg:90,
  steering_fresh:true,steer_deg:0,steering_ratio:15.5,wheelbase_m:2.6};
const straight=[{...origin,break_before:true,rest_m:0},
  {...destination(origin,90,10),break_before:false,rest_m:10,evidence:{step:sensors}},
  {...destination(origin,90,20),break_before:false,rest_m:10,evidence:{step:sensors}}];
assert.equal(quality(straight,2).color,'#36e6a1','Consistent fresh sensors must be green');
straight[2].evidence={step:{...sensors,motion_direction:-1,compass_sensor_calibrated_deg:270}};
straight[1].evidence=straight[2].evidence;
assert.equal(quality(straight,2).color,'#36e6a1','Reverse displacement must be compared to body heading');
console.log('Reference geometry: rope lengths, fragment boundaries, missing sensors and compass conflicts PASS');
