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
let selectedMarker, rawMarker, sentMarker, state=null, routeRevision=null, routeLayerMode='both',showStalePoints=false,stateBusy=false,routeBusy=false,toastTimer,lastShownError=null;

function toast(message, error=false){const el=$('toast');el.textContent=message;el.style.borderColor=error?'rgba(255,98,119,.45)':'';el.classList.add('show');clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),2400)}
async function request(path, options={}){const response=await fetch(`${API_BASE}${path}`,{credentials:'include',headers:{'Content-Type':'application/json','X-X50-Client':'navigation-lab'},...options});const text=await response.text();let data;try{data=JSON.parse(text)}catch(err){if(!response.ok)throw new Error(`HTTP ${response.status}: ${text.slice(0,60)}`);throw new Error(`Ошибка ответа (${response.status}): ${text.slice(0,60)}`)}if(!response.ok)throw new Error(data.detail||data.error||`HTTP ${response.status}`);return data}
async function control(patch, quiet=true){try{return await request('/api/controller/control',{method:'POST',body:JSON.stringify(patch)})}catch(error){if(!quiet)toast(error.message,true);throw error}}
function number(id){return Number($(id).value)}
function pct(id){return number(id)/100}
function saved(){try{return JSON.parse(localStorage.getItem('x50-simulator')||'{}')}catch{return {}}}
function persist(extra={}){localStorage.setItem('x50-simulator',JSON.stringify({...saved(),vehicleScale:number('vehicleScale'),odoScale:number('odoScale'),gpsScale:number('gpsScale'),gpsHz:number('gpsHz'),token:$('token').value,...extra}))}

function setRouteLayer(mode){
  routeLayerMode=mode;
  const showLine=mode==='line'||mode==='both',showPoints=mode==='points'||mode==='both';
  for(const layer of [routeGlow,routeLine,exactRouteLine])showLine?layer.addTo(map):layer.remove();
  for(const layer of [exactPointLayer,guidancePointLayer,historyPointLayer])showPoints?layer.addTo(map):layer.remove();
  showPoints&&showStalePoints?staleHistoryPointLayer.addTo(map):staleHistoryPointLayer.remove();
  document.querySelectorAll('#routeLayers button').forEach(button=>button.classList.toggle('active',button.dataset.layer===mode));
  persist({routeLayer:mode});
}

function setStalePoints(show){showStalePoints=show;$('stalePointsToggle').classList.toggle('active',show);setRouteLayer(routeLayerMode);persist({showStalePoints:show})}
function fillPointLayer(layer,points,style){layer.clearLayers();for(const p of points)L.circleMarker([p[0],p[1]],{renderer:rawRenderer,interactive:false,...style}).addTo(layer)}

function calibrationPatch(){persist();return {vehicle_speed_scale:pct('vehicleScale'),odometer_scale:pct('odoScale'),gps_speed_scale:pct('gpsScale'),gps_hz:number('gpsHz'),token:$('token').value,gateway_url:$('gatewayUrl').value,ha_url:$('haUrl').value,ha_token:$('haToken').value,odometer_km:number('odometer')}}
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
  if(document.activeElement!==$('haToken')&&next.ha_token!=null)$('haToken').value=next.ha_token;
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
        $('exactCount').textContent='0';$('guidanceCount').textContent='0';$('historyCount').textContent='0';$('staleHistoryCount').textContent='0';
      }
      return;
    }
    if(signature===routeRevision)return;
    routeRevision=signature;
    const latlngs=route.points.map(p=>[p[0],p[1]]),exact=route.exact_points||[],exactLatLngs=exact.map(p=>[p[0],p[1]]);
    routeGlow.setLatLngs(latlngs);routeLine.setLatLngs(latlngs);exactRouteLine.setLatLngs(exactLatLngs);
    const guidance=route.guidance_points||route.points||[],history=route.history_points||route.raw_points||[],stale=route.stale_history_points||[];
    fillPointLayer(exactPointLayer,exact,{radius:2.6,weight:.8,color:'#e1fff1',fillColor:'#39efa0',fillOpacity:.82,opacity:.9});
    fillPointLayer(guidancePointLayer,guidance,{radius:5.2,weight:1.6,color:'#dff8ff',fillColor:'#28cfff',fillOpacity:.88,opacity:.98});
    fillPointLayer(historyPointLayer,history,{radius:4.5,weight:1.4,color:'#ffdc83',fillColor:'#ffad32',fillOpacity:.8,opacity:.94});
    fillPointLayer(staleHistoryPointLayer,stale,{radius:4.2,weight:1.2,color:'#ff8795',fillColor:'#ff5268',fillOpacity:.38,opacity:.7,dashArray:'2 2'});
    $('exactCount').textContent=exact.length;$('guidanceCount').textContent=guidance.length;$('historyCount').textContent=history.length;$('staleHistoryCount').textContent=stale.length;
    document.querySelector('.stale-legend').classList.toggle('visible',!!stale.length);$('stalePointsToggle').disabled=!stale.length;$('stalePointsToggle').title=stale.length?`Устаревшие/вне маршрута history: ${stale.length}`:'Устаревших history-точек нет';
    setRouteLayer(routeLayerMode);$('routeProgress').max=Math.max(1,Math.round(route.length_m));
    if(latlngs.length>1){map.fitBounds((exactLatLngs.length>1?exactRouteLine:routeLine).getBounds(),{padding:[90,90]});const pointInfo=`MapKit ${exact.length}, guidance ${guidance.length}, history ${history.length}${stale.length?`, старых ${stale.length}`:''}`;toast(`${route.route_source==='exact'?'Точный MapKit-маршрут':'Маршрут'} ${(route.length_m/1000).toFixed(1)} км · ${pointInfo}`)}
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

