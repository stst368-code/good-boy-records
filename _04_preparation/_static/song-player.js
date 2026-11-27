import {spans,warp,sounds,size,duration} from './song-model.js';

// The draft player auditions arrangement edits and samples. FFmpeg renders the
// complete mastering chain for the processed audition and release master.
export function createSongPlayer({getProject,sourceUrl,toast,onTick}){
 let context=null,sourceBuffer=null,sourceKey='',position=0,anchor=0,playing=false,loading=0;
 let nodes=[],timer=null,scheduled=new Set(),musicBus=null,masterBus=null;
 const decoded=new Map(),pending=new Map();
 const processed=new Audio();processed.preload='metadata';
 let previewStart=0,previewEnd=0,previewKey='',mode='draft',loop=null,speed=1,stopAt=null;
 const time=()=>mode==='processed'&&!processed.paused?previewStart+processed.currentTime:
  playing?Math.max(anchor,Math.min(duration(getProject()),anchor+(context.currentTime-position)*speed)):anchor;
 const active=()=>playing||!processed.paused;
 async function buffer(url){
  if(decoded.has(url)){const value=decoded.get(url);decoded.delete(url);decoded.set(url,value);return value;}
  if(pending.has(url))return pending.get(url);
  const audioContext=context;
  const promise=(async()=>{
   const response=await fetch(url);if(!response.ok)throw Error('Audio unavailable. Reload the song or import the clip again.');
   const result=await audioContext.decodeAudioData(await response.arrayBuffer());
   decoded.set(url,result);
   while(decoded.size>4)decoded.delete(decoded.keys().next().value);
   return result;
  })();pending.set(url,promise);
  try{return await promise;}finally{pending.delete(url);}
 }
 function stopNodes(){
  for(const node of nodes){try{node.stop?.();node.disconnect();}catch{}}
  nodes=[];scheduled.clear();if(timer)clearInterval(timer);timer=null;
 }
 function pause(){const at=time();++loading;playing=false;processed.pause();anchor=at;stopNodes();onTick?.();}
 function curveAt(event,at){
  const local=at-event.position;if(local<0||local>=event.duration)return 0;
  return Math.min(1,event.fade_in?local/event.fade_in:1,event.fade_out?(event.duration-local)/event.fade_out:1);
 }
 function duckGain(event,at){return 1+(10**(event.duck_db/20)-1)*curveAt(event,at);}
 function outputWhen(at){return position+(at-anchor)/speed;}
 function connectMusic(project){
  masterBus=context.createGain();masterBus.gain.value=1;masterBus.connect(context.destination);nodes.push(masterBus);
  musicBus=context.createGain();let output=musicBus;nodes.push(musicBus);
  for(const event of sounds(project).filter(s=>s.duck_db)){
   const gain=context.createGain(),param=gain.gain;nodes.push(gain);output.connect(gain);output=gain;
   param.setValueAtTime(duckGain(event,anchor),context.currentTime);
   if(event.position>anchor)param.setValueAtTime(event.fade_in?1:10**(event.duck_db/20),outputWhen(event.position));
   if(event.fade_in&&event.position+event.fade_in>anchor)param.linearRampToValueAtTime(10**(event.duck_db/20),outputWhen(event.position+event.fade_in));
   if(event.position+event.duration-event.fade_out>anchor)param.setValueAtTime(10**(event.duck_db/20),outputWhen(event.position+event.duration-event.fade_out));
   if(event.fade_out&&event.position+event.duration>anchor)param.linearRampToValueAtTime(1,outputWhen(event.position+event.duration));
   if(event.position+event.duration>anchor)param.setValueAtTime(1,outputWhen(event.position+event.duration));
  }
  output.connect(masterBus);
 }
 function warped(segment){
  const rate=sourceBuffer.sampleRate,count=Math.round(size(segment)*rate),result=context.createBuffer(sourceBuffer.numberOfChannels,count,rate);
  const low=Math.max(0,Math.round(segment.start*rate)),high=Math.min(sourceBuffer.length-1,Math.ceil(segment.end*rate)-1);
  for(let channel=0;channel<result.numberOfChannels;channel++){
   const original=sourceBuffer.getChannelData(channel),target=result.getChannelData(channel);
   for(const [left,right,begin,end] of warp(segment)){
    const first=Math.round(left*rate),last=Math.min(count,Math.round(right*rate));
    for(let index=first;index<last;index++){
     const point=Math.max(low,Math.min(high,(begin+(index/rate-left)/(right-left)*(end-begin))*rate-(end<begin?1:0)));
     const base=Math.floor(point),fraction=point-base;
     const edgeStart=segment.mode==='stutter'?first:0,edgeEnd=segment.mode==='stutter'?last:count;
     const edge=Math.max(0,Math.min(1,(index-edgeStart)/(rate*.002),(edgeEnd-1-index)/(rate*.002)));
     target[index]=(original[base]*(1-fraction)+original[Math.min(base+1,high)]*fraction)*edge;
    }
   }
  }
  return result;
 }
 function scheduleMusic(segment,now){
  if(segment.kind==='gap'||segment.mode==='mute')return;
  const from=Math.max(segment.at,now),end=Math.min(segment.until,stopAt??Infinity);
  if(end<=from)return;
  const source=context.createBufferSource(),gain=context.createGain();nodes.push(source,gain);
  source.buffer=['normal','mute'].includes(segment.mode)?sourceBuffer:warped(segment);
  source.playbackRate.value=speed;
  gain.gain.value=10**((segment.gain_db||0)/20);source.connect(gain);gain.connect(musicBus);
  source.start(Math.max(context.currentTime,outputWhen(from)),
   segment.mode==='normal'?segment.start+from-segment.at:from-segment.at,end-from);
 }
 async function scheduleSound(event,now,ticket){
  const clip=await buffer('/api/preparation/sounds/'+encodeURIComponent(event.clip_id)+'/audio');
  if(!playing||ticket!==loading)return;
  const from=Math.max(event.position,time()),end=Math.min(duration(getProject()),event.position+event.duration,stopAt??Infinity);
  if(end<=from)return;
  const node=context.createBufferSource(),gain=context.createGain();nodes.push(node,gain);
  node.buffer=clip;node.playbackRate.value=speed;node.connect(gain);
  if(context.createStereoPanner){const pan=context.createStereoPanner();pan.pan.value=event.pan||0;gain.connect(pan);pan.connect(masterBus);nodes.push(pan);}else gain.connect(masterBus);
  const when=Math.max(context.currentTime,outputWhen(from)),level=10**(event.gain_db/20);
  gain.gain.setValueAtTime(level*curveAt(event,from),when);
  for(const at of [event.position+event.fade_in,event.position+event.duration-event.fade_out,event.position+event.duration]){
   if(at>from)gain.gain.linearRampToValueAtTime(level*curveAt(event,at===event.position+event.duration?at-.000001:at),outputWhen(at));
  }
  node.start(when,event.trim+from-event.position,end-from);
 }
 function pump(){
  if(!playing)return;
  const project=getProject(),now=time();
  if(loop&&now>=loop.end-.01){seek(loop.start);return;}
  if(stopAt!=null&&now>=stopAt){pause();anchor=stopAt;stopAt=null;return;}
  if(now>=duration(project)-.005){pause();anchor=0;onTick?.();return;}
  for(const segment of spans(project)){
   if(segment.until<=now||segment.at>now+8||scheduled.has(segment.id))continue;
   scheduled.add(segment.id);scheduleMusic(segment,now);
  }
  for(const event of sounds(project)){
   if(event.position+event.duration<=now||event.position>now+8||scheduled.has(event.id))continue;
   scheduled.add(event.id);scheduleSound(event,now,loading).catch(error=>{if(playing){pause();toast(error.message);}});
  }
  onTick?.();
 }
 async function play(){
  if(active())return;
  const ticket=++loading;
  document.getElementById('audio')?.pause();
  if(mode==='processed'){
   if(!previewKey)throw Error('Render a processed preview first');
   if(anchor<previewStart||anchor>=previewEnd)anchor=previewStart;
   processed.currentTime=anchor-previewStart;
   try{await processed.play();}catch(error){if(ticket!==loading)return;throw error;}
   return;
  }
  const audioContext=context||=new AudioContext();
  try{await audioContext.resume();}catch(error){if(ticket!==loading||context!==audioContext)return;throw error;}
  if(ticket!==loading||context!==audioContext)return;
  const key=sourceUrl();
  if(sourceKey!==key||!sourceBuffer){
   let decoded;
   try{decoded=await buffer(key);}catch(error){if(ticket!==loading)return;throw error;}
   if(ticket!==loading||key!==sourceUrl())return;
   sourceKey=key;sourceBuffer=decoded;
  }
  if(ticket!==loading)return;
  if(anchor>=duration(getProject()))anchor=0;
  playing=true;position=context.currentTime+.04;connectMusic(getProject());pump();timer=setInterval(pump,120);
 }
 function seek(at){const resume=active();pause();anchor=Math.max(0,Math.min(duration(getProject()),at));if(resume)play().catch(error=>toast(error.message));onTick?.();}
 processed.onended=()=>{anchor=previewEnd;if(loop&&loop.start>=previewStart&&loop.end<=previewEnd){anchor=loop.start;processed.currentTime=anchor-previewStart;processed.play().catch(error=>toast(error.message));}onTick?.();};
 processed.ontimeupdate=()=>{
  if(!processed.paused&&loop&&loop.start>=previewStart&&loop.end<=previewEnd&&previewStart+processed.currentTime>=loop.end)processed.currentTime=loop.start-previewStart;
  if(!processed.paused&&stopAt!=null&&previewStart+processed.currentTime>=stopAt){pause();anchor=stopAt;stopAt=null;}
 };
 return {time,active,pause,play,seek,
  setMode(value){pause();mode=value;},
  setLoop(value){loop=value;},
  setStop(value){stopAt=value;},
  setSpeed(value){const resume=active();pause();speed=value;processed.playbackRate=value;if(resume)play().catch(error=>toast(error.message));},
  preview(url,start,seconds){if(mode==='processed')pause();previewKey=url;previewStart=start;previewEnd=start+seconds;processed.src=url;},
  invalidate(stopDraft=true){if(stopDraft||mode==='processed')pause();mode='draft';previewKey='';processed.removeAttribute('src');processed.load();},
  reset(){pause();anchor=0;stopAt=null;loop=null;speed=1;mode='draft';previewKey='';previewStart=previewEnd=0;processed.playbackRate=1;processed.removeAttribute('src');processed.load();sourceKey='';sourceBuffer=null;decoded.clear();pending.clear();},
  close(){this.reset();context?.close().catch(()=>{});context=null;}
 };
}
