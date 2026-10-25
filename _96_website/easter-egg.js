/* A cover gesture, not an authentication mechanism. */
window.GBRcreateCoverEgg=function({open=room=>location.assign(new URL(window.GBRlibrary?.url(room)||'hidden.html',document.baseURI)),clock=()=>performance.now()}={}){
  let entrances=new Map(),active=null,count=0,last=0,opened=false;
  const reset=()=>{count=0;last=0;active=null;};
  return {
    configure(value){
      reset();opened=false;entrances=new Map();
      if(value?.format==='gbr-library-v1')for(const room of value.rooms||[]){if(room.enabled&&room.id!=='public'&&typeof room.entry_track_id==='string')entrances.set(room.entry_track_id,room.id);}
      else if(value?.format==='gbr-easter-egg-v1'&&value.enabled===true&&typeof value.track_id==='string')entrances.set(value.track_id,'hidden');
    },
    reset,
    tap(track){
      if(opened||!entrances.has(track?.id)){reset();return false;}
      if(active!==track.id){reset();active=track.id;}
      const time=clock();count=count&&time-last<=1400?count+1:1;last=time;
      if(count<3)return false;
      const room=entrances.get(track.id);reset();opened=true;open(room);return true;
    },
    async load(){
      if(window.GBRlibrary){await window.GBRlibrary.load();if(window.GBRlibrary.configured){this.configure(window.GBRlibrary.settings);return;}}
      if(!window.GBR_EASTER_CONFIG)return;
      try{
        const response=await fetch(window.GBR_EASTER_CONFIG,{cache:'no-store',credentials:'omit',signal:AbortSignal.timeout(5000)});
        if(!response.ok)return;
        this.configure(await response.json());
      }catch(_){/* A missing Easter egg never blocks music playback. */}
    }
  };
};
window.GBRcoverEgg=window.GBRcreateCoverEgg();
