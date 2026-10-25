/* Library-wide presentation and named room entrances; no private app data. */
window.GBRlibrary={
 settings:{sensitivity:1,display_order:'genre',rooms:[{id:'public',name:'Main player',enabled:true},{id:'hidden',name:'Secret room',enabled:true},{id:'easter',name:'Easter eggs',enabled:true}]},
 configured:false,pending:null,
 room(id){return this.settings.rooms.find(room=>room.id===id&&room.enabled);},
 url(id){if(id==='public')return './';if(id==='easter')return 'easter.html';return 'hidden.html'+(id==='hidden'?'':'?room='+encodeURIComponent(id));},
 catalogueUrl(id){
  if(!this.room(id))return null;
  if(id==='public')return window.GBR_RELEASE_CATALOGUE;
  if(id==='hidden')return window.GBR_HIDDEN_CATALOGUE;
  const base=window.GBR_LIBRARY_CONFIG;if(!base)return null;
  return new URL('rooms/'+id+'/catalogue.json',base).href;
 },
 async load(){
  if(this.pending)return this.pending;
  this.pending=(async()=>{
   if(!window.GBR_LIBRARY_CONFIG)return;
   try{
    const response=await fetch(window.GBR_LIBRARY_CONFIG,{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(5000)});
    if(!response.ok)return;const data=await response.json();
    if(data.format!=='gbr-library-v1'||!Array.isArray(data.rooms)||data.rooms.length>100||!data.rooms.some(r=>r.id==='public'&&r.enabled))throw Error('Invalid room configuration');
    const ids=new Set();
    for(const room of data.rooms){
     if(!/^[a-z][a-z0-9-]{0,63}$/.test(room.id)||ids.has(room.id)||typeof room.name!=='string'||room.name.length>100||typeof room.enabled!=='boolean'||room.entry_track_id!=null&&!/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$/.test(room.entry_track_id))throw Error('Invalid room');
     ids.add(room.id);
    }
    this.settings=data;this.configured=true;
   }catch(error){console.warn('Library settings unavailable; retaining player defaults.',error.message);}
  })();return this.pending;
 }
};
