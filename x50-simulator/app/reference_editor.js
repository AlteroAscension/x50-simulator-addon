(function(root){
  'use strict';
  const rad=Math.PI/180,R=6371000,valid=v=>typeof v==='number'&&Number.isFinite(v);
  const angle=v=>((v+540)%360)-180;
  function distance(a,b){const p=a.lat*rad,q=b.lat*rad,x=(b.lat-a.lat)*rad,y=(b.lon-a.lon)*rad;return R*2*Math.asin(Math.min(1,Math.sqrt(Math.sin(x/2)**2+Math.cos(p)*Math.cos(q)*Math.sin(y/2)**2)))}
  function bearing(a,b){const p=a.lat*rad,q=b.lat*rad,y=(b.lon-a.lon)*rad;return (Math.atan2(Math.sin(y)*Math.cos(q),Math.cos(p)*Math.sin(q)-Math.sin(p)*Math.cos(q)*Math.cos(y))/rad+360)%360}
  function destination(a,heading,length){const p=a.lat*rad,q=a.lon*rad,h=heading*rad,d=length/R;const lat=Math.asin(Math.sin(p)*Math.cos(d)+Math.cos(p)*Math.sin(d)*Math.cos(h));return {lat:lat/rad,lon:angle((q+Math.atan2(Math.sin(h)*Math.sin(d)*Math.cos(p),Math.cos(d)-Math.sin(p)*Math.sin(lat)))/rad)}}
  function rope(nodes,index,target){
    nodes[index].lat=target.lat;nodes[index].lon=target.lon;
    // Both free ends follow; only explicit fragment boundaries stop the rope.
    for(let i=index+1;i<nodes.length&&!nodes[i].break_before;i++){
      Object.assign(nodes[i],destination(nodes[i-1],bearing(nodes[i-1],nodes[i]),nodes[i].rest_m));
    }
    for(let i=index-1;i>=0&&!nodes[i+1].break_before;i--){
      Object.assign(nodes[i],destination(nodes[i+1],bearing(nodes[i+1],nodes[i]),nodes[i+1].rest_m));
    }
  }
  function joinFragment(nodes,index){
    let start=index;while(start>0&&!nodes[start].break_before)start--;
    if(start===0)return false;
    const a=nodes[start],b=nodes[start-1],heading=bearing(a,b),length=distance(a,b);
    for(let i=start;i<nodes.length&&(i===start||!nodes[i].break_before);i++)Object.assign(nodes[i],destination(nodes[i],heading,length));
    rope(nodes,start,{lat:b.lat,lon:b.lon});
    return true;
  }
  function quality(nodes,i){
    const n=nodes[i],p=nodes[i-1];if(!p||n.break_before)return {color:'#94a3b8',label:'Свободный стык'};
    const length=distance(p,n),heading=bearing(p,n),e=n.evidence||{},s=e.step||{},c=e.compass||{};
    const reverse=s.motion_direction===-1,course=(heading+(reverse?180:0))%360;
    const compass=(s.compass_valid!==false&&s.compass_age_ms<=2000&&valid(s.compass_sensor_calibrated_deg))?s.compass_sensor_calibrated_deg*(s.compass_direction??1)+(s.compass_offset_deg??0):
      (c.valid&&c.age_ms<=2000&&valid(c.calibrated_deg)?c.calibrated_deg:null);
    const lengthError=Math.abs(length-n.rest_m),compassError=valid(compass)&&length>.5?Math.abs(angle(course-compass)):null;
    let steeringError=null;
    const ps=p.evidence?.step||{},wheelValid=v=>v.steering_fresh&&valid(v.steer_deg)&&valid(v.steering_ratio)&&v.steering_ratio>0&&valid(v.wheelbase_m)&&v.wheelbase_m>0;
    if(i>1&&!p.break_before&&length>.5&&distance(nodes[i-2],p)>.5&&wheelValid(s)&&wheelValid(ps)){
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
    let document=null,active=false,dirty=false,selected=0,undo=[],drag=null,busy=false,frame=null,marks=[],edges=[];
    const layer=L.layerGroup().addTo(map),links=L.layerGroup().addTo(map),renderer=L.canvas({padding:.3,pane:'referenceTrajectory'});
    const pane=map.createPane('referenceTrajectory');pane.style.zIndex=650;
    map.createPane('referenceInfo').style.zIndex=750;
    const $=id=>window.document.getElementById(id),positions=()=>document.nodes.map(n=>({lat:n.lat,lon:n.lon}));
    const patch=()=>({revision:document.revision,source_sha256:document.source_sha256,nodes:document.nodes.map(n=>({id:n.id,lat:n.lat,lon:n.lon}))});
    function checkpoint(){undo.push(positions());if(undo.length>30)undo.shift()}
    function beginDrag(index,event){
      if(busy||!active||drag)return;
      L.DomEvent.stop(event);checkpoint();selected=index;
      drag={index,origin:positions(),wasDragging:map.dragging.enabled()};
      map.dragging.disable();marks[index].closeTooltip();
    }
    function status(){if(!document)return;$('referenceStatus').textContent=`${dirty?'Есть несохранённые правки':'Сохранено'} · версия ${document.revision} · ${document.nodes.length} узлов · ${document.nodes.filter(n=>n.break_before).length} фрагментов`;$('referenceSave').disabled=busy||!dirty;$('referenceUndo').disabled=busy||!undo.length}
    function download(){if(!document)return;const blob=new Blob([JSON.stringify({...document,draft:dirty},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=window.document.createElement('a');a.href=url;a.download=`${document.trip_id}.reference${dirty?'.draft':''}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
    function inspect(i,marker){
      selected=i;$('referenceIndex').value=i+1;links.clearLayers();const n=document.nodes[i],e=n.evidence||{},q=quality(document.nodes,i),fmt=(v,suffix='')=>valid(v)?v.toFixed(1)+suffix:'нет данных';
      const info=[`Узел ${i+1} · ${new Date(n.t_ms).toLocaleTimeString('ru-RU')}`,q.label,
        `Звено ${fmt(q.length,' м')} / ${fmt(n.rest_m,' м')} · ${n.length_source==='vehicle_speed_integral'?'интеграл скорости':'исходная геометрия'}`,
        `Курс ${fmt(q.heading,'°')} · ошибка компаса ${fmt(q.compassError,'°')} · руля ${fmt(q.steeringError,'°')}`,
        `Скорость ${fmt(e.speed_kmh,' км/ч')} · одометр ${fmt(e.odometer_km,' км')}`,
        `Компас ${fmt(e.step?.compass_sensor_calibrated_deg??e.compass?.calibrated_deg,'°')} · возраст ${fmt(e.step?.compass_age_ms??e.compass?.age_ms,' мс')}`,
        `Руль ${fmt(e.step?.steer_deg??e.step?.fusion_steering_angle_deg,'°')} · ${e.step?.steering_fresh?'свежий':'свежесть не подтверждена'}`,
        `Смещение от исходного узла ${fmt(distance(n,{lat:n.original_lat,lon:n.original_lon}),' м')}`,
        `GPS: точность ${fmt(e.gps_accuracy_m,' м')}, возраст ${fmt(e.gps_age_ms,' мс')}, ${e.gps_good?'качественный':'качество не подтверждено'}`,
        `Δ времени измерения ${fmt(valid(e.sample_time_ms)?e.sample_time_ms-n.t_ms:null,' мс')}`];
      for(const [key,color,label] of [['fake','#ffb33f','FakeGPS'],['gps','#36caff','GPS']])if(e[key]){const point=[e[key].lat,e[key].lon];L.polyline([[n.lat,n.lon],point],{pane:'referenceTrajectory',color,weight:2,dashArray:'4 6',interactive:false}).addTo(links);L.circleMarker(point,{pane:'referenceTrajectory',radius:5,color,interactive:false}).bindTooltip(label).addTo(links)}
      marker.bindTooltip(info.join('<br>'),{pane:'referenceInfo',direction:'auto',offset:[12,0],className:'reference-tooltip'}).openTooltip();
      $('referenceNode').textContent=`Выбран узел ${i+1}. ${n.break_before?'Начало фрагмента: стык не входит в пробег.':''}`;
    }
    function draw(){
      links.clearLayers();if(!document)return;
      const nodes=document.nodes;
      if(marks.length===nodes.length){nodes.forEach((n,i)=>{const q=quality(nodes,i);marks[i].setLatLng([n.lat,n.lon]).setStyle({fillColor:q.color});if(edges[i])edges[i].setLatLngs([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]]).setStyle({color:q.color})});status();return}
      layer.clearLayers();marks=[];edges=[];
      nodes.forEach((n,i)=>{
        const q=quality(nodes,i);
        if(i&&!n.break_before)edges[i]=L.polyline([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]],{pane:'referenceTrajectory',renderer,color:q.color,weight:4,interactive:false}).addTo(layer);
        else if(i)edges[i]=L.polyline([[nodes[i-1].lat,nodes[i-1].lon],[n.lat,n.lon]],{pane:'referenceTrajectory',color:'#94a3b8',weight:2,dashArray:'2 9',interactive:false}).addTo(layer);
        if(active){const marker=L.circleMarker([n.lat,n.lon],{pane:'referenceTrajectory',renderer,radius:n.break_before?6:(map.getZoom()<15?2:4),weight:1,color:'#162338',fillColor:q.color,fillOpacity:1}).addTo(layer);
          marks[i]=marker;marker.on('mouseover',()=>{if(!drag)inspect(i,marker)});
          marker.on('mousedown',event=>beginDrag(i,event.originalEvent));
        }
      });status();
    }
    function move(event){if(!drag)return;if(event.cancelable)event.preventDefault();const latlng=map.mouseEventToLatLng(event.touches?event.touches[0]:event);drag.target={lat:latlng.lat,lon:latlng.lng};if(frame)cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{frame=null;if(!drag)return;document.nodes.forEach((n,i)=>Object.assign(n,drag.origin[i]));if($('referenceRope').checked)rope(document.nodes,drag.index,{lat:latlng.lat,lon:latlng.lng});else Object.assign(document.nodes[drag.index],{lat:latlng.lat,lon:latlng.lng});dirty=true;draw()})}
    function end(){if(!drag)return;if(frame){cancelAnimationFrame(frame);frame=null}if(drag.target){document.nodes.forEach((n,i)=>Object.assign(n,drag.origin[i]));if($('referenceRope').checked)rope(document.nodes,drag.index,drag.target);else Object.assign(document.nodes[drag.index],drag.target);dirty=true}if(drag.wasDragging)map.dragging.enable();drag=null;draw()}
    window.document.addEventListener('mousemove',move);window.document.addEventListener('mouseup',end);window.document.addEventListener('touchmove',move,{passive:false});window.document.addEventListener('touchend',end);window.document.addEventListener('touchcancel',end);
    // Canvas paths have no native touchstart event. Hit-test the visible nodes
    // before Leaflet starts panning; keep pinch gestures available elsewhere.
    map.getContainer().addEventListener('touchstart',event=>{
      if(!active||busy||event.touches.length!==1)return;
      const at=map.mouseEventToContainerPoint(event.touches[0]);let found=-1,best=14;
      document.nodes.forEach((n,i)=>{const p=map.latLngToContainerPoint([n.lat,n.lon]),d=p.distanceTo(at);if(d<best){best=d;found=i}});
      if(found>=0){beginDrag(found,event);inspect(found,marks[found])}
    },{passive:false,capture:true});
    async function open(){if(active||busy)return;busy=true;try{const current=trip();if(!current)throw Error('Выберите поездку');document=await request(`/api/controller/trips/${encodeURIComponent(current.summary.id)}/reference`);active=true;window.document.body.classList.add('reference-editing');if(background)map.removeLayer(background);dirty=!document.saved;undo=[];$('referenceIndex').max=document.nodes.length;$('referenceIndex').value=1;selected=0;$('referenceToolbar').hidden=false;$('tripPanel').classList.remove('open');map.fitBounds(document.nodes.map(n=>[n.lat,n.lon]),{padding:[70,100]});draw();toast('Референс: перетаскивайте узлы. Измерения сохраняются отдельно.')}catch(error){toast(error.message,true)}finally{busy=false;status()}}
    async function save(){if(busy||!document)return;busy=true;status();try{document=await request(`/api/controller/trips/${encodeURIComponent(document.trip_id)}/reference`,{method:'POST',body:JSON.stringify(patch())});dirty=false;toast('Референс сохранён')}catch(error){toast(error.message,true)}finally{busy=false;status()}}
    function close(discard=false){if(busy)return false;if(dirty&&!discard){$('referenceDiscard').hidden=false;toast('Сохраните правки или нажмите «Отбросить и выйти»',true);return false}end();active=false;window.document.body.classList.remove('reference-editing');if(background)background.addTo(map);document=null;marks=[];edges=[];layer.clearLayers();links.clearLayers();$('referenceToolbar').hidden=true;$('referenceDiscard').hidden=true;return true}
    $('editReference').addEventListener('click',open);$('referenceSave').addEventListener('click',save);$('referenceExport').addEventListener('click',download);$('referenceClose').addEventListener('click',()=>close());$('referenceDiscard').addEventListener('click',()=>close(true));
    $('referenceUndo').addEventListener('click',()=>{if(!undo.length)return;const previous=undo.pop();document.nodes.forEach((n,i)=>Object.assign(n,previous[i]));dirty=true;draw()});
    $('referenceZoom').addEventListener('click',()=>{if(!document)return;selected=Math.max(0,Math.min(document.nodes.length-1,Number($('referenceIndex').value)-1));const n=document.nodes[selected];map.setView([n.lat,n.lon],18);inspect(selected,marks[selected])});
    $('referenceJoin').addEventListener('click',()=>{if(busy||!document)return;checkpoint();if(!joinFragment(document.nodes,selected)){undo.pop();toast('Выберите узел следующего фрагмента',true);return}dirty=true;draw();toast('Фрагмент перенесён к предыдущему. Стык исключён из пробега')});
    map.on('zoomend',()=>{if(active)marks.forEach((m,i)=>m.setRadius(document.nodes[i].break_before?6:(map.getZoom()<15?2:4)))});
    window.addEventListener('beforeunload',event=>{if(active&&dirty){event.preventDefault();event.returnValue=''}});
    return {get active(){return active},close};
  }
  const api={distance,bearing,destination,rope,joinFragment,quality,create};root.X50Reference=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window==='undefined'?globalThis:window);
