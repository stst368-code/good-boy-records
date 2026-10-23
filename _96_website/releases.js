/* Add approved R2 releases without rebuilding the legacy catalogue or share IDs. */
window.GBRloadReleases=async function(base){
  const source=window.GBR_RELEASE_CATALOGUE;
  if(!source)return base;
  try{
    const response=await fetch(source,{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(!response.ok)return base;
    const data=await response.json();
    if(data.format!=='gbr-public-releases-v1'||!Array.isArray(data.tracks))throw Error('Invalid release catalogue');
    const safeUrl=value=>{if(value==null)return true;try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password;}catch{return false;}};
    const ids=new Set();
    const original=Array.isArray(base)?{tracks:base}:base||{tracks:[]};
    const validId=value=>typeof value==='string'&&/^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}$/.test(value);
    for(const track of data.tracks){
      if(!track||!validId(track.id)||!(track.id.startsWith('gbr-')||track.legacy_id===track.id&&/^[a-zA-Z0-9]{8}$/.test(track.shareToken||''))||ids.has(track.id)||typeof track.displayTitle!=='string'||
         !track.audio?.sources?.mp3||![track.audio.sources.mp3,track.audio.sources.flac,track.artwork_url,track.lyrics_url,track.playback_url].every(safeUrl))throw Error('Invalid public release');
      ids.add(track.id);
    }
    if(data.withdrawn!==undefined&&(!Array.isArray(data.withdrawn)||!data.withdrawn.every(validId)))throw Error('Invalid withdrawal list');
    const withdrawn=new Set(data.withdrawn||[]);
    const tracks=new Map((original.tracks||[]).filter(track=>!withdrawn.has(track.id)).map(track=>[track.id,track]));
    for(const track of data.tracks)if(!withdrawn.has(track.id))tracks.set(track.id,track);
    return {...original,tracks:[...tracks.values()],trackCount:tracks.size};
  }catch(error){console.warn('New releases unavailable; using the saved catalogue.',error.message);return base;}
};
