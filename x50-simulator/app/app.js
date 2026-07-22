const $ = id => document.getElementById(id);
const getApiBase = () => {
  let path = location.pathname;
  if (path.endsWith('.html')) path = path.substring(0, path.lastIndexOf('/'));
  if (path.endsWith('/')) path = path.slice(0, -1);
  return path;
};
const API_BASE = getApiBase();
const map = L.map('map', {zoomControl:false, attributionControl:false}).setView([55.751244,37.618423], 12);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {maxZoom:20, subdomains:'abcd'}).addTo(map);

const icons = type => L.divIcon({className:`sim-marker ${type}`,html:'<div></div>',iconSize:[22,22],iconAnchor:[11,11]});
const routeLine = L.polyline([], {color:'#36caff',weight:6,opacity:.8,lineCap:'round'}).addTo(map);
const routeGlow = L.polyline([], {color:'#1676ff',weight:14,opacity:.16,lineCap:'round'}).addTo(map);
const exactRouteLine = L.polyline([], {color:'#39efa0',weight:4,opacity:.94,lineCap:'round'}).addTo(map);
const rawRenderer = L.canvas({padding:.5});
const exactPointLayer = L.layerGroup().addTo(map);
const guidancePointLayer = L.layerGroup().addTo(map);
const historyPointLayer = L.layerGroup().addTo(map);
const staleHistoryPointLayer = L.layerGroup();
const speedLimitLayer = L.layerGroup().addTo(map);
const roadEventLayer = L.layerGroup().addTo(map);
const tripRealTrackLayer = L.layerGroup().addTo(map);
const tripFakeTrackLayer = L.layerGroup().addTo(map);
const tripRouteLayer = L.layerGroup().addTo(map);
const tripEventLayer = L.layerGroup().addTo(map);
const segmentHighlight = L.polyline([], {color:'#ffbd4a',weight:10,opacity:.96,lineCap:'round',className:'segment-highlight'}).addTo(map);
const segmentInspectMarker = L.circleMarker([0,0],{radius:6,weight:2,color:'#fff',fillColor:'#ffbd4a',fillOpacity:1,interactive:false});
let selectedMarker, rawMarker, sentMarker, state=null, routeRevision=null,mapkitRevision=null,routeLayerMode='both',showStalePoints=false,stateBusy=false,routeBusy=false,toastTimer,lastShownError=null;
let mapClickMode='gps',currentRoutePoints=[],currentMapkitData={},inspectedSegmentIndex=null,hasAutoFittedRoute=false,userAdjustedMap=false;
let tripsBusy=false,selectedTripId=null,lastTripSignature='',selectedTripData=null,tripTrackMode='both',tripShowRoutes=true;
const tripRouteColors=['#a977ff','#3bd6ff','#ff6fae','#ffe06b','#66e0bd','#ff916b','#79a7ff','#d9f06b'];

function toast(message, error=false){const el=$('toast');el.textContent=message;el.style.borderColor=error?'rgba(255,98,119,.45)':'';el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2400)}
async function request(path, options={}){const response=await fetch(`${API_BASE}${path}`,{credentials:'include',headers:{'Content-Type':'application/json','X-X50-Client':'navigation-lab'},...options});const text=await response.text();let data;try{data=JSON.parse(text)}catch(err){if(!response.ok)throw new Error(`HTTP ${response.status}: ${text.slice(0,60)}`);throw new Error(`Ошибка ответа (${response.status}): ${text.slice(0,60)}`)}if(!response.ok)throw new Error(data.detail||data.error||`HTTP ${response.status}`);return data}
async function control(patch, quiet=true){try{return await request('/api/controller/control',{method:'POST',body:JSON.stringify(patch)})}catch(error){if(!quiet)toast(error.message,true);throw error}}
function number(id){return Number($(id).value)}
function pct(id){return number(id)/100}
function saved(){try{return JSON.parse(localStorage.getItem('x50-simulator')||'{}')}catch{return {}}}
function persist(extra={}){const previous=saved();delete previous.token;localStorage.setItem('x50-simulator',JSON.stringify({...previous,vehicleScale:number('vehicleScale'),odoScale:number('odoScale'),gpsScale:number('gpsScale'),gpsHz:number('gpsHz'),...extra}))}

function setRouteLayer(mode){
  routeLayerMode=mode;
  const showLine=mode==='line'||mode==='both',showPoints=mode==='points'||mode==='both';
  for(const layer of [routeGlow,routeLine,exactRouteLine,speedLimitLayer])showLine?layer.addTo(map):layer.remove();
  for(const layer of [exactPointLayer,guidancePointLayer,historyPointLayer])showPoints?layer.addTo(map):layer.remove();
  showPoints&&showStalePoints?staleHistoryPointLayer.addTo(map):staleHistoryPointLayer.remove();
  document.querySelectorAll('#routeLayers button').forEach(button=>button.classList.toggle('active',button.dataset.layer===mode));
  persist({routeLayer:mode});
}

function setStalePoints(show){showStalePoints=show;$('stalePointsToggle').classList.toggle('active',show);setRouteLayer(routeLayerMode);persist({showStalePoints:show})}
function fillPointLayer(layer,points,style){layer.clearLayers();for(const p of points)L.circleMarker([p[0],p[1]],{renderer:rawRenderer,interactive:false,...style}).addTo(layer)}

