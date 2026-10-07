(function(root){
  'use strict';
  const rad=Math.PI/180,R=6371000,valid=v=>typeof v==='number'&&Number.isFinite(v);
  const angle=v=>((v+540)%360)-180;
  function distance(a,b){const p=a.lat*rad,q=b.lat*rad,x=(b.lat-a.lat)*rad,y=(b.lon-a.lon)*rad;return R*2*Math.asin(Math.min(1,Math.sqrt(Math.sin(x/2)**2+Math.cos(p)*Math.cos(q)*Math.sin(y/2)**2)))}
  function bearing(a,b){const p=a.lat*rad,q=b.lat*rad,y=(b.lon-a.lon)*rad;return (Math.atan2(Math.sin(y)*Math.cos(q),Math.cos(p)*Math.sin(q)-Math.sin(p)*Math.cos(q)*Math.cos(y))/rad+360)%360}
  function destination(a,heading,length){const p=a.lat*rad,q=a.lon*rad,h=heading*rad,d=length/R;const lat=Math.asin(Math.sin(p)*Math.cos(d)+Math.cos(p)*Math.sin(d)*Math.cos(h));return {lat:lat/rad,lon:angle((q+Math.atan2(Math.sin(h)*Math.sin(d)*Math.cos(p),Math.cos(d)-Math.sin(p)*Math.sin(lat)))/rad)}}
  const broken=(nodes,i)=>i===0||nodes[i].break_before||nodes[i].cut_before||nodes[i].excluded||nodes[i-1].excluded;
  function rope(nodes,index,target){
    if(nodes[index].locked||nodes[index].excluded)return false;
    const before=nodes.map(n=>({lat:n.lat,lon:n.lon}));
    nodes[index].lat=target.lat;nodes[index].lon=target.lon;
    for(const direction of [-1,1]){
      const ids=[index];
      for(let i=index+direction;i>=0&&i<nodes.length&&!broken(nodes,Math.max(i,i-direction));i+=direction){ids.push(i);if(nodes[i].locked)break}
      const lengths=ids.slice(1).map((i,k)=>nodes[Math.max(i,ids[k])].rest_m);
      const last=ids.at(-1),anchor=nodes[last].locked?before[last]:null;
      if(anchor){
        const total=lengths.reduce((a,b)=>a+b,0),min=Math.max(0,2*Math.max(0,...lengths)-total),span=distance(target,anchor);
        if(span>total+.005||span<min-.005){nodes.forEach((n,i)=>Object.assign(n,before[i]));return false}
        // FABRIK: fixed dragged endpoint and fixed nearest lock. Seed a small
        // bend so collinear chains can fold rather than stall indefinitely.
        if(ids.length>2&&span<total-.1){const mid=ids[Math.floor(ids.length/2)];Object.assign(nodes[mid],destination(nodes[mid],bearing(target,anchor)+90,Math.min(1,total*.01)))}
      }
      let solved=!anchor;
      for(let iteration=0;iteration<(anchor?160:1);iteration++){
        Object.assign(nodes[index],target);
        for(let k=1;k<ids.length;k++)Object.assign(nodes[ids[k]],destination(nodes[ids[k-1]],bearing(nodes[ids[k-1]],nodes[ids[k]]),lengths[k-1]));
        if(!anchor||distance(nodes[last],anchor)<.02){solved=true;break}
        Object.assign(nodes[last],anchor);
        for(let k=ids.length-2;k>=0;k--)Object.assign(nodes[ids[k]],destination(nodes[ids[k+1]],bearing(nodes[ids[k+1]],nodes[ids[k]]),lengths[k]));
      }
      if(!solved){nodes.forEach((n,i)=>Object.assign(n,before[i]));return false}
      if(anchor)Object.assign(nodes[last],anchor);
    }
    return true;
  }
  function excludeRange(nodes,start,end){
    if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end>=nodes.length||start>end)return false;
    const range=nodes.slice(start,end+1);
    if(range.some(n=>n.locked)||nodes.filter((n,i)=>!n.excluded&&(i<start||i>end)).length<2)return false;
    range.forEach(n=>n.excluded=true);return true;
  }
  function segments(nodes){
    const result=[];let line=[];
    nodes.forEach((n,i)=>{if(broken(nodes,i)){if(line.length)result.push(line);line=[]}if(!n.excluded)line.push([n.lat,n.lon])});
    if(line.length)result.push(line);return result;
  }
  function restoreRange(nodes,start,end){
    if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end>=nodes.length||start>end)return false;
    nodes.slice(start,end+1).forEach(n=>n.excluded=false);return true;
  }
  function joinFragment(nodes,index){
    let start=index;while(start>0&&!broken(nodes,start))start--;
    if(start===0)return false;
    let end=start+1;while(end<nodes.length&&!broken(nodes,end))end++;
    if(nodes[start-1].excluded||nodes.slice(start,end).some(n=>n.locked||n.excluded))return false;
    const a=nodes[start],b=nodes[start-1],heading=bearing(a,b),length=distance(a,b);
    for(let i=start;i<end;i++)Object.assign(nodes[i],destination(nodes[i],heading,length));
    rope(nodes,start,{lat:b.lat,lon:b.lon});
    return true;
  }
  function quality(nodes,i){
    const n=nodes[i],p=nodes[i-1];if(!p||broken(nodes,i))return {color:'#94a3b8',label:'Свободный стык'};
    const length=distance(p,n),heading=bearing(p,n),e=n.evidence||{},s=e.step||{},c=e.compass||{};
    const reverse=s.motion_direction===-1,course=(heading+(reverse?180:0))%360;
    const compass=(s.compass_valid!==false&&s.compass_age_ms<=2000&&valid(s.compass_sensor_calibrated_deg))?s.compass_sensor_calibrated_deg*(s.compass_direction??1)+(s.compass_offset_deg??0):
      (c.valid&&c.age_ms<=2000&&valid(c.calibrated_deg)?c.calibrated_deg:null);
    const lengthError=Math.abs(length-n.rest_m),compassError=valid(compass)&&length>.5?Math.abs(angle(course-compass)):null;
    let steeringError=null;
    const ps=p.evidence?.step||{},wheelValid=v=>v.steering_fresh&&valid(v.steer_deg)&&valid(v.steering_ratio)&&v.steering_ratio>0&&valid(v.wheelbase_m)&&v.wheelbase_m>0;
    if(i>1&&!broken(nodes,i-1)&&length>.5&&distance(nodes[i-2],p)>.5&&wheelValid(s)&&wheelValid(ps)){
      const turn=(v,meters)=>(v.motion_direction===-1?-1:1)*meters/v.wheelbase_m*Math.tan((v.steer_deg-(v.steering_zero_deg||0))/v.steering_ratio*rad)/rad;
      // Chord courses represent link midpoints, rather than endpoint headings.
      const expected=.5*(turn(ps,p.rest_m)+turn(s,n.rest_m));
      const previousCourse=(bearing(nodes[i-2],p)+(ps.motion_direction===-1?180:0))%360;
      steeringError=Math.abs(angle(angle(course-previousCourse)-expected));
    }
    const score=Math.max(lengthError/Math.max(1,n.rest_m*.1),compassError===null?0:compassError/5,steeringError===null?0:steeringError/8);
    const known=compassError!==null&&steeringError!==null;
    return {length,heading,lengthError,compassError,steeringError,color:score>3?'#ff6277':score>1?'#ffbd4a':known?'#36e6a1':'#94a3b8',label:score>3?'Большое расхождение':score>1?'Есть расхождение':known?'В пределах допуска':'Недостаточно угловых данных'};
  }
  function create({map,L,request,trip,toast,background}){
    let document=null,active=false,dirty=false,selected=0,undo=[],drag=null,busy=false,frame=null,marks=[],edges=[],savedDocument=null,viewToken=0;
    const readonly=L.layerGroup().addTo(map),layer=L.layerGroup().addTo(map),links=L.layerGroup().addTo(map),renderer=L.canvas({padding:.3,pane:'referenceTrajectory'});
    const pane=map.createPane('referenceTrajectory');pane.style.zIndex=650;
    map.createPane('referenceInfo').style.zIndex=750;
    const $=id=>window.document.getElementById(id),positions=()=>document.nodes.map(n=>({lat:n.lat,lon:n.lon,locked:!!n.locked,excluded:!!n.excluded,cut_before:!!n.cut_before}));
    const patch=()=>({revision:document.revision,source_sha256:document.source_sha256,nodes:document.nodes.map(n=>({id:n.id,lat:n.lat,lon:n.lon,locked:!!n.locked,excluded:!!n.excluded,cut_before:!!n.cut_before}))});
    function drawReadonly(){readonly.clearLayers();if(savedDocument?.saved)L.polyline(segments(savedDocument.nodes),{pane:'referenceTrajectory',renderer,color:'#ed66ff',weight:4,opacity:.95,smoothFactor:.3,interactive:true}).bindTooltip('Реальная траектория · сохранённый референс').addTo(readonly)}
    async function view(current){if(active)return;const token=++viewToken;readonly.clearLayers();savedDocument=null;if(!current)return;try{const result=await request(`/api/controller/trips/${encodeURIComponent(current.summary.id)}/reference?saved_only=1`);if(token!==viewToken||active)return;savedDocument=result;drawReadonly()}catch(error){if(token===viewToken)toast(error.message,true)}}
    function pick(){selected=Math.max(0,Math.min(document.nodes.length-1,Math.trunc(Number($('referenceIndex').value)||1)-1));return document.nodes[selected]}
    function checkpoint(){undo.push(positions());if(undo.length>30)undo.shift()}
    function beginDrag(index,event){
      if(busy||!active||drag||document.nodes[index].locked||document.nodes[index].excluded)return;
      L.DomEvent.stop(event);checkpoint();selected=index;
      drag={index,origin:positions(),wasDragging:map.dragging.enabled()};
      map.dragging.disable();marks[index].closeTooltip();
    }
    function status(){if(!document)return;$('referenceStatus').textContent=`${dirty?'Есть несохранённые правки':'Сохранено'} · версия ${document.revision} · ${document.nodes.filter(n=>!n.excluded).length} узлов · ${document.nodes.filter(n=>n.locked).length} замков · ${document.nodes.filter(n=>n.excluded).length} исключено`;$('referenceSave').disabled=busy||!dirty;$('referenceUndo').disabled=busy||!undo.length;const n=document.nodes[selected];$('referenceLock').textContent=n?.locked?'Снять замок':'Закрепить узел';$('referenceLock').setAttribute('aria-pressed',String(!!n?.locked));$('referenceLock').disabled=busy||!!n?.excluded}
    function download(){if(!document)return;const blob=new Blob([JSON.stringify({...document,draft:dirty},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=window.document.createElement('a');a.href=url;a.download=`${document.trip_id}.reference${dirty?'.draft':''}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
    function inspect(i,marker){
      marks[selected]?.closeTooltip();selected=i;$('referenceIndex').value=i+1;status();links.clearLayers();const n=document.nodes[i],e=n.evidence||{},q=quality(document.nodes,i),fmt=(v,suffix='')=>valid(v)?v.toFixed(1)+suffix:'нет данных';
      const info=[`Узел ${i+1} · ${new Date(n.t_ms).toLocaleTimeString('ru-RU')} ${n.locked?'🔒':''}`,q.label,
        `Звено ${fmt(q.length,' м')} / ${fmt(n.rest_m,' м')} · ${n.length_source==='vehicle_speed_integral'?'интеграл скорости':'исходная геометрия'}`,
        `Курс ${fmt(q.heading,'°')} · ошибка компаса ${fmt(q.compassError,'°')} · руля ${fmt(q.steeringError,'°')}`,
        `Скорость ${fmt(e.speed_kmh,' км/ч')} · одометр ${fmt(e.odometer_km,' км')}`,
        `Компас ${fmt(e.step?.compass_sensor_calibrated_deg??e.compass?.calibrated_deg,'°')} · возраст ${fmt(e.step?.compass_age_ms??e.compass?.age_ms,' мс')}`,
        `Руль ${fmt(e.step?.steer_deg??e.step?.fusion_steering_angle_deg,'°')} · ${e.step?.steering_fresh?'свежий':'свежесть не подтверждена'}`,
        `Смещение от исходного узла ${fmt(distance(n,{lat:n.original_lat,lon:n.original_lon}),' м')}`,
        `GPS: точность ${fmt(e.gps_accuracy_m,' м')}, возраст ${fmt(e.gps_age_ms,' мс')}, ${e.gps_good?'качественный':'качество не подтверждено'}`,
        `Δ времени измерения ${fmt(valid(e.sample_time_ms)?e.sample_time_ms-n.t_ms:null,' мс')}`];
      for(const [key,color,label] of [['fake','#ffb33f','FakeGPS'],['gps','#36caff','GPS'],['original','#b890ff','Inertial']])if(key==='original'||e[key]){const point=key==='original'?[n.original_lat,n.original_lon]:[e[key].lat,e[key].lon];L.polyline([[n.lat,n.lon],point],{pane:'referenceTrajectory',color,weight:2,dashArray:'4 6',interactive:false}).addTo(links);L.circleMarker(point,{pane:'referenceTrajectory',radius:5,color,interactive:false}).bindTooltip(label).addTo(links)}
      marker.bindTooltip(info.join('<br>'),{pane:'referenceInfo',direction:'auto',offset:[12,0],className:'reference-tooltip'}).openTooltip();
      $('referenceNode').textContent=`Выбран узел ${i+1}. ${n.break_before?'Начало фрагмента: стык не входит в пробег.':''}`;
    }
    function draw(){
      links.clearLayers();if(!document)return;
      const nodes=document.nodes;
      if(marks.length===nodes.length){nodes.forEach((n,i)=>{if(n.excluded)return;const q=quality(nodes,i);marks[i].setLatLng([n.lat,n.lon]).setStyle({fillColor:q.color,color:n.locked?'#fff':'#162338',weight:n.locked?3:1});if(edges[i])edges[i].setLatLngs([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]]).setStyle({color:broken(nodes,i)?'#94a3b8':q.color})});status();return}
      layer.clearLayers();marks=new Array(nodes.length);edges=[];
      nodes.forEach((n,i)=>{
        if(n.excluded)return;const q=quality(nodes,i);
        if(i&&!broken(nodes,i))edges[i]=L.polyline([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]],{pane:'referenceTrajectory',renderer,color:q.color,weight:4,interactive:false}).addTo(layer);
        else if(i&&!nodes[i-1].excluded)edges[i]=L.polyline([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]],{pane:'referenceTrajectory',color:'#94a3b8',weight:2,dashArray:'2 9',interactive:false}).addTo(layer);
        if(active){const marker=L.circleMarker([n.lat,n.lon],{pane:'referenceTrajectory',renderer,radius:n.break_before?6:(map.getZoom()<15?2:4),weight:n.locked?3:1,color:n.locked?'#fff':'#162338',fillColor:q.color,fillOpacity:1}).addTo(layer);
          marks[i]=marker;marker.on('mouseover',()=>{if(!drag)inspect(i,marker)});
          marker.on('mousedown',event=>beginDrag(i,event.originalEvent));
        }
      });status();
    }
    function applyDrag(){document.nodes.forEach((n,i)=>Object.assign(n,drag.origin[i]));drag.rejected=$('referenceRope').checked&&!rope(document.nodes,drag.index,drag.target);if(!$('referenceRope').checked)Object.assign(document.nodes[drag.index],drag.target);if(!drag.rejected)dirty=true;draw()}
    function move(event){if(!drag)return;if(event.cancelable)event.preventDefault();const latlng=map.mouseEventToLatLng(event.touches?event.touches[0]:event);drag.target={lat:latlng.lat,lon:latlng.lng};if(frame)cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{frame=null;if(drag)applyDrag()})}
    function end(){if(!drag)return;if(frame){cancelAnimationFrame(frame);frame=null}if(drag.target)applyDrag();if(drag.rejected)toast('Замки и длины звеньев не позволяют это перемещение. Снимите ближайший замок или отключите верёвку.',true);if(drag.wasDragging)map.dragging.enable();drag=null;draw()}
    window.document.addEventListener('mousemove',move);window.document.addEventListener('mouseup',end);window.document.addEventListener('touchmove',move,{passive:false});window.document.addEventListener('touchend',end);window.document.addEventListener('touchcancel',end);
    // Canvas paths have no native touchstart event. Hit-test the visible nodes
    // before Leaflet starts panning; keep pinch gestures available elsewhere.
    map.getContainer().addEventListener('touchstart',event=>{
      if(!active||busy||event.touches.length!==1)return;
      const at=map.mouseEventToContainerPoint(event.touches[0]);let found=-1,best=14;
      document.nodes.forEach((n,i)=>{if(n.excluded)return;const p=map.latLngToContainerPoint([n.lat,n.lon]),d=p.distanceTo(at);if(d<best){best=d;found=i}});
      if(found>=0){beginDrag(found,event);inspect(found,marks[found])}
    },{passive:false,capture:true});
    async function open(){if(active||busy)return;busy=true;try{const current=trip();if(!current)throw Error('Выберите поездку');document=await request(`/api/controller/trips/${encodeURIComponent(current.summary.id)}/reference`);active=true;window.document.body.classList.add('reference-editing');viewToken++;readonly.clearLayers();savedDocument=document.saved?structuredClone(document):null;dirty=!document.saved;undo=[];$('referenceIndex').max=document.nodes.length;selected=document.nodes.findIndex(n=>!n.excluded);$('referenceIndex').value=selected+1;for(const id of ['referenceFrom','referenceTo']){$(id).max=document.nodes.length;$(id).value=1}$('referenceToolbar').hidden=false;$('tripPanel').classList.remove('open');map.fitBounds(document.nodes.filter(n=>!n.excluded).map(n=>[n.lat,n.lon]),{padding:[70,100]});draw();toast('Референс: перетаскивайте узлы. Измерения сохраняются отдельно.')}catch(error){toast(error.message,true)}finally{busy=false;status()}}
    async function save(){if(busy||!document)return;busy=true;status();try{document=await request(`/api/controller/trips/${encodeURIComponent(document.trip_id)}/reference`,{method:'POST',body:JSON.stringify(patch())});dirty=false;savedDocument=structuredClone(document);toast('Референс сохранён')}catch(error){toast(error.message,true)}finally{busy=false;status()}}
    function close(discard=false){if(busy)return false;if(dirty&&!discard){$('referenceDiscard').hidden=false;toast('Сохраните правки или нажмите «Отбросить и выйти»',true);return false}end();active=false;window.document.body.classList.remove('reference-editing');drawReadonly();document=null;marks=[];edges=[];layer.clearLayers();links.clearLayers();$('referenceToolbar').hidden=true;$('referenceDiscard').hidden=true;return true}
    $('editReference').addEventListener('click',open);$('referenceSave').addEventListener('click',save);$('referenceExport').addEventListener('click',download);$('referenceClose').addEventListener('click',()=>close());$('referenceDiscard').addEventListener('click',()=>close(true));
    $('referenceUndo').addEventListener('click',()=>{if(!undo.length)return;const previous=undo.pop();document.nodes.forEach((n,i)=>Object.assign(n,previous[i]));marks=[];dirty=true;draw()});
    $('referenceIndex').addEventListener('change',()=>{if(document){pick();status()}});
    $('referenceLock').addEventListener('click',()=>{if(busy||!document)return;const n=pick();if(n.excluded)return;checkpoint();n.locked=!n.locked;dirty=true;draw()});
    $('referenceCut').addEventListener('click',()=>{if(busy||!document)return;const n=pick();if(n.excluded)return;checkpoint();n.cut_before=!n.cut_before;marks=[];dirty=true;draw();toast(n.cut_before?'Разрыв перед выбранным узлом добавлен':'Ручной разрыв убран')});
    $('referenceRangeStart').addEventListener('click',()=>{if(document){pick();$('referenceFrom').value=selected+1}});
    $('referenceRangeEnd').addEventListener('click',()=>{if(document){pick();$('referenceTo').value=selected+1}});
    $('referenceDelete').addEventListener('click',()=>{if(busy||!document)return;checkpoint();if(!excludeRange(document.nodes,Number($('referenceFrom').value)-1,Number($('referenceTo').value)-1)){undo.pop();toast('Проверьте диапазон, снимите его замки и оставьте не менее двух узлов.',true);return}marks=[];dirty=true;draw();toast('Диапазон исключён из реальной траектории. Исходные измерения сохранены.')});
    $('referenceRestore').addEventListener('click',()=>{if(busy||!document)return;checkpoint();if(!restoreRange(document.nodes,Number($('referenceFrom').value)-1,Number($('referenceTo').value)-1)){undo.pop();toast('Проверьте диапазон узлов',true);return}marks=[];dirty=true;draw();toast('Диапазон восстановлен. Проверьте стыки и сохраните правки.')});
    $('referenceZoom').addEventListener('click',()=>{if(!document)return;const n=pick();if(n.excluded){toast('Узел исключён из референса',true);return}map.setView([n.lat,n.lon],18);inspect(selected,marks[selected])});
    $('referenceJoin').addEventListener('click',()=>{if(busy||!document)return;pick();checkpoint();if(!joinFragment(document.nodes,selected)){undo.pop();toast('Выберите следующий фрагмент без замков и без исключённого участка перед ним',true);return}dirty=true;draw();toast('Фрагмент перенесён к предыдущему. Стык исключён из пробега')});
    map.on('zoomend',()=>{if(active)marks.forEach((m,i)=>m&&m.setRadius(document.nodes[i].break_before?6:(map.getZoom()<15?2:4)))});
    window.addEventListener('beforeunload',event=>{if(active&&dirty){event.preventDefault();event.returnValue=''}});
    return {get active(){return active},close,view,clear:()=>{viewToken++;savedDocument=null;readonly.clearLayers()}};
  }
  const api={distance,bearing,destination,rope,joinFragment,quality,excludeRange,restoreRange,segments,create};root.X50Reference=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
