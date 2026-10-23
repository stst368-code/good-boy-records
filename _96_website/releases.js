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
    for(const track of data.tracks){
      if(!track||typeof track.id!=='string'||!track.id.startsWith('gbr-')||ids.has(track.id)||typeof track.displayTitle!=='string'||
         !track.audio?.sources?.mp3||![track.audio.sources.mp3,track.audio.sources.flac,track.artwork_url,track.lyrics_url,track.playback_url].every(safeUrl))throw Error('Invalid public release');
      ids.add(track.id);
    }
    const original=Array.isArray(base)?{tracks:base}:base||{tracks:[]};
    const tracks=new Map((original.tracks||[]).map(track=>[track.id,track]));
    for(const track of data.tracks)tracks.set(track.id,track);
    return {...original,tracks:[...tracks.values()],trackCount:tracks.size};
  }catch(error){console.warn('New releases unavailable; using the saved catalogue.',error.message);return base;}
};
