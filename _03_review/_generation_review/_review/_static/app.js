import {initPreparation} from "/tools/preparation/preparation.js";
import {initYamlEditor} from "/tools/yaml-editor/editor.js";
import {initGeneration} from "/tools/generation/generation.js";
import {initWorkers} from "/tools/generation/workers.js";
import {ReviewTags, savedTags} from "/static/tags.js";
const $=id=>document.getElementById(id);
const reviewTags=new ReviewTags($("tagGroups"),$("clearTags"));
const audio=$('audio'), canvas=$('waveform');
const DIMENSIONS=['audio_quality','composition','consistency','realism'];
const state={track:null,preview:null,scores:{},view:'review',mode:'quick',context:null,format:localStorage.getItem('gbr-format')||'flac',durations:{fast:10,quick:20,standard:50},ratingIndex:0,lastSampleMode:'quick',waitForRetune:false,analysisIds:new Set(),submitting:false,advancePending:false,autoSubmit:localStorage.getItem('gbr-auto-submit')!=='false',saveError:false,flagSaving:false,flagError:false,flags:null,draftFlags:{distribution:false,bookmarked:false,rejected:false},flagWrite:Promise.resolve(),submissionId:'',reservationLost:false,reviewerId:localStorage.getItem('gbr-reviewer-id')||'',reviewer:null,selection:null,selecting:false,canBack:false,canConfigure:false,listened:new Set(),heard:0,fullCompleted:false,fullIntervals:[],lastTime:0,comparison:null,candidates:[],chosen:[],offset:0,queue:{order:'random',direction:'asc',song:'',genre:'',directory:''}};
let toastTimer, loadSequence=0, prefetchTimer, heartbeatTimer, reviewerChanging=false;
const fmt=t=>{t=Math.max(0,Number(t)||0);return `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}`;};
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-GBR-Request':'1'},body:JSON.stringify(body)};
  options.headers={...(options.headers||{}),...(state.reviewerId?{'X-GBR-Reviewer':state.reviewerId}:{})};
  const response=await fetch('/api'+path,options);
  if(!response.ok){let data;try{data=await response.json();}catch{data={detail:response.statusText};}if(response.status===401&&path!=='/login'&&!$('loginDialog').open)$('loginDialog').showModal();const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.code=data.code;error.status=response.status;throw error;}
  return response.json();
}
function guarded(fn){return (...args)=>Promise.resolve(fn(...args)).catch(error=>toast(error.message));}
function sourceURL(mode,extra={}){const q=new URLSearchParams({mode,format:state.format,...(state.preview?.analysis_id?{analysis_id:state.preview.analysis_id}:{}),...extra});return `/api/playback/${state.track.id}?${q}`;}
function ratingButtons(){
  for(const row of document.querySelectorAll('.rating')){
    for(let score=0;score<=5;score++){
      const button=document.createElement('button');button.type='button';button.textContent=score;button.dataset.score=score;button.setAttribute('aria-pressed','false');const group=[...row.querySelectorAll(".rating-anchor")].find(item=>score>=Number(item.dataset.scoreMin)&&score<=Number(item.dataset.scoreMax));const anchor=group?.querySelector(".anchor-label")?.textContent;button.setAttribute('aria-label',`${row.querySelector('.rating-label span').textContent}: ${score} out of 5${anchor?' — '+anchor:''}`);
      button.addEventListener('click',guarded(async()=>{
        if(state.submitting||state.advancePending||state.reservationLost||!state.track)return;
        state.scores[row.dataset.dimension]=score;const missing=DIMENSIONS.findIndex(d=>state.scores[d]===undefined);if(missing>=0)state.ratingIndex=missing;paintRatings();
        if(state.autoSubmit&&DIMENSIONS.every(d=>state.scores[d]!==undefined))await submitReview();
      }));row.querySelector('.rating-values').append(button);
    }
  }
  for(const button of document.querySelectorAll('.help'))button.onclick=()=>toast(button.title);
}
function paintRatings(){
  $('changeUser').disabled=state.submitting||state.advancePending||state.flagSaving;
  $('back').disabled=!state.canBack||state.submitting||state.advancePending;$('skip').disabled=!state.track||state.submitting||state.advancePending||state.reservationLost;$('queueButton').disabled=state.submitting||state.advancePending;$('selectionTab').disabled=state.submitting||state.advancePending;$('yamlTab').disabled=state.submitting||state.advancePending;$('generationTab').disabled=state.submitting||state.advancePending;$('preparationTab').disabled=state.submitting||state.advancePending;
  const complete=DIMENSIONS.every(d=>state.scores[d]!==undefined);$('submitReview').hidden=state.autoSubmit||state.selectionListen||!state.track;$('submitReview').disabled=!complete||state.submitting||state.advancePending||state.reservationLost||state.flagSaving;$('retryReview').hidden=!state.autoSubmit||!state.saveError||state.reservationLost||state.advancePending;$('retryReview').disabled=state.submitting;$('reason').disabled=state.submitting||state.advancePending;$('autoSubmit').disabled=state.submitting||state.advancePending;paintFlags();reviewTags.setDisabled(!state.track||state.selectionListen||state.submitting||state.advancePending||state.reservationLost);
  const missing=DIMENSIONS.findIndex(d=>state.scores[d]===undefined);if(missing>=0&&state.scores[DIMENSIONS[state.ratingIndex]]!==undefined)state.ratingIndex=missing;
  document.querySelectorAll('.rating').forEach((row,i)=>row.classList.toggle('keyboard-active',i===state.ratingIndex));
  for(const row of document.querySelectorAll('.rating'))for(const button of row.querySelectorAll('.rating-values button')){button.setAttribute('aria-pressed',String(state.scores[row.dataset.dimension]===Number(button.dataset.score)));button.disabled=state.submitting||state.advancePending||state.reservationLost;}
}
async function submitReview(){
  if(state.submitting||state.advancePending||state.reservationLost||!state.track||state.selectionListen||!DIMENSIONS.every(d=>state.scores[d]!==undefined))return;
  let saved=false;state.saveError=false;state.submitting=true;paintRatings();$('reviewMessage').textContent='Saving…';const gid=state.track.id;
  try{
    const modes=[...state.listened];const mode=modes.length>1?'mixed':modes[0]||state.track.previous_mode||state.mode;
    await state.flagWrite.catch(()=>{});
    await api(`/tracks/${gid}/review`,{...state.scores,mode,tags:reviewTags.value(),reason:$('reason').value,distribution:state.draftFlags.distribution,bookmarked:state.draftFlags.bookmarked,rejected:Boolean(state.draftFlags.rejected),expected_publishing_status:state.flags?.publishing_status||'not_selected',submission_id:state.submissionId,expected_review_id:state.track.review_id||null,reassessment:Boolean(state.track.reassessment),listening:{modes_played:modes,played_seconds:Math.round(state.heard*10)/10,complete_full_playback:state.fullCompleted,analysis_id:state.context?.analysis_id||state.preview?.analysis_id||null,analyses_played:[...state.analysisIds],selected_range:state.selection,playback_speed:audio.playbackRate}});
    saved=true;audio.pause();$('reviewMessage').textContent='Saved';await nextTrack(true);state.submitting=false;paintRatings();
  }catch(error){state.submitting=false;state.advancePending=saved;state.saveError=!saved;if(error.code==='flags_changed')$('refreshFlags').hidden=false;if(error.code==='submission_saved'){state.canBack=true;$('back').disabled=false;state.saveError=false;}if(['reservation_lost','already_reviewed','other_reviewer'].includes(error.code))loseReservation(error.message);paintRatings();$('reviewMessage').textContent=saved?'Review saved. Retrying the next track…':state.reservationLost?error.message:`Not saved: ${error.message} Your entries are retained. ${error.code==='flags_changed'?'Refresh flags, then retry.':error.code==='submission_saved'?'Use Back to open the saved review.':'Retry save or choose a rating again.'}`;if(saved)throw new Error('Review saved; retrying the next track.');throw error;}
}
function preferredMode(){const mode=localStorage.getItem('gbr-review-mode')||'quick';return ['fast','quick','standard','full'].includes(mode)?mode:'quick';}
function modeAvailable(mode){return mode==='full'||Boolean(state.preview?.ready&&state.preview.segments.some(s=>s.mode===mode));}
function prepareNext(){
  clearTimeout(prefetchTimer);const gid=state.track?.id;if(!gid||state.track.correction||state.selectionListen||state.reservationLost)return;
  const mode=preferredMode();
  prefetchTimer=setTimeout(()=>{if(state.track?.id===gid)api('/queue/prefetch',{current_id:gid,mode,format:state.format}).catch(()=>{});},200);
}
async function nextTrack(advance=false){
  const previousMode=preferredMode(),reviewerId=state.reviewerId;
  const n=await api('/queue/next?include_preview=true');if(reviewerId!==state.reviewerId)return;paintCounts(n.counts);state.emptyReason=n.reason;state.canBack=n.can_back;$('returnSkipped').hidden=!n.has_skips;
  await loadTrack(n.track,false,n.preview,advance?previousMode:preferredMode());
  if(advance&&$('autoplayNext').checked&&state.track&&!state.track.correction){try{await audio.play();}catch{toast('Tap Play to continue — the browser blocked automatic playback.');}}
}
async function loadTrack(track,selectionListen=false,initialPreview=null,wantedMode=preferredMode()){
  renderTechnicalChecks(null);
  const sequence=++loadSequence;clearTimeout(prefetchTimer);audio.pause();audio.removeAttribute('src');audio.load();
  state.track=track;reviewTags.load(track?.tags||[]);$("reviewTags").hidden=selectionListen||!track;state.saveError=false;state.submissionId=newSubmissionId();state.flagSaving=false;state.flagError=false;state.flagWrite=Promise.resolve();state.flags=track?.flags||{distribution:false,bookmarked:false,publishing_status:'not_selected'};state.draftFlags={...state.flags};$('reason').value=track?.reason||'';$('reasonWrap').hidden=selectionListen;$('viewReviews').hidden=!(selectionListen||track?.correction);$('autoSubmitLabel').hidden=selectionListen;$('flagMessage').textContent='';$('retryFlags').hidden=true;$('refreshFlags').hidden=true;state.draftFlags.distribution=state.flags.distribution;state.draftFlags.bookmarked=state.flags.bookmarked;state.preview=null;state.scores=track?.scores?{...track.scores}:{};state.advancePending=false;state.reservationLost=false;$('resumeQueue').hidden=true;state.selection=null;state.selecting=false;state.context=null;state.ratingIndex=0;state.analysisIds=new Set();state.listened=new Set();state.heard=0;state.fullCompleted=false;state.fullIntervals=[];state.lastTime=0;state.selectionListen=selectionListen;
  $('playRange').hidden=true;$('selectRange').textContent='Select range';canvas.classList.remove('selecting');$('spectralWrap').hidden=true;$('spectralToggle').setAttribute('aria-expanded','false');
  $('reviewCard').hidden=!track;$('empty').hidden=Boolean(track);$('ratings').hidden=selectionListen;$('skip').disabled=!track;$('back').disabled=!state.canBack;
  $('skip').textContent=track?.correction?'Return →':'Skip →';$('correctionBadge').hidden=!track?.correction;paintRatings();$('reviewMessage').textContent=track?.correction?'Adjust tags or Reason first, then choose a rating to save.':'';
  if(!track){$('emptyText').textContent={first_pass_complete:'First pass complete. Every recording has a review; no second pass is assigned.',reserved:'The remaining matching recordings are reserved by other reviewers. They will appear when reservations are released.',all_skipped:'You’ve skipped the remaining recordings. Return skipped tracks for another look.',no_match:'No unreviewed recordings match your queue filters.',no_tracks:'No recordings are available. Check input folders, rescan or adjust your queue.'}[state.emptyReason]||'No recordings are available.';document.querySelector('#empty h1').textContent=state.emptyReason==='first_pass_complete'?'First pass complete.':state.emptyReason==='reserved'?'Waiting for recordings.':'Ready when you are.';drawWave();return;}
  $('trackTitle').textContent=track.title;$('trackGenre').textContent=track.genre;$('compositionGenre').textContent=(track.cluster&&track.cluster!==track.genre?`${track.cluster} / ${track.genre}`:track.genre);$('duration').textContent=fmt(track.duration);$('elapsed').textContent='0:00';$('progress').value='0';$('snippets').replaceChildren();$('waveStatus').hidden=false;$('waveStatus').textContent='Preparing waveform…';
  await refreshPreview(sequence,initialPreview);
  if(sequence!==loadSequence)return;
  setMode(selectionListen?'full':modeAvailable(wantedMode)?wantedMode:modeAvailable('quick')?'quick':'full',{},false);mediaSession();prepareNext();
}
async function refreshPreview(sequence=loadSequence,initialPreview=null){
  if(!state.track)return;const gid=state.track.id;const preview=initialPreview||await api(`/tracks/${gid}/preview`);
  if(sequence!==loadSequence||state.track?.id!==gid)return;
  const wasReady=state.preview?.ready;state.preview=preview;renderTechnicalChecks(preview.technical_checks);
  $('waveStatus').hidden=preview.ready;$('waveStatus').textContent=state.track.analysis_state==='failed'?'Waveform unavailable · full playback available':'Preparing waveform…';
  for(const button of document.querySelectorAll('[data-mode]'))button.disabled=!modeAvailable(button.dataset.mode);
  $('spectralToggle').disabled=!preview.ready;$('nextPoint').disabled=!preview.ready||!preview.markers.length;
  if(preview.ready){document.querySelector('.freq-top').textContent=`${Math.round(preview.frequency_axis.max_hz/1000)} kHz`;drawSnippets();drawWave();if(!wasReady&&!state.selectionListen&&audio.paused&&state.heard===0&&state.context){const wanted=preferredMode();if(modeAvailable(wanted)&&state.mode!==wanted)setMode(wanted,{},false);}prepareNext();}
}
function renderTechnicalChecks(checks){
  const status=$('technicalStatus'), flags=$('technicalFlags');flags.replaceChildren();$('technicalMetrics').textContent='';
  const assessed=checks&&checks.status!=='not_assessed';
  status.textContent=!assessed?(state.track?.analysis_state==='failed'?'Analysis unavailable':'Not assessed yet'):checks.status==='flagged'?`${checks.flags.length} possible issue${checks.flags.length===1?'':'s'}`:'No strong technical flags';
  status.classList.toggle('technical-warning',checks?.status==='flagged');
  if(!assessed)return;
  const m=checks.measurements||{},parts=[];
  for(const [key,label,unit] of [['integrated_lufs','Loudness',' LUFS'],['true_peak_dbtp_4x','Peak estimate',' dBTP'],['rms_dbfs','RMS',' dBFS']])if(Number.isFinite(m[key]))parts.push(`${label} ${m[key].toFixed(1)}${unit}`);
  if(Number.isFinite(m.clipped_fraction))parts.push(`Full-scale samples ${(m.clipped_fraction*100).toFixed(3)}%`);
  $('technicalMetrics').textContent=parts.join(' · ');
  const labels={near_silent_recording:'Almost silent recording',frequent_full_scale_samples:'Frequent full-scale samples',estimated_intersample_overs:'Possible intersample peaks',possible_mono_cancellation:'Possible mono cancellation',channel_imbalance:'Large channel imbalance',interior_quiet_gap:'Quiet gap'};
  for(const flag of checks.flags||[]){
    const timed=Number.isFinite(flag.start)&&Number.isFinite(flag.end),item=document.createElement(timed?'button':'span');item.className='technical-flag';
    item.textContent=(labels[flag.code]||flag.code)+(timed?` · ${fmt(flag.start)}–${fmt(flag.end)} · Listen`:'');
    item.title=Object.entries(flag.evidence||{}).map(([key,value])=>`${key}: ${value}`).join('\n');
    if(timed){item.type='button';item.onclick=guarded(async()=>{setMode('range',{start:Math.max(0,flag.start-2),end:Math.min(state.track.duration,flag.end+2)});await audio.play();});}
    flags.append(item);
  }
}
function setMode(mode,extra={},remember=true){
  if(!state.track)return;
  if(mode!=='range'&&!modeAvailable(mode)){toast('Sampling is being prepared. Full playback is available.');return;}
  if(['fast','quick','standard','full'].includes(mode)&&remember)localStorage.setItem('gbr-review-mode',mode);if(['fast','quick','standard'].includes(mode))state.lastSampleMode=mode;
  audio.pause();state.mode=mode;state.context={kind:mode,analysis_id:state.preview?.analysis_id||null,ranges:mode==='range'?[{start:Number(extra.start),end:Number(extra.end)}]:mode==='full'?[{start:0,end:state.track.duration}]:(state.preview?.segments||[]).filter(s=>s.mode===mode)};
  audio.src=sourceURL(mode,extra);audio.load();state.lastTime=0;
  for(const button of document.querySelectorAll('[data-mode]'))button.classList.toggle('active',button.dataset.mode===mode);
  $('modeDescription').textContent=mode==='full'?'Complete recording':mode==='range'?'Selected passage':`${state.durations[mode]}-second overview`;
  drawSnippets();drawWave();updateTransport();prepareNext();
}
async function playPause(){if(!state.track)return;if(audio.paused){if(!audio.src)setMode(state.mode);await audio.play();}else audio.pause();}
function drawSnippets(){
  $('snippets').replaceChildren();if(!state.preview?.ready)return;
  const sections=state.preview.segments.filter(s=>s.mode===(['fast','quick','standard'].includes(state.mode)?state.mode:state.lastSampleMode));
  if(state.mode==='full')return;
  for(const [i,section] of sections.entries()){
    const button=document.createElement('button');button.className='snippet';button.textContent=`${i+1} · ${fmt(section.start)}`;button.setAttribute('aria-label',`Replay section ${i+1} starting at ${fmt(section.start)}`);
    button.onclick=guarded(async()=>{audio.pause();state.mode='range';state.context={kind:'range',analysis_id:state.preview?.analysis_id||null,ranges:[section]};audio.src=sourceURL('range',{segment_id:section.id});audio.load();state.lastTime=0;await audio.play();drawWave();});$('snippets').append(button);
  }
}
function sourceTime(){
  if(!state.context||state.context.comparison)return 0;
  let time=audio.currentTime;
  for(const r of state.context.ranges){const length=r.end-r.start;if(time<=length)return r.start+time;time-=length;}
  return state.context.ranges.at(-1)?.end||0;
}
function waveWindow(){const duration=state.track?.duration||1;return {start:0,end:duration,length:duration};}
function drawWave(){
  const rect=canvas.getBoundingClientRect();if(!rect.width)return;
  const dpr=devicePixelRatio||1;canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const width=rect.width,height=rect.height;ctx.clearRect(0,0,width,height);ctx.fillStyle='#11171d';ctx.fillRect(0,0,width,height);
  if(!state.preview?.ready||!state.track)return;
  const {start,length,end}=waveWindow();const x=time=>(time-start)/length*width;
  for(const range of state.context?.ranges||[]){if(state.mode==='full')break;ctx.fillStyle='#e9ba631a';ctx.fillRect(x(range.start),0,x(range.end)-x(range.start),height);}
  if(state.selection){ctx.fillStyle='#76b9d344';ctx.fillRect(x(state.selection.start),0,x(state.selection.end)-x(state.selection.start),height);}
  const waves=state.preview.waveform,rows=waves.length,rowHeight=height/rows,position=sourceTime();
  for(let row=0;row<rows;row++){
    const wave=waves[row],center=rowHeight*(row+.5);ctx.strokeStyle='#34404b';ctx.beginPath();ctx.moveTo(0,center);ctx.lineTo(width,center);ctx.stroke();
    for(let pixel=0;pixel<width;pixel+=2){const t=start+pixel/width*length,index=Math.min(wave.length-1,Math.floor(t/state.track.duration*wave.length));const amplitude=Math.max(1,wave[index]*rowHeight*.43);ctx.fillStyle=t<position?'#e9ba63':'#84919c';ctx.fillRect(pixel,center-amplitude,1.4,amplitude*2);}
    if(rows>1){ctx.fillStyle='#a5b0bb';ctx.font='9px system-ui';ctx.fillText(row===0?'L':'R',5,rowHeight*row+10);}
  }
  // All event types use the same neutral marker; no diagnosis appears in review.
  const markers=state.preview.markers.filter(m=>m.start>=start&&m.start<=end),visible=[];
  for(const marker of markers){const px=x(marker.start);if(visible.some(p=>Math.abs(px-p)<9))continue;visible.push(px);ctx.fillStyle='#bdc7cd';ctx.beginPath();ctx.arc(px,5,2.4,0,2*Math.PI);ctx.fill();}
  if(position>=start&&position<=end){ctx.fillStyle='#f4d498';ctx.fillRect(x(position),0,1.5,height);}
}
async function seekSource(time){if(!state.track)return;const wasPlaying=!audio.paused;setMode('full');await new Promise(resolve=>audio.readyState>=1?resolve():audio.addEventListener('loadedmetadata',resolve,{once:true}));audio.currentTime=Math.min(time,state.track.duration);if(wasPlaying)await audio.play();drawWave();}
let pointerStart=null;
canvas.addEventListener('pointerdown',event=>{if(!state.track)return;const r=canvas.getBoundingClientRect(),w=waveWindow();pointerStart={x:event.clientX,time:w.start+(event.clientX-r.left)/r.width*w.length,selection:state.selecting||event.shiftKey};if(pointerStart.selection)canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener('pointermove',event=>{if(!pointerStart?.selection)return;const r=canvas.getBoundingClientRect(),w=waveWindow(),t=Math.max(0,Math.min(state.track.duration,w.start+(event.clientX-r.left)/r.width*w.length));state.selection={start:Math.min(pointerStart.time,t),end:Math.max(pointerStart.time,t)};drawWave();});
canvas.addEventListener('pointerup',guarded(async event=>{
  if(!pointerStart||!state.track)return;const start=pointerStart;pointerStart=null;const r=canvas.getBoundingClientRect(),w=waveWindow(),t=Math.max(0,Math.min(state.track.duration,w.start+(event.clientX-r.left)/r.width*w.length));
  if(start.selection){state.selection={start:Math.min(start.time,t),end:Math.max(start.time,t)};if(state.selection.end-state.selection.start<.2){state.selection=null;toast('Drag across the waveform to select a passage.');}$('playRange').hidden=!state.selection;drawWave();return;}
  const marker=state.preview?.markers.find(m=>Math.abs(m.start-t)/w.length*r.width<7&&event.clientY-r.top<16);
  if(marker){const beginning=Math.max(0,marker.start-2),ending=Math.min(state.track.duration,Math.max(marker.end+2,beginning+4));setMode('range',{start:beginning,end:ending});await audio.play();}else await seekSource(t);
}));
canvas.addEventListener('pointercancel',()=>pointerStart=null);
$('selectRange').onclick=()=>{state.selecting=!state.selecting;canvas.classList.toggle('selecting',state.selecting);$('selectRange').textContent=state.selecting?'Done selecting':'Select range';};
$('playRange').onclick=guarded(async()=>{if(state.selection){setMode('range',state.selection);await audio.play();}});
$('spectralToggle').onclick=()=>{const show=$('spectralWrap').hidden;$('spectralWrap').hidden=!show;$('spectralToggle').setAttribute('aria-expanded',String(show));if(show&&state.track)$('spectrogram').src=`/api/tracks/${state.track.id}/spectrogram`;};
$('spectralWrap').onclick=guarded(async event=>{const r=$('spectralWrap').getBoundingClientRect();await seekSource((event.clientX-r.left)/r.width*state.track.duration);});
function updateTransport(){
  const total=Number.isFinite(audio.duration)?audio.duration:state.context?.ranges?.reduce((sum,r)=>sum+r.end-r.start,0)||0;
  $('elapsed').textContent=fmt(audio.currentTime);$('duration').textContent=fmt(total);$('progress').value=total?audio.currentTime/total*1000:0;$('play').textContent=audio.paused?'▶':'Ⅱ';$('play').setAttribute('aria-label',audio.paused?'Play':'Pause');
  if(state.track)$('spectralCursor').style.left=`${sourceTime()/state.track.duration*100}%`;
  drawWave();
  if('mediaSession' in navigator){try{navigator.mediaSession.playbackState=audio.paused?'paused':'playing';if(total>0)navigator.mediaSession.setPositionState({duration:total,playbackRate:audio.playbackRate,position:Math.min(audio.currentTime,total)});}catch{}}
}
audio.addEventListener('timeupdate',()=>{const delta=audio.currentTime-state.lastTime;if(!state.context?.comparison&&!audio.paused&&delta>0&&delta<2){state.heard+=delta;state.listened.add(state.context?.kind||state.mode);if(state.context?.kind==='full')state.fullIntervals.push([state.lastTime,audio.currentTime]);}state.lastTime=audio.currentTime;updateTransport();});
audio.addEventListener('seeking',()=>state.lastTime=audio.currentTime);
audio.addEventListener('play',()=>{state.lastTime=audio.currentTime;if(!state.context?.comparison){state.listened.add(state.context?.kind||state.mode);if(state.context?.analysis_id)state.analysisIds.add(state.context.analysis_id);}updateTransport();});audio.addEventListener('pause',updateTransport);audio.addEventListener('loadedmetadata',updateTransport);audio.addEventListener('ended',()=>{if(state.context?.kind==='full'){const intervals=[...state.fullIntervals].sort((a,b)=>a[0]-b[0]);let covered=0,end=0;for(const interval of intervals){covered+=Math.max(0,interval[1]-Math.max(end,interval[0]));end=Math.max(end,interval[1]);}if(covered>=state.track.duration*.95)state.fullCompleted=true;}updateTransport();});
audio.addEventListener('error',()=>{
  if(!audio.error||!state.track)return;
  if(state.format==='flac'){
    const url=new URL(audio.currentSrc||audio.src,location.href),position=audio.currentTime,resume=!audio.paused;
    state.format='wav';localStorage.setItem('gbr-format','wav');url.searchParams.set('format','wav');audio.src=url.href;
    audio.addEventListener('loadedmetadata',()=>{audio.currentTime=Math.min(position,Math.max(0,audio.duration-.1));if(resume)audio.play().catch(()=>toast('Tap Play to continue.'));},{once:true});audio.load();toast('Using lossless WAV for playback compatibility.');
  }else toast('Playback unavailable. Check Settings for file issues.');
});
$('play').onclick=guarded(playPause);$('restart').onclick=guarded(async()=>{audio.currentTime=0;await audio.play();});
$('progress').oninput=()=>{if(Number.isFinite(audio.duration))audio.currentTime=Number($('progress').value)/1000*audio.duration;};
$('volume').oninput=()=>{audio.volume=Number($('volume').value)/100;localStorage.setItem('gbr-volume',$('volume').value);};
$('compatibility').onclick=()=>{const range=state.context?.ranges?.[0];state.format=state.format==='flac'?'wav':'flac';localStorage.setItem('gbr-format',state.format);setMode(state.mode,state.mode==='range'?state.selection||range||{}:{});toast(`Playback format: lossless ${state.format.toUpperCase()}`);};
for(const button of document.querySelectorAll('[data-mode]'))button.onclick=()=>setMode(button.dataset.mode);
function mediaSession(){
  if(!('mediaSession' in navigator)||!state.track)return;
  navigator.mediaSession.metadata=new MediaMetadata({title:state.track.title,artist:'Good Boy Records',album:'Review Room',artwork:[{src:'/static/icon-192.png',sizes:'192x192',type:'image/png'}]});
  const actions={play:()=>audio.play(),pause:()=>audio.pause(),stop:()=>{audio.pause();audio.currentTime=0;},seekbackward:details=>audio.currentTime=Math.max(0,audio.currentTime-(details.seekOffset||10)),seekforward:details=>audio.currentTime=Math.min(audio.duration||0,audio.currentTime+(details.seekOffset||10)),seekto:details=>audio.currentTime=details.seekTime,nexttrack:()=>audio.currentTime=Math.min(audio.duration||0,audio.currentTime+10),previoustrack:()=>audio.currentTime=Math.max(0,audio.currentTime-10)};
  for(const [name,handler] of Object.entries(actions))try{navigator.mediaSession.setActionHandler(name,handler);}catch{}
}
function showView(view){if(view!=='preparation')preparation.close();state.view=view;for(const name of ['review','selection','yaml','generation','preparation']){$(name+'View').hidden=view!==name;$(name+'Tab').classList.toggle('active',view===name);}if(view==='review')drawWave();}
$('reviewTab').onclick=guarded(async()=>{showView('review');if(state.selectionListen||!state.track)await nextTrack();else await refreshFlags();});
$('selectionTab').onclick=guarded(async()=>{showView('selection');await loadCandidates();});
const yamlEditor=initYamlEditor({api,toast,browse:async callback=>{await openDirectory(callback);}});
const preparation=initPreparation({api,toast});
$('preparationTab').onclick=guarded(async()=>{audio.pause();showView('preparation');await preparation.open();});
const workers=initWorkers({api,toast,onChange:async()=>{await generation.reloadWorkers();}});
const generation=initGeneration({api,toast,browse:async callback=>{await openDirectory(callback);},configureWorkers:()=>workers.open()});
$('openWorkerSettings').onclick=guarded(async()=>{$('settingsDialog').close();await workers.open();});
$('generationTab').onclick=guarded(async()=>{audio.pause();showView('generation');await generation.open();});
$('yamlTab').onclick=guarded(async()=>{audio.pause();showView('yaml');await yamlEditor.open();});
$('back').onclick=guarded(async()=>{const reviewerId=state.reviewerId,result=await api('/queue/back');if(reviewerId===state.reviewerId&&result.track)await loadTrack(result.track);});
$('autoplayNext').checked=localStorage.getItem('gbr-autoplay-next')==='true';
$('autoplayNext').onchange=()=>localStorage.setItem('gbr-autoplay-next',String($('autoplayNext').checked));
$('skip').onclick=guarded(async()=>{if(state.track&&!state.track.correction&&!state.selectionListen)await api(`/tracks/${state.track.id}/skip`,{});await nextTrack(true);});
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$(button.dataset.close).close();
$('queueButton').onclick=guarded(async()=>{
  $('order').value=state.queue.order;$('direction').value=state.queue.direction;$('filterSong').value=state.queue.song;$('filterGenre').value=state.queue.genre;
  $('filterDirectory').replaceChildren(new Option('All input folders',''));const b=await api('/bootstrap');for(const directory of b.queue_directories||b.input_directories||[])$('filterDirectory').append(new Option(directory,directory));$('filterDirectory').value=state.queue.directory;$('queueDialog').showModal();await previewQueueCounts();
});
async function applyQueue(returnSkipped=false){state.queue={order:$('order').value,direction:$('direction').value,song:$('filterSong').value,genre:$('filterGenre').value,directory:$('filterDirectory').value};await api('/queue',{...state.queue,return_skipped:returnSkipped});$('queueDialog').close();await nextTrack();}
$('queueForm').onsubmit=guarded(async event=>{event.preventDefault();await applyQueue();});$('resetSkipped').onclick=guarded(()=>applyQueue(true));$('returnSkipped').onclick=guarded(async()=>{await api('/queue',{...state.queue,return_skipped:true});await nextTrack();});
async function status(){const result=await api('/status');$('systemStatus').textContent=`Background: ${result.worker}. Analysis waiting: ${result.analysis_pending}. File issues: ${result.scan_errors}. Archive retries needed: ${result.archive_failures}.`;if(result.archive_failures&&!state.saveError&&!state.submitting)$('reviewMessage').textContent='Review saved. An archive move needs attention in Settings.';}
$('settingsButton').onclick=guarded(async()=>{
  $('hostSettings').hidden=!state.canConfigure;$('remoteSettings').hidden=state.canConfigure;$('reconcile').hidden=!state.canConfigure;$('retune').hidden=!state.canConfigure;$('diagnostics').hidden=!state.canConfigure;$('cacheManagement').hidden=!state.canConfigure;$('settingsError').textContent='';$('saveSettings').disabled=true;$('settingsDialog').showModal();
  if(state.canConfigure){try{const setting=await api('/settings');$('inputFolders').value=setting.input_directories.join('\n');$('archiveFolder').value=setting.archive_directory;$('archiveFolder').readOnly=setting.archive_locked;$('browseArchive').disabled=setting.archive_locked;$('archiveLockNote').hidden=!setting.archive_locked;for(const [id,key] of [['quickSeconds','quick_seconds'],['standardSeconds','standard_seconds'],['scanInterval','scan_interval_seconds'],['fadeMs','fade_ms'],['fastSeconds','fast_seconds'],['transitionSensitivity','transition_sensitivity'],['queueAhead','reservation_queue_ahead'],['reservationSeconds','reservation_seconds']])$(id).value=setting[key];$('saveSettings').disabled=false;}catch(error){$('settingsError').textContent=error.message;}}
  status().catch(error=>{$('systemStatus').textContent=error.message;});
  if(state.canConfigure){api('/mobile').then(m=>{$('mobileAccess').hidden=false;$('phoneLinks').replaceChildren();$('phoneInstructions').textContent=m.enabled?'Open a link below on the same network.':'Use Start-Mobile-Windows.bat on the host. No certificate is required.';for(const url of m.urls){const link=document.createElement('a');link.href=url;link.textContent=url;link.target='_blank';link.rel='noopener';$('phoneLinks').append(link,document.createElement('br'));}$('phoneCode').textContent=m.access_code?`Access code: ${m.access_code}`:m.enabled?'Use your existing password.':'';}).catch(()=>{});api('/diagnostics').then(d=>{$('diagnosticText').textContent=JSON.stringify(d,null,2);}).catch(error=>{$('diagnosticText').textContent=error.message;});cacheReport().catch(error=>{$('cacheSummary').textContent=error.message;});}
});
$('settingsForm').onsubmit=async event=>{event.preventDefault();$('settingsError').textContent='';$('saveSettings').disabled=true;try{await api('/settings',{input_directories:$('inputFolders').value.split('\n').map(value=>value.trim().replace(/^"|"$/g,'')).filter(Boolean),archive_directory:$('archiveFolder').value,quick_seconds:Number($('quickSeconds').value),standard_seconds:Number($('standardSeconds').value),scan_interval_seconds:Number($('scanInterval').value),fade_ms:Number($('fadeMs').value),fast_seconds:Number($('fastSeconds').value),transition_sensitivity:$('transitionSensitivity').value,reservation_queue_ahead:Number($('queueAhead').value),reservation_seconds:Number($('reservationSeconds').value)});state.durations={fast:Number($('fastSeconds').value),quick:Number($('quickSeconds').value),standard:Number($('standardSeconds').value)};state.waitForRetune=true;toast('Settings saved. Input folders will be rescanned.');$('settingsDialog').close();}catch(error){$('settingsError').textContent=error.message+' Your edits are retained.';}finally{$('saveSettings').disabled=false;}};
for(const [id,path] of [['rescan','/scan'],['rescanEmpty','/scan'],['retryArchive','/archive/retry'],['reconcile','/reconcile'],['reanalyse','/reanalyse'],['retune','/retune']])$(id).onclick=guarded(async()=>{await api(path,{});if(['retune','reanalyse'].includes(id))state.waitForRetune=true;toast(id==='retune'?'Cached zones queued for retuning.':'Background task scheduled.');});
$('backup').onclick=guarded(async()=>{const r=await fetch('/api/backup',{method:'POST',headers:{'X-GBR-Request':'1'}});if(!r.ok)throw new Error('Database backup failed');const url=URL.createObjectURL(await r.blob()),link=document.createElement('a');link.href=url;link.download='gbr-review-backup.sqlite';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast('Database backup downloaded. Back up the archive separately.');});
$('signOut').onclick=guarded(async()=>{await api('/logout',{});audio.pause();location.reload();});
async function loadCandidates(){
  const q=new URLSearchParams({status:$('selectionStatus').value,song:$('selectionSong').value,offset:state.offset,limit:50,bucket:$('selectionBucket').value,bookmarked:String($('selectionBookmarks').value==='only')});
  const result=await api('/publishing?'+q);state.candidates=result.items;state.chosen=[];$('compare').disabled=true;$('candidateList').replaceChildren();$('previousPage').disabled=state.offset===0;$('nextPage').disabled=!result.has_more;
  if(!result.items.length){const p=document.createElement('p');p.className='subtle';p.textContent='No tracks match these filters.';$('candidateList').append(p);return;}
  for(const item of result.items){
    const card=document.createElement('article');card.className='candidate';card.dataset.generationId=item.generation_id;
    const check=document.createElement('input');check.type='checkbox';check.disabled=!item.review_id;check.setAttribute('aria-label',`Compare ${item.title}, version ${item.generation_id.slice(0,6)}`);
    check.onchange=()=>{if(check.checked){if(state.chosen.length===2){check.checked=false;toast('Choose two versions for comparison.');return;}state.chosen.push(item);}else state.chosen=state.chosen.filter(i=>i.generation_id!==item.generation_id);$('compare').disabled=state.chosen.length!==2;};
    const title=document.createElement('div');title.className='candidate-title';const h=document.createElement('h3');h.textContent=item.title;const p=document.createElement('p');p.textContent=item.genre;const version=document.createElement('span');version.className='candidate-version';version.textContent=`Version ${item.generation_id.slice(0,6)}${item.review_id?'':' · awaiting review'}`;title.append(h,p,version);
    const actions=document.createElement('div');actions.className='candidate-actions';
    const listen=document.createElement('button');listen.textContent='Listen';listen.onclick=guarded(async()=>{showView('review');await loadTrack(await api(`/tracks/${item.generation_id}`),true);$('reviewMessage').textContent='Selection listening · use View saved reviews for reasons.';});
    const reassess=document.createElement('button');reassess.className='secondary';reassess.textContent=item.can_review?'Review':'Reassess';reassess.hidden=!item.can_review&&!item.can_reassess;reassess.onclick=guarded(async()=>{if(item.can_review){const result=await api('/queue/open',{generation_id:item.generation_id});state.canBack=result.can_back;showView('review');await loadTrack(result.track);}else{showView('review');await loadTrack(await api(`/tracks/${item.generation_id}?reassess=true`));}});
    const select=document.createElement('select');select.setAttribute('aria-label',`Publishing state for ${item.title}`);for(const [value,label] of [['not_selected','Not selected'],['selected','Flagged for distribution'],['published','Published'],['passed','Passed'],['rejected','Rejected'],['withdrawn','Withdrawn']]){const option=new Option(label,value);option.disabled=!item.review_id&&!['not_selected','selected','rejected'].includes(value);select.append(option);}select.value=item.publishing_status;
    select.onchange=guarded(async()=>{try{await api(`/publishing/${item.generation_id}`,{status:select.value});item.publishing_status=select.value;item.flags.publishing_status=select.value;item.flags.distribution=['selected','published'].includes(select.value);if(state.track?.id===item.generation_id)await refreshFlags();toast('Distribution state saved.');}catch(error){select.value=item.publishing_status;throw error;}});
    const bookmark=document.createElement('button');bookmark.className='quiet-button candidate-bookmark';const paintBookmark=()=>{bookmark.textContent=item.flags.bookmarked?'★ Bookmarked':'☆ Bookmark';bookmark.setAttribute('aria-pressed',String(item.flags.bookmarked));};paintBookmark();bookmark.onclick=guarded(async()=>{bookmark.disabled=true;try{item.flags=await api(`/tracks/${item.generation_id}/flags`,{bookmarked:!item.flags.bookmarked});paintBookmark();if(state.track?.id===item.generation_id)await refreshFlags();if($('selectionBookmarks').value==='only'&&!item.flags.bookmarked)await loadCandidates();}finally{bookmark.disabled=false;}});
    const details=document.createElement('button');details.className='quiet-button';details.textContent='Reviews / Reason';details.hidden=!item.review_id;details.onclick=guarded(()=>showReviews(item.generation_id));
    const reference=document.createElement('button');reference.className='quiet-button';reference.textContent='Reference';reference.onclick=guarded(async()=>{const value=prompt('Optional external publishing reference',item.external_reference||'');if(value!==null){await api(`/publishing/${item.generation_id}`,{status:item.publishing_status,external_reference:value});item.external_reference=value;toast('Reference saved.');}});
    actions.append(listen,reassess,select,bookmark,details,reference);card.append(check,title,actions);$('candidateList').append(card);
  }
}
$('selectionBookmarks').onchange=guarded(async()=>{if($('selectionBookmarks').value==='only'){$('selectionBucket').value='';$('selectionStatus').value='';}state.offset=0;await loadCandidates();});
for(const id of ['selectionBucket','selectionStatus'])$(id).onchange=guarded(async()=>{if(id==='selectionStatus'&&$('selectionStatus').value==='selected')$('selectionBucket').value='';state.offset=0;await loadCandidates();});
let songTimer;$('selectionSong').oninput=()=>{clearTimeout(songTimer);songTimer=setTimeout(guarded(async()=>{state.offset=0;await loadCandidates();}),300);};
$('previousPage').onclick=guarded(async()=>{state.offset=Math.max(0,state.offset-50);await loadCandidates();});$('nextPage').onclick=guarded(async()=>{state.offset+=50;await loadCandidates();});
$('compare').onclick=guarded(async()=>{const [a,b]=state.chosen;if(a.song_id!==b.song_id)throw new Error('Choose two versions of the same song.');state.comparison=await api('/comparisons',{generation_a:a.generation_id,generation_b:b.generation_id});$('compareTitle').textContent=state.comparison.title;$('compareResult').textContent='';$('compareDialog').showModal();});
async function playComparison(slot){if(!state.comparison)return;const time=state.context?.comparison===state.comparison.id?audio.currentTime:0;audio.pause();state.context={comparison:state.comparison.id,slot};audio.src=`/api/comparisons/${state.comparison.id}/${slot}/audio?format=${state.format}`;audio.load();await new Promise(resolve=>audio.addEventListener('loadedmetadata',resolve,{once:true}));audio.currentTime=Math.min(time,Math.max(0,audio.duration-.1));await audio.play();}
$('playA').onclick=guarded(()=>playComparison('a'));$('playB').onclick=guarded(()=>playComparison('b'));$('comparePause').onclick=()=>audio.pause();
$('compareDialog').addEventListener('close',()=>{if(state.context?.comparison){audio.pause();if(state.track)setMode(state.mode==='range'?'full':state.mode);}});
for(const button of document.querySelectorAll('[data-prefer]'))button.onclick=guarded(async()=>{const result=await api(`/comparisons/${state.comparison.id}/preference`,{preferred:button.dataset.prefer});$('compareResult').textContent=result.preferred_generation_id?`Preference saved for version ${result.preferred_generation_id.slice(0,6)}. Original ratings are unchanged.`:'No preference saved. Original ratings are unchanged.';});
$('loginForm').onsubmit=async event=>{event.preventDefault();try{await api('/login',{password:$('password').value});$('password').value='';$('loginDialog').close();await boot();}catch(error){$('loginError').textContent=error.message;}};
$('nextPoint').onclick=guarded(async()=>{
  const position=sourceTime(),points=state.preview?.points?.length?state.preview.points:state.preview?.markers||[];
  const point=points.find(p=>(p.start+p.end)/2>position+1)||points[0];if(!point)return;
  const center=(point.start+point.end)/2;setMode('range',{start:Math.max(0,center-2),end:Math.min(state.track.duration,center+3)});await audio.play();
});
document.addEventListener('keydown',guarded(async event=>{
  if(event.repeat||event.ctrlKey||event.altKey||event.metaKey||event.target.closest('input,textarea,select,[contenteditable=true]')||document.querySelector('dialog[open]')||state.view!=='review'||!state.track||state.selectionListen||state.submitting||state.advancePending||state.reservationLost)return;
  if(/^[0-5]$/.test(event.key)){event.preventDefault();document.querySelector(`[data-dimension="${DIMENSIONS[state.ratingIndex]}"] [data-score="${event.key}"]`).click();}
  else if(['ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();state.ratingIndex=(state.ratingIndex+(event.key==='ArrowDown'?1:3))%4;document.querySelectorAll('.rating').forEach((row,i)=>row.classList.toggle('keyboard-active',i===state.ratingIndex));}
  else if(event.code==='Space'&&!event.target.closest('button,a')){event.preventDefault();await playPause();}
  else if(event.code==='ArrowLeft'){event.preventDefault();audio.currentTime=Math.max(0,audio.currentTime-5);}
  else if(event.code==='ArrowRight'){event.preventDefault();audio.currentTime=Math.min(audio.duration||0,audio.currentTime+5);}
  else if(event.key.toLowerCase()==='r'){event.preventDefault();audio.currentTime=0;}
  else if(event.key.toLowerCase()==='n'){event.preventDefault();$('skip').click();}
  else if(event.key.toLowerCase()==='b'){event.preventDefault();$('back').click();}
  else if(event.key.toLowerCase()==='t'){event.preventDefault();$('nextPoint').click();}
  else {const mode={f:'fast',q:'quick',s:'standard',u:'full'}[event.key.toLowerCase()];if(mode&&modeAvailable(mode)){event.preventDefault();setMode(mode);}}
}));
new ResizeObserver(drawWave).observe(canvas);
function loseReservation(message='Your reservation expired. Resume your queue before rating.'){
  state.reservationLost=true;audio.pause();clearTimeout(prefetchTimer);paintRatings();$('skip').disabled=true;$('resumeQueue').hidden=false;$('reviewMessage').textContent=message;
}
async function heartbeat(){
  if(!state.reviewer||$('reviewerDialog').open||$('loginDialog').open||state.submitting)return;
  const current=state.track&&!state.track.correction&&!state.selectionListen?state.track.id:null;
  const result=await api('/reviewer/heartbeat',{current_id:current});paintCounts(result.counts);if(current&&!result.valid_current&&!state.advancePending)loseReservation();
}
async function chooseReviewer(name){
  if(state.submitting||state.advancePending||state.flagSaving||reviewerChanging)return;
  reviewerChanging=true;const buttons=[...$('reviewerForm').querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try{const result=await api('/reviewer/select',{name});state.canBack=false;await loadTrack(null);state.reviewer=result.reviewer;state.reviewerId=result.reviewer.id;localStorage.setItem('gbr-reviewer-id',state.reviewerId);$('reviewerDialog').close();showView('review');await boot();}
  finally{reviewerChanging=false;buttons.forEach(button=>button.disabled=false);}
}
async function showReviewerDialog(names=null){
  if(state.submitting||state.advancePending||state.flagSaving)return;
  const reviewers=names||(await api('/bootstrap')).reviewers;$('knownReviewers').replaceChildren();$('reviewerError').textContent='';$('newReviewerName').value='';$('cancelReviewer').hidden=!state.reviewer;
  for(const reviewer of reviewers){const button=document.createElement('button');button.type='button';button.textContent=reviewer.display_name;button.onclick=guarded(()=>chooseReviewer(reviewer.display_name));$('knownReviewers').append(button);}
  if(!$('reviewerDialog').open)$('reviewerDialog').showModal();
}
$('reviewerForm').onsubmit=async event=>{event.preventDefault();try{await chooseReviewer($('newReviewerName').value);}catch(error){$('reviewerError').textContent=error.message;}};
$('reviewerDialog').addEventListener('cancel',event=>{if(!state.reviewer)event.preventDefault();});
$('changeUser').onclick=guarded(()=>showReviewerDialog());
$('resumeQueue').onclick=guarded(()=>nextTrack(false));
window.addEventListener('focus',()=>heartbeat().catch(()=>{}));
async function boot(){
  const b=await api('/bootstrap');if(!b.authenticated){if(!$('loginDialog').open)$('loginDialog').showModal();return;}
  reviewTags.configure(b.review_tags||[]);state.canConfigure=b.can_configure;$("reviewImportLabel").hidden=!state.canConfigure;$('generationTab').hidden=!state.canConfigure;state.reviewer=b.reviewer;if(!b.reviewer){state.reviewerId='';localStorage.removeItem('gbr-reviewer-id');await showReviewerDialog(b.reviewers);return;}$('reviewerBar').hidden=false;$('reviewerName').textContent=b.reviewer.display_name;clearInterval(heartbeatTimer);heartbeatTimer=setInterval(()=>heartbeat().catch(()=>{}),b.heartbeat_seconds*1000);state.durations=b.durations;if(b.queue){state.queue={song:'',genre:'',directory:'',order:b.queue.order_by,direction:b.queue.direction,...JSON.parse(b.queue.filters_json)};}
  audio.volume=Number(localStorage.getItem('gbr-volume')||50)/100;$('volume').value=audio.volume*100;
  await nextTrack();if('serviceWorker' in navigator&&isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
function newSubmissionId(){return globalThis.crypto?.randomUUID?.()||Array.from(globalThis.crypto.getRandomValues(new Uint32Array(4)),value=>value.toString(16)).join('-');}
function paintFlags(){
  const busy=state.submitting||state.advancePending||state.flagSaving;
  const autoReject=!state.draftFlags.distribution&&!state.draftFlags.bookmarked&&Object.values(state.scores).some(score=>score<=3);
  for(const [id,key] of [['distributionFlag','distribution'],['bookmarkFlag','bookmarked'],['rejectFlag','rejected']]){
    $(id).disabled=busy||!state.track||(id!=='bookmarkFlag'&&state.flags?.publishing_status==='published');
    $(id).setAttribute('aria-pressed',String(Boolean(state.draftFlags[key]||(key==='rejected'&&autoReject))));
  }
  $('flagSummary').textContent=autoReject?(state.flags?.rejected?'Rejected · score 3 or below':'Will reject on save · score 3 or below'):state.draftFlags.distribution||state.draftFlags.bookmarked?'Protected from automatic rejection':'';
}
async function saveFlags(){
  if(!state.track||state.submitting||state.advancePending||state.flagSaving)return;
  const gid=state.track.id,rid=state.reviewerId,sequence=loadSequence;
  const body={distribution:state.draftFlags.distribution,bookmarked:state.draftFlags.bookmarked,rejected:Boolean(state.draftFlags.rejected),expected_publishing_status:state.flags?.publishing_status||'not_selected'};
  state.flagSaving=true;paintRatings();$('flagMessage').textContent='Saving flags…';$('retryFlags').hidden=true;
  const task=(async()=>{try{const flags=await api(`/tracks/${gid}/flags`,body);if(sequence!==loadSequence||rid!==state.reviewerId)return;state.flagError=false;state.flags=flags;state.draftFlags={...flags};state.track.flags=flags;$('flagMessage').textContent='Flags saved. Your review is not submitted.';$('refreshFlags').hidden=true;}catch(error){if(sequence===loadSequence){state.flagError=true;$('flagMessage').textContent=`Flags not saved: ${error.message} Your choices are retained; retry flags or submit the review.`;$('retryFlags').hidden=false;$('refreshFlags').hidden=error.code!=='flags_changed';}throw error;}finally{if(sequence===loadSequence){state.flagSaving=false;paintRatings();}}})();
  state.flagWrite=task;return task;
}
async function refreshFlags(quiet=false){
  if(!state.track||state.submitting||state.flagSaving)return;
  const gid=state.track.id,rid=state.reviewerId,sequence=loadSequence;
  const flags=await api(`/tracks/${gid}/flags`);if(sequence!==loadSequence||rid!==state.reviewerId)return;
  state.flagError=false;state.flags=flags;state.draftFlags={...flags};state.track.flags=flags;$('refreshFlags').hidden=true;$('retryFlags').hidden=true;if(!quiet)$('flagMessage').textContent='Current track flags loaded.';paintFlags();
}
for(const [id,key] of [['distributionFlag','distribution'],['bookmarkFlag','bookmarked'],['rejectFlag','rejected']])$(id).onclick=guarded(async()=>{
  if(state.flagSaving||state.submitting||!state.track)return;
  state.draftFlags[key]=!state.draftFlags[key];
  if(state.draftFlags[key]){if(key==='rejected'){state.draftFlags.distribution=false;state.draftFlags.bookmarked=false;}else state.draftFlags.rejected=false;}
  paintFlags();await saveFlags();
});$('retryFlags').onclick=guarded(saveFlags);$('refreshFlags').onclick=guarded(refreshFlags);
$('autoSubmit').checked=state.autoSubmit;
$('autoSubmit').onchange=()=>{state.autoSubmit=$('autoSubmit').checked;localStorage.setItem('gbr-auto-submit',String(state.autoSubmit));paintRatings();if(state.autoSubmit&&state.track&&!state.selectionListen&&!state.track.correction&&DIMENSIONS.every(d=>state.scores[d]!==undefined))guarded(submitReview)();};
$('submitReview').onclick=guarded(submitReview);$('retryReview').onclick=guarded(submitReview);
async function showReviews(gid){
  const result=await api(`/tracks/${gid}/reviews`);$('reviewDetails').replaceChildren();
  if(!result.reviews.length){const p=document.createElement('p');p.textContent='This recording has no saved review yet.';$('reviewDetails').append(p);}
  for(const review of result.reviews){const section=document.createElement('section');section.className='review-details-entry';const heading=document.createElement('h3');heading.textContent=`${review.reviewer_name} · revision ${review.assessment_revision}`;const date=document.createElement('p');date.className='subtle small';date.textContent=`${new Date(review.created_at).toLocaleString()} · version ${review.generation_id.slice(0,6)} · ${review.mode}`;const scores=document.createElement('p');scores.textContent=`Audio Quality ${review.audio_quality} · Composition ${review.composition} · Consistency ${review.consistency} · Realism ${review.realism}`;const label=document.createElement('strong');label.textContent='Reason';const reason=document.createElement('p');reason.className='saved-reason';reason.textContent=review.reason||'No reason supplied';const flags=document.createElement('p');flags.className='subtle small';const snapshot=JSON.parse(review.flags_json||'{}');flags.textContent=Object.keys(snapshot).length?`At submission: ${snapshot.distribution?'flagged for distribution':'not flagged for distribution'} · ${snapshot.bookmarked?'bookmarked':snapshot.rejected?'rejected':'not bookmarked'}`:'Flags were not recorded for this historical review.';section.append(heading,date,scores);savedTags(section,review.tags_json);section.append(label,reason,flags);$('reviewDetails').append(section);}
  $('reviewsDialog').showModal();
}
$('viewReviews').onclick=guarded(()=>showReviews(state.track.id));
let directoryTarget='input',directoryChoice=null,directorySequence=0;
async function browseDirectory(path){
  const sequence=++directorySequence;
  $('directoryError').textContent='';$('useDirectory').disabled=true;
  try{const result=await api('/directories'+(path?'?path='+encodeURIComponent(path):''));if(sequence!==directorySequence)return;directoryChoice=result.path;$('directoryPath').value=result.path;$('directoryRoots').replaceChildren();for(const root of result.roots){const button=document.createElement('button');button.type='button';button.className='quiet-button';button.textContent=root;button.onclick=()=>browseDirectory(root);$('directoryRoots').append(button);}$('directoryUp').disabled=!result.parent;$('directoryUp').onclick=()=>browseDirectory(result.parent);$('directoryList').replaceChildren();for(const folder of result.directories){const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=folder.name;button.onclick=()=>browseDirectory(folder.path);$('directoryList').append(button);}if(!result.directories.length){const p=document.createElement('p');p.className='subtle';p.textContent='No subfolders.';$('directoryList').append(p);}$('useDirectory').disabled=false;}catch(error){$('directoryError').textContent=error.message;}
}
async function openDirectory(target){directoryTarget=target;directoryChoice=null;$('directoryDialog').showModal();await browseDirectory();}
$('browseInput').onclick=guarded(()=>openDirectory('input'));$('browseArchive').onclick=guarded(()=>openDirectory('archive'));
$('directoryPathForm').onsubmit=event=>{event.preventDefault();browseDirectory($('directoryPath').value);};
$('useDirectory').onclick=()=>{if(!directoryChoice)return;if(typeof directoryTarget==='function'){directoryTarget(directoryChoice);}else if(directoryTarget==='input'){const folders=$('inputFolders').value.split('\n').map(value=>value.trim()).filter(Boolean);if(!folders.includes(directoryChoice))folders.push(directoryChoice);$('inputFolders').value=folders.join('\n');}else $('archiveFolder').value=directoryChoice;$('directoryDialog').close();};
const humanBytes=bytes=>`${(bytes/1024**3).toFixed(2)} GiB`;
async function cacheReport(){const report=await api('/playback-cache');$('cacheSummary').textContent=`${humanBytes(report.bytes)} in ${report.files} playback files · WAV: ${humanBytes(report.wav_bytes)} · ${report.untracked_files} older untracked files kept. Automatic cleanup is off.`;}
$('cachePreview').onclick=guarded(async()=>{$('cacheResult').textContent='Checking cached files and original audio…';$('cacheClean').disabled=true;const result=await api('/playback-cache/cleanup',{older_than_days:Number($('cacheDays').value),dry_run:true});$('cacheResult').textContent=`Eligible: ${result.eligible_files} files (${humanBytes(result.eligible_bytes)}). Protected: ${result.protected_files}; unavailable/changed originals: ${result.unavailable_or_changed_source}; untracked files retained: ${result.untracked_files_retained}.`;$('cacheClean').disabled=result.eligible_files===0;});
$('cacheDays').oninput=()=>{$('cacheClean').disabled=true;};
$('cacheClean').onclick=guarded(async()=>{$('cacheClean').disabled=true;$('cacheResult').textContent='Checking originals and removing eligible derived previews…';const result=await api('/playback-cache/cleanup',{older_than_days:Number($('cacheDays').value),dry_run:false});$('cacheResult').textContent=`Removed ${result.removed_files} files (${humanBytes(result.removed_bytes)}). ${result.errors.join(' ')}`;await cacheReport();});

ratingButtons();guarded(boot)();
setInterval(async()=>{try{if($('loginDialog').open||$('reviewerDialog').open||!state.reviewer||state.reservationLost)return;if($('settingsDialog').open)await status();if(state.view==='review'&&!state.submitting){if(state.advancePending)await nextTrack(true);else if(!state.track)await nextTrack();else if(!state.preview?.ready)await refreshPreview();else if(state.waitForRetune||!modeAvailable('fast')){const result=await api(`/tracks/${state.track.id}/analysis-state`);if(result.analysis_id!==state.preview.analysis_id)await refreshPreview();state.waitForRetune=['pending','analysing','retune_pending','retuning'].includes(result.state);}}}catch{}},5000);

setInterval(()=>{if(state.view==='review'&&state.track&&!state.submitting&&!state.advancePending&&!state.flagSaving&&!state.flagError&&!state.saveError&&!document.querySelector('dialog[open]'))refreshFlags(true).catch(()=>{});},5000);


function paintCounts(counts,target='queueCount'){
  if(!counts)return;
  $(target).textContent=`${counts.remaining.toLocaleString()} left · ${counts.reviewed.toLocaleString()} / ${counts.total.toLocaleString()} reviewed${counts.skipped?' · '+counts.skipped+' skipped':''}`;
}
async function previewQueueCounts(){
  const query=new URLSearchParams({directory:$('filterDirectory').value,song:$('filterSong').value,genre:$('filterGenre').value});
  paintCounts(await api('/queue/counts?'+query),'queuePreviewCount');
}
$('filterDirectory').onchange=guarded(previewQueueCounts);
let countTimer;for(const id of ['filterSong','filterGenre'])$(id).oninput=()=>{clearTimeout(countTimer);countTimer=setTimeout(guarded(previewQueueCounts),250);};

$('reviewImport').addEventListener('change',guarded(async()=>{
  const file=$('reviewImport').files[0];if(!file)return;
  $('reviewImport').disabled=true;$('reviewImportResult').textContent='Importing…';
  try{const result=await api('/import/reviews',{payload:JSON.parse(await file.text())});$('reviewImportResult').textContent=`${result.imported} reviews imported · ${result.already_present} already present`;const b=await api('/bootstrap');reviewTags.configure(b.review_tags||[]);await heartbeat();}
  catch(error){$('reviewImportResult').textContent=`Import failed: ${error.message}`;throw error;}
  finally{$('reviewImport').value='';$('reviewImport').disabled=false;}
}));
