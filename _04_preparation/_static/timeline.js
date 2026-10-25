export function lightEvents(features,sensitivity,sections){
 const events=[];let previous=0,last=-10;
 for(const frame of features?.frames||[]){const time=frame.t,energy=frame.energy,boost=Math.max(0,...sections.filter(s=>s.start<=time&&time<s.end).map(s=>s.boost));if(time-last>=.15&&(energy*sensitivity>.65&&(energy-previous)*sensitivity>.1||boost>0&&energy>.1&&time-last>=.45)){events.push({time,strength:Math.min(1,energy*sensitivity*(1+boost)),duration:.25+Math.min(boost,2)*.15});last=time;}previous=energy;}
 return events;
}
export function shiftWordTiming(word,mode,delta,duration){
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value)),minimum=Math.min(.02,duration);
 if(mode==='start')return {start:clamp(word.start+delta,0,Math.max(0,word.end-minimum)),end:word.end};
 if(mode==='end')return {start:word.start,end:clamp(word.end+delta,Math.min(duration,word.start+minimum),duration)};
 const length=Math.min(duration,word.end-word.start),start=clamp(word.start+delta,0,duration-length);
 return {start,end:start+length};
}

export function initTimeline({root,api,toast,audio,getTime,seek,play=()=>audio.play(),pause=()=>audio.pause(),isPlaying=()=>!audio.paused,setRate=value=>{audio.playbackRate=value}}){
 const panel=document.createElement('section');panel.className='prep-timeline';
 panel.innerHTML=`<div class="prep-light-row"><h2>Lyrics and lighting</h2><span id="prepStar" aria-label="Star light reaction">✦</span></div>
 <div class="prep-actions prep-timeline-config"><label>Working audio<select id="prepAudioSource"><option value="">Reviewed source</option></select></label><label>Language<input id="prepLanguage" value="en" maxlength="3"></label><button id="prepAlign">Separate vocals + check lyrics</button><button id="prepReloadTimeline" class="secondary">Reload saved timeline</button></div>
 <div class="prep-timeline-toolbar" role="group" aria-label="Lyric timeline playback">
  <div class="prep-transport-row"><button id="prepTimelineBack" title="Back two seconds" aria-label="Back two seconds">−2s</button><button id="prepTimelinePlay" aria-label="Play working audio">Play</button><button id="prepTimelineAhead" title="Forward two seconds" aria-label="Forward two seconds">+2s</button><output id="prepClock">0:00 / 0:00</output><label>Speed<select id="prepTimelineSpeed"><option value="0.5">0.5×</option><option value="0.75">0.75×</option><option value="1" selected>1×</option></select></label><label>Zoom<select id="prepTimelineWindow"><option value="2">2s</option><option value="5">5s</option><option value="10" selected>10s</option><option value="30">30s</option><option value="60">60s</option></select></label></div>
  <input id="prepTimelineScrub" type="range" min="0" max="1" step=".01" value="0" aria-label="Position in recording">
  <div class="prep-window-row"><button id="prepWindowBack" aria-label="Previous timeline window">‹</button><output id="prepWindowLabel"></output><button id="prepWindowAhead" aria-label="Next timeline window">›</button><button id="prepFollow" class="secondary" aria-pressed="true">Follow playhead</button></div>
 </div>
 <div id="prepWordTimeline" role="group" tabindex="0" aria-label="Word timeline; click to seek, drag words or their edges to adjust timing"><div id="prepTimelineRuler"></div><div id="prepPlayhead"></div><div id="prepWordLayer"></div></div>
 <div id="prepWordEditor" hidden>
  <div class="prep-selected-word"><button id="prepWordPrevious" aria-label="Previous lyric word">‹</button><label>Word<input id="prepWordText"></label><button id="prepWordNext" aria-label="Next lyric word">›</button><button id="prepWordListen">Play word</button><output id="prepWordTiming"></output></div>
  <div class="prep-word-options"><button id="prepWordStartNow">Start at playhead</button><button id="prepWordEndNow">End at playhead</button><label><input id="prepWordConfirmed" type="checkbox"> Checked by me</label><label><input id="prepWordOmitted" type="checkbox"> Not sung</label></div>
  <details id="prepExactTiming"><summary>Exact timing (optional)</summary><div class="prep-actions"><label>Start / sec<input id="prepWordStart" type="number" step="0.01" min="0"></label><label>End / sec<input id="prepWordEnd" type="number" step="0.01" min="0"></label><button id="prepWordApply">Apply exact timing</button></div></details>
 </div>
 <div class="prep-actions prep-lyric-actions"><button id="prepSaveLyrics">Save corrections</button><button id="prepApproveLyrics" aria-pressed="false" title="Save and approve, including unchecked and estimated words">Approve lyrics</button><button id="prepUndoLyrics" class="secondary" disabled>Undo</button><button id="prepRedoLyrics" class="secondary" disabled>Redo</button></div>
 <p id="prepTimelineStatus" role="status"></p><p class="prep-note">Drag a word to move it, or either edge to change its length. Amber words are estimated or unchecked. Approval is your choice.</p>
 <div id="prepLyricWords"></div>
 <div class="prep-actions"><label>Sensitivity<input id="prepSensitivity" type="range" min="0.25" max="3" step="0.05" value="1"></label><output id="prepSensitivityValue">1.00</output><button id="prepSaveLights">Save lighting</button><button id="prepAddCue">Enhance section at playhead</button></div><div id="prepCues"></div>
 <details><summary>Recognition differences</summary><p id="prepRecognitionDifferences" class="prep-note"></p></details>`;
 root.querySelector('#prepHistoryHeading, #prepHistory').before(panel);
 const q=id=>panel.querySelector('#'+id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 let gid='',artifact='',data=null,word=null,dirty=false,lightDirty=false,frame=null,signature='',sequence=0,lastWordTick=-1,activeWords=new Set(),previewEvents=[];
 let viewStart=0,follow=true,dragging=false,auditionEnd=null,undo=[],redo=[],revision=0,saving=false;
 const handle=fn=>async()=>{try{await fn()}catch(e){toast(e.message)}};
 const clock=t=>`${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;
 const precise=t=>{const hundredths=Math.max(0,Math.round(t*100));return `${clock(Math.floor(hundredths/100))}.${String(hundredths%100).padStart(2,'0')}`;};
 const clamp=(t,min=0,max=data?.duration||0)=>Math.max(min,Math.min(max,t));
 const windowSize=()=>Math.min(Number(q('prepTimelineWindow').value),data?.duration||10);
 const low=w=>!w.confirmed&&(w.estimated||w.edited||w.confidence==null||w.confidence<.7);
 const snapshot=()=>data.words.map(w=>({...w}));
 function approval(){q('prepApproveLyrics').textContent=data?.approved?'Approved':'Approve lyrics';q('prepApproveLyrics').setAttribute('aria-pressed',String(!!data?.approved));}
 function changed(){dirty=true;revision++;data.approved=false;approval();q('prepTimelineStatus').textContent='Unsaved lyric changes';}
 function commit(before){undo.push(before);if(undo.length>40)undo.shift();redo=[];changed();paint();}
 function history(backwards){const from=backwards?undo:redo,to=backwards?redo:undo;if(!from.length)return;const id=word?.id;to.push(snapshot());data.words=from.pop();changed();paint();if(id)select(id);}
 function select(id,reveal=false){
  word=data.words.find(w=>w.id===id);if(!word)return;
  q('prepWordEditor').hidden=false;q('prepWordText').value=word.text;q('prepWordStart').value=word.start??'';q('prepWordEnd').value=word.end??'';
  q('prepWordConfirmed').checked=word.confirmed;q('prepWordOmitted').checked=word.omitted;
  q('prepWordTiming').textContent=word.start==null?'Untimed':`${precise(word.start)} – ${precise(word.end)}${word.estimated?' · estimated':''}`;
  const index=data.words.indexOf(word);q('prepWordPrevious').disabled=index===0;q('prepWordNext').disabled=index===data.words.length-1;
  for(const element of panel.querySelectorAll('[data-word]'))element.classList.toggle('selected',element.dataset.word===id);
  if(reveal&&word.start!=null){viewStart=clamp(word.start-windowSize()*.2,0,Math.max(0,data.duration-windowSize()));seek(word.start);timeline();}
 }
 function paint(){
  if(!data)return;activeWords=new Set();lastWordTick=-1;
  const lines=new Map();for(const w of data.words){if(!lines.has(w.line))lines.set(w.line,[]);lines.get(w.line).push(w);}
  q('prepLyricWords').innerHTML=[...lines.values()].map(words=>`<div class="prep-lyric-line">${words.map(w=>`<button type="button" data-word="${esc(w.id)}" class="prep-word ${w.omitted?'omitted':w.start==null?'untimed':low(w)?'uncertain':'checked'}" title="${esc(w.section)} · ${w.estimated?'Estimated position':w.confidence==null?'No alignment confidence':Math.round(w.confidence*100)+'% alignment score'}">${esc(w.text)}</button>`).join('')}</div>`).join('');
  for(const button of q('prepLyricWords').querySelectorAll('[data-word]'))button.onclick=()=>select(button.dataset.word,true);
  q('prepUndoLyrics').disabled=!undo.length;q('prepRedoLyrics').disabled=!redo.length;
  timeline();if(word)select(word.id);
 }
 function positionTag(tag,w){
  const width=windowSize();tag.style.left=Math.max(0,(w.start-viewStart)/width*100)+'%';
  tag.style.width=Math.max(.5,(Math.min(viewStart+width,w.end)-Math.max(viewStart,w.start))/width*100)+'%';
 }
 function retime(mode,delta){if(!word||word.start==null)return;const before=snapshot();Object.assign(word,shiftWordTiming(word,mode,delta,data.duration),{estimated:false,edited:true});commit(before);select(word.id);}
 function wireTag(tag,w){
  tag.onclick=()=>select(w.id);
  tag.onkeydown=event=>{
   if(event.key==='Enter'){event.preventDefault();select(w.id);return;}
   if(event.code==='Space'){event.preventDefault();event.stopPropagation();togglePlayback();return;}
   if(!['ArrowLeft','ArrowRight'].includes(event.key))return;
   event.preventDefault();event.stopPropagation();select(w.id);
   const edge=event.target.dataset.edge;
   retime(edge||'move',(event.key==='ArrowLeft'?-1:1)*(event.shiftKey?.25:.05));
   const updated=q('prepWordLayer').querySelector(`[data-word="${CSS.escape(w.id)}"]`);
   (edge?updated?.querySelector(`[data-edge="${edge}"]`):updated)?.focus({preventScroll:true});
  };
  tag.onpointerdown=event=>{
   if(event.button!==0||dragging)return;event.preventDefault();select(w.id);tag.focus({preventScroll:true});
   const before=snapshot(),original={...w},x=event.clientX,width=windowSize(),rect=q('prepWordTimeline').getBoundingClientRect(),mode=event.target.dataset.edge||'move';
   let moved=false;dragging=true;tag.setPointerCapture(event.pointerId);
   tag.onpointermove=move=>{
    if(Math.abs(move.clientX-x)<2&&!moved)return;moved=true;
    Object.assign(w,shiftWordTiming(original,mode,(move.clientX-x)/rect.width*width,data.duration));positionTag(tag,w);
    q('prepWordStart').value=w.start.toFixed(3);q('prepWordEnd').value=w.end.toFixed(3);q('prepWordTiming').textContent=`${precise(w.start)} – ${precise(w.end)}`;
   };
   const finish=cancel=>{
    tag.onpointermove=tag.onpointerup=tag.onpointercancel=tag.onlostpointercapture=null;dragging=false;
    if(tag.hasPointerCapture(event.pointerId))tag.releasePointerCapture(event.pointerId);
    if(cancel){Object.assign(w,original);timeline();select(w.id);return;}
    if(moved&&(w.start!==original.start||w.end!==original.end)){w.estimated=false;w.edited=true;commit(before);select(w.id);}else select(w.id);
   };
   tag.onpointerup=()=>finish(false);tag.onpointercancel=()=>finish(true);tag.onlostpointercapture=()=>finish(true);
  };
 }
 function timeline(){
  if(!data||dragging)return;const width=windowSize(),pixels=q('prepWordTimeline').clientWidth||1,lanes=[];
  viewStart=clamp(viewStart,0,Math.max(0,data.duration-width));q('prepWordLayer').replaceChildren();
  q('prepWindowLabel').textContent=`${clock(viewStart)} – ${clock(Math.min(data.duration,viewStart+width))}`;
  q('prepWindowBack').disabled=viewStart<=0;q('prepWindowAhead').disabled=viewStart+width>=data.duration;
  q('prepTimelineRuler').innerHTML=Array.from({length:6},(_,i)=>`<span>${precise(viewStart+width*i/5)}</span>`).join('');
  for(const w of data.words){
   if(w.omitted||w.start==null||w.end<=viewStart||w.start>=viewStart+width)continue;
   const left=Math.max(0,(w.start-viewStart)/width*pixels),right=left+Math.max(44,(Math.min(viewStart+width,w.end)-Math.max(viewStart,w.start))/width*pixels);
   let lane=lanes.findIndex(row=>row.line===w.line&&row.right+3<=left);
   if(lane<0){lane=lanes.length;lanes.push({line:w.line,right});}else lanes[lane].right=right;
   const tag=document.createElement('div');tag.className='prep-word-tag '+(low(w)?'uncertain':'checked')+(w.estimated?' estimated':'');tag.dataset.word=w.id;
   positionTag(tag,w);tag.style.top=(lane*40+32)+'px';
   tag.innerHTML=`<span class="prep-word-label">${esc(w.text)}</span><span class="prep-word-handle start" data-edge="start" role="slider" tabindex="0" aria-label="Start of ${esc(w.text)}" aria-valuemin="0" aria-valuemax="${data.duration}" aria-valuenow="${w.start}" title="Drag word start"></span><span class="prep-word-handle end" data-edge="end" role="slider" tabindex="0" aria-label="End of ${esc(w.text)}" aria-valuemin="0" aria-valuemax="${data.duration}" aria-valuenow="${w.end}" title="Drag word end"></span>`;
   tag.tabIndex=0;tag.setAttribute('role','button');tag.setAttribute('aria-label',`${w.text}, ${w.start.toFixed(2)} to ${w.end.toFixed(2)} seconds${w.estimated?', estimated':''}. Arrow keys move timing; Shift moves faster.`);
   wireTag(tag,w);q('prepWordLayer').append(tag);
  }
  q('prepWordTimeline').style.height=Math.max(130,lanes.length*40+40)+'px';
  if(word)for(const tag of q('prepWordLayer').children)tag.classList.toggle('selected',tag.dataset.word===word.id);
  lastWordTick=-1;
 }
 function reaction(){previewEvents=lightEvents(data?.features,Number(q('prepSensitivity').value),data?.sections||[]);}
 function cues(){q('prepCues').innerHTML=data.sections.map((s,i)=>`<div class="prep-cue" data-cue="${i}"><label>Section<input data-field="label" aria-label="Section label" value="${esc(s.label)}"></label><label>Start / s<input data-field="start" aria-label="Section start" type="number" min="0" step=".1" value="${s.start}"></label><label>End / s<input data-field="end" aria-label="Section end" type="number" min="0" step=".1" value="${s.end}"></label><label>Boost<input data-field="boost" aria-label="Light boost" type="number" min="0" max="3" step=".1" value="${s.boost}"></label><button data-remove="${i}">Remove</button></div>`).join('');for(const row of q('prepCues').children){for(const input of row.querySelectorAll('input'))input.onchange=()=>{data.sections[Number(row.dataset.cue)][input.dataset.field]=input.dataset.field==='label'?input.value:Number(input.value);lightDirty=true;reaction();};row.querySelector('button').onclick=()=>{data.sections.splice(Number(row.dataset.cue),1);lightDirty=true;cues();reaction();};}}
 const togglePlayback=handle(async()=>{auditionEnd=null;if(isPlaying())pause();else await play();});
 function jump(time){auditionEnd=null;time=clamp(time);seek(time);if(time<viewStart||time>viewStart+windowSize()){viewStart=clamp(time-windowSize()*.2,0,Math.max(0,data.duration-windowSize()));timeline();}}
 function tick(){
  if(root.hidden){frame=null;return;}const t=getTime(),playing=isPlaying(),width=windowSize();
  if(auditionEnd!=null&&t>=auditionEnd){pause();auditionEnd=null;}
  q('prepClock').textContent=`${precise(t)} / ${clock(data?.duration||0)}`;q('prepTimelineScrub').value=t;
  q('prepTimelinePlay').textContent=playing?'Pause':'Play';q('prepTimelinePlay').setAttribute('aria-label',playing?'Pause working audio':'Play working audio');
  if(data&&follow&&playing&&!dragging&&(t<viewStart||t>viewStart+width*.92)){viewStart=clamp(t-width*.2,0,Math.max(0,data.duration-width));timeline();}
  q('prepPlayhead').style.left=Math.max(0,Math.min(100,(t-viewStart)/width*100))+'%';
  const brightness=Math.max(.04,...previewEvents.filter(e=>e.time<=t&&t<e.time+e.duration).map(e=>e.strength*(1-(t-e.time)/e.duration)));
  q('prepStar').style.opacity=brightness;q('prepStar').style.filter=`drop-shadow(0 0 ${brightness*24}px #ffc562)`;
  if(data&&Math.floor(t*20)!==lastWordTick){
   lastWordTick=Math.floor(t*20);const next=new Set(data.words.filter(w=>!w.omitted&&w.start!=null&&t>=w.start&&t<w.end).map(w=>w.id));
   for(const element of panel.querySelectorAll('[data-word]'))element.classList.toggle('singing',next.has(element.dataset.word));activeWords=next;
  }
  frame=requestAnimationFrame(tick);
 }
 async function load(force=false){
  if(!gid)return;if(!force&&(dirty||lightDirty||saving||dragging)){q('prepTimelineStatus').textContent='New processing available; save or reload your draft.';return;}
  const ticket=++sequence,value=await api(`/preparation/projects/${encodeURIComponent(gid)}/editorial?`+new URLSearchParams(artifact?{artifact}:{}));
  if(ticket!==sequence||!force&&(dirty||lightDirty||saving||dragging))return;
  data=value;word=null;dirty=lightDirty=false;undo=[];redo=[];revision++;auditionEnd=null;
  q('prepWordEditor').hidden=true;q('prepRecognitionDifferences').textContent=(data.unmatched_recognition||[]).map(w=>w.word).join(' ')||'No additional recognition tokens recorded.';
  q('prepLanguage').value=data.language;q('prepSensitivity').value=data.sensitivity;q('prepSensitivityValue').value=Number(data.sensitivity).toFixed(2);q('prepTimelineScrub').max=data.duration;
  const estimated=data.words.filter(w=>w.estimated&&!w.omitted).length;
  q('prepTimelineStatus').textContent=(data.features?'Visual features ready. ':'Calculate visual features to preview star reactions. ')+(estimated?`${estimated} words roughly positioned in lyric order. `:'')+(data.approved?'Lyrics approved.':'You can approve without checking every word.');
  approval();paint();cues();reaction();if(!frame)tick();
 }
 const payload=()=>({source_sha256:data.source_sha256,input_artifact_id:artifact||null});
 async function saveLyrics(approved=data?.approved||false){
  if(!data||saving||dragging)return;saving=true;const savedRevision=revision;
  q('prepSaveLyrics').disabled=q('prepApproveLyrics').disabled=true;
  try{
   const result=await api(`/preparation/projects/${gid}/editorial/lyric_edit`,{...payload(),parent_task_id:data.lyric_task_id,words:data.words,approved});
   data.lyric_task_id=result.lyric_task_id;
   if(revision===savedRevision){dirty=false;data.approved=result.approved;data.words=result.words;paint();approval();q('prepTimelineStatus').textContent='Lyric revision saved'+(result.approved?' · Approved for publishing':'');}
   else q('prepTimelineStatus').textContent='Revision saved; newer changes are still unsaved';
  }finally{saving=false;q('prepSaveLyrics').disabled=q('prepApproveLyrics').disabled=false;}
 }
 q('prepTimelinePlay').onclick=togglePlayback;q('prepTimelineBack').onclick=()=>jump(getTime()-2);q('prepTimelineAhead').onclick=()=>jump(getTime()+2);
 q('prepTimelineSpeed').onchange=()=>setRate(Number(q('prepTimelineSpeed').value));q('prepTimelineScrub').oninput=()=>jump(Number(q('prepTimelineScrub').value));
 q('prepTimelineWindow').onchange=()=>{viewStart=clamp(getTime()-windowSize()*.2,0,Math.max(0,data.duration-windowSize()));timeline();};
 for(const [id,direction] of [['prepWindowBack',-1],['prepWindowAhead',1]])q(id).onclick=()=>{follow=false;q('prepFollow').setAttribute('aria-pressed','false');viewStart+=direction*windowSize()*.8;timeline();};
 q('prepFollow').onclick=()=>{follow=!follow;q('prepFollow').setAttribute('aria-pressed',String(follow));if(follow){viewStart=getTime()-windowSize()*.2;timeline();}};
 q('prepWordTimeline').onclick=event=>{if(event.target.closest('[data-word]'))return;const rect=q('prepWordTimeline').getBoundingClientRect();jump(viewStart+(event.clientX-rect.left)/rect.width*windowSize());};
 q('prepWordTimeline').onkeydown=event=>{if(event.target===q('prepWordTimeline')&&event.code==='Space'){event.preventDefault();togglePlayback();}};
 q('prepReloadTimeline').onclick=handle(()=>load(true));q('prepSaveLyrics').onclick=handle(()=>saveLyrics());q('prepApproveLyrics').onclick=handle(()=>saveLyrics(true));
 q('prepUndoLyrics').onclick=()=>history(true);q('prepRedoLyrics').onclick=()=>history(false);
 panel.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'&&!event.target.closest('input,textarea,select')){event.preventDefault();history(!event.shiftKey);}});
 for(const [id,direction] of [['prepWordPrevious',-1],['prepWordNext',1]])q(id).onclick=()=>{const next=data.words[data.words.indexOf(word)+direction];if(next)select(next.id,true);};
 q('prepWordListen').onclick=handle(async()=>{if(word?.start==null)return;seek(Math.max(0,word.start-.25));auditionEnd=Math.min(data.duration,word.end+.25);await play();});
 q('prepWordStartNow').onclick=()=>retime('start',getTime()-word.start);q('prepWordEndNow').onclick=()=>retime('end',getTime()-word.end);
 function editSelected(values){if(!word)return;const before=snapshot();Object.assign(word,values,{edited:true});commit(before);}
 q('prepWordText').onchange=handle(()=>{const text=q('prepWordText').value;if(!text.trim()||text.length>200)throw Error('Enter a short lyric word');editSelected({text});});
 q('prepWordConfirmed').onchange=()=>editSelected({confirmed:q('prepWordConfirmed').checked});q('prepWordOmitted').onchange=()=>editSelected({omitted:q('prepWordOmitted').checked});
 q('prepWordApply').onclick=handle(()=>{if(!word)return;const start=q('prepWordStart').value,end=q('prepWordEnd').value,a=start===''?null:Number(start),b=end===''?null:Number(end);if((a==null)!=(b==null)||a!=null&&(!Number.isFinite(a)||!Number.isFinite(b)||a<0||b<=a||b>data.duration))throw Error('Set both start and end within the recording');editSelected({start:a,end:b,estimated:false});});
 q('prepSensitivity').oninput=()=>{q('prepSensitivityValue').value=Number(q('prepSensitivity').value).toFixed(2);lightDirty=true;reaction();};
 q('prepAddCue').onclick=()=>{if(!data)return;const start=Math.max(0,Math.min(data.duration-.1,getTime()));data.sections.push({start,end:Math.min(data.duration,start+5),boost:1,label:''});lightDirty=true;cues();reaction();};
 q('prepSaveLights').onclick=handle(async()=>{if(!data)return;const result=await api(`/preparation/projects/${gid}/editorial/visual_edit`,{...payload(),parent_task_id:data.lighting_task_id,sensitivity:Number(q('prepSensitivity').value),sections:data.sections});data.lighting_task_id=result.lighting_task_id;lightDirty=false;q('prepTimelineStatus').textContent='Lighting saved';});
 q('prepAlign').onclick=handle(async()=>{if(!data)return;await api(`/preparation/projects/${gid}/jobs`,{kind:'lyric_alignment',language:q('prepLanguage').value.trim(),input_artifact_id:artifact||null});q('prepTimelineStatus').textContent='Separation/alignment queued; output will appear when complete';});
 let lastWidth=0;new ResizeObserver(entries=>{const width=entries[0].contentRect.width;if(width!==lastWidth){lastWidth=width;timeline();}}).observe(q('prepWordTimeline'));
 return {canSwitch:()=>!dirty&&!lightDirty&&!saving&&!dragging,artifact:()=>artifact,update:async(p)=>{
  if(!p?.ready)return;const changedSong=gid!==p.generation_id,nextSig=p.tasks.map(t=>t.id+t.status).join('|');
  if(changedSong){gid=p.generation_id;artifact='';signature='';dirty=lightDirty=false;viewStart=0;auditionEnd=null;}
  const source=q('prepAudioSource'),old=artifact;source.replaceChildren(new Option('Reviewed source',''));
  for(const a of p.masters||p.tasks.flatMap(t=>t.artifacts.filter(a=>a.available&&a.kind==='master_candidate').map(a=>({...a,created_at:t.created_at}))))source.append(new Option(`Master · ${a.created_at}`,a.id));source.value=old;
  if(signature!==nextSig||changedSong){signature=nextSig;await load();}if(!frame&&data)tick();
 },onSourceChange(fn){q('prepAudioSource').onchange=handle(async()=>{if(dirty||lightDirty||saving||dragging){q('prepAudioSource').value=artifact;throw Error('Save or reload the timeline draft before changing audio');}artifact=q('prepAudioSource').value;viewStart=0;auditionEnd=null;pause();await fn(artifact);await load(true);});},close:()=>{if(frame)cancelAnimationFrame(frame);frame=null;auditionEnd=null;}};
}
