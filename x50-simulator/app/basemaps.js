(function(root){
  const osm='&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors';
  const carto=osm+' &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener noreferrer">CARTO</a>';
  const sources={
    osm:{label:'OpenStreetMap',url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',maxNativeZoom:19,attribution:osm},
    hot:{label:'OSM France — HOT',url:'https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png',subdomains:'abc',maxNativeZoom:19,attribution:osm+' · <a href="https://www.hotosm.org/" target="_blank" rel="noopener noreferrer">HOT</a> · <a href="https://www.openstreetmap.fr/" target="_blank" rel="noopener noreferrer">OSM France</a>'},
    voyager:{label:'CARTO — цветная',url:'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',subdomains:'abcd',maxNativeZoom:20,attribution:carto},
    light:{label:'CARTO — светлая',url:'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',subdomains:'abcd',maxNativeZoom:20,attribution:carto},
    dark:{label:'CARTO — тёмная',url:'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',subdomains:'abcd',maxNativeZoom:20,attribution:carto}
  };
  function create({map,leaflet,select,status,storage}){
    const key='x50-map-source';
    let layer=null,current=null;
    const update=(message,error=false)=>{status.textContent=message;status.classList.toggle('warn',error)};
    for(const [id,source] of Object.entries(sources)){
      const option=select.ownerDocument.createElement('option');option.value=id;option.textContent=source.label;select.appendChild(option);
    }
    function setSource(id){
      if(!Object.prototype.hasOwnProperty.call(sources,id))id='osm';
      if(id===current)return;
      if(layer){map.removeLayer(layer);layer.off()}
      current=id;select.value=id;
      const source=sources[id];let errors=0;
      const next=leaflet.tileLayer(source.url,{maxZoom:20,maxNativeZoom:source.maxNativeZoom,subdomains:source.subdomains||'abc',attribution:source.attribution,updateWhenIdle:true,keepBuffer:1});
      layer=next;
      next.on('loading',()=>{if(layer===next){errors=0;update('Загрузка карты…')}});
      next.on('tileerror',()=>{if(layer===next){errors++;update('Источник карты недоступен. Выберите другой.',true)}});
      next.on('load',()=>{if(layer===next&&!errors)update(source.label+' · карта загружена')});
      update('Загрузка карты…');next.addTo(map);
      try{storage.setItem(key,id)}catch{}
    }
    select.addEventListener('change',()=>setSource(select.value));
    let initial='osm';try{initial=storage.getItem(key)||initial}catch{}
    setSource(initial);
    return {setSource,getSource:()=>current};
  }
  const api={sources,create};
  if(typeof module==='object'&&module.exports)module.exports=api;
  else root.X50Basemaps=api;
})(typeof window==='object'?window:globalThis);
