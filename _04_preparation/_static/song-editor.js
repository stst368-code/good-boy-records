import {uid,clone,size,spans,duration,sounds,words,warp,split,insertGap,gesture,duplicate,remove,move} from './song-model.js';
import {createSongPlayer} from './song-player.js';
import {shiftWordTiming} from './timeline.js';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=seconds=>`${Math.floor(Math.max(0,seconds)/60)}:${(Math.max(0,seconds)%60).toFixed(2).padStart(5,'0')}`;
const clamp=(value,low,high)=>Math.max(low,Math.min(high,value));

export function initSongEditor({root,api,toast,getMix,setMix,getArtwork,setArtwork,getStems}){
 const host=globalThis.document.createElement('section');host.className='song-editor prep-card';host.setAttribute('aria-label','Song timeline editor');
 host.innerHTML=`
 <header class="song-heading"><div><span class="song-eyebrow">SONG STUDIO</span><h2 id="songTitle">Your song, one timeline</h2></div><div class="song-save"><span id="songSaveState" role="status">Loading…</span><button id="songUndo" class="secondary" title="Undo · Ctrl Z">↶</button><button id="songRedo" class="secondary" title="Redo · Ctrl Shift Z">↷</button><button id="songSave">Save version</button></div></header>
 <div class="song-transport"><button id="songPlay" class="song-play" aria-label="Play song">▶ Play</button><button id="songBack" class="secondary" aria-label="Back two seconds">−2s</button><output id="songClock">0:00 / 0:00</output><label>Listen to<select id="songListen"><option value="draft">Arrangement · edits + clips</option><option value="processed" disabled>Processed preview</option></select></label><label class="prep-checkbox"><input id="songLoop" type="checkbox">Loop range</label><label>Speed<select id="songSpeed"><option value=".5">0.5×</option><option value=".75">0.75×</option><option value="1" selected>1×</option></select></label><div class="song-zoom"><button id="songZoomOut" class="secondary" aria-label="Zoom out">−</button><output id="songZoomLabel">Fit</output><button id="songZoomIn" class="secondary" aria-label="Zoom in">+</button><button id="songFit" class="secondary">Fit</button></div></div>
 <div class="song-tools" role="toolbar" aria-label="Song editing tools"><button id="songSplit" class="secondary">Split at playhead</button><button id="songBreak" class="secondary">＋ Silence</button><button id="songNeedle" class="secondary">Needle break</button><label>Selected range<select id="songGesture"><option value="">Record gesture…</option><option value="scratch">Vinyl scratch</option><option value="stop">Record stop</option><option value="start">Record start</option><option value="stutter">Stutter / repeat</option><option value="reverse">Reverse</option><option value="mute">Mute music</option><option value="normal">Normal playback</option></select></label><button id="songAddLight" class="secondary">＋ Light cue</button></div>
 <div class="song-canvas-wrap"><canvas id="songTimeline" height="390" tabindex="0" role="application" aria-label="Song timeline. Drag empty space to select a range. Drag music sections to reorder, sound clips to move, and lyric words or their edges to adjust timing."></canvas></div>
 <div class="song-view"><input id="songScroll" type="range" min="0" max="0" step=".01" value="0" aria-label="Timeline view start"><label>Range<input id="songRangeStart" type="number" step=".01" min="0" value="0" aria-label="Range start in seconds"></label><span>→</span><input id="songRangeEnd" type="number" step=".01" min="0" value="0" aria-label="Range end in seconds"><button id="songSelection" class="secondary">Play range</button></div>
 <p id="songHint" class="song-hint">Drag the ruler to select a range. Music sections carry their lyrics and attached clips when moved.</p>
 <div class="song-bottom"><section class="song-library" aria-label="Reusable sound library"><div class="song-library-heading"><h3>Sound library <span id="songSoundCount"></span></h3><label class="song-import">＋ Import audio<input id="songImport" type="file" accept="audio/*,.wav,.flac,.mp3,.ogg,.m4a,.aiff" multiple></label></div><input id="songSoundSearch" type="search" placeholder="Search your sounds…" aria-label="Search sound library"><p class="song-hint" id="songImportStatus" role="status">Drop clips onto the timeline, or add at the playhead. Imports stay in your library.</p><div id="songSoundList"></div></section><section class="song-inspector" aria-label="Selected timeline object"><h3 id="songInspectorTitle">Selection</h3><select id="songObjectPick" aria-label="Select a timeline object"></select><div id="songInspector"></div></section></div>
 <details class="song-lyrics"><summary>Lyrics · <span id="songLyricState">not approved</span></summary><div class="song-lyric-toolbar"><button id="songAlign" class="secondary">Run lyric alignment</button><button id="songLoadAlignment" class="secondary">Load latest alignment</button><button id="songApprove">Approve lyrics</button><label>Language<input id="songLanguage" maxlength="3" value="en"></label><label>Light sensitivity<input id="songSensitivity" type="number" min=".25" max="3" step=".05" value="1"></label><output id="songAlignmentStatus" role="status"></output></div><div id="songLyricText"></div><p class="song-hint">Word corrections edit the source; every occurrence follows your cuts and repeats.</p></details>
 <footer class="song-render"><div><strong>Ready to hear the full mix?</strong><p class="song-hint">Draft playback auditions edits and clips. Render to hear mastering effects and automation.</p></div><button id="songPreview">Render range preview</button><button id="songMaster" class="primary">Render master</button><select id="songMasterChoice" aria-label="Release master"><option value="">Choose release master…</option></select><output id="songRenderState" role="status"></output></footer>
 <details class="song-versions"><summary>Saved versions</summary><div class="prep-actions"><select id="songVersions" aria-label="Saved song version"></select><button id="songRestore" class="secondary">Restore as draft</button><button id="songReload" class="secondary">Reload latest saved</button></div></details>`;
 root.querySelector('#prepWorkspace').prepend(host);
 const q=id=>root.querySelector('#'+id),canvas=q('songTimeline'),ctx=canvas.getContext('2d');
 let gid='',data=null,document=null,row=null,wave=null,library=[],selected=null,revision=null,dirty=false;
 let viewStart=0,viewSpan=1,range={start:0,end:0},undo=[],redo=[],hits=[],drag=null,ticket=0,frame=null,lastDraw=0;
 let sourceChanged=async()=>{},releaseMaster='',renderPending='',renderBusy=false;
 let restoreMix=false,knownVersions='',alignmentId='',lyricProjection=[],lastPreview='',masterSignature='';
 let alignmentLoading=false,alignmentSequence=0,alignmentFailure=null,requestedAlignment=null;
 let processingItems=new Map();
 const sessionDrafts=new Map();
 let loadingProject=false,localDraftStored=true;
 const endpoint=(suffix='')=>'/preparation/projects/'+encodeURIComponent(gid)+suffix;
 const sourceUrl=()=>'/api/preparation/audio/'+encodeURIComponent(gid)+(data?.source_artifact_id?'?'+new URLSearchParams({artifact:data.source_artifact_id}):'');
 const player=createSongPlayer({getProject:()=>document,sourceUrl,toast,onTick:()=>clock()});
 const audition=new Audio();audition.preload='none';
 function resetPlayback(close=false){
  if(close)player.close();else player.reset();
  audition.pause();lastPreview='';q('songListen').value='draft';q('songListen').options[1].disabled=true;
  q('songLoop').checked=false;q('songSpeed').value='1';
 }
 const guard=fn=>async(...args)=>{try{await fn(...args);}catch(error){toast(error.message);q('songSaveState').textContent=error.message;}};
 const snapshot=()=>clone(document);
 function cache(){
  if(!gid||!document||!data||loadingProject)return;
  const draft={revision_id:revision,source_sha256:data.source_sha256,project:clone(document),dirty,
   undo:undo.slice(),redo:redo.slice(),viewStart,viewSpan,range:{...range},releaseMaster};
  sessionDrafts.set(gid,draft);
  try{
   if(dirty)localStorage.setItem('gbr-song-draft:'+gid,JSON.stringify({revision_id:revision,source_sha256:data.source_sha256,project:document,dirty:true}));
   else localStorage.removeItem('gbr-song-draft:'+gid);
   localDraftStored=true;
  }catch{localDraftStored=false;}
 }
 function equivalentDraft(left,right){
  // Unsaved originals get a fresh section UUID from the server. Ignore only
  // those object identities and mix schema bookkeeping when reading old drafts.
  const normalize=value=>{
   const result=clone(value),ids=new Map(result.segments.map((s,i)=>[s.id,i]));
   result.segments=result.segments.map(({id,...segment})=>segment);
   result.sounds=result.sounds.map(({id,...clip})=>({...clip,segment_id:clip.segment_id==null?null:ids.get(clip.segment_id)}));
   result.lighting.cues=result.lighting.cues.map(cue=>({...cue,segment_id:cue.segment_id==null?null:ids.get(cue.segment_id)}));
   delete result.mix.version;result.mix.effects||=[];result.stem_task_id||=null;
   const ordered=v=>Array.isArray(v)?v.map(ordered):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,ordered(v[k])])):v;
   return JSON.stringify(ordered(result));
  };
  return normalize(left)===normalize(right);
 }
 function state(){
  q('songSaveState').textContent=dirty?(localDraftStored?'Unsaved edits · local draft':'Unsaved edits · kept in this session; save a version'):revision?'Saved · '+revision.slice(0,8):'Original audio · ready to edit';
  q('songUndo').disabled=!undo.length;q('songRedo').disabled=!redo.length;q('songSave').disabled=renderBusy||(!dirty&&!!revision);
  q('songLyricState').textContent=document?.approved?'approved':'not approved';q('songApprove').textContent=document?.approved?'Lyrics approved ✓':'Approve lyrics';q('songApprove').setAttribute('aria-pressed',String(Boolean(document?.approved)));
 }
 function changed(before){
  if(JSON.stringify(before)===JSON.stringify(document))return;
  undo.push(before);if(undo.length>60)undo.shift();redo=[];dirty=true;
  const musicChanged=JSON.stringify([before.segments,before.sounds])!==JSON.stringify([document.segments,document.sounds]);
  const audioChanged=musicChanged||JSON.stringify(before.mix)!==JSON.stringify(document.mix)||before.stem_task_id!==document.stem_task_id;
  if(audioChanged){player.invalidate(musicChanged);q('songListen').value='draft';q('songListen').options[1].disabled=true;lastPreview='';}
  lyricProjection=words(document);viewSpan=Math.min(Math.max(.25,viewSpan),duration(document));viewStart=clamp(viewStart,0,Math.max(0,duration(document)-viewSpan));
  range.start=clamp(range.start,0,duration(document));range.end=clamp(range.end,range.start,duration(document));
  rangeInputs();cache();state();draw();inspect();lyricText();renders();
 }
 function mutate(fn){if(!document)return;const before=snapshot();try{fn();changed(before);}catch(error){document=before;toast(error.message);draw();inspect();}}
 function syncMix(){restoreMix=true;setMix(document.mix,document.stem_task_id);setArtwork(document.artwork_id||'');q('songLanguage').value=document.language;q('songSensitivity').value=document.lighting.sensitivity;restoreMix=false;}
 function history(backwards){
  const from=backwards?undo:redo,to=backwards?redo:undo;if(!from.length)return;to.push(snapshot());document=from.pop();dirty=true;player.invalidate();q('songListen').value='draft';q('songListen').options[1].disabled=true;lastPreview='';syncMix();lyricProjection=words(document);cache();rangeInputs();state();draw();inspect();lyricText();renders();
 }
 async function save(label='Song edit'){
  if(!document)throw Error('Choose a song first');if(renderBusy)throw Error('Wait for this version to finish saving');
  renderBusy=true;q('songSave').disabled=true;q('songSaveState').textContent='Saving song version…';
  const current=ticket,before=JSON.stringify(document);
  try{
   const saved=await api(endpoint('/song-project'),{parent_revision_id:revision,source_sha256:data.source_sha256,input_artifact_id:data.source_artifact_id||null,project:document,label});
   if(current!==ticket)return saved;data=saved;revision=saved.revision_id;
   if(JSON.stringify(document)===before){document=clone(saved.project);dirty=false;}
   if(saved.reused_master_id){releaseMaster=saved.reused_master_id;await sourceChanged(releaseMaster);}
   cache();versions(saved.versions);state();renders();return saved;
  }finally{renderBusy=false;state();renders();}
 }
 function versions(items){
  const signature=JSON.stringify(items);if(signature===knownVersions)return;knownVersions=signature;
  q('songVersions').replaceChildren(...items.map((v,i)=>new Option(`${i===0?'Latest · ':''}${new Date(v.created_at).toLocaleString()} · ${v.label||'Song edit'}`,v.id)));q('songRestore').disabled=!items.length;
 }
 function rangeInputs(){
  q('songRangeStart').value=range.start.toFixed(2);q('songRangeEnd').value=range.end.toFixed(2);q('songScroll').max=Math.max(0,duration(document)-viewSpan);q('songScroll').value=viewStart;q('songZoomLabel').value=viewSpan>=duration(document)-.01?'Fit':stamp(viewSpan);
 }
 const X=time=>90+(time-viewStart)/viewSpan*(canvas.clientWidth-100);
 const T=x=>clamp(viewStart+(x-90)/(canvas.clientWidth-100)*viewSpan,0,duration(document));
 function label(text,x,y,maxWidth,color='#dce5eb'){if(maxWidth<=0)return;ctx.fillStyle=color;ctx.fillText(text,x,y,maxWidth);}
 function box(x,y,w,h,fill,stroke,object){
  if(x+w<90||x>canvas.clientWidth-10)return;const left=Math.max(90,x),right=Math.min(canvas.clientWidth-10,x+w);
  ctx.fillStyle=fill;ctx.fillRect(left,y,Math.max(1,right-left),h);ctx.strokeStyle=stroke;ctx.strokeRect(left+.5,y+.5,Math.max(1,right-left)-1,h-1);
  if(object)hits.push({x:left,y,w:Math.max(5,right-left),h,...object});
 }
 function draw(){
  if(!document||!canvas.clientWidth)return;
  const dpr=Math.min(2,window.devicePixelRatio||1),w=canvas.clientWidth,h=390;
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==h*dpr){canvas.width=Math.round(w*dpr);canvas.height=h*dpr;}
  ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#0a1118';ctx.fillRect(0,0,w,h);hits=[];ctx.font='11px ui-monospace,monospace';
  for(const [text,y,height] of [['MUSIC',32,116],['SOUNDS',153,61],['LYRICS',220,68],['EFFECTS',294,42],['LIGHTS',342,38]]){
   ctx.fillStyle='#0d1822';ctx.fillRect(0,y,w,height);label(text,10,y+20,75,'#8ba3b6');
  }
  ctx.save();ctx.beginPath();ctx.rect(90,0,w-100,h);ctx.clip();
  for(let i=0;i<=6;i++){const x=90+(w-100)*i/6;ctx.strokeStyle='#22313c';ctx.beginPath();ctx.moveTo(x,25);ctx.lineTo(x,h);ctx.stroke();label(stamp(viewStart+viewSpan*i/6),x+4,18,75,'#8ba3b6');}
  for(const segment of spans(document)){
   if(segment.until<viewStart||segment.at>viewStart+viewSpan)continue;
   const left=X(segment.at),width=X(segment.until)-left,chosen=selected?.kind==='music'&&selected.id===segment.id;
   box(left,37,width,105,segment.kind==='gap'?'#1c2730':'#203844',chosen?'#f0bd67':'#395763',{kind:'music',id:segment.id});
   if(segment.kind==='audio'&&segment.mode!=='mute'&&wave){
    const pieces=warp(segment);ctx.strokeStyle='#78c4c6';ctx.beginPath();
    for(let x=Math.max(90,Math.ceil(left));x<Math.min(w-10,left+width);x++){
     const local=T(x)-segment.at,piece=pieces.find(p=>local>=p[0]&&local<=p[1]);if(!piece)continue;
     const source=piece[2]+(local-piece[0])/(piece[1]-piece[0])*(piece[3]-piece[2]),index=clamp(Math.floor(source/data.source_duration*wave.peaks.length),0,wave.peaks.length-1);
     const peak=wave.peaks[index]?.[0]||[0,0];ctx.moveTo(x,93+peak[0]*30);ctx.lineTo(x,93+peak[1]*30);
    }ctx.stroke();
   }
   label(segment.kind==='gap'?(segment.label||'Silence'):(segment.label||'Music')+(segment.mode!=='normal'?' · '+segment.mode:''),Math.max(94,left+8),56,width-12,chosen?'#f4c77d':'#d3e5eb');
   if(width>65)label(stamp(size(segment)),Math.max(94,left+8),133,width-12,'#839baa');
  }
  let soundLane=0;for(const sound of sounds(document)){
   const x=X(sound.position),width=X(sound.position+sound.duration)-x,y=158+(soundLane++%2)*25;
   box(x,y,width,23,'#3c3150',selected?.id===sound.id?'#f0bd67':'#8b6dab',{kind:'sound',id:sound.id});label(sound.label||'Sound',Math.max(93,x+5),y+15,width-8,'#e3c8f4');
  }
  const lyricLanes=[-Infinity,-Infinity,-Infinity];
  for(const word of lyricProjection){
   if(word.end<viewStart||word.start>viewStart+viewSpan)continue;
   const x=X(word.start),right=X(word.end),index=lyricLanes.findIndex(end=>end<x-2),lane=index<0?0:index;lyricLanes[lane]=right;
   const y=224+lane*20,width=Math.max(3,right-x);box(x,y,width,18,word.confirmed?'#214536':'#4e4128',selected?.id===word.id?'#f0bd67':'#6b6653',{kind:'word',id:word.id});if(width>18)label(word.text,Math.max(92,x+4),y+13,width-6,'#f0e5ce');
  }
  for(const [index,effect] of (document.mix.effects||[]).entries()){
   if(effect.bypassed)continue;const start=effect.range?.start||0,end=effect.range?.end||duration(document),left=X(start),right=X(end),y=299+(index%2)*16;
   box(left,y,right-left,14,'#243b3b','#467476',{kind:'effect',id:index});label(effect.preset.replaceAll('_',' '),Math.max(94,left+4),y+10,right-left-8,'#b6d7d0');
   if(effect.automation?.length){ctx.strokeStyle='#b4e4c0';ctx.beginPath();effect.automation.forEach((p,i)=>{const x=X(p.time),py=y+13-p.value/100*12;i?ctx.lineTo(x,py):ctx.moveTo(x,py);});ctx.stroke();}
  }
  const offsets=new Map(spans(document).map(s=>[s.id,s.at]));
  document.lighting.cues.forEach((cue,index)=>{const at=cue.segment_id?(offsets.get(cue.segment_id)||0)+cue.offset:cue.at;box(X(at),349,X(at+cue.duration)-X(at),23,'#3c3924','#9e9255',{kind:'light',id:index});label('✦ '+(cue.label||'Cue'),X(at)+4,365,X(at+cue.duration)-X(at)-8,'#f2d88b');});
  if(range.end>range.start){ctx.fillStyle='#e5b56513';ctx.fillRect(X(range.start),25,X(range.end)-X(range.start),h-25);ctx.strokeStyle='#c79e5b';ctx.strokeRect(X(range.start),25,X(range.end)-X(range.start),h-25);}
  const playhead=X(player.time());ctx.strokeStyle='#f9ebd1';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(playhead,24);ctx.lineTo(playhead,h);ctx.stroke();ctx.lineWidth=1;ctx.restore();
 }
 function clock(){q('songClock').value=stamp(player.time())+' / '+stamp(duration(document));q('songPlay').textContent=player.active()?'Ⅱ Pause':'▶ Play';q('songPlay').setAttribute('aria-label',player.active()?'Pause song':'Play song');}
 function tick(time){if(root.hidden){frame=null;player.pause();return;}if(time-lastDraw>45){lastDraw=time;clock();draw();}frame=requestAnimationFrame(tick);}
 function refreshPlaybackLoop(){player.setLoop(q('songLoop').checked&&range.end>range.start?range:null);}
 const input=(label,key,value,min,max,step='.01')=>`<label>${esc(label)}<input data-key="${key}" type="number" step="${step}" min="${min}" max="${max}" value="${value}"></label>`;
 function wordOccurrence(id){
  const visible=lyricProjection.find(w=>w.id===id);if(visible)return visible;
  const source=id?.startsWith('source_')?document.words.find(w=>w.id===id.slice(7)):null;
  return source?(lyricProjection.find(w=>w.source_id===source.id)||{...source,source_id:source.id,outside:true}):null;
 }
 function inspect(){
  if(!document)return;const panel=q('songInspector');panel.parentElement.dataset.empty=String(!selected);q('songInspectorTitle').textContent='Selection';
  const options=[...spans(document).map(s=>({value:'music|'+s.id,label:stamp(s.at)+' · '+(s.label||(s.kind==='gap'?'Silence':'Music'))})),
   ...sounds(document).map(s=>({value:'sound|'+s.id,label:stamp(s.position)+' · '+s.label})),
   ...(document.mix.effects||[]).map((s,i)=>({value:'effect|'+i,label:'Effect · '+s.preset.replaceAll('_',' ')})),
   ...document.lighting.cues.map((s,i)=>({value:'light|'+i,label:'Light · '+(s.label||'Cue')}))];
  if(selected?.kind==='word')options.push({value:'word|'+selected.id,label:'Selected lyric word'});
  q('songObjectPick').innerHTML='<option value="">Select an object…</option>'+options.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join('');
  q('songObjectPick').value=selected?selected.kind+'|'+selected.id:'';
  if(!selected){panel.innerHTML='<p class="song-hint">Select a music section, sound clip, lyric word or light cue to edit it here.</p><p><strong>Needle break</strong> lifts the record, pauses the song and resumes with a needle drop.</p>'+input('New break length / seconds','break',2,.1,120);return;}
  if(selected.kind==='music'){
   const object=document.segments.find(s=>s.id===selected.id);if(!object){selected=null;return inspect();}
   q('songInspectorTitle').textContent=object.kind==='gap'?'Silence / break':'Music section';
   panel.innerHTML=`<label>Name<input data-key="label" value="${esc(object.label)}"></label><div class="song-fields">${object.kind==='gap'?input('Length / s','duration',object.duration,.01,120):input('Source in / s','start',object.start,0,data.source_duration)+input('Source out / s','end',object.end,0,data.source_duration)+input('Gain / dB','gain_db',object.gain_db||0,-60,12,.5)}</div>${object.kind==='audio'?`<label>Playback<select data-key="mode">${['normal','mute','scratch','stop','start','stutter','reverse'].map(mode=>`<option ${object.mode===mode?'selected':''}>${mode}</option>`).join('')}</select></label><button data-object="attachEffects" class="secondary">Attach effect rack to this section</button><p class="song-hint">${object.effects?.length||0} attached effects follow this section when moved or repeated.</p>`:''}<div class="prep-actions"><button data-object="left" class="secondary">← Move</button><button data-object="right" class="secondary">Move →</button><button data-object="duplicate" class="secondary">Repeat</button><button data-object="remove" class="secondary">Delete</button></div>`;
  }else if(selected.kind==='sound'){
   const object=document.sounds.find(s=>s.id===selected.id);if(!object){selected=null;return inspect();}
   const absolute=sounds(document).find(s=>s.id===object.id).position,clip=library.find(c=>c.id===object.clip_id);
   q('songInspectorTitle').textContent=clip?.name||object.label||'Sound clip';
   panel.innerHTML=`<label>Name<input data-key="label" value="${esc(object.label)}"></label><div class="song-fields">${input('Timeline start / s','position',absolute,0,duration(document))+input('Clip trim in / s','trim',object.trim,0,clip?.duration||120)+input('Length / s','duration',object.duration,.01,clip?.duration||120)+input('Volume / dB','gain_db',object.gain_db,-60,12,.5)+input('Fade in / s','fade_in',object.fade_in,0,object.duration)+input('Fade out / s','fade_out',object.fade_out,0,object.duration)+input('Duck music / dB','duck_db',object.duck_db,-36,0,.5)+input('Pan · L −1 / R +1','pan',object.pan,-1,1,.1)}</div><label class="prep-checkbox"><input type="checkbox" data-key="anchor" ${object.segment_id?'checked':''}>Follow the music section underneath</label><div class="prep-actions"><button data-object="duplicateSound" class="secondary">Duplicate</button><button data-object="remove" class="secondary">Remove</button></div>`;
  }else if(selected.kind==='word'){
   const occurrence=wordOccurrence(selected.id),word=document.words.find(w=>w.id===occurrence?.source_id);if(!word){selected=null;return inspect();}
   q('songInspectorTitle').textContent='Lyric · '+word.text;
   panel.innerHTML=`<label>Word<input data-key="text" value="${esc(word.text)}"></label><p class="song-hint">${occurrence.outside?'Source word · omitted, untimed or outside the arrangement.':'Timeline '+stamp(occurrence.start)+' → '+stamp(occurrence.end)+'.'} Source timing applies to every occurrence.</p><div class="song-fields">${input('Source start / s','start',word.start??0,0,data.source_duration)+input('Source end / s','end',word.end??.1,0,data.source_duration)}</div><label class="prep-checkbox"><input type="checkbox" data-key="confirmed" ${word.confirmed?'checked':''}>Checked by me</label><label class="prep-checkbox"><input type="checkbox" data-key="omitted" ${word.omitted?'checked':''}>Not sung</label><button data-object="listenWord" class="secondary" ${occurrence.outside?'disabled':''}>Play this word</button>`;
  }else if(selected.kind==='light'){
   const cue=document.lighting.cues[selected.id];if(!cue){selected=null;return inspect();}q('songInspectorTitle').textContent='Light cue';
   const at=cue.segment_id?(spans(document).find(s=>s.id===cue.segment_id)?.at||0)+cue.offset:cue.at;
   panel.innerHTML=`<label>Name<input data-key="label" value="${esc(cue.label)}"></label><div class="song-fields">${input('Timeline start / s','at',at,0,duration(document))+input('Length / s','duration',cue.duration,.01,120)+input('Boost','boost',cue.boost,0,3,.1)}</div><button data-object="removeLight" class="secondary">Remove cue</button>`;
  }else{
   const effect=document.mix.effects?.[selected.id];q('songInspectorTitle').textContent='Effect · '+effect?.preset.replaceAll('_',' ');
   panel.innerHTML='<p>Adjust strength, range and automation in the effect rack below. Effect times share this song timeline.</p>'+input('Point strength / %','pointStrength',100,0,100,1)+'<div class="prep-actions"><button data-object="addPoint" class="secondary">Add point at playhead</button><button data-object="revealEffect" class="secondary">Open effect controls ↓</button></div>';
  }
  if(selected?.kind==='music'){
   const section=document.segments.find(s=>s.id===selected.id);
   if(section.effects?.length)panel.insertAdjacentHTML('beforeend','<div class="song-fields">'+section.effects.map((e,i)=>input(e.preset.replaceAll('_',' ')+' / %','attached_'+i,e.strength,0,100,1)).join('')+'</div><button data-object="clearEffects" class="secondary">Remove attached effects</button>');
  }
 }
 q('songObjectPick').onchange=()=>{const [kind,id]=q('songObjectPick').value.split('|');selected=kind?{kind,id:['effect','light'].includes(kind)?Number(id):id}:null;inspect();draw();};
 function anchorAt(object,at){
  const section=spans(document).find(s=>at>=s.at&&at<s.until)||spans(document).at(-1);
  object.segment_id=section?.id||null;object.offset=at-(section?.at||0);object.at=at;
 }
 function addSound(clip,at=player.time(),anchor=true){
  if(document.sounds.length>=128)throw Error('Use up to 128 clips in a song');
  const object={id:uid(),clip_id:clip.id,segment_id:null,at:clamp(at,0,duration(document)),offset:0,trim:0,duration:clip.duration,gain_db:-6,fade_in:Math.min(.02,clip.duration/4),fade_out:Math.min(.02,clip.duration/4),duck_db:0,pan:0,label:clip.name};
  if(anchor)anchorAt(object,object.at);document.sounds.push(object);selected={kind:'sound',id:object.id};
 }
 q('songInspector').onchange=event=>{
  const field=event.target,key=field.dataset.key;if(!key||!selected||key==='break')return;
  mutate(()=>{
   if(key==='pointStrength')return;
   let object;if(selected.kind==='music')object=document.segments.find(s=>s.id===selected.id);
   if(selected.kind==='sound')object=document.sounds.find(s=>s.id===selected.id);
   if(selected.kind==='word')object=document.words.find(s=>s.id===wordOccurrence(selected.id)?.source_id);
   if(selected.kind==='light')object=document.lighting.cues[selected.id];if(!object)return;
   const value=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value;
   if(field.type==='number'&&(!Number.isFinite(value)||value<Number(field.min)||value>Number(field.max)))throw Error('Enter a value inside the displayed range');
   if(key.startsWith('attached_')){object.effects[Number(key.slice(9))].strength=value;return;}
   if(key==='position'){object.segment_id?anchorAt(object,value):object.at=value;}
   else if(key==='anchor'){const at=sounds(document).find(s=>s.id===object.id).position;if(value)anchorAt(object,at);else{object.at=at;object.segment_id=null;object.offset=0;}}
   else{
    if(selected.kind==='music'&&key==='start')object.effect_offset=Math.max(0,(object.effect_offset||0)+value-object.start);
    if(selected.kind==='light'&&key==='at'){object.segment_id=null;object.offset=0;}object[key]=value;
   }
   if(selected.kind==='word'){if(object.start>=object.end)throw Error('Word end must be after its start');object.estimated=false;document.approved=false;selected.id='source_'+object.id;}
   if(selected.kind==='music'&&object.kind==='audio'){
    if(size(object)<.01)throw Error('Music sections need a start before their end');
    if(['scratch','stop','start','stutter'].includes(object.mode)&&size(object)>30)throw Error('Select up to 30 seconds for a record gesture');document.approved=false;
   }
   if(selected.kind==='sound'){
    const clip=library.find(c=>c.id===object.clip_id);if(object.trim+object.duration>(clip?.duration||120)+.001)throw Error('Trim and length must fit inside the clip');
    if(object.fade_in+object.fade_out>object.duration)throw Error('Fades must fit inside the clip');
   }
  });
 };
 q('songInspector').onclick=guard(async event=>{
  const action=event.target.closest('[data-object]')?.dataset.object;if(!action||!selected)return;
  if(action==='listenWord'){const word=wordOccurrence(selected.id);if(!word||word.outside)throw Error('This word is outside the song arrangement');player.pause();player.seek(word.start);player.setStop(word.end);await player.play();return;}
  if(action==='revealEffect'){root.querySelector('#prepEffectChain').children[selected.id]?.scrollIntoView({behavior:'smooth',block:'center'});return;}
  mutate(()=>{
   if(action==='duplicate')selected.id=duplicate(document,selected.id);
   if(action==='remove'){remove(document,selected.id);selected=null;}
   if(action==='left'||action==='right')move(document,selected.id,action==='left'?-1:1);
   if(action==='duplicateSound'){const sound=clone(document.sounds.find(s=>s.id===selected.id));sound.id=uid();document.sounds.push(sound);selected.id=sound.id;}
   if(action==='removeLight'){document.lighting.cues.splice(selected.id,1);selected=null;}
   if(action==='clearEffects')document.segments.find(s=>s.id===selected.id).effects=[];
   if(action==='addPoint'){
    const effect=document.mix.effects[selected.id];effect.automation||=[];
    if(effect.automation.length>=32)throw Error('Use up to 32 automation points');
    const time=Math.round(player.time()*100)/100,value=Number(q('songInspector').querySelector('[data-key=pointStrength]').value);
    effect.automation=effect.automation.filter(p=>Math.abs(p.time-time)>.005);effect.automation.push({time,value});effect.automation.sort((a,b)=>a.time-b.time);syncMix();
   }
   if(action==='attachEffects'){
    const section=document.segments.find(s=>s.id===selected.id),at=spans(document).find(s=>s.id===section.id).at;
    section.effects=clone(document.mix.effects||[]);
    for(const effect of section.effects){
     if(effect.range){effect.range.start=Math.max(0,effect.range.start-at);effect.range.end=Math.max(effect.range.start+.01,effect.range.end-at);effect.range.fade_in=Math.min(effect.range.fade_in,(effect.range.end-effect.range.start)/2);effect.range.fade_out=Math.min(effect.range.fade_out,(effect.range.end-effect.range.start)/2);}
     if(effect.automation)effect.automation=effect.automation.map(p=>({...p,time:Math.max(0,p.time-at)})).filter((p,i,list)=>!i||p.time>list[i-1].time);
    }
    section.effect_offset=0;document.mix.effects=[];syncMix();
   }
  });
 });
 function libraryList(){
  const query=q('songSoundSearch').value.trim().toLowerCase(),items=library.filter(c=>(c.name+' '+c.category).toLowerCase().includes(query));
  q('songSoundCount').textContent=library.length;
  q('songSoundList').innerHTML=items.map(c=>`<div class="song-sample" draggable="true" data-clip="${esc(c.id)}"><button data-audition="${esc(c.id)}" class="song-sample-play secondary" aria-label="Audition ${esc(c.name)}">▶</button><div><strong>${esc(c.name)}</strong><span>${esc(c.category)} · ${stamp(c.duration)}</span></div><button data-add="${esc(c.id)}" class="secondary" aria-label="Add ${esc(c.name)}">＋</button></div>`).join('')||'<p class="song-hint">No matching clips. Import your audio above.</p>';
  for(const item of q('songSoundList').querySelectorAll('[data-clip]'))item.ondragstart=event=>{event.dataTransfer.setData('application/x-gbr-sound',item.dataset.clip);event.dataTransfer.effectAllowed='copy';};
 }
 q('songSoundSearch').oninput=libraryList;
 q('songSoundList').onclick=guard(async event=>{
  const add=event.target.closest('[data-add]')?.dataset.add,id=event.target.closest('[data-audition]')?.dataset.audition;
  if(add)mutate(()=>addSound(library.find(c=>c.id===add)));
  if(id){globalThis.document.getElementById('audio')?.pause();player.pause();audition.pause();audition.src='/api/preparation/sounds/'+encodeURIComponent(id)+'/audio';await audition.play();}
 });
 q('songImport').onchange=guard(async()=>{
  const files=[...q('songImport').files];q('songImport').disabled=true;
  try{
   for(let i=0;i<files.length;i++){
    const file=files[i];if(file.size>32*1024**2)throw Error(file.name+' is larger than 32 MiB');
    q('songImportStatus').textContent=`Importing ${i+1} / ${files.length} · ${file.name}`;
    const response=await fetch('/api/preparation/sounds/import?'+new URLSearchParams({name:file.name.replace(/\.[^.]+$/,'')}),{method:'POST',headers:{'X-GBR-Request':'1','Content-Type':'application/octet-stream'},body:file});
    const result=await response.json();if(!response.ok)throw Error(result.detail||'Clip import failed');
    library=library.filter(c=>c.id!==result.id);library.unshift(result);libraryList();
   }
   q('songImportStatus').textContent=`${files.length} clip${files.length===1?'':'s'} added. Available for every song.`;
  }finally{q('songImport').value='';q('songImport').disabled=false;}
 });
 canvas.ondragover=event=>{if(event.dataTransfer.types.includes('application/x-gbr-sound')){event.preventDefault();event.dataTransfer.dropEffect='copy';}};
 canvas.ondrop=event=>{const clip=library.find(c=>c.id===event.dataTransfer.getData('application/x-gbr-sound'));if(!clip)return;event.preventDefault();mutate(()=>addSound(clip,T(event.clientX-canvas.getBoundingClientRect().left)));};
 canvas.onpointerdown=event=>{
  if(event.button!==0||!document)return;canvas.focus();const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=(event.clientY-rect.top)*390/rect.height;
  const hit=y<30?null:[...hits].reverse().find(h=>x>=h.x&&x<=h.x+h.w&&y>=h.y&&y<=h.y+h.h);
  selected=hit?{kind:hit.kind,id:hit.id}:null;inspect();
  drag={x:event.clientX,time:T(x),hit,before:snapshot(),moved:false,original:null,edge:null};
  if(hit?.kind==='sound')drag.original=clone(sounds(document).find(s=>s.id===hit.id));
  if(hit?.kind==='word'){
   const occurrence=lyricProjection.find(w=>w.id===hit.id);drag.wordSegment=spans(document).find(s=>s.id===occurrence.segment_id);drag.original=clone(document.words.find(w=>w.id===occurrence.source_id));drag.edge=x-hit.x<6?'start':hit.x+hit.w-x<6?'end':'move';
  }
  canvas.setPointerCapture(event.pointerId);draw();
 };
 canvas.onpointermove=event=>{
  if(!drag)return;const delta=(event.clientX-drag.x)/(canvas.clientWidth-100)*viewSpan;
  if(Math.abs(event.clientX-drag.x)>4)drag.moved=true;if(!drag.moved)return;
  if(drag.hit?.kind==='sound'){
   const object=document.sounds.find(s=>s.id===drag.hit.id),at=clamp(drag.original.position+delta,0,duration(document));object.segment_id?anchorAt(object,at):object.at=at;
  }else if(drag.hit?.kind==='word'&&drag.wordSegment?.mode==='normal'){
   const object=document.words.find(s=>s.id===drag.original.id);Object.assign(object,shiftWordTiming(drag.original,drag.edge,delta,data.source_duration),{estimated:false,edited:true});document.approved=false;lyricProjection=words(document);
  }else if(!drag.hit||event.shiftKey){
   range={start:Math.min(drag.time,clamp(drag.time+delta,0,duration(document))),end:Math.max(drag.time,clamp(drag.time+delta,0,duration(document)))};rangeInputs();
  }else if(drag.hit.kind==='music')q('songHint').textContent='Release over another music section to move this section there.';draw();
 };
 canvas.onpointerup=event=>{
  if(!drag)return;const current=drag;drag=null;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
  if(current.moved&&current.hit?.kind==='music'&&!event.shiftKey){
   const at=T(event.clientX-canvas.getBoundingClientRect().left),target=spans(document).find(s=>at>=s.at&&at<s.until);
   if(target&&target.id!==current.hit.id){
    const from=document.segments.findIndex(s=>s.id===current.hit.id),object=document.segments.splice(from,1)[0],to=document.segments.findIndex(s=>s.id===target.id);document.segments.splice(to+(at>(target.at+target.until)/2?1:0),0,object);document.approved=false;
   }
  }
  if(!current.moved&&!current.hit){player.seek(current.time);range={start:current.time,end:current.time};rangeInputs();}
  else if(!current.moved&&current.hit?.kind==='music')player.seek(current.time);
  changed(current.before);refreshPlaybackLoop();inspect();draw();
 };
 canvas.onpointercancel=()=>{if(drag){document=drag.before;drag=null;lyricProjection=words(document);draw();}};
 canvas.onkeydown=guard(async event=>{
  if(event.code==='Space'){event.preventDefault();await toggle();return;}
  if(['ArrowLeft','ArrowRight'].includes(event.key)){
   event.preventDefault();
   if(selected?.kind==='word')mutate(()=>{const source=document.words.find(w=>w.id===wordOccurrence(selected.id)?.source_id);if(source?.start!=null){Object.assign(source,shiftWordTiming(source,event.altKey?'end':'move',(event.key==='ArrowLeft'?-1:1)*(event.shiftKey?.25:.05),data.source_duration),{estimated:false,edited:true});document.approved=false;}});
   else player.seek(player.time()+(event.key==='ArrowLeft'?-1:1)*(event.shiftKey?1:.1));
  }
  if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();history(!event.shiftKey);}
 });
 async function toggle(){
  audition.pause();if(player.active())player.pause();else{player.setStop(null);q('songHint').textContent='Loading audio…';await player.play();q('songHint').textContent=q('songListen').value==='draft'?'Playing edits and clips · render to hear the complete mastering chain':'Playing processed range preview';}
 }
 q('songPlay').onclick=guard(toggle);q('songBack').onclick=()=>player.seek(Math.max(0,player.time()-2));q('songListen').onchange=()=>{player.setMode(q('songListen').value);clock();};q('songLoop').onchange=refreshPlaybackLoop;
 q('songSpeed').onchange=()=>player.setSpeed(Number(q('songSpeed').value));
 q('songScroll').oninput=()=>{viewStart=Number(q('songScroll').value);draw();};
 for(const id of ['songRangeStart','songRangeEnd'])q(id).onchange=()=>{
  range={start:clamp(Number(q('songRangeStart').value)||0,0,duration(document)),end:clamp(Number(q('songRangeEnd').value)||0,0,duration(document))};
  if(range.start>range.end)[range.start,range.end]=[range.end,range.start];rangeInputs();refreshPlaybackLoop();draw();
 };
 function zoom(factor){const center=viewStart+viewSpan/2;viewSpan=clamp(viewSpan*factor,.25,duration(document));viewStart=clamp(center-viewSpan/2,0,duration(document)-viewSpan);rangeInputs();draw();}
 q('songZoomIn').onclick=()=>zoom(.5);q('songZoomOut').onclick=()=>zoom(2);q('songFit').onclick=()=>{viewStart=0;viewSpan=duration(document);rangeInputs();draw();};
 q('songSelection').onclick=guard(async()=>{if(range.end<=range.start)throw Error('Select a range first');player.pause();player.seek(range.start);player.setStop(q('songLoop').checked?null:range.end);refreshPlaybackLoop();await player.play();});
 q('songSplit').onclick=()=>mutate(()=>{const id=split(document,player.time());if(id)selected={kind:'music',id};});
 const breakLength=()=>clamp(Number(q('songInspector').querySelector('[data-key="break"]')?.value)||2,.1,120);
 q('songBreak').onclick=()=>mutate(()=>{const id=insertGap(document,player.time(),breakLength());selected={kind:'music',id};document.approved=false;});
 q('songNeedle').onclick=()=>mutate(()=>{
  const at=player.time(),length=breakLength(),id=insertGap(document,at,length,'Needle break');document.approved=false;
  const lift=library.find(c=>c.preset==='lift'),drop=library.find(c=>c.preset==='drop');
  if(lift){addSound(lift,at);const sound=document.sounds.at(-1);sound.segment_id=id;sound.offset=0;sound.duration=Math.min(sound.duration,length/3);}
  if(drop){addSound(drop,at+length-Math.min(drop.duration,length/3));const sound=document.sounds.at(-1);sound.segment_id=id;sound.offset=length-Math.min(drop.duration,length/3);sound.duration=Math.min(drop.duration,length/3);}
  selected={kind:'music',id};range={start:Math.max(0,at-.5),end:Math.min(duration(document),at+length+1)};
 });
 q('songGesture').onchange=()=>{const mode=q('songGesture').value;if(mode)mutate(()=>gesture(document,range.start,range.end,mode));q('songGesture').value='';};
 q('songAddLight').onclick=()=>mutate(()=>{document.lighting.cues.push({segment_id:null,at:player.time(),offset:0,duration:Math.max(.1,Math.min(2,duration(document)-player.time())),boost:1,label:'Light cue'});selected={kind:'light',id:document.lighting.cues.length-1};});
 q('songUndo').onclick=()=>history(true);q('songRedo').onclick=()=>history(false);q('songSave').onclick=guard(()=>save());
 q('songLanguage').onchange=()=>mutate(()=>{document.language=q('songLanguage').value;document.approved=false;});
 q('songSensitivity').onchange=()=>mutate(()=>{const value=Number(q('songSensitivity').value);if(!Number.isFinite(value)||value<.25||value>3)throw Error('Use sensitivity from 0.25 to 3');document.lighting.sensitivity=value;});
 q('songApprove').onclick=guard(async()=>{mutate(()=>{document.approved=true;});await save('Lyric approval');});
 function lyricText(){
  const lines=new Map();for(const word of document.words){if(!lines.has(word.line))lines.set(word.line,[]);lines.get(word.line).push(word);}
  q('songLyricText').innerHTML=[...lines.values()].map(line=>'<p>'+line.map(word=>`<button data-source-word="${esc(word.id)}" class="secondary ${word.omitted?'omitted':''}">${esc(word.text)}</button>`).join(' ')+'</p>').join('');
 }
 q('songLyricText').onclick=event=>{
  const id=event.target.closest('[data-source-word]')?.dataset.sourceWord;if(!id)return;const occurrence=lyricProjection.find(w=>w.source_id===id);
  selected={kind:'word',id:occurrence?.id||'source_'+id};if(occurrence){player.seek(occurrence.start);viewSpan=Math.min(viewSpan,10);viewStart=clamp(occurrence.start-viewSpan/3,0,Math.max(0,duration(document)-viewSpan));}rangeInputs();inspect();draw();
 };
 async function runAlignment(device='auto'){
  const request=ticket,language=document.language,words=JSON.stringify(document.words);
  const result=await api(endpoint('/jobs'),{kind:'lyric_alignment',input_artifact_id:data.source_artifact_id||null,language,force:true,device});
  if(request!==ticket)return;
  requestedAlignment={id:result.id,language,words};alignmentFailure=null;
  q('songAlignmentStatus').textContent='Alignment '+result.status+' · timings will load when complete unless you edit the words.';
 }
 q('songAlign').onclick=guard(()=>runAlignment());
 const cpuButton=globalThis.document.createElement('button');cpuButton.id='songAlignCpu';cpuButton.className='secondary';
 cpuButton.textContent='Run here on CPU';cpuButton.onclick=guard(()=>runAlignment('cpu'));q('songAlign').after(cpuButton);
 function currentAlignment(){
  return row?.tasks?.find(task=>task.kind==='lyric_alignment'&&task.input_sha256===data?.source_sha256&&
   (task.recipe.input_artifact_id||null)===(data?.source_artifact_id||null)&&task.recipe.language===document?.language&&
   (!task.recipe.lyrics_sha256||task.recipe.lyrics_sha256===data?.lyrics_sha256));
 }
 function canLoadAlignmentAutomatically(){
  // A background completion may fill estimates, never saved timing corrections.
  if(!document||renderBusy)return false;
  const current=JSON.stringify(document.words);
  if(requestedAlignment?.id===currentAlignment()?.id&&requestedAlignment.language===document.language&&requestedAlignment.words===current)return true;
  return !document.approved&&current===JSON.stringify(data.project.words)&&
   document.words.every(word=>word.omitted||word.estimated||word.start==null);
 }
 async function loadAlignment(automatic=false){
  if(!document||!data||loadingProject)return;
  const request=ticket,sequence=++alignmentSequence,before=JSON.stringify(document.words),language=document.language;
  alignmentLoading=true;q('songLoadAlignment').disabled=true;
  try{
   const query=new URLSearchParams({alignment_only:'true',language});
   if(data.source_artifact_id)query.set('artifact',data.source_artifact_id);
   const result=await api(endpoint('/editorial')+'?'+query);
   if(request!==ticket||sequence!==alignmentSequence)return;
   if(JSON.stringify(document.words)!==before||document.language!==language){
    if(automatic)return;
    throw Error('Lyrics changed while the alignment was loading. Load it again to apply the timings.');
   }
   if(automatic&&!canLoadAlignmentAutomatically())return;
   if(result.source_sha256!==data.source_sha256)throw Error('The alignment belongs to different audio. Reload this recording.');
   if(!result.lyric_task_id)throw Error('No completed alignment is available for this audio and language. Check the processing queue.');
   const matches=result.words.length===document.words.length&&result.words.every((word,index)=>
    word.id===document.words[index].id&&(word.source_text||word.text)===(document.words[index].source_text||document.words[index].text));
   if(!matches)throw Error('Source lyrics changed. This alignment was not applied to your edited words.');
   mutate(()=>{
    document.words=document.words.map((word,index)=>{const aligned=result.words[index];return {...word,
     start:aligned.start,end:aligned.end,confidence:aligned.confidence,estimated:Boolean(aligned.estimated),confirmed:false};});
    document.approved=false;
   });
   alignmentId=result.lyric_task_id;alignmentFailure=null;
   q('songAlignmentStatus').textContent=`${result.timed_words} / ${result.words.length} words aligned · loaded into the timeline`;
  }catch(error){
   if(request!==ticket||sequence!==alignmentSequence)return;
   alignmentFailure={id:currentAlignment()?.id,message:error.message};
   q('songAlignmentStatus').textContent=error.message;
   if(!automatic)toast(error.message);
  }finally{
   if(request===ticket&&sequence===alignmentSequence){alignmentLoading=false;q('songLoadAlignment').disabled=false;}
  }
 }
 function updateAlignment(){
  const alignment=currentAlignment();
  if(!alignment||alignmentLoading)return;
  const live=processingItems.get(alignment.id),status=live?.status||alignment.status,progress=live?.progress||alignment.telemetry||{};
  if(alignmentFailure?.id===alignment.id){q('songAlignmentStatus').textContent=alignmentFailure.message;return;}
  if(status==='done'){
   if(alignment.id!==alignmentId){
    if(canLoadAlignmentAutomatically())loadAlignment(true);
    else q('songAlignmentStatus').textContent='Alignment done · newer alignment available. Load it to replace timings while keeping your lyric text.';
   }else if(!q('songAlignmentStatus').textContent)q('songAlignmentStatus').textContent=document.words.filter(word=>!word.estimated&&word.start!=null).length+' / '+document.words.length+' words aligned';
  }else{
   const reason=live?.reason||alignment.error||progress.message;
   const detail=['failed','interrupted'].includes(status)?' · Open Processing queue for the error and retry':reason?' · '+reason:'';
   q('songAlignmentStatus').textContent=(progress.remote?.name?'Alignment on '+progress.remote.name+' · ':'Alignment ')+status+detail;
  }
 }
 q('songLoadAlignment').onclick=()=>loadAlignment();
 async function render(kind){
  const request=ticket;
  const saved=dirty||!revision?await save('Before '+(kind==='mix_master'?'master':'preview')+' render'):data;
  if(request!==ticket)return;
  const start=range.end>range.start?range.start:player.time(),seconds=Math.max(1,Math.min(60,Math.ceil(range.end>range.start?range.end-range.start:30)));
  const result=await api(endpoint('/jobs'),{kind,song_project_id:saved.revision_id,input_artifact_id:saved.source_artifact_id||null,start:Math.min(start,duration(document)-.01),seconds});
  if(request!==ticket)return;
  renderPending=result.id;q('songRenderState').textContent=(kind==='mix_master'?'Master':'Preview')+' '+result.status+'…';renders();
 }
 q('songPreview').onclick=guard(()=>render('mix_preview'));q('songMaster').onclick=guard(()=>render('mix_master'));
 async function chooseMaster(id){
  const request=ticket;
  if(id&&!revision&&!dirty){
   const task=(row?.tasks||[]).find(t=>t.artifacts.some(a=>a.id===id));
   if(!task?.recipe.song_project_id){
    const value=await api(endpoint('/song-project')+'?'+new URLSearchParams({artifact:id}));
    if(request!==ticket)return;
    data=value;document=clone(value.project);revision=value.revision_id;resetPlayback();syncMix();lyricProjection=words(document);viewSpan=duration(document);rangeInputs();state();inspect();lyricText();
    const result=await api(endpoint('/waveform')+'?'+new URLSearchParams({artifact:id}));
    if(request!==ticket)return;
    wave=result;draw();
   }
  }
  releaseMaster=id||'';q('songMasterChoice').value=releaseMaster;await sourceChanged(releaseMaster);renders();
 }
 q('songMasterChoice').onchange=guard(()=>chooseMaster(q('songMasterChoice').value));
 function renders(){
  if(!row||!document)return;const tasks=(row.tasks||[]).filter(t=>t.recipe.song_project_id===revision);
  const active=tasks.filter(t=>['queued','running'].includes(t.status)),pending=tasks.find(t=>t.id===renderPending)||active[0];
  q('songMaster').disabled=renderBusy||active.some(t=>t.kind==='mix_master');q('songPreview').disabled=renderBusy||active.some(t=>t.kind==='mix_preview');
  const preview=tasks.find(t=>t.kind==='mix_preview'&&t.status==='done'&&t.artifacts.some(a=>a.kind==='preview_audio'&&a.available));
  if(preview&&!dirty&&preview.id!==lastPreview){
   lastPreview=preview.id;player.preview('/api/preparation/artifacts/'+encodeURIComponent(preview.artifacts.find(a=>a.kind==='preview_audio').id),preview.recipe.start,preview.recipe.seconds);q('songListen').options[1].disabled=false;
  }
  const masters=(row.tasks||[]).filter(t=>t.kind==='mix_master'&&t.status==='done').flatMap(t=>t.artifacts.filter(a=>a.kind==='master_candidate'&&a.available).map(a=>({id:a.id,current:t.recipe.song_project_id===revision,time:t.completed_at,task:t.id})));
  if(data.reused_master_id&&!masters.some(m=>m.id===data.reused_master_id))masters.unshift({id:data.reused_master_id,current:true,time:data.versions[0]?.created_at});
  const signature=JSON.stringify([masters,releaseMaster,revision]);if(signature!==masterSignature){
   masterSignature=signature;q('songMasterChoice').replaceChildren(new Option('Choose release master…',''),...masters.map(m=>new Option((m.current?'✓ Current timeline':'Earlier render')+' · '+new Date(m.time).toLocaleTimeString(),m.id)));q('songMasterChoice').value=releaseMaster;
  }
  if(pending?.status==='failed')q('songRenderState').textContent=pending.error;
  else if(active.length)q('songRenderState').textContent=active.map(t=>(t.kind==='mix_master'?'Master':'Preview')+' '+t.status).join(' · ')+'…';
  else{
   const latest=masters.find(m=>m.current),chosen=masters.find(m=>m.id===releaseMaster);
   if(latest&&!dirty&&(!releaseMaster||latest.task===renderPending)){releaseMaster=latest.id;q('songMasterChoice').value=releaseMaster;renderPending='';sourceChanged(releaseMaster).catch(error=>toast(error.message));}
   q('songRenderState').textContent=dirty?'Save and render to include these edits.':(chosen?.current||latest?.id===releaseMaster)?'Current timeline master selected for release.':latest?'Current timeline master ready. Choose it for release.':revision?'Render a master of this timeline before finishing preparation.':'';
  }
 }
 q('songRestore').onclick=guard(async()=>{const request=ticket;const value=await api(endpoint('/song-project')+'?'+new URLSearchParams({revision:q('songVersions').value}));if(request!==ticket)return;mutate(()=>{document=clone(value.project);syncMix();});});
 q('songReload').onclick=guard(async()=>{
  const request=ticket,value=await api(endpoint('/song-project'));if(request!==ticket)return;resetPlayback();data=value;revision=value.revision_id;document=clone(value.project);dirty=false;undo=[];redo=[];selected=null;syncMix();lyricProjection=words(document);versions(value.versions);cache();rangeInputs();state();draw();inspect();lyricText();renders();
 });
 const resize=new ResizeObserver(()=>draw());resize.observe(canvas);
 return {
  processingStatus(status){processingItems=new Map((status.items||[]).map(item=>[item.id,item]));updateAlignment();},
  artifact:()=>releaseMaster,chooseSource:chooseMaster,onSourceChange:fn=>sourceChanged=fn,canSwitch:()=>!renderBusy,
  releaseReady(){if(!document||!data||dirty||renderBusy||loadingProject)throw Error('Save the song timeline before finishing preparation');if(revision&&releaseMaster!==data.reused_master_id&&!row?.tasks.some(t=>t.kind==='mix_master'&&t.status==='done'&&t.recipe.song_project_id===revision&&t.artifacts.some(a=>a.id===releaseMaster&&a.available)))throw Error('Render and select a master of the latest saved song timeline before finishing');},
  time:player.time,play:player.play,pause:()=>{player.pause();audition.pause();},seek:player.seek,isPlaying:player.active,
  range:()=>{if(range.end<=range.start)throw Error('Select a range on the song timeline first');const fade=Math.min(.1,(range.end-range.start)/4);return {...range,fade_in:fade,fade_out:fade};},
  duration:()=>duration(document),source:()=>data?.source_artifact_id||null,
  mixChanged(){if(!document||restoreMix)return;mutate(()=>{document.mix=clone(getMix());document.stem_task_id=getStems?.()||null;});},
  artworkChanged(){if(!document||restoreMix)return;mutate(()=>{document.artwork_id=getArtwork()||null;});},
  async update(project){
   row=project;if(!project?.ready)return;
   globalThis.document.getElementById('audio')?.pause();
   if(project.generation_id===gid&&document){
    renders();if(!frame&&!root.hidden)frame=requestAnimationFrame(tick);updateAlignment();return;
   }
   cache();
   const request=++ticket;loadingProject=true;host.inert=true;resetPlayback();gid=project.generation_id;document=null;data=null;dirty=false;wave=null;canvas.dataset.waveform='loading';selected=null;undo=[];redo=[];releaseMaster='';masterSignature='';knownVersions='';renderPending='';
   alignmentLoading=false;alignmentFailure=null;requestedAlignment=null;q('songLoadAlignment').disabled=false;q('songAlignmentStatus').textContent='';
   q('songTitle').textContent=project.title;q('songSaveState').textContent='Loading song…';
   try{
   const value=await api(endpoint('/song-project'));if(request!==ticket)return;data=value;revision=value.revision_id;document=clone(value.project);alignmentId=value.lyric_task_id;
   const remembered=sessionDrafts.get(gid);
   let draft=remembered;
   if(!draft){try{draft=JSON.parse(localStorage.getItem('gbr-song-draft:'+gid)||'null');}catch{}}
   if(draft?.source_sha256===value.source_sha256){
    if(draft.dirty===true||(draft.dirty===undefined&&draft.revision_id===revision&&!equivalentDraft(draft.project,document))){document=clone(draft.project);dirty=true;revision=draft.revision_id;undo=draft.undo||[];redo=draft.redo||[];}
    if(draft.revision_id===revision)releaseMaster=draft.releaseMaster||'';
   }
   if(!revision&&!dirty){document.mix=clone(getMix());document.stem_task_id=getStems?.()||null;document.artwork_id=getArtwork()||null;}
   syncMix();lyricProjection=words(document);viewSpan=Math.min(remembered?.viewSpan||duration(document),duration(document));viewStart=clamp(remembered?.viewStart||0,0,Math.max(0,duration(document)-viewSpan));range=remembered?.range||{start:0,end:Math.min(30,viewSpan)};versions(value.versions);rangeInputs();state();draw();inspect();lyricText();renders();
   loadingProject=false;host.inert=false;cache();
   updateAlignment();
   if(!frame&&!root.hidden)frame=requestAnimationFrame(tick);
   if(!library.length){try{const result=await api('/preparation/sounds');if(request!==ticket)return;library=result.sounds;libraryList();}catch(error){if(request===ticket)q('songImportStatus').textContent=error.message;}}
   if(request!==ticket)return;
   api(endpoint('/waveform')+(value.source_artifact_id?'?'+new URLSearchParams({artifact:value.source_artifact_id}):'')).then(result=>{if(request===ticket){wave=result;canvas.dataset.waveform='ready';draw();}}).catch(error=>{if(request===ticket){canvas.dataset.waveform='failed';toast(error.message);}});
   }catch(error){if(request!==ticket)return;document=null;data=null;loadingProject=false;q('songSaveState').textContent=error.message;draw();throw error;}
  },
  close(){cache();++ticket;loadingProject=false;resetPlayback(true);if(frame)cancelAnimationFrame(frame);frame=null;}
 };
}