function mapkitPosition(points,position){
  if(!position||!points.length)return null;
  const index=Math.max(0,Math.min(points.length-1,Number(position.segment_index)||0));
  const a=points[index],b=points[Math.min(points.length-1,index+1)],t=Math.max(0,Math.min(1,Number(position.segment_position)||0));
  return [a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
}
function roadIcon(symbol,type){return L.divIcon({className:`road-object ${type}`,html:`<span>${symbol}</span>`,iconSize:[22,22],iconAnchor:[11,11]})}
function speedColor(limit){return limit<=20?'#ff8d4d':limit<=30?'#ffbd4a':limit<=40?'#d8df55':limit<=60?'#38e28b':'#3bd6ff'}
function updateMapkitRoute(points,data){
  speedLimitLayer.clearLayers();roadEventLayer.clearLayers();
  const limits=Array.isArray(data.speed_limits_mps)?data.speed_limits_mps:[];
  let start=0;
  for(let i=1;i<=limits.length;i++){
    if(i<limits.length&&Math.abs(Number(limits[i])-Number(limits[start]))<.001)continue;
    const kmh=Math.round(Number(limits[start])*3.6),segment=points.slice(start,Math.min(points.length,i+1));
    if(segment.length>1)L.polyline(segment,{color:speedColor(kmh),weight:7,opacity:.76,lineCap:'round'}).bindTooltip(`Ограничение ${kmh} км/ч · сегменты ${start}–${i-1}`).addTo(speedLimitLayer);
    start=i;
  }
  const addPositioned=(items,symbol,type,label)=>{for(const item of items||[]){const p=mapkitPosition(points,item.position||item);if(p)L.marker(p,{icon:roadIcon(symbol,type),zIndexOffset:650}).bindTooltip(label(item)).addTo(roadEventLayer)}};
  for(const event of data.events||[]){const p=Array.isArray(event.location)?event.location:mapkitPosition(points,event.position);if(!p)continue;const tags=(event.tags||[]).join(', '),limit=event.speed_limit_mps==null?'':` · ${Math.round(event.speed_limit_mps*3.6)} км/ч`,camera=event.camera_data?` · камера ${event.camera_data.in_back?'в спину':''}${event.camera_data.in_face?' в лицо':''}`:'';L.marker(p,{icon:roadIcon('C','camera'),zIndexOffset:800}).bindTooltip(`${tags||'Дорожное событие'}${limit}${camera}`).addTo(roadEventLayer)}
  addPositioned(data.traffic_lights,'●','light',item=>`Светофор${item.id?` · ${item.id}`:''}`);
  addPositioned(data.speed_bumps,'≈','bump',()=>`Лежачий полицейский`);
  addPositioned(data.pedestrian_crossings,'↔','crossing',()=>`Пешеходный переход`);
  const counts=data.feature_counts||{};
  $('mapkitSchema').textContent=data.schema||'—';
  $('speedLimitCount').textContent=limits.length;
  $('eventCount').textContent=(data.events||[]).length;
  $('trafficLightCount').textContent=(data.traffic_lights||[]).length;
  $('speedBumpCount').textContent=(data.speed_bumps||[]).length;
  $('crossingCount').textContent=(data.pedestrian_crossings||[]).length;
  $('laneCount').textContent=(data.lane_signs||[]).length;
  $('jamCount').textContent=(data.jam_segments||[]).length;
  $('mapkitDataState').textContent=limits.length===Math.max(0,points.length-1)?'Сегменты синхронизированы':`Сегменты ${limits.length}/${Math.max(0,points.length-1)}`;
  $('mapkitDataState').classList.toggle('warn',limits.length!==Math.max(0,points.length-1));
  void counts;
}

function setMapClickMode(mode,quiet=false){
  mapClickMode=mode==='inspect'?'inspect':'gps';
  document.body.classList.toggle('inspect-mode',mapClickMode==='inspect');
  document.querySelectorAll('#mapClickMode button').forEach(button=>button.classList.toggle('active',button.dataset.clickMode===mapClickMode));
  persist({mapClickMode});
  if(!quiet)toast(mapClickMode==='inspect'?'Клик по карте: данные сегмента':'Клик по карте: передача GPS-точки');
}

function segmentPositionValue(position){return position?(Number(position.segment_index)||0)+Math.max(0,Math.min(1,Number(position.segment_position)||0)):null}
function rangeContainsSegment(range,index){const begin=segmentPositionValue(range?.begin),end=segmentPositionValue(range?.end),middle=index+.5;return begin!=null&&end!=null&&middle>=begin&&middle<=end}
function itemsAtSegment(items,index){return (items||[]).filter(item=>Math.floor(Number((item.position||item)?.segment_index))===index)}
function closestRouteSegment(latlng){
  if(currentRoutePoints.length<2)return null;
  const click=map.latLngToContainerPoint(latlng);let best=null;
  for(let index=0;index<currentRoutePoints.length-1;index++){
    const a=currentRoutePoints[index],b=currentRoutePoints[index+1],pa=map.latLngToContainerPoint(a),pb=map.latLngToContainerPoint(b);
    const dx=pb.x-pa.x,dy=pb.y-pa.y,length2=dx*dx+dy*dy;
    const t=length2?Math.max(0,Math.min(1,((click.x-pa.x)*dx+(click.y-pa.y)*dy)/length2)):0;
    const x=pa.x+dx*t,y=pa.y+dy*t,distance=Math.hypot(click.x-x,click.y-y);
    if(!best||distance<best.distance)best={index,distance,t,point:[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t]};
  }
  return best;
}
function inspectSegment(index,clickPoint=null){
  if(index<0||index>=currentRoutePoints.length-1)return;
  inspectedSegmentIndex=index;
  const a=currentRoutePoints[index],b=currentRoutePoints[index+1],data=currentMapkitData||{};
  const limit=Number(data.speed_limits_mps?.[index]),jam=data.jam_segments?.[index]||null;
  const sectionIndex=(data.sections||[]).findIndex(section=>rangeContainsSegment(section.geometry,index));
  const section=sectionIndex>=0?data.sections[sectionIndex]:null;
  const events=itemsAtSegment(data.events,index),trafficLights=itemsAtSegment(data.traffic_lights,index),speedBumps=itemsAtSegment(data.speed_bumps,index),crossings=itemsAtSegment(data.pedestrian_crossings,index),laneSigns=itemsAtSegment(data.lane_signs,index);
  const details={segment_index:index,start:{lat:a[0],lon:a[1]},end:{lat:b[0],lon:b[1]},length_m:Number(map.distance(a,b).toFixed(2)),speed_limit_mps:Number.isFinite(limit)?limit:null,speed_limit_kmh:Number.isFinite(limit)?Number((limit*3.6).toFixed(1)):null,jam,section_index:sectionIndex>=0?sectionIndex:null,section,events,traffic_lights:trafficLights,speed_bumps:speedBumps,pedestrian_crossings:crossings,lane_signs:laneSigns,hd_section:(data.hd_sections||[]).some(range=>rangeContainsSegment(range,index)),standing:(data.standing_segments||[]).some(range=>rangeContainsSegment(range,index))};
  segmentHighlight.setLatLngs([a,b]);
  if(!map.hasLayer(segmentHighlight))segmentHighlight.addTo(map);
  segmentInspectMarker.setLatLng(clickPoint||[(a[0]+b[0])/2,(a[1]+b[1])/2]);if(!map.hasLayer(segmentInspectMarker))segmentInspectMarker.addTo(map);
  $('segmentTitle').textContent=`Сегмент ${index}`;
  $('segmentSpeedLimit').textContent=Number.isFinite(limit)?`${Math.round(limit*3.6)} км/ч`:'нет данных';
  $('segmentJam').textContent=jam?`${jam.jam_type||'—'}${jam.speed_mps==null?'':` · ${Math.round(Number(jam.speed_mps)*3.6)} км/ч`}`:'нет данных';
  $('segmentLength').textContent=`${details.length_m.toFixed(1)} м`;$('segmentSection').textContent=sectionIndex>=0?`№ ${sectionIndex}`:'—';
  const annotation=section?.annotation||{},objects=[];if(events.length)objects.push(`событий: ${events.length}`);if(trafficLights.length)objects.push(`светофоров: ${trafficLights.length}`);if(speedBumps.length)objects.push(`неровностей: ${speedBumps.length}`);if(crossings.length)objects.push(`переходов: ${crossings.length}`);if(laneSigns.length)objects.push(`схем полос: ${laneSigns.length}`);if(details.hd_section)objects.push('HD-секция');if(details.standing)objects.push('остановка');
  $('segmentDescription').textContent=[annotation.description,annotation.toponym,objects.join(' · ')].filter(Boolean).join(' · ')||'Дополнительных объектов на сегменте нет';
  $('segmentCoordinates').textContent=`${a[0].toFixed(6)}, ${a[1].toFixed(6)} → ${b[0].toFixed(6)}, ${b[1].toFixed(6)}`;
  $('segmentJson').textContent=JSON.stringify(details,null,2);$('segmentInspector').classList.add('open');
}
function closeSegmentInspector(){inspectedSegmentIndex=null;$('segmentInspector').classList.remove('open');segmentHighlight.setLatLngs([]);segmentInspectMarker.remove()}

function calibrationPatch(){persist();const patch={vehicle_speed_scale:pct('vehicleScale'),odometer_scale:pct('odoScale'),gps_speed_scale:pct('gpsScale'),gps_hz:number('gpsHz'),gateway_url:$('gatewayUrl').value,ha_url:$('haUrl').value,odometer_km:number('odometer')};const token=$('token').value.trim();if(token)patch.token=token;return patch}
let controlTimer;
function queueControl(patch){clearTimeout(controlTimer);controlTimer=setTimeout(()=>control({...calibrationPatch(),...patch}).catch(()=>{}),70)}

function formatCoord(point){return point?`${point.lat.toFixed(6)}, ${point.lon.toFixed(6)}`:'Нажмите на карту'}
function setMarker(current, point, type){if(!point)return current;if(current)return current.setLatLng([point.lat,point.lon]);return L.marker([point.lat,point.lon],{icon:icons(type),zIndexOffset:type==='sent'?1000:500}).addTo(map)}
function updateState(next){state=next;
  $('runState').textContent=next.running?'Работает':'Остановлен';$('runState').classList.toggle('active',next.running);
  $('runButton').disabled=next.running;$('speedValue').textContent=Math.round(next.target_speed_kmh);
  $('gpsSpeedValue').textContent=next.gps_speed_kmh.toFixed(1);$('vehicleSpeedValue').textContent=next.vehicle_speed_kmh.toFixed(1);
  $('effectiveHz').textContent=next.effective_hz.toFixed(1);$('latency').textContent=next.latency_ms==null?'— мс':`${next.latency_ms.toFixed(0)} мс`;
  $('measuredSpeed').textContent=next.measured_gps_speed_kmh.toFixed(1);$('speedError').textContent=`Δ ${next.speed_error_kmh>=0?'+':''}${next.speed_error_kmh.toFixed(1)} км/ч`;
  $('odoValue').textContent=next.odometer_km.toFixed(3);$('odoBias').textContent=`×${next.odometer_scale.toFixed(3)}`;
  if(document.activeElement!==$('odometer'))$('odometer').value=next.odometer_km.toFixed(3);
  if(document.activeElement!==$('gatewayUrl')&&next.gateway_url)$('gatewayUrl').value=next.gateway_url;
  if(document.activeElement!==$('haUrl')&&next.ha_url)$('haUrl').value=next.ha_url;
  $('sentCount').textContent=next.sent_count;$('failedCount').textContent=next.failed_count;
  $('gatewayChip').classList.toggle('online',next.gateway_online);$('gatewayChip').classList.toggle('warn',!next.gateway_online);
  const isHa=next.gateway_mode==='ha';
  const gwHost=isHa?'🌐 HA Queue':(next.gateway_url||'127.0.0.1:8080').replace(/^https?:\/\//,'');
  $('gatewayChip').querySelector('span').textContent=next.gateway_online?`GW (${gwHost})`:`GW off (${gwHost})`;
  $('routeChip').classList.toggle('online',next.route_available);$('routeChip').querySelector('span').textContent=next.route_available?`${next.route_source==='exact'?'MapKit exact':'Маршрут'} ${(next.route_length_m/1000).toFixed(1)} км`:'Маршрут —';
  $('routeSummary').textContent=next.route_available?`${(next.route_progress_m/1000).toFixed(2)} / ${(next.route_length_m/1000).toFixed(1)} км`:'Ожидание захвата';
  $('routeProgressText').textContent=next.route_available?`${(next.route_progress_m/1000).toFixed(2)} / ${(next.route_length_m/1000).toFixed(1)} км`:'0 / 0 км';
  $('routeProgress').max=Math.max(1,Math.round(next.route_length_m));if(document.activeElement!==$('routeProgress'))$('routeProgress').value=Math.round(next.route_progress_m);
  const fake=next.fake_nav||{};$('gatewayFake').checked=!!fake.enabled;$('fakeMode').textContent=`Gateway Fake: ${fake.enabled?(fake.mode||'вкл.'):'выкл.'}`;
  document.querySelectorAll('#gpsMode button').forEach(button=>button.classList.toggle('active',button.dataset.mode===next.gps_mode));
  $('selectedCoordinate').textContent=formatCoord(next.selected);
  selectedMarker=setMarker(selectedMarker,next.selected,'selected');rawMarker=setMarker(rawMarker,next.last_raw,'raw');sentMarker=setMarker(sentMarker,next.last_sent,'sent');
  if(next.last_error&&next.last_error!==lastShownError){lastShownError=next.last_error;toast(next.last_error,true)}else if(!next.last_error){lastShownError=null}
}

async function pollState(){if(stateBusy)return;stateBusy=true;try{updateState(await request('/api/controller/state'))}catch(error){$('gatewayChip').classList.remove('online');$('gatewayChip').classList.add('warn')}finally{stateBusy=false}}

function tripDate(ms){return new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(ms))}
function tripDuration(seconds){const value=Math.max(0,Math.round(Number(seconds)||0)),hours=Math.floor(value/3600),minutes=Math.floor(value%3600/60),secs=value%60;return hours?`${hours} ч ${minutes} мин`:`${minutes} мин ${secs} с`}
function metric(value,digits=1,suffix=''){const number=Number(value);return Number.isFinite(number)?`${number.toFixed(digits)}${suffix}`:'—'}
function renderTripList(payload){
  const trips=payload.trips||[];$('finishTrip').disabled=!payload.active_trip_id;
  if(!trips.length){$('tripList').innerHTML='<div class="trip-empty">Поездки появятся после начала движения</div>';return}
  $('tripList').innerHTML=trips.map(trip=>`<button class="trip-list-item ${trip.id===selectedTripId?'active':''}" data-trip-id="${trip.id}"><span><b>${tripDate(trip.started_ms)}</b>${trip.active?'<em>идёт сейчас</em>':''}</span><small>${tripDuration(trip.duration_s)} · ${metric(trip.distance_odometer_m/1000,2,' км')}</small><small>GPS: ${trip.gps_outages||0} · коррекций: ${trip.correction_events||0} · Σ ${metric(trip.correction_total_m,1,' м')}</small></button>`).join('');
  document.querySelectorAll('.trip-list-item').forEach(button=>button.addEventListener('click',()=>loadTrip(button.dataset.tripId)));
  if(!selectedTripId&&trips[0])loadTrip(trips[0].id);
}
function drawTripChart(samples,events){
  const canvas=$('tripChart'),box=canvas.getBoundingClientRect(),ratio=window.devicePixelRatio||1,width=Math.max(320,Math.round(box.width)),height=170;canvas.width=width*ratio;canvas.height=height*ratio;const ctx=canvas.getContext('2d');ctx.scale(ratio,ratio);ctx.clearRect(0,0,width,height);ctx.fillStyle='rgba(4,12,21,.58)';ctx.fillRect(0,0,width,height);
  if(!samples.length){ctx.fillStyle='#94a6b9';ctx.font='12px system-ui';ctx.fillText('Недостаточно данных',14,25);return}
  const start=samples[0].time_ms,end=Math.max(start+1,samples[samples.length-1].time_ms),speeds=samples.map(s=>Number(s.vehicle_speed_kmh??s.simulator?.vehicle_speed_kmh??0)),maxSpeed=Math.max(20,...speeds)*1.12,x=ms=>(ms-start)/(end-start)*(width-28)+14,y=value=>height-18-Math.max(0,value)/maxSpeed*(height-34);
  ctx.strokeStyle='rgba(255,255,255,.08)';ctx.lineWidth=1;for(let i=0;i<4;i++){const yy=14+i*(height-32)/3;ctx.beginPath();ctx.moveTo(14,yy);ctx.lineTo(width-14,yy);ctx.stroke()}
  ctx.lineWidth=3;ctx.strokeStyle='#3bd6ff';ctx.beginPath();samples.forEach((s,index)=>{const px=x(s.time_ms),py=y(speeds[index]);index?ctx.lineTo(px,py):ctx.moveTo(px,py)});ctx.stroke();
  ctx.strokeStyle='rgba(255,98,119,.72)';ctx.lineWidth=2;let outageStart=null;samples.forEach(s=>{if(!s.gps_good&&outageStart==null)outageStart=s.time_ms;if(s.gps_good&&outageStart!=null){ctx.fillStyle='rgba(255,98,119,.14)';ctx.fillRect(x(outageStart),14,Math.max(2,x(s.time_ms)-x(outageStart)),height-32);outageStart=null}});if(outageStart!=null){ctx.fillStyle='rgba(255,98,119,.14)';ctx.fillRect(x(outageStart),14,width-14-x(outageStart),height-32)}
  events.forEach(event=>{const px=x(event.time_ms),correction=Number(event.correction_m??event.gps_catch_up_m);ctx.strokeStyle=correction>=0?'#ffbd4a':'#ff6277';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(px,14);ctx.lineTo(px,height-18);ctx.stroke();ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.arc(px,18,4,0,Math.PI*2);ctx.fill()});
  ctx.fillStyle='#94a6b9';ctx.font='10px system-ui';ctx.fillText(`0`,14,height-5);ctx.fillText(`${Math.round(maxSpeed)} км/ч`,14,11);
}
function validTripPoint(record,latKey,lonKey){
  if(record?.[latKey]==null||record?.[lonKey]==null)return null;
  const lat=Number(record[latKey]),lon=Number(record[lonKey]);
  return Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180?[lat,lon]:null;
}
function splitTripTrack(samples,latKey,lonKey,requireGoodGps=false){
  const timestamps=samples.map(sample=>Number(sample.time_ms)).filter(Number.isFinite).sort((a,b)=>a-b),gaps=[];
  for(let index=1;index<timestamps.length;index++){const gap=timestamps[index]-timestamps[index-1];if(gap>0)gaps.push(gap)}
  gaps.sort((a,b)=>a-b);
  const medianGap=gaps.length?gaps[Math.floor(gaps.length/2)]:5000;
  // HA relay normally records every ~5 s. Treat only a gap several times longer
  // than the actual journal cadence as a break in the driven track.
  const breakAfterMs=Math.max(15000,Math.min(120000,medianGap*3.5));
  const segments=[];let segment=[],previousTime=null,previousPoint=null;
  const flush=()=>{if(segment.length)segments.push(segment);segment=[];previousPoint=null};
  for(const sample of samples){
    const point=validTripPoint(sample,latKey,lonKey),time=Number(sample.time_ms);
    if(!point||(requireGoodGps&&!sample.gps_good)||(previousTime!=null&&time-previousTime>breakAfterMs)){flush();previousTime=time;continue}
    previousTime=time;
    if(previousPoint&&map.distance(previousPoint,point)<.5)continue;
    segment.push(point);previousPoint=point;
  }
  flush();return segments;
}
function tripPointCount(segments){return segments.reduce((sum,segment)=>sum+segment.length,0)}
function tripEventLabel(event){
  const shift=Number(event.correction_m??event.gps_catch_up_m),name=event.event==='gps_reacquired'?'GPS вернулся':'Коррекция';
  return `<b>${name}</b><br>${new Date(event.time_ms).toLocaleTimeString('ru-RU')} · ${Number.isFinite(shift)?`${shift>=0?'+':''}${shift.toFixed(1)} м`:'сдвиг —'}`;
}
function tripRoutePoint(point){const lat=Number(point?.[0]),lon=Number(point?.[1]);return Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=90&&Math.abs(lon)<=180?[lat,lon]:null}
function tripTime(ms){return Number.isFinite(Number(ms))?new Date(Number(ms)).toLocaleTimeString('ru-RU'):'—'}
function tripRouteIntervals(data){
  const routes=data?.routes||[],switches=(data?.route_switches||[]).slice().sort((a,b)=>Number(a.time_ms)-Number(b.time_ms)),byId=new Map(routes.map(route=>[route.snapshot_id,route]));
  return switches.map((change,index)=>({change,route:byId.get(change.to_snapshot_id),index,start:Number(change.time_ms),end:index+1<switches.length?Number(switches[index+1].time_ms):Number(data?.summary?.ended_ms||Date.now())})).filter(item=>item.route);
}
function nearestTripPosition(samples,timeMs,fallback){
  const direct=validTripPoint(fallback,'carlinkit_lat','carlinkit_lon')||validTripPoint(fallback,'fake_lat','fake_lon');if(direct)return direct;
  let nearest=null,distance=Infinity;for(const sample of samples){const point=validTripPoint(sample,'carlinkit_lat','carlinkit_lon')||validTripPoint(sample,'fake_lat','fake_lon'),delta=Math.abs(Number(sample.time_ms)-timeMs);if(point&&delta<distance){nearest=point;distance=delta}}return nearest;
}
function routeTooltip(item){const route=item.route,id=String(route.route_id||'').slice(0,12)||'без ID';return `<b>Маршрут ${item.index+1}</b><br>${tripTime(item.start)} → ${tripTime(item.end)}<br>${route.route_source||'unknown'} · ${id}<br>${metric(Number(route.length_m)/1000,2,' км')} · ${route.point_count||route.points?.length||0} точек`}
function renderTripRouteTimeline(data){
  const intervals=tripRouteIntervals(data);if(!intervals.length){$('tripRouteTimeline').innerHTML='<div class="trip-route-empty">Маршруты не записаны. Для старых поездок восстановить их задним числом невозможно.</div>';return}
  $('tripRouteTimeline').innerHTML=intervals.map((item,index)=>`${index?'<div class="trip-route-switch-icon">→</div>':''}<div class="trip-route-item" style="--route-color:${tripRouteColors[index%tripRouteColors.length]}"><b>Маршрут ${index+1}</b><span>${tripTime(item.start)} → ${tripTime(item.end)}</span><small>${item.route.route_source||'unknown'} · ${metric(Number(item.route.length_m)/1000,2,' км')} · ${item.route.point_count||item.route.points?.length||0} точек</small></div>`).join('');
}
function drawSelectedTrip(fit=false){
  tripRealTrackLayer.clearLayers();tripFakeTrackLayer.clearLayers();tripRouteLayer.clearLayers();tripEventLayer.clearLayers();
  const samples=selectedTripData?.samples||[],events=selectedTripData?.events||[],routeIntervals=tripRouteIntervals(selectedTripData);
  const realSegments=splitTripTrack(samples,'carlinkit_lat','carlinkit_lon'),fakeSegments=splitTripTrack(samples,'fake_lat','fake_lon');
  if(tripTrackMode!=='fake'&&tripPointCount(realSegments)>1)L.polyline(realSegments,{color:'#36e6a1',weight:5,opacity:.95,lineCap:'round',lineJoin:'round',smoothFactor:.6}).addTo(tripRealTrackLayer);
  if(tripTrackMode!=='gps'&&tripPointCount(fakeSegments)>1)L.polyline(fakeSegments,{color:'#ffb33f',weight:4,opacity:.9,lineCap:'round',lineJoin:'round',dashArray:'8 7',smoothFactor:.6}).addTo(tripFakeTrackLayer);
  for(const event of events){
    const real=validTripPoint(event,'carlinkit_lat','carlinkit_lon'),fake=validTripPoint(event,'fake_lat','fake_lon'),point=real||fake;
    if(!point)continue;
    L.circleMarker(point,{renderer:rawRenderer,radius:6,weight:2,color:'#fff',fillColor:'#ff6277',fillOpacity:.95}).bindTooltip(tripEventLabel(event)).addTo(tripEventLayer);
    if(real&&fake&&map.distance(real,fake)>2)L.polyline([fake,real],{color:'#ff6277',weight:2,opacity:.72,dashArray:'3 5'}).addTo(tripEventLayer);
  }
  const all=[],travelPoints=[];
  if(tripTrackMode!=='fake')realSegments.forEach(segment=>{all.push(...segment);travelPoints.push(...segment)});
  if(tripTrackMode!=='gps')fakeSegments.forEach(segment=>{all.push(...segment);travelPoints.push(...segment)});
  if(tripShowRoutes)for(const item of routeIntervals){
    const points=(item.route.points||[]).map(tripRoutePoint).filter(Boolean),color=tripRouteColors[item.index%tripRouteColors.length];if(points.length<2)continue;
    L.polyline(points,{color:'#07111e',weight:9,opacity:.75,lineCap:'round',lineJoin:'round',interactive:false}).addTo(tripRouteLayer);
    L.polyline(points,{color,weight:5,opacity:.9,lineCap:'round',lineJoin:'round',smoothFactor:.25}).bindTooltip(routeTooltip(item)).addTo(tripRouteLayer);all.push(...points);
    const switchPoint=nearestTripPosition(samples,item.start,item.change);if(switchPoint){const icon=L.divIcon({className:'',html:`<div class="trip-route-marker" style="--route-color:${color}">${item.index+1}</div>`,iconSize:[24,24],iconAnchor:[12,12]});L.marker(switchPoint,{icon,zIndexOffset:850}).bindTooltip(`Переключение на маршрут ${item.index+1}<br>${tripTime(item.start)}`).addTo(tripRouteLayer)}
  }
  if(travelPoints.length){L.circleMarker(travelPoints[0],{radius:7,weight:3,color:'#fff',fillColor:'#36e6a1',fillOpacity:1}).bindTooltip('Начало поездки').addTo(tripEventLayer);L.circleMarker(travelPoints[travelPoints.length-1],{radius:7,weight:3,color:'#fff',fillColor:'#ff6277',fillOpacity:1}).bindTooltip('Конец поездки').addTo(tripEventLayer)}
  $('tripTrackStats').textContent=`GPS ${tripPointCount(realSegments)} · Fake ${tripPointCount(fakeSegments)} · маршрутов ${routeIntervals.length} · событий ${events.length}`;
  $('showTripOnMap').disabled=all.length<2;$('clearTripFromMap').disabled=all.length<2;
  if(fit&&all.length>1){map.fitBounds(L.latLngBounds(all),{padding:[70,70]});$('tripPanel').classList.remove('open')}
}
function clearTripTrack(){tripRealTrackLayer.clearLayers();tripFakeTrackLayer.clearLayers();tripRouteLayer.clearLayers();tripEventLayer.clearLayers();$('clearTripFromMap').disabled=true;$('tripTrackStats').textContent='Трек скрыт'}
function renderTripDetail(data){
  const trip=data.summary||{},events=data.events||[],samples=data.samples||[],routes=data.routes||[],switches=data.route_switches||[];
  $('tripSummary').innerHTML=`<span><small>Длительность</small><b>${tripDuration(trip.duration_s)}</b></span><span><small>Одометр</small><b>${metric(trip.distance_odometer_m,1,' м')}</b></span><span><small>Интеграл скорости</small><b>${metric(trip.distance_integrated_m,1,' м')}</b></span><span><small>Коррекции Σ</small><b>${metric(trip.correction_total_m,1,' м')}</b></span><span><small>Макс. вперёд</small><b>${metric(trip.max_forward_correction_m,1,' м')}</b></span><span><small>Разрывы GPS</small><b>${trip.gps_outages||0}</b></span><span><small>Маршруты</small><b>${routes.length} / ${switches.length} вкл.</b></span>`;
  $('tripEvents').innerHTML=events.length?events.slice().reverse().map(event=>{const reacquired=event.event==='gps_reacquired',distance=reacquired?(event.distance_by_odometer_m??event.distance_by_speed_integral_m):event.odometer_delta_m,shift=reacquired?event.gps_catch_up_m:event.correction_m;return `<tr><td>${new Date(event.time_ms).toLocaleTimeString('ru-RU')}</td><td><b>${reacquired?'GPS вернулся':'Коррекция'}</b><small>${event.progress_source||''}</small></td><td>${reacquired?metric(event.outage_duration_s,1,' с'):'—'}</td><td>${metric(event.vehicle_speed_kmh,1,' км/ч')}</td><td>${metric(distance,1,' м')}</td><td class="${Number(shift)>=0?'forward':'backward'}">${Number(shift)>=0?'+':''}${metric(shift,1,' м')}</td></tr>`}).join(''):'<tr><td colspan="6">Коррекций и разрывов GPS пока нет</td></tr>';
  selectedTripData=data;renderTripRouteTimeline(data);drawSelectedTrip(false);
  requestAnimationFrame(()=>drawTripChart(samples,events));
}
async function loadTrip(id){selectedTripId=id;try{const data=await request(`/api/controller/trips/${encodeURIComponent(id)}`);renderTripDetail(data);document.querySelectorAll('.trip-list-item').forEach(button=>button.classList.toggle('active',button.dataset.tripId===id))}catch(error){toast(error.message,true)}}
async function pollTrips(force=false){if(tripsBusy||(!$('tripPanel').classList.contains('open')&&!force))return;tripsBusy=true;try{const payload=await request('/api/controller/trips'),signature=JSON.stringify((payload.trips||[]).map(t=>[t.id,t.ended_ms,t.samples,t.correction_events,t.gps_outages]));if(force||signature!==lastTripSignature){lastTripSignature=signature;renderTripList(payload);if(selectedTripId)await loadTrip(selectedTripId)}}catch(error){if(force)toast(error.message,true)}finally{tripsBusy=false}}
async function pollRoute(){
  if(routeBusy)return;
  routeBusy=true;
  try{
    const route=await request('/api/controller/route');
    const signature=route.source_revision||`${route.revision}:${route.raw_points?.length||0}`;
    if(!route.available){
      if(signature!==routeRevision){
        routeRevision=signature;
        routeGlow.setLatLngs([]);routeLine.setLatLngs([]);exactRouteLine.setLatLngs([]);
        exactPointLayer.clearLayers();guidancePointLayer.clearLayers();historyPointLayer.clearLayers();staleHistoryPointLayer.clearLayers();
        speedLimitLayer.clearLayers();roadEventLayer.clearLayers();mapkitRevision=null;
        currentRoutePoints=[];currentMapkitData={};closeSegmentInspector();
        $('exactCount').textContent='0';$('guidanceCount').textContent='0';$('historyCount').textContent='0';$('staleHistoryCount').textContent='0';
      }
      return;
    }
    const exact=route.exact_points||[],mapkitData=route.mapkit_route||{};
    currentRoutePoints=(exact.length>1?exact:route.points||[]).map(p=>[Number(p[0]),Number(p[1])]);currentMapkitData=mapkitData;
    const nextMapkitRevision=`${mapkitData.captured_at_ms||0}:${mapkitData.signature||''}:${mapkitData.speed_limits_mps?.length||0}:${mapkitData.events?.length||0}`;
    if(nextMapkitRevision!==mapkitRevision){mapkitRevision=nextMapkitRevision;updateMapkitRoute(exact,mapkitData);if(inspectedSegmentIndex!=null&&inspectedSegmentIndex<currentRoutePoints.length-1)inspectSegment(inspectedSegmentIndex)}
    if(signature===routeRevision)return;
    routeRevision=signature;
    const latlngs=route.points.map(p=>[p[0],p[1]]),exactLatLngs=exact.map(p=>[p[0],p[1]]);
    routeGlow.setLatLngs(latlngs);routeLine.setLatLngs(latlngs);exactRouteLine.setLatLngs(exactLatLngs);
    const guidance=route.guidance_points||route.points||[],history=route.history_points||route.raw_points||[],stale=route.stale_history_points||[];
    fillPointLayer(exactPointLayer,exact,{radius:2.6,weight:.8,color:'#e1fff1',fillColor:'#39efa0',fillOpacity:.82,opacity:.9});
    fillPointLayer(guidancePointLayer,guidance,{radius:5.2,weight:1.6,color:'#dff8ff',fillColor:'#28cfff',fillOpacity:.88,opacity:.98});
    fillPointLayer(historyPointLayer,history,{radius:4.5,weight:1.4,color:'#ffdc83',fillColor:'#ffad32',fillOpacity:.8,opacity:.94});
    fillPointLayer(staleHistoryPointLayer,stale,{radius:4.2,weight:1.2,color:'#ff8795',fillColor:'#ff5268',fillOpacity:.38,opacity:.7,dashArray:'2 2'});
    $('exactCount').textContent=exact.length;$('guidanceCount').textContent=guidance.length;$('historyCount').textContent=history.length;$('staleHistoryCount').textContent=stale.length;
    document.querySelector('.stale-legend').classList.toggle('visible',!!stale.length);$('stalePointsToggle').disabled=!stale.length;$('stalePointsToggle').title=stale.length?`Устаревшие/вне маршрута history: ${stale.length}`:'Устаревших history-точек нет';
    setRouteLayer(routeLayerMode);$('routeProgress').max=Math.max(1,Math.round(route.length_m));
    if(inspectedSegmentIndex!=null){if(inspectedSegmentIndex<currentRoutePoints.length-1)inspectSegment(inspectedSegmentIndex);else closeSegmentInspector()}
    if(latlngs.length>1){if(!hasAutoFittedRoute){if(!userAdjustedMap)map.fitBounds((exactLatLngs.length>1?exactRouteLine:routeLine).getBounds(),{padding:[90,90]});hasAutoFittedRoute=true}const pointInfo=`MapKit ${exact.length}, guidance ${guidance.length}, history ${history.length}${stale.length?`, старых ${stale.length}`:''}`;toast(`${route.route_source==='exact'?'Точный MapKit-маршрут':'Маршрут'} ${(route.length_m/1000).toFixed(1)} км · ${pointInfo}`)}
  }catch{}finally{routeBusy=false}
}

async function refreshRouteSources(source='all'){
  const buttons=[$('reloadExact'),$('reloadGuidance'),$('reloadHistory'),$('reloadRoute')];
  buttons.forEach(button=>button.disabled=true);
  try{
    toast(`Получаю свежий ${source==='exact'?'MapKit exact':source==='history'?'History':'Guidance'}…`);
    const result=await request('/api/controller/reload-route',{method:'POST',body:JSON.stringify({source})});
    routeRevision=null;
    await pollRoute();
    const route=result.route||{};
    const guidance=route.guidance_points?.length??$('guidanceCount').textContent;
    const history=route.history_points?.length??$('historyCount').textContent;
    const stale=route.stale_history_points?.length??$('staleHistoryCount').textContent;
    const exact=route.exact_points?.length??$('exactCount').textContent;
    toast(`Точки обновлены: MapKit ${exact}, Guidance ${guidance}, History ${history}, вне маршрута ${stale}`);
  }catch(error){toast(error.message,true)}finally{buttons.forEach(button=>button.disabled=false)}
}

map.on('click',event=>{if(mapClickMode==='inspect'){const nearest=closestRouteSegment(event.latlng);if(!nearest){toast('Сначала загрузите маршрут',true);return}if(nearest.distance>55){toast('Нажмите ближе к линии маршрута',true);return}inspectSegment(nearest.index,nearest.point);return}const point={lat:event.latlng.lat,lon:event.latlng.lng};selectedMarker=setMarker(selectedMarker,point,'selected');$('selectedCoordinate').textContent=formatCoord(point);control({latitude:point.lat,longitude:point.lon,...calibrationPatch()},false).then(()=>toast('Начальная точка передана в AVD')).catch(()=>{})});
map.on('dragstart zoomstart',()=>{userAdjustedMap=true});
$('speed').addEventListener('input',()=>{$('speedValue').textContent=$('speed').value;queueControl({target_speed_kmh:number('speed')})});
$('runButton').addEventListener('click',()=>control({running:true,target_speed_kmh:number('speed'),...calibrationPatch()},false).then(()=>toast('Симуляция запущена')).catch(()=>{}));
$('stopButton').addEventListener('click',()=>control({running:false},false).then(()=>toast('Симуляция остановлена')).catch(()=>{}));
$('sendNow').addEventListener('click',()=>control({send_now:true,odometer_km:number('odometer')},false).then(()=>toast('Состояние отправлено')).catch(()=>{}));
document.querySelectorAll('#gpsMode button').forEach(button=>button.addEventListener('click',()=>control({gps_mode:button.dataset.mode},false).then(()=>toast(button.dataset.mode==='route'?'FakeGPS по маршруту':'Статичная GPS-точка')).catch(()=>{})));
$('gatewayFake').addEventListener('change',async event=>{try{await request('/api/controller/fake-nav',{method:'POST',body:JSON.stringify({enabled:event.target.checked})});toast(`Gateway Fake ${event.target.checked?'включён':'выключен'}`)}catch(error){event.target.checked=!event.target.checked;toast(error.message,true)}});
['vehicleScale','odoScale','gpsScale','gpsHz','odometer','token','gatewayUrl','haUrl','haToken'].forEach(id=>$(id).addEventListener('change',()=>queueControl({})));
$('gatewayChip').addEventListener('click',()=>{$('settingsPanel').classList.add('open');$('gatewayUrl').focus()});
$('setGwavd').addEventListener('click',()=>{$('gatewayUrl').value='http://127.0.0.1:8080';queueControl({gateway_mode:'direct',gateway_url:'http://127.0.0.1:8080'});toast('Выбран режим: 💻 AVD (127.0.0.1)')});
$('setGwhu').addEventListener('click',()=>{$('gatewayUrl').value='http://192.168.66.124:8080';queueControl({gateway_mode:'direct',gateway_url:'http://192.168.66.124:8080'});toast('Выбран режим: 🚗 ГУ Direct (192.168.66.124)')});
$('setGwha').addEventListener('click',()=>{$('gatewayUrl').value='http://supervisor/core';queueControl({gateway_mode:'ha',ha_url:$('haUrl').value||'http://supervisor/core'});toast('Выбран режим: 🌐 HA / Internet (через Home Assistant API)')});
$('mobileExpandToggle').addEventListener('click', () => {
  const stack = $('leftStack');
  const expanded = stack.classList.toggle('expanded');
  $('mobileExpandToggle').textContent = expanded ? '▲ Свернуть панель' : '🎛 Панель сценариев';
});
$('routeProgress').addEventListener('input',()=>{$('routeProgressText').textContent=`${(number('routeProgress')/1000).toFixed(2)} / ${state?(state.route_length_m/1000).toFixed(1):0} км`});
$('routeProgress').addEventListener('change',()=>control({route_progress_m:number('routeProgress')},false).catch(()=>{}));
$('reloadExact').addEventListener('click',()=>refreshRouteSources('exact'));
$('reloadGuidance').addEventListener('click',()=>refreshRouteSources('guidance'));
$('reloadHistory').addEventListener('click',()=>refreshRouteSources('history'));
$('reloadRoute').addEventListener('click',()=>refreshRouteSources('all'));
$('fitRoute').addEventListener('click',()=>{if(exactRouteLine.getLatLngs().length>1)map.fitBounds(exactRouteLine.getBounds(),{padding:[80,80]});else if(routeLine.getLatLngs().length>1)map.fitBounds(routeLine.getBounds(),{padding:[80,80]});else if(state?.last_sent)map.setView([state.last_sent.lat,state.last_sent.lon],15)});
$('tripLogToggle').addEventListener('click',()=>{$('tripPanel').classList.add('open');pollTrips(true)});
$('tripPanelClose').addEventListener('click',()=>$('tripPanel').classList.remove('open'));
$('showTripOnMap').addEventListener('click',()=>drawSelectedTrip(true));
$('clearTripFromMap').addEventListener('click',clearTripTrack);
document.querySelectorAll('#tripTrackMode button').forEach(button=>button.addEventListener('click',()=>{tripTrackMode=button.dataset.tripTrack;document.querySelectorAll('#tripTrackMode button').forEach(item=>item.classList.toggle('active',item===button));drawSelectedTrip(false)}));
$('tripRouteToggle').addEventListener('click',()=>{tripShowRoutes=!tripShowRoutes;$('tripRouteToggle').classList.toggle('active',tripShowRoutes);$('tripRouteToggle').setAttribute('aria-pressed',String(tripShowRoutes));drawSelectedTrip(false)});
$('finishTrip').addEventListener('click',async()=>{try{await request('/api/controller/trips/finish',{method:'POST',body:'{}'});selectedTripId=null;await pollTrips(true);toast('Поездка завершена')}catch(error){toast(error.message,true)}});
$('settingsToggle').addEventListener('click',()=>$('settingsPanel').classList.toggle('open'));
$('settingsClose').addEventListener('click',()=>$('settingsPanel').classList.remove('open'));
document.querySelectorAll('#routeLayers button').forEach(button=>button.addEventListener('click',()=>setRouteLayer(button.dataset.layer)));
$('stalePointsToggle').addEventListener('click',()=>setStalePoints(!showStalePoints));
document.querySelectorAll('#mapClickMode button').forEach(button=>button.addEventListener('click',()=>setMapClickMode(button.dataset.clickMode)));
$('segmentInspectorClose').addEventListener('click',closeSegmentInspector);

async function toggleFullscreen(){
  try{
    if(document.fullscreenElement)await document.exitFullscreen();
    else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();
    else if(document.documentElement.webkitRequestFullscreen)document.documentElement.webkitRequestFullscreen();
  }catch(error){toast(`Полноэкранный режим недоступен: ${error.message}`,true)}
}
function fullscreenChanged(){const active=!!(document.fullscreenElement||document.webkitFullscreenElement);$('fullscreenToggle').textContent=active?'↙':'⛶';$('fullscreenToggle').title=active?'Выйти из полноэкранного режима':'Полноэкранный режим';setTimeout(()=>map.invalidateSize(),120)}
$('fullscreenToggle').addEventListener('click',toggleFullscreen);document.addEventListener('fullscreenchange',fullscreenChanged);document.addEventListener('webkitfullscreenchange',fullscreenChanged);window.addEventListener('resize',()=>setTimeout(()=>map.invalidateSize(),80));

const preferences=saved();delete preferences.token;for(const [id,key] of [['vehicleScale','vehicleScale'],['odoScale','odoScale'],['gpsScale','gpsScale'],['gpsHz','gpsHz']])if(preferences[key]!=null)$(id).value=preferences[key];
setStalePoints(!!preferences.showStalePoints);setRouteLayer(['points','line','both'].includes(preferences.routeLayer)?preferences.routeLayer:'both');setMapClickMode(preferences.mapClickMode,true);pollState().then(()=>control(calibrationPatch()).catch(()=>{}));pollRoute();setInterval(pollState,250);setInterval(pollRoute,1000);setInterval(()=>pollTrips(false),5000);
