import {initLibrary} from '/tools/preparation/library.js';
import {initPreparation} from "/tools/preparation/preparation.js";
import {initPublishing} from "/tools/preparation/publishing.js";
import {initYamlEditor} from "/tools/yaml-editor/editor.js";
import {initGeneration} from "/tools/generation/generation.js";
import {initWorkers} from "/tools/generation/workers.js";
import {initWriting} from "/tools/writing/writing.js";
import {toggleChoices,selectChoices} from "/static/choices.js";
import {ReviewTags, savedTags} from "/static/tags.js";
import {formatTime as fmt, initPlayer} from "/static/player.js";
const $=id=>document.getElementById(id);
const reviewTags=new ReviewTags($("tagGroups"),$("clearTags"));
const audio=$('audio'), canvas=$('waveform');
const DIMENSIONS=['audio_quality','composition','consistency','realism'];
const state={track:null,preview:null,scores:{},view:'listen',source:'review',mode:'quick',loadingReview:false,durations:{fast:10,quick:20,standard:50},ratingIndex:0,lastSampleMode:'quick',waitForRetune:false,analysisIds:new Set(),submitting:false,advancePending:false,autoSubmit:localStorage.getItem('gbr-auto-submit')!=='false',saveError:false,flagSaving:false,flagError:false,flags:null,draftFlags:{distribution:false,bookmarked:false,rejected:false},flagWrite:Promise.resolve(),submissionId:'',reservationLost:false,reviewerId:localStorage.getItem('gbr-reviewer-id')||'',reviewer:null,selection:null,selecting:false,canBack:false,canConfigure:false,listened:new Set(),heard:0,fullCompleted:false,fullIntervals:[],lastTime:0,comparison:null,chosen:[],queue:{order:'random',direction:'asc',song:'',genre:'',directory:''}};
let toastTimer, loadSequence=0, prefetchTimer, heartbeatTimer, reviewerChanging=false;
let reviewAudio = null;
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,5500);}
async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-GBR-Request':'1'},body:JSON.stringify(body)};
  options.headers={...(options.headers||{}),...(state.reviewerId?{'X-GBR-Reviewer':state.reviewerId}:{})};
  const response=await fetch('/api'+path,options);
  if(!response.ok){let data;try{data=await response.json();}catch{data={detail:response.statusText};}if(response.status===401&&path!=='/login'&&!$('loginDialog').open)$('loginDialog').showModal();const error=new Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail));error.code=data.code;error.status=response.status;throw error;}
  return response.json();
}
function guarded(fn){return (...args)=>Promise.resolve(fn(...args)).catch(error=>toast(error.message));}
function recordingURL(id, mode, format, extra = {}) {
  return `/api/playback/${encodeURIComponent(id)}?` + new URLSearchParams({mode, format, ...extra});
}
function reviewVisible() {
  return state.view === 'listen' && state.source === 'review';
}
function canChangeReview() {
  return !state.loadingReview && !state.submitting && !state.advancePending && !state.flagSaving && !reviewerChanging;
}
async function changeReview(action) {
  if (!canChangeReview()) return;
  state.loadingReview = true;
  paintRatings();
  try {
    await action();
  } finally {
    state.loadingReview = false;
    paintRatings();
  }
}
function paintPlayback(entry, playing) {
  const inReview = entry && entry === reviewAudio;
  const reviewPlay = $('reviewPlay');
  reviewPlay.disabled = !reviewAudio;
  reviewPlay.textContent = inReview && playing ? 'Pause review audio' : 'Play review audio';
  const selector = entry?.key ? `[data-play-key="${CSS.escape(entry.key)}"]` : '';
  for (const row of document.querySelectorAll(`.listen-row[data-current]${selector ? ', ' + selector : ''}`)) {
    const active = row.dataset.playKey === entry?.key;
    if (active) row.dataset.current = 'true';
    else delete row.dataset.current;
    const button = row.querySelector('.listen-play');
    button.dataset.playing = String(active && playing);
    button.setAttribute('aria-label', (active && playing ? 'Pause ' : 'Play ') + button.dataset.title);
    button.title = button.getAttribute('aria-label');
  }
}
const player = initPlayer({toast, onChange: paintPlayback});
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty('--player-height', entry.target.offsetHeight + 'px');
}).observe($('nowPlaying'));
function ratingButtons(){
  for(const row of document.querySelectorAll('.rating')){
    for(let score=0;score<=5;score++){
      const button=document.createElement('button');button.type='button';button.textContent=score;button.dataset.score=score;button.setAttribute('aria-pressed','false');const group=[...row.querySelectorAll(".rating-anchor")].find(item=>score>=Number(item.dataset.scoreMin)&&score<=Number(item.dataset.scoreMax));const anchor=group?.querySelector(".anchor-label")?.textContent;button.setAttribute('aria-label',`${row.querySelector('.rating-label span').textContent}: ${score} out of 5${anchor?' — '+anchor:''}`);
      button.addEventListener('click',guarded(async()=>{
        if(state.loadingReview||state.submitting||state.advancePending||state.reservationLost||!state.track)return;
        state.scores[row.dataset.dimension]=score;const missing=DIMENSIONS.findIndex(d=>state.scores[d]===undefined);if(missing>=0)state.ratingIndex=missing;paintRatings();
        if(state.autoSubmit&&DIMENSIONS.every(d=>state.scores[d]!==undefined))await submitReview();
      }));row.querySelector('.rating-values').append(button);
    }
  }
  for(const button of document.querySelectorAll('.help'))button.onclick=()=>toast(button.title);
}
function paintRatings(){
  const busy = !canChangeReview();
  $('changeUser').disabled = busy;
  $('back').disabled = !state.canBack || busy;
  $('skip').disabled = !state.track || busy || state.reservationLost;
  $('queueButton').disabled = busy;
  $('resumeQueue').disabled = busy;
  const complete = DIMENSIONS.every(d => state.scores[d] !== undefined);
  $('submitReview').hidden = state.autoSubmit || !state.track;
  $('submitReview').disabled = !complete || busy || state.reservationLost;
  $('retryReview').hidden = !state.autoSubmit || !state.saveError || state.reservationLost || state.advancePending;
  $('retryReview').disabled = busy;
  $('reason').disabled = state.loadingReview || state.submitting || state.advancePending;
  $('autoSubmit').disabled = $('reason').disabled;
  paintFlags();
  reviewTags.setDisabled(!state.track || $('reason').disabled || state.reservationLost);
  for (const button of document.querySelectorAll('[data-open-review]')) button.disabled = busy;
  player.refresh();
  const missing=DIMENSIONS.findIndex(d=>state.scores[d]===undefined);if(missing>=0&&state.scores[DIMENSIONS[state.ratingIndex]]!==undefined)state.ratingIndex=missing;
  document.querySelectorAll('.rating').forEach((row,i)=>row.classList.toggle('keyboard-active',i===state.ratingIndex));
  for(const row of document.querySelectorAll('.rating'))for(const button of row.querySelectorAll('.rating-values button')){button.setAttribute('aria-pressed',String(state.scores[row.dataset.dimension]===Number(button.dataset.score)));button.disabled=state.loadingReview||state.submitting||state.advancePending||state.reservationLost;}
}
async function submitReview(){
  if(state.loadingReview||state.submitting||state.advancePending||state.reservationLost||!state.track||!DIMENSIONS.every(d=>state.scores[d]!==undefined))return;
  let saved=false;state.saveError=false;state.submitting=true;paintRatings();$('reviewMessage').textContent='Saving…';const gid=state.track.id;
  try{
    const modes=[...state.listened];const mode=modes.length>1?'mixed':modes[0]||state.track.previous_mode||state.mode;
    await state.flagWrite.catch(()=>{});
    await api(`/tracks/${gid}/review`,{...state.scores,mode,tags:reviewTags.value(),reason:$('reason').value,distribution:state.draftFlags.distribution,bookmarked:state.draftFlags.bookmarked,rejected:Boolean(state.draftFlags.rejected),expected_publishing_status:state.flags?.publishing_status||'not_selected',submission_id:state.submissionId,expected_review_id:state.track.review_id||null,reassessment:Boolean(state.track.reassessment),listening:{modes_played:modes,played_seconds:Math.round(state.heard*10)/10,complete_full_playback:state.fullCompleted,analysis_id:reviewAudio?.analysis_id||state.preview?.analysis_id||null,analyses_played:[...state.analysisIds],selected_range:state.selection,playback_speed:audio.playbackRate}});
    saved=true;selectionDirty=true;if(player.current===reviewAudio)player.pause();$('reviewMessage').textContent='Saved';await nextTrack($('autoplayNext').checked);state.submitting=false;paintRatings();
  }catch(error){state.submitting=false;state.advancePending=saved;state.saveError=!saved;if(error.code==='flags_changed')$('refreshFlags').hidden=false;if(error.code==='submission_saved'){state.canBack=true;$('back').disabled=false;state.saveError=false;}if(['reservation_lost','already_reviewed','other_reviewer'].includes(error.code))loseReservation(error.message);paintRatings();$('reviewMessage').textContent=saved?'Review saved. Retrying the next track…':state.reservationLost?error.message:`Not saved: ${error.message} Your entries are retained. ${error.code==='flags_changed'?'Refresh flags, then retry.':error.code==='submission_saved'?'Use Back to open the saved review.':'Retry save or choose a rating again.'}`;if(saved)throw new Error('Review saved; retrying the next track.');throw error;}
}
function preferredMode(){const mode=localStorage.getItem('gbr-review-mode')||'quick';return ['fast','quick','standard','full'].includes(mode)?mode:'quick';}
function modeAvailable(mode){return mode==='full'||Boolean(state.preview?.ready&&state.preview.segments.some(s=>s.mode===mode));}
function prepareNext(){
  clearTimeout(prefetchTimer);const gid=state.track?.id;if(!gid||state.track.correction||state.reservationLost)return;
  const mode=preferredMode();
  prefetchTimer=setTimeout(()=>{if(state.track?.id===gid)api('/queue/prefetch',{current_id:gid,mode,format:player.format}).catch(()=>{});},200);
}
async function nextTrack(autoplay = false) {
  const reviewerId = state.reviewerId;
  const result = await api('/queue/next?include_preview=true');
  if (reviewerId !== state.reviewerId) return;
  state.emptyReason = result.reason;
  state.canBack = result.can_back;
  paintCounts(result.counts);
  $('returnSkipped').hidden = !result.has_skips;
  const previous = state.track;
  await loadTrack(result.track, result.preview, {autoplay, replace: true});
  if (previous || result.track || !reviewList) reviewListDirty = true;
  if (reviewVisible() && reviewListDirty) refreshReviewList().catch(error => toast(error.message));
}
async function previousReview() {
  const result = await api('/queue/back');
  if (result.track) await loadTrack(result.track, null, {replace: true});
}
async function advanceReview(autoplay) {
  const track = state.track;
  if (!track || state.reservationLost) return;
  if (!track.correction) await api(`/tracks/${track.id}/skip`, {});
  try {
    await nextTrack(autoplay);
  } catch (error) {
    if (!track.correction) {
      state.advancePending = true;
      $('reviewMessage').textContent = 'Track skipped. Retrying the next review…';
    }
    throw error;
  }
}
async function loadTrack(track, initialPreview = null, {autoplay = false, replace = false} = {}) {
  const sequence = ++loadSequence;
  clearTimeout(prefetchTimer);
  const previousAudio = reviewAudio;
  const canReplace = () => player.current === previousAudio && (replace || audio.paused);
  reviewAudio = null;
  state.track = track;
  state.preview = null;
  state.mode = preferredMode();
  state.scores = {...track?.scores};
  state.ratingIndex = 0;
  state.submissionId = newSubmissionId();
  state.saveError = false;
  state.advancePending = false;
  state.reservationLost = false;
  state.flagSaving = false;
  state.flagError = false;
  state.flagWrite = Promise.resolve();
  state.flags = track?.flags || {distribution: false, bookmarked: false, publishing_status: 'not_selected'};
  state.draftFlags = {...state.flags};
  state.selection = null;
  state.selecting = false;
  state.analysisIds = new Set();
  state.listened = new Set();
  state.heard = 0;
  state.fullCompleted = false;
  state.fullIntervals = [];
  state.lastTime = 0;
  pointerStart = null;

  renderTechnicalChecks(null);
  reviewTags.load(track?.tags || []);
  $('reason').value = track?.reason || '';
  $('viewReviews').hidden = !track?.correction;
  $('flagMessage').textContent = '';
  $('retryFlags').hidden = $('refreshFlags').hidden = $('resumeQueue').hidden = true;
  $('playRange').hidden = true;
  $('selectRange').textContent = 'Select range';
  canvas.classList.remove('selecting');
  $('spectralWrap').hidden = true;
  $('spectralToggle').setAttribute('aria-expanded', 'false');
  $('reviewCard').hidden = !track;
  $('empty').hidden = Boolean(track);
  $('correctionBadge').hidden = !track?.correction;
  $('skip').textContent = track?.correction ? 'Return to queue' : 'Skip →';
  $('reviewMessage').textContent = track?.correction ? 'Adjust tags or Reason first, then choose a rating to save.' : '';
  paintRatings();
  if (!track) {
    if (canReplace() && player.current) player.load(null);
    $('emptyText').textContent = {
      first_pass_complete: 'Every recording has a review.',
      reserved: 'Other reviewers have reserved the remaining recordings.',
      all_skipped: 'Return skipped tracks for another look.',
      no_match: 'No unreviewed recordings match your queue filters.',
      no_tracks: 'Check input folders, rescan or adjust your queue.',
    }[state.emptyReason] || 'No recordings are available.';
    document.querySelector('#empty h1').textContent = state.emptyReason === 'first_pass_complete' ? 'First pass complete' :
      state.emptyReason === 'reserved' ? 'Waiting for recordings' : 'Review queue is empty';
    drawWave();
    return;
  }

  $('trackTitle').textContent = track.title;
  $('trackGenre').textContent = track.genre;
  $('compositionGenre').textContent = track.cluster && track.cluster !== track.genre ? `${track.cluster} / ${track.genre}` : track.genre;
  $('snippets').replaceChildren();
  $('waveStatus').hidden = false;
  $('waveStatus').textContent = 'Preparing waveform…';
  $('nextPoint').disabled = $('spectralToggle').disabled = true;
  for (const button of document.querySelectorAll('[data-mode]')) button.disabled = true;
  try {
    await refreshPreview(sequence, initialPreview);
  } catch (error) {
    if (sequence === loadSequence) toast(error.message);
  }
  if (sequence !== loadSequence) return;
  const playing = player.current;
  if (playing?.source === 'queue' && playing.id === track.id) {
    setMode('full', {}, {remember: false, activate: false, reuse: playing});
    state.lastTime = audio.currentTime;
    if (!audio.paused) {
      state.listened.add('full');
      if (reviewAudio.analysis_id) state.analysisIds.add(reviewAudio.analysis_id);
    }
    return;
  }
  const mode = modeAvailable(state.mode) ? state.mode : modeAvailable('quick') ? 'quick' : 'full';
  const activate = !playing || canReplace();
  await setMode(mode, {}, {remember: false, activate, autoplay: activate && autoplay && !track.correction});

}
async function refreshPreview(sequence=loadSequence,initialPreview=null){
  if(!state.track)return;const gid=state.track.id;const preview=initialPreview||await api(`/tracks/${gid}/preview`);
  if(sequence!==loadSequence||state.track?.id!==gid)return;
  const wasReady=state.preview?.ready;state.preview=preview;renderTechnicalChecks(preview.technical_checks);
  $('waveStatus').hidden=preview.ready;$('waveStatus').textContent=state.track.analysis_state==='failed'?'Waveform unavailable · full playback available':'Preparing waveform…';
  for(const button of document.querySelectorAll('[data-mode]'))button.disabled=!modeAvailable(button.dataset.mode);
  $('spectralToggle').disabled=!preview.ready;$('nextPoint').disabled=!preview.ready||!preview.markers.length;
  if(preview.ready){document.querySelector('.freq-top').textContent=`${Math.round(preview.frequency_axis.max_hz/1000)} kHz`;drawSnippets();drawWave();if(!wasReady&&state.heard===0&&reviewAudio&&(player.current!==reviewAudio||audio.paused)){const wanted=preferredMode();if(modeAvailable(wanted)&&state.mode!==wanted)setMode(wanted,{}, {remember:false, activate:player.current===reviewAudio});}prepareNext();}
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
    if(timed){item.type='button';item.onclick=guarded(async()=>{await setMode('range',{start:Math.max(0,flag.start-2),end:Math.min(state.track.duration,flag.end+2)},{autoplay:true});});}
    flags.append(item);
  }
}
function setMode(mode, extra = {}, {remember = true, activate = true, autoplay = false, position = 0, ranges, reuse} = {}) {
  const track = state.track;
  if (!track) return;
  if (mode !== 'range' && !modeAvailable(mode)) {
    toast('Sampling is being prepared. Full playback is available.');
    return;
  }
  if (mode !== 'range' && remember) localStorage.setItem('gbr-review-mode', mode);
  if (['fast','quick','standard'].includes(mode)) state.lastSampleMode = mode;
  state.mode = mode;
  const description = mode === 'full' ? 'Complete recording' : mode === 'range' ? 'Selected passage' : `${state.durations[mode]}-second overview`;
  const analysisId = state.preview?.analysis_id;
  const params = analysisId ? {analysis_id: analysisId, ...extra} : extra;
  const portions = ranges || (mode === 'range' ? [{start: Number(extra.start), end: Number(extra.end)}] :
    mode === 'full' ? [{start: 0, end: track.duration}] : state.preview.segments.filter(segment => segment.mode === mode));
  const entry = {
    id: track.id, key: `queue:${track.id}`, source: 'review', title: track.title,
    subtitle: `Review queue · ${description}`, kind: mode, ranges: portions,
    duration: portions.reduce((total, range) => total + range.end - range.start, 0),
    analysis_id: analysisId || null,
    url: format => recordingURL(track.id, mode, format, params),
  };
  if (reuse) {
    reviewAudio = Object.assign(reuse, entry, {url: reuse.url});
  } else {
    reviewAudio = entry;
    attachReviewQueue(reviewAudio, reviewList);
  }
  state.lastTime = 0;
  for (const button of document.querySelectorAll('[data-mode]')) {
    button.classList.toggle('active', button.dataset.mode === mode);
    button.disabled = !modeAvailable(button.dataset.mode);
  }
  $('modeDescription').textContent = description;
  drawSnippets();
  const playback = activate ? player.load(reviewAudio, {autoplay, position}) : reuse ? player.updateTrack() : player.refresh();
  updateWavePosition();
  return playback;
}
async function playReview() {
  if (!reviewAudio) return;
  if (player.current === reviewAudio) await player.toggle();
  else await player.load(reviewAudio, {autoplay: true});
}
function drawSnippets(){
  $('snippets').replaceChildren();if(!state.preview?.ready)return;
  const sections=state.preview.segments.filter(s=>s.mode===(['fast','quick','standard'].includes(state.mode)?state.mode:state.lastSampleMode));
  if(state.mode==='full')return;
  for(const [i,section] of sections.entries()){
    const button=document.createElement('button');button.className='snippet';button.textContent=`${i+1} · ${fmt(section.start)}`;button.setAttribute('aria-label',`Replay section ${i+1} starting at ${fmt(section.start)}`);
    button.onclick=guarded(()=>setMode('range',{segment_id:section.id},{ranges:[section],autoplay:true}));$('snippets').append(button);
  }
}
function sourceTime(){
  if(!reviewAudio)return 0;
  let time=player.position(reviewAudio);
  for(const r of reviewAudio.ranges){const length=r.end-r.start;if(time<=length)return r.start+time;time-=length;}
  return reviewAudio.ranges.at(-1)?.end||0;
}
function waveWindow(){const duration=state.track?.duration||1;return {start:0,end:duration,length:duration};}
function drawWave(){
  const rect=canvas.getBoundingClientRect();if(!rect.width)return;
  const dpr=devicePixelRatio||1;canvas.width=Math.round(rect.width*dpr);canvas.height=Math.round(rect.height*dpr);const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const width=rect.width,height=rect.height;ctx.clearRect(0,0,width,height);ctx.fillStyle='#11171d';ctx.fillRect(0,0,width,height);
  if(!state.preview?.ready||!state.track)return;
  const {start,length,end}=waveWindow();const x=time=>(time-start)/length*width;
  for(const range of reviewAudio?.ranges||[]){if(state.mode==='full')break;ctx.fillStyle='#e9ba631a';ctx.fillRect(x(range.start),0,x(range.end)-x(range.start),height);}
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
async function seekSource(time) {
  if (!state.track) return;
  if (player.current === reviewAudio && state.mode === 'full' && audio.readyState >= 1) {
    player.seek(time);
  } else {
    await setMode('full', {}, {position: Math.min(time, state.track.duration), autoplay: player.current === reviewAudio && !audio.paused});
  }
}
let pointerStart=null;
canvas.addEventListener('pointerdown',event=>{if(!state.track)return;const r=canvas.getBoundingClientRect(),w=waveWindow();pointerStart={x:event.clientX,time:w.start+(event.clientX-r.left)/r.width*w.length,selection:state.selecting||event.shiftKey};if(pointerStart.selection)canvas.setPointerCapture(event.pointerId);});
canvas.addEventListener('pointermove',event=>{if(!pointerStart?.selection)return;const r=canvas.getBoundingClientRect(),w=waveWindow(),t=Math.max(0,Math.min(state.track.duration,w.start+(event.clientX-r.left)/r.width*w.length));state.selection={start:Math.min(pointerStart.time,t),end:Math.max(pointerStart.time,t)};drawWave();});
canvas.addEventListener('pointerup',guarded(async event=>{
  if(!pointerStart||!state.track)return;const start=pointerStart;pointerStart=null;const r=canvas.getBoundingClientRect(),w=waveWindow(),t=Math.max(0,Math.min(state.track.duration,w.start+(event.clientX-r.left)/r.width*w.length));
  if(start.selection){state.selection={start:Math.min(start.time,t),end:Math.max(start.time,t)};if(state.selection.end-state.selection.start<.2){state.selection=null;toast('Drag across the waveform to select a passage.');}$('playRange').hidden=!state.selection;drawWave();return;}
  const marker=state.preview?.markers.find(m=>Math.abs(m.start-t)/w.length*r.width<7&&event.clientY-r.top<16);
  if(marker){const beginning=Math.max(0,marker.start-2),ending=Math.min(state.track.duration,Math.max(marker.end+2,beginning+4));await setMode('range',{start:beginning,end:ending},{autoplay:true});}else await seekSource(t);
}));
canvas.addEventListener('pointercancel',()=>pointerStart=null);
$('selectRange').onclick=()=>{state.selecting=!state.selecting;canvas.classList.toggle('selecting',state.selecting);$('selectRange').textContent=state.selecting?'Done selecting':'Select range';};
$('playRange').onclick=guarded(async()=>{if(state.selection)await setMode('range',state.selection,{autoplay:true});});
$('spectralToggle').onclick=()=>{const show=$('spectralWrap').hidden;$('spectralWrap').hidden=!show;$('spectralToggle').setAttribute('aria-expanded',String(show));if(show&&state.track)$('spectrogram').src=`/api/tracks/${state.track.id}/spectrogram`;};
$('spectralWrap').onclick=guarded(async event=>{const r=$('spectralWrap').getBoundingClientRect();await seekSource((event.clientX-r.left)/r.width*state.track.duration);});
function updateWavePosition() {
  if (!reviewVisible()) return;
  if (state.track) $('spectralCursor').style.left = `${sourceTime() / state.track.duration * 100}%`;
  drawWave();
}
audio.addEventListener('timeupdate', () => {
  if (!reviewAudio || player.current !== reviewAudio) return;
  const delta = audio.currentTime - state.lastTime;
  if (!audio.paused && !audio.seeking && delta > 0 && delta < 2) {
    state.heard += delta;
    if (reviewAudio.kind === 'full') state.fullIntervals.push([state.lastTime, audio.currentTime]);
  }
  state.lastTime = audio.currentTime;
  updateWavePosition();
});
audio.addEventListener('seeking', () => {
  if (player.current === reviewAudio) state.lastTime = audio.currentTime;
});
audio.addEventListener('play', () => {
  if (!reviewAudio || player.current !== reviewAudio) return;
  state.lastTime = audio.currentTime;
  state.listened.add(reviewAudio.kind);
  if (reviewAudio.analysis_id) state.analysisIds.add(reviewAudio.analysis_id);
});
audio.addEventListener('ended', () => {
  if (!reviewAudio || player.current !== reviewAudio || reviewAudio.kind !== 'full') return;
  const intervals = [...state.fullIntervals].sort((a,b) => a[0]-b[0]);
  let covered = 0, end = 0;
  for (const interval of intervals) {
    covered += Math.max(0, interval[1] - Math.max(end, interval[0]));
    end = Math.max(end, interval[1]);
  }
  if (covered >= state.track.duration * .95) state.fullCompleted = true;
  updateWavePosition();
});
$('reviewPlay').onclick = guarded(playReview);
for (const button of document.querySelectorAll('[data-mode]')) {
  button.onclick = () => setMode(button.dataset.mode, {}, {activate: !player.current || player.current === reviewAudio});
}

function showView(view, source = state.source) {
  const changed = view !== state.view || source !== state.source;
  if (!changed) return;
  if ($('preparationView').offsetParent !== null && !preparation.canLeave()) {
    throw Error('Save or reload the lyric and lighting draft before leaving');
  }
  const libraryVisible = view === 'listen' && source === 'library';
  if (!libraryVisible) {
    library.close();
    restorePreparation();
  }
  if (view !== 'preparation') preparation.close();
  if (view !== 'publishing') publishing.close();
  if (view !== 'writing') writing.close();
  if (view !== 'generation') for (const preview of $('generationView').querySelectorAll('audio')) preview.pause();
  state.view = view;
  state.source = source;
  for (const name of ['writing','yaml','generation','listen','preparation','publishing']) {
    const active = view === name;
    $(name+'View').hidden = !active;
    $(name+'Tab').classList.toggle('active', active);
    if (active) $(name+'Tab').setAttribute('aria-current', 'page');
    else $(name+'Tab').removeAttribute('aria-current');
  }
  document.querySelector('.listen-content').dataset.source = source;
  for (const name of ['review','selection','library']) {
    $(name+'View').hidden = source !== name;
    if (source === name) $(name+'Tab').setAttribute('aria-current', 'true');
    else $(name+'Tab').removeAttribute('aria-current');
  }
  $('queueView').hidden = source !== 'review';
  $('jumpQueue').hidden = source !== 'review';
  if (reviewVisible()) updateWavePosition();
}
async function showListen(source) {
  const changed = state.view !== 'listen' || state.source !== source;
  showView('listen', source);
  if (source === 'review') {
    if (!state.track && !state.advancePending) await changeReview(() => nextTrack());
    else {
      const list = reviewListDirty || !reviewList ? refreshReviewList() : null;
      await Promise.all([list, !state.flagError && !state.saveError ? refreshFlags(true) : null]);
    }
  } else if (source === 'selection') {
    if (!selectionList || selectionDirty) await loadCandidates();
  } else if (changed) {
    await library.open();
  }
}
$('listenTab').onclick = guarded(() => showListen(state.source));
$('reviewTab').onclick = guarded(() => showListen('review'));
$('selectionTab').onclick = guarded(() => showListen('selection'));
$('libraryTab').onclick = guarded(() => showListen('library'));
document.querySelector('.wordmark').onclick = guarded(event => {
  event.preventDefault();
  return showListen(state.source);
});
const yamlEditor=initYamlEditor({api,toast,browse:async callback=>{await openDirectory(callback);}});
const preparation=initPreparation({api,toast});
const publishing=initPublishing({api,toast});
const preparationAnchor=document.createComment('Preparation home');$('preparationView').before(preparationAnchor);
function restorePreparation(){if($('preparationView').parentElement?.id==='libraryEditorContainer')preparationAnchor.after($('preparationView'));}
const library = initLibrary({
  api, toast,
  onPlay: (items, index) => playCollection({items}, index),
  onListChange: () => player.refresh(),
  onEdit: async item => {
    $('libraryEditorContainer').append($('preparationView'));
    $('preparationView').hidden = false;
    await preparation.open({published: item.generation_id, master_id: item.master_id, room: item.room,
      onBack: async () => {
        preparation.close();
        restorePreparation();
        $('preparationView').hidden = true;
        await library.open();
      },
    });
  },
});
$('publishingTab').onclick=guarded(async()=>{showView('publishing');await publishing.open();});
$('preparationTab').onclick=guarded(async()=>{showView('preparation');await preparation.open();});
const writing=initWriting({api,toast,onPublished:kind=>{generation.writingChanged(kind);if(kind==='track')yamlEditor.invalidateCatalogue();}});
$('writingTab').onclick=guarded(async()=>{showView('writing');await writing.open();});
$('openWritingSettings').onclick=guarded(async()=>{$('settingsDialog').close();await writing.openSettings();});
const workers=initWorkers({api,toast,onChange:async()=>{await generation.reloadWorkers();},configureWriting:()=>writing.openSettings()});
const generation=initGeneration({api,toast,browse:async callback=>{await openDirectory(callback);},configureWorkers:()=>workers.open()});
$('openWorkerSettings').onclick=guarded(async()=>{$('settingsDialog').close();await workers.open();});
$('generationTab').onclick=guarded(async()=>{showView('generation');await generation.open();});
$('yamlTab').onclick=guarded(async()=>{showView('yaml');await yamlEditor.open();});
$('back').onclick=guarded(()=>changeReview(()=>previousReview()));
$('autoplayNext').checked=localStorage.getItem('gbr-autoplay-next')==='true';
$('autoplayNext').onchange=()=>localStorage.setItem('gbr-autoplay-next',String($('autoplayNext').checked));
$('skip').onclick=guarded(()=>changeReview(()=>advanceReview($('autoplayNext').checked)));
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$(button.dataset.close).close();
const queueChoices={},selectionChoices={};
for(const [key,id,label,any] of [['titles','Song','Track names','Any track'],['clusters','Cluster','Clusters','Any cluster'],['genres','Genre','Styles / genres','Any style']]){
  queueChoices[key]=toggleChoices($('filter'+id),{label,any,count:'remaining',contentOnly:true,onChange:guarded(refreshQueueFilters)});
  selectionChoices[key]=toggleChoices($('selection'+id),{label,any,contentOnly:true,onChange:guarded(loadCandidates)});
}
let queueOptionsSequence=0,queueCountSequence=0,candidateSequence=0;
async function queueOptions(){
  const sequence=++queueOptionsSequence;
  const options=await api('/review/options',queueFields());
  if(sequence!==queueOptionsSequence)return false;
  for(const key of Object.keys(queueChoices))queueChoices[key].load(options[key],options.selection[key],options.labels);
  selectChoices($('filterDirectory'),options.directories,{empty:'All input folders',disabledMissing:true});return true;
}
function queueFields(){return {order:$('order').value,direction:$('direction').value,directory:$('filterDirectory').value,song:'',genre:'',...Object.fromEntries(Object.entries(queueChoices).map(([key,picker])=>[key,picker.values()]))};}
async function selectionOptions(sequence){
  const query=new URLSearchParams({scope:'selection',bucket:$('selectionBucket').value,status:$('selectionStatus').value,bookmarked:String($('selectionBookmarks').value==='only')});
  const filters=Object.fromEntries(Object.entries(selectionChoices).map(([key,picker])=>[key,picker.values()]));
  const options=await api('/review/options?'+query,filters);
  if(sequence!==candidateSequence)return false;
  for(const key of Object.keys(selectionChoices))selectionChoices[key].load(options[key],options.selection[key],options.labels);
  const count = Object.values(selectionChoices).reduce((sum, picker) => sum + picker.values().length, 0);
  $('selectionFilterCount').textContent = count ? `· ${count} selected` : '';
  selectChoices($('selectionBucket'),options.buckets.map(value=>({value:String(value),label:'Bucket '+value})),{empty:'All flagged / reviewed',disabledMissing:true});
  selectChoices($('selectionStatus'),options.statuses.map(value=>({value,label:publishingLabels[value]||value})),{empty:'All',disabledMissing:true});
  $('selectionBookmarks').options[1].disabled=!options.has_bookmarks;
  return true;
}
const publishingLabels={not_selected:'Not selected',selected:'Flagged for distribution',published:'Published',passed:'Passed',rejected:'Rejected',withdrawn:'Withdrawn'};
$('queueButton').onclick=guarded(async()=>{
  $('order').value=state.queue.order;$('direction').value=state.queue.direction;
  selectChoices($('filterDirectory'),state.queue.directory?[state.queue.directory]:[],{empty:'All input folders',value:state.queue.directory});
  for(const key of Object.keys(queueChoices))queueChoices[key].set(state.queue[key]||[]);
  const sequence=++queueOptionsSequence,request={...state.queue};
  const options=await api('/review/options',request);
  if(sequence!==queueOptionsSequence)return;
  for(const key of Object.keys(queueChoices))queueChoices[key].load(options[key],options.selection[key],options.labels);
  selectChoices($('filterDirectory'),options.directories,{empty:'All input folders',disabledMissing:true});
  $('queueDialog').showModal();await previewQueueCounts();
});
async function refreshQueueFilters(){if(await queueOptions())await previewQueueCounts();}
async function applyQueue(returnSkipped=false){const settings=queueFields();await api('/queue',{...settings,return_skipped:returnSkipped});state.queue=settings;reviewListDirty=true;$('queueDialog').close();await nextTrack();}
$('queueForm').onsubmit = guarded(event => {event.preventDefault(); return changeReview(() => applyQueue());});
$('resetSkipped').onclick = guarded(() => changeReview(() => applyQueue(true)));
$('returnSkipped').onclick = guarded(() => changeReview(async () => {
  await api('/queue', {...state.queue, return_skipped: true});
  reviewListDirty = true;
  await nextTrack();
}));
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
$('signOut').onclick=guarded(async()=>{await api('/logout',{});player.pause();location.reload();});
const listenPageSize = 50;
let reviewList, selectionList, reviewListDirty = true, selectionDirty = true;

function makeList({path, query, entryFor, container, moreButton, render, isVisible, emptyText}) {
  const list = {items: [], loadMore: append};
  const ids = new Set();
  let offset = 0, request;
  async function append() {
    if (!list.loadMore) return;
    if (!request) request = fetchPage().finally(() => {request = null;});
    return request;
  }
  async function fetchPage() {
    if (isVisible()) moreButton.disabled = true;
    try {
      const params = new URLSearchParams(query);
      params.set('limit', listenPageSize);
      params.set('offset', offset);
      const result = await api(path + '?' + params);
      offset += result.items.length;
      if (!result.has_more || !result.items.length) list.loadMore = null;
      for (const item of result.items) {
        const entry = entryFor(item);
        if (ids.has(entry.id)) continue;
        ids.add(entry.id);
        list.items.push(entry);
        if (isVisible()) container.append(render(item, list, list.items.length - 1));
      }
      if (isVisible() && !list.items.length) {
        const empty = document.createElement('p');
        empty.className = 'subtle';
        empty.textContent = emptyText;
        container.append(empty);
      }
    } finally {
      if (isVisible()) {
        moreButton.hidden = !list.loadMore;
        moreButton.disabled = false;
      }
      player.refresh();
    }
  }
  return list;
}

function originalEntry(id, title, genre, duration, source) {
  const collection = source === 'queue' ? 'Review queue' : 'Selection';
  return {
    id, title, duration, source, key: `${source}:${id}`, subtitle: `${collection} · ${genre}`,
    url: format => recordingURL(id, 'full', format),
  };
}

// Keep the list that started playback even if another collection is opened.
function playCollection(list, index) {
  const entry = list.items[index];
  if (player.current?.key === entry.key) {
    player.current.queue = list;
    player.current.index = index;
    player.refresh();
    return player.toggle();
  }
  if (entry.key === reviewAudio?.key) return playReview();
  return player.playQueue(list, index);
}

function playButton(list, index) {
  const entry = list.items[index];
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'listen-play';
  button.dataset.title = entry.title;
  button.setAttribute('aria-label', `Play ${entry.title}`);
  button.title = button.getAttribute('aria-label');
  const icon = document.createElement('span');
  icon.className = 'play-symbol';
  icon.setAttribute('aria-hidden', 'true');
  button.append(icon);
  button.onclick = guarded(() => playCollection(list, index));
  return button;
}

async function openReview(gid, reassess = false) {
  await changeReview(async () => {
    const alreadyOpen = state.track?.id === gid && !state.reservationLost &&
      (reassess ? state.track.reassessment : !state.track.correction);
    if (!alreadyOpen) {
      if (reassess) {
        await loadTrack(await api(`/tracks/${gid}?reassess=true`));
      } else {
        const result = await api('/queue/open', {generation_id: gid});
        state.canBack = result.can_back;
        paintCounts(result.counts);
        await loadTrack(result.track);
      }
      reviewListDirty = true;
    }
    await showListen('review');
    $('trackTitle').scrollIntoView({block: 'nearest'});
  });
}

function attachReviewQueue(entry, list) {
  if (!list) return;
  const index = list.items.findIndex(item => item.id === entry.id);
  if (index >= 0 || state.track?.correction) {
    entry.queue = list;
    entry.index = index;
  }
}

async function refreshReviewList() {
  if (!state.reviewer || !reviewVisible()) return;
  reviewListDirty = false;
  const container = $('queueList');
  const moreButton = $('moreQueue');
  const list = makeList({
    path: '/queue/browse',
    container, moreButton,
    isVisible: () => reviewList === list,
    entryFor: item => originalEntry(item.id, item.title, item.genre, item.duration, 'queue'),
    render: (item, queue, index) => {
      const row = document.createElement('article');
      row.className = 'listen-row';
      row.dataset.playKey = queue.items[index].key;
      const info = document.createElement('div');
      const title = document.createElement('h3');
      title.className = 'listen-track-title';
      title.textContent = item.title;
      const context = document.createElement('p');
      context.textContent = `${item.genre} · ${fmt(item.duration)} · ${item.id.slice(0, 6)}`;
      info.append(title, context);
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'listen-open';
      open.dataset.openReview = '';
      open.disabled = !canChangeReview();
      open.textContent = state.track?.id === item.id && !state.track.correction ? 'In review' : 'Review';
      open.setAttribute('aria-label', `Review ${item.title}`);
      open.onclick = guarded(() => openReview(item.id));
      row.append(playButton(queue, index), info, open);
      return row;
    },
    emptyText: 'No recordings match this queue.',
  });
  reviewList = list;
  container.replaceChildren();
  moreButton.hidden = true;
  try {
    await list.loadMore();
    if (reviewList === list && reviewAudio) {
      attachReviewQueue(reviewAudio, list);
      player.refresh();
    }
  } catch (error) {
    if (reviewList !== list) return;
    reviewListDirty = true;
    throw error;
  }
}
$('moreQueue').onclick = guarded(() => reviewList?.loadMore?.());
$('jumpQueue').onclick = () => {
  $('queueView').scrollIntoView({block: 'start'});
  $('queueButton').focus({preventScroll: true});
};

async function loadCandidates() {
  const sequence = ++candidateSequence;
  const container = $('candidateList');
  const moreButton = $('moreCandidates');
  selectionList = null;
  selectionDirty = true;
  state.chosen = [];
  $('compare').disabled = true;
  moreButton.hidden = true;
  container.textContent = 'Loading recordings…';
  if (!await selectionOptions(sequence) || sequence !== candidateSequence) return;
  const query = new URLSearchParams({
    status: $('selectionStatus').value,
    bucket: $('selectionBucket').value,
    bookmarked: String($('selectionBookmarks').value === 'only'),
  });
  for (const [key, picker] of Object.entries(selectionChoices)) {
    for (const value of picker.values()) query.append(key, value);
  }
  const list = makeList({
    path: '/publishing', query, container, moreButton,
    isVisible: () => selectionList === list,
    entryFor: item => originalEntry(item.generation_id, item.title, item.genre, item.audio_duration, 'selection'),
    render: candidateRow,
    emptyText: 'No tracks match these filters.',
  });
  selectionList = list;
  container.replaceChildren();
  await list.loadMore();
  if (selectionList === list) selectionDirty = false;
}

function candidateRow(item, list, index) {
  const card = document.createElement('article');
  card.className = 'candidate listen-row';
  card.dataset.generationId = item.generation_id;
  card.dataset.playKey = list.items[index].key;
  const title = document.createElement('div');
  title.className = 'candidate-title';
  const h = document.createElement('h3');
  h.className = 'listen-track-title';
  h.textContent = item.title;
  const context = document.createElement('p');
  context.textContent = `${item.genre} · ${fmt(item.audio_duration)}`;
  const version = document.createElement('span');
  version.className = 'candidate-version';
  version.textContent = `Version ${item.generation_id.slice(0,6)}${item.review_id ? '' : ' · awaiting review'}`;
  title.append(h, context, version);

  const actions = document.createElement('div');
  actions.className = 'candidate-actions';
  const reassess = document.createElement('button');
  reassess.className = 'secondary';
  reassess.dataset.openReview = '';
  reassess.textContent = item.can_review ? 'Review' : 'Reassess';
  reassess.hidden = !item.can_review && !item.can_reassess;
  reassess.disabled = !canChangeReview();
  reassess.onclick = guarded(() => openReview(item.generation_id, !item.can_review));
  const select = document.createElement('select');
  select.setAttribute('aria-label', `Publishing state for ${item.title}`);
  for (const [value, label] of Object.entries(publishingLabels)) {
    const option = new Option(label, value);
    option.disabled = !item.review_id && !['not_selected','selected','rejected'].includes(value);
    select.append(option);
  }
  select.value = item.publishing_status;
  select.onchange = guarded(async () => {
    const status = select.value;
    select.disabled = true;
    try {
      await api(`/publishing/${item.generation_id}`, {status});
      item.publishing_status = status;
      if (state.track?.id === item.generation_id) await refreshFlags();
      toast('Distribution state saved.');
    } catch (error) {
      select.value = item.publishing_status;
      throw error;
    } finally {
      select.disabled = false;
    }
    if (selectionList === list) await loadCandidates();
  });

  const bookmark = document.createElement('button');
  bookmark.className = 'quiet-button candidate-bookmark';
  const paintBookmark = () => {
    bookmark.textContent = item.flags.bookmarked ? '★ Bookmarked' : '☆ Bookmark';
    bookmark.setAttribute('aria-pressed', String(item.flags.bookmarked));
  };
  paintBookmark();
  bookmark.onclick = guarded(async () => {
    bookmark.disabled = true;
    try {
      item.flags = await api(`/tracks/${item.generation_id}/flags`, {bookmarked: !item.flags.bookmarked});
      paintBookmark();
      if (state.track?.id === item.generation_id) await refreshFlags();
      if (selectionList === list) await loadCandidates();
    } finally {
      bookmark.disabled = false;
    }
  });

  const details = document.createElement('button');
  details.className = 'quiet-button';
  details.textContent = 'Reviews / Reason';
  details.hidden = !item.review_id;
  details.onclick = guarded(() => showReviews(item.generation_id));
  const reference = document.createElement('button');
  reference.className = 'quiet-button';
  reference.textContent = 'Reference';
  reference.onclick = guarded(async () => {
    const value = prompt('Optional external publishing reference', item.external_reference || '');
    if (value === null) return;
    await api(`/publishing/${item.generation_id}`, {status: item.publishing_status, external_reference: value});
    item.external_reference = value;
    toast('Reference saved.');
  });

  const compareLabel = document.createElement('label');
  compareLabel.className = 'compare-check';
  const check = document.createElement('input');
  check.type = 'checkbox';
  compareLabel.hidden = !item.review_id;
  check.setAttribute('aria-label', `Compare ${item.title}, version ${item.generation_id.slice(0,6)}`);
  compareLabel.append(check, 'A/B');
  check.onchange = () => {
    if (check.checked && state.chosen.length === 2) {
      check.checked = false;
      toast('Choose two versions for comparison.');
      return;
    }
    if (check.checked) state.chosen.push(item);
    else state.chosen = state.chosen.filter(row => row.generation_id !== item.generation_id);
    $('compare').disabled = state.chosen.length !== 2;
  };
  actions.append(reassess, select, bookmark, details, reference, compareLabel);
  card.append(playButton(list, index), title, actions);
  return card;
}

$('selectionBookmarks').onchange = guarded(async () => {
  if ($('selectionBookmarks').value === 'only') {
    $('selectionBucket').value = '';
    $('selectionStatus').value = '';
  }
  await loadCandidates();
});
for (const id of ['selectionBucket','selectionStatus']) $(id).onchange = guarded(async () => {
  if (id === 'selectionStatus' && $(id).value === 'selected') $('selectionBucket').value = '';
  await loadCandidates();
});
$('moreCandidates').onclick = guarded(() => selectionList?.loadMore?.());
$('compare').onclick=guarded(async()=>{const [a,b]=state.chosen;if(a.song_id!==b.song_id)throw new Error('Choose two versions of the same song.');state.comparison=await api('/comparisons',{generation_a:a.generation_id,generation_b:b.generation_id});$('compareTitle').textContent=state.comparison.title;$('compareResult').textContent='';$('compareDialog').showModal();});
let beforeComparison;
async function playComparison(slot) {
  const comparison = state.comparison;
  if (!comparison) return;
  const current = player.current;
  if (current?.source !== 'comparison') beforeComparison = {entry: current, playing: !audio.paused};
  const position = current?.source === 'comparison' ? player.position(current) : 0;
  await player.load({
    source: 'comparison', title: comparison.title, subtitle: `Blind comparison · ${slot.toUpperCase()}`,
    url: format => `/api/comparisons/${comparison.id}/${slot}/audio?` + new URLSearchParams({format}),
  }, {autoplay: true, position});
}
$('playA').onclick = guarded(() => playComparison('a'));
$('playB').onclick = guarded(() => playComparison('b'));
$('comparePause').onclick = () => {if (player.current?.source === 'comparison') player.pause();};
$('compareDialog').addEventListener('close', guarded(async () => {
  if (player.current?.source === 'comparison') {
    await player.load(beforeComparison?.entry || null, {autoplay: Boolean(beforeComparison?.playing)});
  }
  beforeComparison = null;
}));
for(const button of document.querySelectorAll('[data-prefer]'))button.onclick=guarded(async()=>{const result=await api(`/comparisons/${state.comparison.id}/preference`,{preferred:button.dataset.prefer});$('compareResult').textContent=result.preferred_generation_id?`Preference saved for version ${result.preferred_generation_id.slice(0,6)}. Original ratings are unchanged.`:'No preference saved. Original ratings are unchanged.';});
$('loginForm').onsubmit=async event=>{event.preventDefault();try{await api('/login',{password:$('password').value});$('password').value='';$('loginDialog').close();await boot();}catch(error){$('loginError').textContent=error.message;}};
$('nextPoint').onclick=guarded(async()=>{
  const position=sourceTime(),points=state.preview?.points?.length?state.preview.points:state.preview?.markers||[];
  const point=points.find(p=>(p.start+p.end)/2>position+1)||points[0];if(!point)return;
  const center=(point.start+point.end)/2;await setMode('range',{start:Math.max(0,center-2),end:Math.min(state.track.duration,center+3)},{autoplay:true});
});
document.addEventListener('keydown', guarded(async event => {
  if (event.defaultPrevented || event.repeat || event.ctrlKey || event.altKey || event.metaKey ||
      event.target.closest('input,textarea,select,[contenteditable=true]') || document.querySelector('dialog[open]')) return;
  if (event.code === 'Space' && player.current && state.view === 'listen' && !event.target.closest('button,a,summary')) {
    event.preventDefault();
    await player.toggle();
    return;
  }
  if (!reviewVisible() || !state.track || state.loadingReview || state.submitting || state.advancePending || state.reservationLost) return;
  if (/^[0-5]$/.test(event.key)) {
    event.preventDefault();
    document.querySelector(`[data-dimension="${DIMENSIONS[state.ratingIndex]}"] [data-score="${event.key}"]`).click();
  } else if (['ArrowUp','ArrowDown'].includes(event.key)) {
    event.preventDefault();
    state.ratingIndex = (state.ratingIndex + (event.key === 'ArrowDown' ? 1 : 3)) % 4;
    document.querySelectorAll('.rating').forEach((row,i) => row.classList.toggle('keyboard-active',i === state.ratingIndex));
  } else if (['arrowleft','arrowright','r'].includes(event.key.toLowerCase())) {
    if (player.current !== reviewAudio) return;
    event.preventDefault();
    player.seek(event.key === 'ArrowLeft' ? audio.currentTime - 5 : event.key === 'ArrowRight' ? audio.currentTime + 5 : 0);
  } else {
    const key = event.key.toLowerCase();
    const button = {n:'skip', b:'back', t:'nextPoint'}[key];
    const mode = {f:'fast', q:'quick', s:'standard', u:'full'}[key];
    if (button || (mode && modeAvailable(mode))) {
      event.preventDefault();
      if (button) $(button).click();
      else document.querySelector(`[data-mode="${mode}"]`).click();
    }
  }
}));
new ResizeObserver(drawWave).observe(canvas);
function loseReservation(message='Your reservation expired. Resume your queue before rating.'){
  state.reservationLost=true;if(player.current===reviewAudio)player.pause();clearTimeout(prefetchTimer);paintRatings();$('skip').disabled=true;$('resumeQueue').hidden=false;$('reviewMessage').textContent=message;
}
async function heartbeat(){
  if(!state.reviewer||$('reviewerDialog').open||$('loginDialog').open||state.submitting||state.loadingReview||reviewerChanging)return;
  const sequence = loadSequence, reviewerId = state.reviewerId;
  const current = state.track && !state.track.correction ? state.track.id : null;
  const result = await api('/reviewer/heartbeat', {current_id: current});
  if (sequence !== loadSequence || reviewerId !== state.reviewerId || !canChangeReview()) return;
  paintCounts(result.counts);
  if (current && !result.valid_current) loseReservation();
}
async function chooseReviewer(name){
  if(!canChangeReview())return;
  reviewerChanging=true;const buttons=[...$('reviewerForm').querySelectorAll('button')];buttons.forEach(button=>button.disabled=true);
  try{const result=await api('/reviewer/select',{name});state.canBack=false;player.load(null);reviewList=selectionList=null;++candidateSequence;await loadTrack(null);state.reviewer=result.reviewer;state.reviewerId=result.reviewer.id;localStorage.setItem('gbr-reviewer-id',state.reviewerId);$('reviewerDialog').close();showView('listen','review');await boot();}
  finally{reviewerChanging=false;buttons.forEach(button=>button.disabled=false);paintRatings();}
}
async function showReviewerDialog(names=null){
  if(!canChangeReview())return;
  const reviewers=names||(await api('/bootstrap')).reviewers;$('knownReviewers').replaceChildren();$('reviewerError').textContent='';$('newReviewerName').value='';$('cancelReviewer').hidden=!state.reviewer;
  for(const reviewer of reviewers){const button=document.createElement('button');button.type='button';button.textContent=reviewer.display_name;button.onclick=guarded(()=>chooseReviewer(reviewer.display_name));$('knownReviewers').append(button);}
  if(!$('reviewerDialog').open)$('reviewerDialog').showModal();
}
$('reviewerForm').onsubmit=async event=>{event.preventDefault();try{await chooseReviewer($('newReviewerName').value);}catch(error){$('reviewerError').textContent=error.message;}};
$('reviewerDialog').addEventListener('cancel',event=>{if(!state.reviewer)event.preventDefault();});
$('changeUser').onclick=guarded(()=>showReviewerDialog());
$('resumeQueue').onclick=guarded(()=>changeReview(()=>nextTrack()));
window.addEventListener('focus',()=>heartbeat().catch(()=>{}));
async function boot(){
  const b=await api('/bootstrap');if(!b.authenticated){if(!$('loginDialog').open)$('loginDialog').showModal();return;}
  reviewTags.configure(b.review_tags||[]);state.canConfigure=b.can_configure;$('libraryTab').hidden=!b.library_available;$("reviewImportLabel").hidden=!state.canConfigure;$('generationTab').hidden=!state.canConfigure;$('writingTab').hidden=!state.canConfigure;state.reviewer=b.reviewer;if(!b.reviewer){state.reviewerId='';localStorage.removeItem('gbr-reviewer-id');await showReviewerDialog(b.reviewers);return;}$('reviewerBar').hidden=false;$('reviewerName').textContent=b.reviewer.display_name;clearInterval(heartbeatTimer);heartbeatTimer=setInterval(()=>heartbeat().catch(()=>{}),b.heartbeat_seconds*1000);state.durations=b.durations;if(b.queue){state.queue={song:'',genre:'',directory:'',order:b.queue.order_by,direction:b.queue.direction,...JSON.parse(b.queue.filters_json)};}
  $('nowPlaying').hidden=false;
  if (reviewerChanging) await nextTrack();
  else await changeReview(() => nextTrack());
  if('serviceWorker' in navigator&&isSecureContext)navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
function newSubmissionId(){return globalThis.crypto?.randomUUID?.()||Array.from(globalThis.crypto.getRandomValues(new Uint32Array(4)),value=>value.toString(16)).join('-');}
function paintFlags(){
  const busy=!canChangeReview();
  const autoReject=!state.draftFlags.distribution&&!state.draftFlags.bookmarked&&Object.values(state.scores).some(score=>score<=3);
  for(const [id,key] of [['distributionFlag','distribution'],['bookmarkFlag','bookmarked'],['rejectFlag','rejected']]){
    $(id).disabled=busy||!state.track||(id!=='bookmarkFlag'&&state.flags?.publishing_status==='published');
    $(id).setAttribute('aria-pressed',String(Boolean(state.draftFlags[key]||(key==='rejected'&&autoReject))));
  }
  $('flagSummary').textContent=autoReject?(state.flags?.rejected?'Rejected · score 3 or below':'Will reject on save · score 3 or below'):state.draftFlags.distribution||state.draftFlags.bookmarked?'Protected from automatic rejection':'';
}
async function saveFlags(){
  if(!state.track||!canChangeReview())return;
  const gid=state.track.id,rid=state.reviewerId,sequence=loadSequence;
  const body={distribution:state.draftFlags.distribution,bookmarked:state.draftFlags.bookmarked,rejected:Boolean(state.draftFlags.rejected),expected_publishing_status:state.flags?.publishing_status||'not_selected'};
  state.flagSaving=true;paintRatings();$('flagMessage').textContent='Saving flags…';$('retryFlags').hidden=true;
  const task=(async()=>{try{const flags=await api(`/tracks/${gid}/flags`,body);if(sequence!==loadSequence||rid!==state.reviewerId)return;state.flagError=false;selectionDirty=true;state.flags=flags;state.draftFlags={...flags};state.track.flags=flags;$('flagMessage').textContent='Flags saved. Your review is not submitted.';$('refreshFlags').hidden=true;}catch(error){if(sequence===loadSequence){state.flagError=true;$('flagMessage').textContent=`Flags not saved: ${error.message} Your choices are retained; retry flags or submit the review.`;$('retryFlags').hidden=false;$('refreshFlags').hidden=error.code!=='flags_changed';}throw error;}finally{if(sequence===loadSequence){state.flagSaving=false;paintRatings();}}})();
  state.flagWrite=task;return task;
}
async function refreshFlags(quiet=false){
  if(!state.track||state.submitting||state.loadingReview||state.flagSaving)return;
  const gid=state.track.id,rid=state.reviewerId,sequence=loadSequence;
  const flags=await api(`/tracks/${gid}/flags`);if(sequence!==loadSequence||rid!==state.reviewerId)return;
  state.flagError=false;state.flags=flags;state.draftFlags={...flags};state.track.flags=flags;$('refreshFlags').hidden=true;$('retryFlags').hidden=true;if(!quiet)$('flagMessage').textContent='Current track flags loaded.';paintFlags();
}
for(const [id,key] of [['distributionFlag','distribution'],['bookmarkFlag','bookmarked'],['rejectFlag','rejected']])$(id).onclick=guarded(async()=>{
  if(!canChangeReview()||!state.track)return;
  state.draftFlags[key]=!state.draftFlags[key];
  if(state.draftFlags[key]){if(key==='rejected'){state.draftFlags.distribution=false;state.draftFlags.bookmarked=false;}else state.draftFlags.rejected=false;}
  paintFlags();await saveFlags();
});$('retryFlags').onclick=guarded(saveFlags);$('refreshFlags').onclick=guarded(refreshFlags);
$('autoSubmit').checked=state.autoSubmit;
$('autoSubmit').onchange=()=>{state.autoSubmit=$('autoSubmit').checked;localStorage.setItem('gbr-auto-submit',String(state.autoSubmit));paintRatings();if(state.autoSubmit&&state.track&&!state.track.correction&&DIMENSIONS.every(d=>state.scores[d]!==undefined))guarded(submitReview)();};
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
setInterval(async () => {
  try {
    if ($('loginDialog').open || $('reviewerDialog').open || !state.reviewer || reviewerChanging) return;
    if ($('settingsDialog').open) await status();
    if (state.loadingReview || state.submitting || state.reservationLost) return;
    if (state.advancePending) {
      state.loadingReview = true;
      try { await nextTrack($('autoplayNext').checked); }
      finally { state.loadingReview = false; paintRatings(); }
    } else if (reviewVisible()) {
      if (!state.track) await changeReview(() => nextTrack());
      else if (!state.preview?.ready) await refreshPreview();
      else if (state.waitForRetune || !modeAvailable('fast')) {
        const sequence = loadSequence;
        const result = await api(`/tracks/${state.track.id}/analysis-state`);
        if (sequence !== loadSequence) return;
        if (result.analysis_id !== state.preview.analysis_id) await refreshPreview(sequence);
        state.waitForRetune = ['pending','analysing','retune_pending','retuning'].includes(result.state);
      }
    }
  } catch {}
}, 5000);
setInterval(() => {
  if (reviewVisible() && state.track && canChangeReview() && !state.flagError && !state.saveError &&
      !document.querySelector('dialog[open]')) refreshFlags(true).catch(() => {});
}, 5000);

function paintCounts(counts,target='queueCount'){
  if(!counts)return;
  $(target).textContent=`${counts.remaining.toLocaleString()} left · ${counts.reviewed.toLocaleString()} / ${counts.total.toLocaleString()} reviewed${counts.skipped?' · '+counts.skipped+' skipped':''}`;
}
async function previewQueueCounts(){
  const sequence=++queueCountSequence,result=await api('/queue/counts',queueFields());
  if(sequence===queueCountSequence)paintCounts(result,'queuePreviewCount');
}
$('filterDirectory').onchange=guarded(refreshQueueFilters);

$('reviewImport').addEventListener('change',guarded(async()=>{
  const file=$('reviewImport').files[0];if(!file)return;
  $('reviewImport').disabled=true;$('reviewImportResult').textContent='Importing…';
  try{const result=await api('/import/reviews',{payload:JSON.parse(await file.text())});$('reviewImportResult').textContent=`${result.imported} reviews imported · ${result.already_present} already present`;const b=await api('/bootstrap');reviewTags.configure(b.review_tags||[]);await heartbeat();}
  catch(error){$('reviewImportResult').textContent=`Import failed: ${error.message}`;throw error;}
  finally{$('reviewImport').value='';$('reviewImport').disabled=false;}
}));

setInterval(()=>{if($('queueDialog').open)guarded(refreshQueueFilters)();},5000);