map.on('click',event=>{const point={lat:event.latlng.lat,lon:event.latlng.lng};selectedMarker=setMarker(selectedMarker,point,'selected');$('selectedCoordinate').textContent=formatCoord(point);control({latitude:point.lat,longitude:point.lon,...calibrationPatch()},false).then(()=>toast('Начальная точка передана в AVD')).catch(()=>{})});
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
$('routeProgress').addEventListener('input',()=>{$('routeProgressText').textContent=`${(number('routeProgress')/1000).toFixed(2)} / ${state?(state.route_length_m/1000).toFixed(1):0} км`});
$('routeProgress').addEventListener('change',()=>control({route_progress_m:number('routeProgress')},false).catch(()=>{}));
$('reloadExact').addEventListener('click',()=>refreshRouteSources('exact'));
$('reloadGuidance').addEventListener('click',()=>refreshRouteSources('guidance'));
$('reloadHistory').addEventListener('click',()=>refreshRouteSources('history'));
$('reloadRoute').addEventListener('click',()=>refreshRouteSources('all'));
$('fitRoute').addEventListener('click',()=>{if(exactRouteLine.getLatLngs().length>1)map.fitBounds(exactRouteLine.getBounds(),{padding:[80,80]});else if(routeLine.getLatLngs().length>1)map.fitBounds(routeLine.getBounds(),{padding:[80,80]});else if(state?.last_sent)map.setView([state.last_sent.lat,state.last_sent.lon],15)});
$('settingsToggle').addEventListener('click',()=>$('settingsPanel').classList.toggle('open'));
$('settingsClose').addEventListener('click',()=>$('settingsPanel').classList.remove('open'));
document.querySelectorAll('#routeLayers button').forEach(button=>button.addEventListener('click',()=>setRouteLayer(button.dataset.layer)));
$('stalePointsToggle').addEventListener('click',()=>setStalePoints(!showStalePoints));

async function toggleFullscreen(){
  try{
    if(document.fullscreenElement)await document.exitFullscreen();
    else if(document.documentElement.requestFullscreen)await document.documentElement.requestFullscreen();
    else if(document.documentElement.webkitRequestFullscreen)document.documentElement.webkitRequestFullscreen();
  }catch(error){toast(`Полноэкранный режим недоступен: ${error.message}`,true)}
}
function fullscreenChanged(){const active=!!(document.fullscreenElement||document.webkitFullscreenElement);$('fullscreenToggle').textContent=active?'↙':'⛶';$('fullscreenToggle').title=active?'Выйти из полноэкранного режима':'Полноэкранный режим';setTimeout(()=>map.invalidateSize(),120)}
$('fullscreenToggle').addEventListener('click',toggleFullscreen);document.addEventListener('fullscreenchange',fullscreenChanged);document.addEventListener('webkitfullscreenchange',fullscreenChanged);window.addEventListener('resize',()=>setTimeout(()=>map.invalidateSize(),80));

const preferences=saved();for(const [id,key] of [['vehicleScale','vehicleScale'],['odoScale','odoScale'],['gpsScale','gpsScale'],['gpsHz','gpsHz'],['token','token']])if(preferences[key]!=null)$(id).value=preferences[key];
setStalePoints(!!preferences.showStalePoints);setRouteLayer(['points','line','both'].includes(preferences.routeLayer)?preferences.routeLayer:'both');control(calibrationPatch()).catch(()=>{});pollState();pollRoute();setInterval(pollState,250);setInterval(pollRoute,1000);
