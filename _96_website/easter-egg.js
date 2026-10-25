/* A cover gesture, not an authentication mechanism. */
window.GBRcreateCoverEgg=function({open=()=>location.assign(new URL('hidden.html',document.baseURI)),clock=()=>performance.now()}={}){
  let trackId=null,count=0,last=0,opened=false;
  const reset=()=>{count=0;last=0;};
  return {
    configure(value){reset();opened=false;trackId=value?.format==='gbr-easter-egg-v1'&&value.enabled===true&&typeof value.track_id==='string'?value.track_id:null;},
    reset,
    tap(track){
      if(opened||!trackId||track?.id!==trackId){reset();return false;}
      const time=clock();count=count&&time-last<=1400?count+1:1;last=time;
      if(count<3)return false;
      reset();opened=true;open();return true;
    },
    async load(){
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
