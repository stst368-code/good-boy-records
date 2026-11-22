export const uid=()=>crypto.randomUUID().replaceAll('-','');
export const clone=value=>structuredClone(value);
export const size=segment=>segment.kind==='gap'?segment.duration:segment.end-segment.start;
export function spans(project){let at=0;return project.segments.map(segment=>{const row={...segment,at,until:at+size(segment)};at=row.until;return row;});}
export const duration=project=>project?.segments.reduce((sum,s)=>sum+size(s),0)||0;
export function warp(segment){
 const length=size(segment),source=segment.start||0,mode=segment.mode||'normal';
 if(['normal','mute'].includes(mode))return [[0,length,source,source+length]];
 if(mode==='reverse')return [[0,length,source+length,source]];
 if(mode==='stutter'){const step=Math.min(.25,length);return Array.from({length:Math.ceil(length/step)},(_,i)=>[i*step,Math.min(length,(i+1)*step),source,source+Math.min(step,length-i*step)]);}
 const knots=mode==='scratch'?[[0,0],[.18,.65],[.38,.12],[.57,.78],[.76,.28],[1,1]]:Array.from({length:33},(_,i)=>[i/32,i/32-(i/32)**2/2]);
 return knots.slice(1).map(([b,d],i)=>{const [a,c]=knots[i];return [a*length,b*length,source+c*length,source+d*length];});
}
export function sounds(project){const offsets=new Map(spans(project).map(s=>[s.id,s.at]));return project.sounds.map(s=>({...s,position:s.segment_id?(offsets.get(s.segment_id)||0)+s.offset:s.at}));}
export function words(project){
 const result=[],groups=new Map();let group=0,previousEnd=null;
 for(const segment of spans(project)){
  if(segment.kind==='gap')continue;
  const normal=segment.mode==='normal';
  if(!normal||previousEnd==null||Math.abs(segment.start-previousEnd)>.00001)group++;
  previousEnd=normal?segment.end:null;if(segment.mode==='mute')continue;
  let occurrence=0,lastRight=null,lastDirection=null;
  for(const [at,end,left,right] of warp(segment)){
   const direction=right>left;
   if(lastRight==null||Math.abs(left-lastRight)>.00001||direction!==lastDirection)occurrence++;
   lastRight=right;lastDirection=direction;
   for(const word of project.words){
    if(word.omitted||word.start==null)continue;
    const low=Math.max(Math.min(left,right),word.start),high=Math.min(Math.max(left,right),word.end);
    if(high<=low)continue;
    const a=at+(low-left)/(right-left)*(end-at),b=at+(high-left)/(right-left)*(end-at);
    const key=normal?`${group}:${word.id}`:`${segment.id}:${occurrence}:${word.id}`;
    if(groups.has(key)){const current=groups.get(key);current.start=Math.min(current.start,segment.at+Math.min(a,b));current.end=Math.max(current.end,segment.at+Math.max(a,b));}
    else {const current={...word,id:key,source_id:word.id,segment_id:segment.id,start:segment.at+Math.min(a,b),end:segment.at+Math.max(a,b)};groups.set(key,current);result.push(current);}
   }
  }
 }
 return result.sort((a,b)=>a.start-b.start||a.end-b.end);
}
export function split(project,time){
 const index=spans(project).findIndex(s=>time>s.at+.009&&time<s.until-.009);
 if(index<0)return null;
 const segment=project.segments[index],offset=time-spans(project)[index].at,right=clone(segment);right.id=uid();
 if(segment.kind==='gap'){right.duration-=offset;segment.duration=offset;}
 else {
  if(!['normal','mute'].includes(segment.mode))throw Error('Split a normal music section before adding a record gesture');
  right.start=segment.start+offset;right.effect_offset=(segment.effect_offset||0)+offset;segment.end=right.start;
 }
 for(const event of [...project.sounds,...project.lighting.cues])if(event.segment_id===segment.id&&event.offset>=offset){event.segment_id=right.id;event.offset-=offset;}
 project.segments.splice(index+1,0,right);return right.id;
}
export function insertGap(project,time,seconds,label='Break'){
 split(project,time);
 const index=spans(project).findIndex(s=>s.at>=time-.009),gap={id:uid(),kind:'gap',duration:seconds,label};
 project.segments.splice(index<0?project.segments.length:index,0,gap);return gap.id;
}
export function gesture(project,start,end,mode){
 if(end-start<.01)throw Error('Drag a range on the ruler or music waveform first');
 if(['scratch','stop','stutter'].includes(mode)&&end-start>30)throw Error('Select up to 30 seconds for a record gesture');
 split(project,end);split(project,start);
 for(const span of spans(project))if(span.kind==='audio'&&span.at>=start-.009&&span.until<=end+.009){
  project.segments.find(s=>s.id===span.id).mode=mode;
 }
 project.approved=false;
}
export function duplicate(project,id){
 const index=project.segments.findIndex(s=>s.id===id);
 if(index<0)return;
 const copy=clone(project.segments[index]);copy.id=uid();copy.label=(copy.label||'Music')+' copy';
 const events=project.sounds.filter(s=>s.segment_id===id).map(s=>({...clone(s),id:uid(),segment_id:copy.id}));
 const cues=project.lighting.cues.filter(s=>s.segment_id===id).map(s=>({...clone(s),segment_id:copy.id}));
 project.segments.splice(index+1,0,copy);project.sounds.push(...events);project.lighting.cues.push(...cues);project.approved=false;
 return copy.id;
}
export function remove(project,id){
 if(project.segments.some(s=>s.id===id)){
  if(project.segments.length===1)throw Error('Keep at least one music section or break');
  project.segments=project.segments.filter(s=>s.id!==id);project.sounds=project.sounds.filter(s=>s.segment_id!==id);
  project.lighting.cues=project.lighting.cues.filter(s=>s.segment_id!==id);project.approved=false;
 }else project.sounds=project.sounds.filter(s=>s.id!==id);
}
export function move(project,id,direction){
 const index=project.segments.findIndex(s=>s.id===id),to=index+direction;
 if(index<0||to<0||to>=project.segments.length)return;
 [project.segments[index],project.segments[to]]=[project.segments[to],project.segments[index]];project.approved=false;
}
