import {initSongEditor} from './song-editor.js';
import {initMasteringStudio} from './mastering-studio.js';
export function initPreparation({api,toast}){
 const root=document.getElementById('preparationView');
 root.innerHTML=`
 <header class="prep-heading prep-page-heading">
  <h1>Preparation</h1>
  <div class="prep-header-actions"><button id="prepBackLibrary" hidden>Back to Library</button><button id="prepRefresh" class="secondary">Refresh tracks</button></div>
 </header>
 <div class="prep-project-bar"><label>Recording<select id="prepProject"></select></label><label id="prepFlagFilter"><input id="prepFlaggedOnly" type="checkbox" checked> Flagged for publish only</label></div>
 <p id="prepNotice" role="status"></p>
 <details id="prepQueue" class="prep-processing prep-disclosure">
  <summary>Processing queue <span id="prepQueueStatus" role="status"></span></summary>
  <button id="prepResumeQueue" class="secondary">Check / resume queue</button>
  <div id="prepQueueJobs"></div>
 </details>
 <div id="prepWorkspace" hidden>
  <div class="prep-studio">
   <section class="prep-card" aria-labelledby="prepMasteringHeading">
    <div class="prep-card-heading"><h2 id="prepMasteringHeading">Mastering</h2><button id="prepReset" class="secondary">Reset mix</button></div>
    <div class="prep-audio-pair">
     <div><span>Working audio</span><audio id="prepRaw" aria-label="Working audio" controls preload="none"></audio></div>
     <div><span>Effect preview</span><audio id="prepProcessed" aria-label="Effect preview" controls preload="metadata"></audio></div>
    </div>
    <div class="prep-audition">
     <label>Preview start <input id="prepStart" type="number" min="0" value="0" step="any" aria-label="Preview start in seconds"></label>
     <label>Length / s<input id="prepSeconds" type="number" min="1" max="60" value="30" step="1"></label>
     <button id="prepFromPlayhead" class="secondary">Use playhead</button>
     <button id="prepPreview">Render preview</button>
     <button id="prepAB" class="secondary" disabled>Compare A/B</button>
     <button id="prepClean" class="secondary" disabled>Clear preview</button>
    </div>
    <p id="prepPreviewStatus" class="prep-render-status" role="status"></p>
    <div class="prep-effect-rack">
     <h3>Add an effect</h3>
     <div id="prepEffectPresets" class="prep-presets" role="group" aria-label="Effect presets"></div>
     <p id="prepEffectsEmpty" class="prep-note">Pick a preset above to add it to the chain.</p>
     <ol id="prepEffectChain" aria-label="Effect chain, applied top to bottom"></ol>
    </div>
    <details id="prepMixDetails" class="prep-disclosure">
     <summary>Advanced mix</summary>
     <label class="prep-checkbox"><input id="prepUseStems" type="checkbox"> Mix separated vocals and accompaniment</label>
     <div id="prepControls" class="prep-controls"></div>
    </details>
    <div class="prep-master-actions">
     <label class="prep-checkbox"><input id="prepLimit" type="checkbox" checked> Peak limiter at −1 dBFS</label>
     <button id="prepMaster" class="primary">Render master</button>
    </div>
    <div class="prep-master-result">
     <p id="prepMasterStatus" class="prep-render-status" role="status"></p>
     <button id="prepUseMaster" class="secondary" hidden>Use this master</button>
    </div>
   </section>
   <section class="prep-card prep-artwork" aria-labelledby="prepArtworkHeading">
    <h2 id="prepArtworkHeading">Release artwork</h2>
    <div class="prep-cover-frame">
     <a id="prepCoverLink" target="_blank" rel="noopener" aria-label="Open full-size release cover" hidden><img id="prepCoverImage" alt="Selected release cover" decoding="async"></a>
     <div id="prepCoverFallback" class="prep-cover-fallback"><img src="/static/icon-512.png" alt=""><span>GBR sleeve</span></div>
    </div>
    <label>Selected cover<select id="prepReleaseArtwork"><option value="">GBR sleeve</option></select></label>
    <label>Upload a cover<input id="prepArtworkFile" type="file" accept="image/png,image/jpeg,image/webp"></label>
    <button id="prepImportArtwork" class="secondary">Attach artwork</button>
    <details id="prepBriefDetails" class="prep-disclosure">
     <summary>Artwork brief</summary>
     <label>Image family<select id="prepFamily"><option value="comfyui">ComfyUI</option></select></label>
     <label>Visual direction<textarea id="prepDirection" rows="3"></textarea></label>
     <div id="prepReferences"></div>
     <button id="prepBrief">Build brief from YAML</button>
     <textarea id="prepPrompt" rows="8" readonly aria-label="Artwork prompt"></textarea>
    </details>
   </section>
  </div>
  <div class="prep-actions"><button id="prepVisuals" class="secondary">Extract visual features</button></div>
  <details id="prepHistoryHeading" class="prep-disclosure prep-history">
   <summary>Processing history</summary>
   <a href="/api/export/json?kind=processing_tasks">Export processing history</a>
   <div id="prepHistory"></div>
  </details>
  <section class="prep-complete prep-card">
   <h2>Finish preparation</h2>
   <label>Website destination<select id="prepReleaseVisibility"><option value="public">Main player</option><option value="hidden">Secret room · Cover unlock</option></select></label>
   <div class="prep-release-options">
    <label class="prep-checkbox"><input id="prepIncludeFlac" type="checkbox" checked> Include lossless FLAC when available</label>
    <label class="prep-checkbox"><input id="prepReleaseLyrics" type="checkbox" checked> Include approved lyric timings</label>
   </div>
   <p id="prepReleaseAudio" class="prep-note"></p>
   <button id="prepComplete" class="primary">Finish preparation → Publish queue</button>
  </section>
  <details id="prepYamlDetails" class="prep-yaml">
   <summary>Preparation YAML details</summary>
   <p id="prepYamlSource" class="prep-note"></p>
   <div id="prepIdentity" class="prep-actions">
    <label>Master song<select id="prepIdentitySong"></select></label><label>Variant<select id="prepIdentityVariant"></select></label>
    <button id="prepSaveIdentity">Save song / variant IDs</button>
   </div>
   <dl id="prepYamlMetadata"></dl>
   <details><summary>Caption</summary><pre id="prepYamlCaption"></pre></details>
   <details><summary>Lyrics</summary><pre id="prepYamlLyrics"></pre></details>
   <details><summary>Current YAML</summary><pre id="prepYamlRaw"></pre><details><summary>Original imported YAML</summary><pre id="prepYamlOriginal"></pre></details></details>
  </details>
 </div>`;
 const controls=[['vocal_db','Vocals / dB',-24,12,.5,0],['accompaniment_db','Accompaniment / dB',-24,12,.5,0],['gain_db','Gain / dB',-24,12,.5,0],['bass_db','Bass / dB',-12,12,.5,0],['mid_db','Mid / dB',-12,12,.5,0],['treble_db','Treble / dB',-12,12,.5,0],['reverb','Room reverb',0,1,.05,0],['room_seconds','Room decay / sec',.1,4,.1,1.2],['fade_in','Fade in / sec',0,30,.5,0],['fade_out','Fade out / sec',0,60,.5,0]];
 const q=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 q('prepControls').innerHTML=controls.map(([key,label,min,max,step,value])=>`<label>${label}<input type="range" data-mix="${key}" min="${min}" max="${max}" step="${step}" value="${value}"><output data-value="${key}">${value}</output></label>`).join('');
 let projects=[],selected='',timer,lastPreview=null,lastBrief=null,previewStart=0,libraryMode=false,back=null,yamlFor='',yamlSequence=0,identity=null;
 let openSequence=0,refreshSequence=0,polling=false,configured=false,historySignature='',projectSignature='';
 const mixInputs = [...root.querySelectorAll('[data-mix]')];
 const effectPresets = new Map();
 const drafts = new Map(), coverChoices = new Map();
 let effects = [], maxEffects = 0, editing = '';
 let requestedPreview = '', requestedMaster = '';
 const submitting = new Set();
 const completing = new Set();
 const artifactUrl = id => '/api/preparation/artifacts/' + encodeURIComponent(id);
 q('prepFlaggedOnly').checked=localStorage.getItem('gbr-preparation-flagged')!=='false';
 const flaggedOnly=()=>!libraryMode&&q('prepFlaggedOnly').checked;
 const handle=fn=>async()=>{try{await fn()}catch(error){toast(error.message)}};
 const getTime=()=>timeline.time(),isPlaying=()=>timeline.isPlaying(),pause=()=>timeline.pause(),play=()=>timeline.play(),seek=time=>timeline.seek(time);
 const timeline=initSongEditor({root,api,toast,getMix:()=>mix(),setMix:(settings,stem)=>loadMix(settings,Boolean(stem)),
  getStems:()=>q('prepUseStems').checked?workingStems(project())?.id:null,
  getArtwork:()=>q('prepReleaseArtwork').value,
  setArtwork:id=>{coverChoices.set(selected,id);q('prepReleaseArtwork').value=id;showCover();}});
 const studio=initMasteringStudio({root,api,toast,getTime,seek,play,pause,isPlaying,previewStart:()=>previewStart,
  onSelection:range=>{q('prepStart').value=range.start;q('prepSeconds').value=Math.max(1,Math.min(60,Math.ceil(range.end-range.start)));editMix();q('prepPreview').click();},
  onChange:()=>editMix(),shared:timeline});
 for(const selector of ['.prep-audio-pair','.prep-audition','#prepPreviewStatus','#prepMaster','.prep-master-result','#prepVisuals'])root.querySelector(selector).hidden=true;
 root.querySelector('#prepMixDetails').append(q('prepLimit').closest('label'));
 root.querySelector('.prep-master-actions').hidden=true;
 q('prepMasteringHeading').textContent='Mastering rack';
 let mixUndo=[],mixRedo=[],lastMix='';
 function mixHistory(backwards){
  const from=backwards?mixUndo:mixRedo,to=backwards?mixRedo:mixUndo;if(!from.length)return;
  to.push(readMix());loadMix(from.pop(),q('prepUseStems').checked,Number(q('prepStart').value));lastMix=JSON.stringify(readMix());saveDraft();updateRenders(project());historyButtons();
 }
 function historyButtons(){studio.history(mixUndo.length,mixRedo.length,()=>mixHistory(true),()=>mixHistory(false));}
 function saveDraft(){
  if(editing)drafts.set(editing,{mix:readMix(),stems:q('prepUseStems').checked,start:Number(q('prepStart').value)});
  try{localStorage.setItem('gbr-mix-drafts-v2',JSON.stringify([...drafts].slice(-50)));}catch{}
 }
 try{for(const [id,draft] of JSON.parse(localStorage.getItem('gbr-mix-drafts-v2')||'[]'))drafts.set(id,draft);}catch{}
 historyButtons();
 q('prepHistoryHeading').before(q('prepComplete').closest('section'));
 const project=()=>projects.find(p=>p.generation_id===selected);
 timeline.onSourceChange(async artifact => {
  setPreview(null);
  requestedPreview = '';
  q('prepRaw').src='/api/preparation/audio/'+encodeURIComponent(selected)+(artifact?'?'+new URLSearchParams({artifact}):'');
  updateRenders(project());
 });

 function readMix() {
  const values = Object.fromEntries(mixInputs.map(input => [input.dataset.mix, Number(input.value)]));
  return {...values, limiter_db: q('prepLimit').checked ? -1 : null, effects: structuredClone(effects)};
 }
 function workingStems(p) {
  return p?.tasks?.find(task => task.kind === 'lyric_alignment' && task.status === 'done' &&
   (task.recipe.input_artifact_id || '') === (timeline.source()||'') &&
   ['stem_vocals', 'stem_accompaniment'].every(kind => task.artifacts.some(item => item.kind === kind && item.available)));
 }
 function mix() {
  const values = readMix();
  if (!q('prepUseStems').checked) values.vocal_db = values.accompaniment_db = 0;
  return values;
 }
 function editMix() {
  const next=JSON.stringify(readMix());
  if(lastMix&&next!==lastMix){mixUndo.push(JSON.parse(lastMix));if(mixUndo.length>50)mixUndo.shift();mixRedo=[];}
  lastMix=next;saveDraft();historyButtons();
  timeline.mixChanged();
  updateRenders(project());
 }
 for (const input of mixInputs) {
  input.oninput = () => {
   root.querySelector(`[data-value="${input.dataset.mix}"]`).textContent = input.value;
   editMix();
  };
 }
 q('prepLimit').onchange = editMix;
 q('prepStart').oninput = editMix;
 q('prepSeconds').oninput = editMix;
 q('prepUseStems').onchange = () => {
  if (q('prepUseStems').checked && !workingStems(project())) {
   q('prepUseStems').checked = false;
   toast('Run lyric alignment for this working audio to separate stems first');
  }
  editMix();
 };

 function paintRack(focus) {
  q('prepEffectsEmpty').hidden = effects.length > 0 || !effectPresets.size;
  for (const button of q('prepEffectPresets').querySelectorAll('button')) button.disabled = effects.length >= maxEffects;
  q('prepEffectChain').innerHTML = effects.map((effect, index) => {
   const {name, description} = effectPresets.get(effect.preset);
   return `<li class="prep-effect" data-index="${index}" data-bypassed="${effect.bypassed}">
    <div class="prep-effect-heading">
     <div><strong>${esc(name)}</strong><p>${esc(description)}</p></div>
     <div class="prep-effect-buttons">
      <button type="button" data-action="bypass" class="secondary" aria-pressed="${effect.bypassed}" aria-label="Bypass ${esc(name)}">Bypass</button>
      <button type="button" data-action="up" class="secondary" aria-label="Move ${esc(name)} up" title="Move up" ${index === 0 ? 'disabled' : ''}>↑</button>
      <button type="button" data-action="down" class="secondary" aria-label="Move ${esc(name)} down" title="Move down" ${index === effects.length - 1 ? 'disabled' : ''}>↓</button>
      <button type="button" data-action="remove" class="secondary" aria-label="Remove ${esc(name)}" title="Remove">×</button>
     </div>
    </div>
    <label class="prep-effect-strength">Strength <input data-strength type="range" min="0" max="100" step="1" value="${effect.strength}" aria-label="${esc(name)} strength"><output>${effect.strength}%</output></label>
   </li>`;
  }).join('');
  for(const row of q('prepEffectChain').children)studio.decorate(row,effects[Number(row.dataset.index)],effectPresets.get(effects[Number(row.dataset.index)].preset));
  if (focus != null) q('prepEffectChain').children[focus]?.querySelector('input').focus({preventScroll: true});
 }
 q('prepEffectChain').oninput = event => {
  const input = event.target;
  if(!input.matches('[data-strength]'))return;
  const row = input.closest('[data-index]');
  effects[Number(row.dataset.index)].strength = Number(input.value);
  row.querySelector('output').textContent = input.value + '%';
  editMix();
 };
 q('prepEffectChain').onclick = event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const index = Number(button.closest('[data-index]').dataset.index);
  const effect = effects[index], action = button.dataset.action;
  let focus = index;
  if (action === 'bypass') effect.bypassed = !effect.bypassed;
  else if (action === 'remove') {
   effects.splice(index, 1);
   focus = Math.min(index, effects.length - 1);
  } else {
   focus += action === 'up' ? -1 : 1;
   [effects[index], effects[focus]] = [effects[focus], effect];
  }
  paintRack(focus);
  editMix();
 };
 function showPresets(catalogue) {
  maxEffects = catalogue.max_effects;
  const picker = q('prepEffectPresets');
  for (const preset of catalogue.presets) {
   effectPresets.set(preset.id, preset);
   const button = document.createElement('button');
   button.type = 'button';
   button.title = preset.description;
   button.textContent = preset.name;
   button.dataset.preset = preset.id;
   button.onclick = () => {
    if (effects.length >= maxEffects) return;
    const effect={preset:preset.id,strength:preset.strength,bypassed:false};
    if(preset.parameters?.length)effect.parameters=Object.fromEntries(preset.parameters.map(p=>[p.id,p.default]));
    effects.push(effect);
    paintRack();
    editMix();
   };
   picker.append(button);
  }
  paintRack();
 }
 function loadMix(settings, stems = false, start = 0) {
  for (const [key,,,,,initial] of controls) {
   const input = mixInputs.find(input => input.dataset.mix === key);
   input.value = settings[key] ?? initial;
   root.querySelector(`[data-value="${key}"]`).textContent = input.value;
  }
  q('prepLimit').checked = settings.limiter_db === undefined || settings.limiter_db !== null;
  q('prepUseStems').checked = stems;
  q('prepStart').value = start;
  effects = structuredClone((settings.effects || []).filter(effect => effectPresets.has(effect.preset)));
  paintRack();
 }

 function showCover() {
  const id = q('prepReleaseArtwork').value;
  const image = q('prepCoverImage'), link = q('prepCoverLink'), fallback = q('prepCoverFallback');
  const url = id ? artifactUrl(id) : '';
  if (url === (image.getAttribute('src') || '')) return;
  link.hidden = !id;
  fallback.hidden = Boolean(id);
  fallback.querySelector('span').textContent = 'GBR sleeve';
  if (id) {
   link.href = url;
   image.src = url;
  } else {
   image.removeAttribute('src');
   link.removeAttribute('href');
  }
 }
 q('prepCoverImage').onerror = () => {
  q('prepCoverLink').hidden = true;
  q('prepCoverFallback').hidden = false;
  q('prepCoverFallback').querySelector('span').textContent = 'Cover unavailable';
 };
 q('prepReleaseArtwork').onchange = () => {
  coverChoices.set(selected, q('prepReleaseArtwork').value);
  showCover();
  timeline.artworkChanged();
 };

 function setPreview(task) {
  const id = task?.id || null;
  const player = q('prepProcessed');
  if (id === lastPreview && player.hasAttribute('src') === Boolean(id)) return;
  player.pause();
  lastPreview = id;
  previewStart = task?.recipe.start || 0;
  if (task) player.src = artifactUrl(task.artifacts.find(item => item.available && item.kind === 'preview_audio').id);
  else {
   player.removeAttribute('src');
   player.load();
  }
 }
 const active = task => ['queued', 'running'].includes(task?.status);
 const playable = task => task.status === 'done' && task.artifacts.some(item => item.available && item.kind === 'preview_audio');
 const effectValues = items => (items || []).map(effect => [effect.preset,effect.strength,Boolean(effect.bypassed),Object.entries(effect.parameters||{}).sort(),effect.range?[effect.range.start,effect.range.end,effect.range.fade_in||0,effect.range.fade_out||0]:null,(effect.automation||[]).map(p=>[p.time,p.value])]);
 function previewMatches(task) {
  const {effects: currentEffects, ...values} = mix();
  const saved = task.recipe.mix;
  const stem = q('prepUseStems').checked ? workingStems(project())?.id : undefined;
  return Object.entries(values).every(([key, value]) => value === saved[key]) &&
   JSON.stringify(effectValues(currentEffects)) === JSON.stringify(effectValues(saved.effects)) &&
   task.recipe.start === Number(q('prepStart').value) && task.recipe.seconds === Math.min(Number(q('prepSeconds').value),(project()?.duration||Infinity)-Number(q('prepStart').value)) && task.recipe.stem_task_id === stem;
 }
 function updateRenders(p) {
  q('prepComplete').disabled=completing.has(selected);
  const tasks = p?.tasks || [], source = timeline.artifact();
  const previews = tasks.filter(task => task.kind === 'mix_preview' && (task.recipe.input_artifact_id || '') === source);
  const previewJob = previews.find(task => task.id === requestedPreview) || previews[0];
  const clip = previewJob && playable(previewJob) ? previewJob : previews.find(playable);
  q('prepPreview').disabled = submitting.has('mix_preview') || active(previewJob);
  q('prepAB').disabled = q('prepClean').disabled = !clip || submitting.has('cleanup_previews');
  q('prepPreviewStatus').textContent = active(previewJob) ? `Preview ${previewJob.status}…` : previewJob?.error ? previewJob.error :
   clip ? (previewMatches(clip) ? 'Preview ready' : 'The preview uses an earlier mix or start time.') : '';

  const masters = tasks.filter(task => task.kind === 'mix_master');
  const masterJob = masters.find(task => task.id === requestedMaster) || masters[0];
  const candidate = masterJob?.status === 'done' && masterJob.artifacts.find(item => item.available && item.kind === 'master_candidate');
  const use = q('prepUseMaster');
  use.hidden = !candidate || source === candidate.id;
  use.dataset.artifact = candidate?.id || '';
  q('prepMaster').disabled = submitting.has('mix_master') || active(masterJob);
  const measurements = masterJob?.telemetry?.measurements;
  const peakValue = measurements?.sample_peak_dbfs;
  const peak = (peakValue != null ? ` · sample peak ${peakValue.toFixed(1)} dBFS` : '')+
   (measurements?.integrated_lufs!=null?` · ${measurements.integrated_lufs.toFixed(1)} LUFS`:'')+
   (measurements?.true_peak_dbfs!=null?` · true peak ${measurements.true_peak_dbfs.toFixed(1)} dBFS`:'');
  q('prepMasterStatus').textContent = active(masterJob) ? `Master ${masterJob.status}…` : masterJob?.error ? masterJob.error :
   candidate ? (source === candidate.id ? 'Master selected for release' : 'Master ready') + peak : '';
  q('prepReleaseAudio').textContent = source ? 'Release audio · Selected master' : 'Release audio · Reviewed source';
  if (q('prepUseStems').checked && !workingStems(p)) q('prepUseStems').checked = false;
  for (const input of mixInputs.filter(input => ['vocal_db','accompaniment_db'].includes(input.dataset.mix))) {
   input.disabled = !q('prepUseStems').checked;
  }
 }
 q('prepUseMaster').onclick = handle(async () => {
  await timeline.chooseSource(q('prepUseMaster').dataset.artifact);
 });
 function history(){const p=project();if(!p)return;
  q('prepWorkspace').hidden=!p.ready;q('prepNotice').textContent=p.ready?'':`Promotion: ${p.promotion?.status||'waiting for review'}${p.promotion?.error?' · '+p.promotion.error:''}`;
  const signature=JSON.stringify([p.generation_id,p.tasks,p.artworks]);
  if(signature===historySignature)return;
  historySignature=signature;
  q('prepHistory').innerHTML=(p.tasks||[]).map(t=>`<div class="prep-task"><strong>${esc(t.kind.replaceAll('_',' '))} · ${esc(t.status)}</strong><span>${esc(t.created_at)}${t.telemetry.wall_seconds?' · '+Number(t.telemetry.wall_seconds).toFixed(2)+' sec':''}</span>${t.telemetry.measurements?.device?`<span>${esc(t.telemetry.measurements.device)}${t.telemetry.measurements.resources?.peak_observed_device_bytes!=null?' · peak device memory '+(t.telemetry.measurements.resources.peak_observed_device_bytes/1024**3).toFixed(2)+' GiB (includes other processes)':''}</span>`:''}${t.telemetry.measurements?.sample_peak_dbfs!=null?`<span>Sample peak ${Number(t.telemetry.measurements.sample_peak_dbfs).toFixed(1)} dBFS · RMS ${Number(t.telemetry.measurements.rms_dbfs).toFixed(1)} dBFS</span>`:''}${t.telemetry.cleanup?.in_use||t.telemetry.cleanup?.error?'<p>Preview cleanup pending. Stop playback, then clear previews.</p>':''}${t.error?`<p>${esc(t.error)}</p>`:''}<div>${t.artifacts.filter(a=>a.available).map(a=>`<a href="/api/preparation/artifacts/${encodeURIComponent(a.id)}" target="_blank" rel="noopener">${esc(a.kind)}</a>`).join(' ')}${['failed','interrupted','queued','running'].includes(t.status)?` <button data-retry="${esc(t.id)}" class="secondary">${t.status==='running'||t.status==='queued'?'Recover stopped task':'Retry'}</button>`:''}${t.recipe.mix?` <button data-restore="${esc(t.id)}" class="secondary">Load settings</button>`:''}</div></div>`).join('')||'<p>No processing trials yet.</p>';
  for(const button of root.querySelectorAll('[data-retry]'))button.onclick=handle(async()=>{await api('/preparation/tasks/'+encodeURIComponent(button.dataset.retry)+'/retry',{});await refresh()});
  for (const button of q('prepHistory').querySelectorAll('[data-restore]')) button.onclick = () => {
   const saved = p.tasks.find(task => task.id === button.dataset.restore).recipe;
   loadMix(saved.mix, Boolean(saved.stem_task_id && workingStems(p)), saved.start || 0);
   editMix();
  };
  const artworks = p.artworks || [];
  const preferred = coverChoices.get(p.generation_id);
  const picker = q('prepReleaseArtwork');
  picker.replaceChildren(new Option('GBR sleeve', ''), ...artworks.map((art, index) =>
   new Option(art.created_at ? 'Artwork · ' + new Date(art.created_at).toLocaleString() : 'Artwork ' + (index + 1), art.id)));
  picker.value = preferred === '' || artworks.some(item => item.id === preferred) ? preferred : artworks[0]?.id || '';
  showCover();
  const savedBrief=(p.tasks||[]).find(t=>t.kind==='artwork_brief'&&t.status==='done');
  if(savedBrief&&savedBrief.id!==lastBrief?.id){lastBrief={id:savedBrief.id,...savedBrief.recipe};q('prepPrompt').value=lastBrief.prompt;q('prepDirection').value=lastBrief.direction||'';q('prepFamily').value='comfyui';for(const input of root.querySelectorAll('[data-reference]'))input.checked=lastBrief.reference_ids.includes(input.dataset.reference);}
 }
 async function processingQueue(){
  const onlyFlagged=flaggedOnly();
  const status=await api('/preparation/processing?'+new URLSearchParams({flagged_only:String(onlyFlagged)}));
  if(onlyFlagged!==flaggedOnly())return;
  q('prepQueueStatus').textContent=`${status.running||0} running · ${status.pending||0} queued · ${status.failed||0} failed / interrupted`+(!status.environment_ready?' · Processing environment missing. Run py -3.12 _97_app/run_gbr.py setup-processing.':' · One local task runs at a time.');
  const opened=new Set([...q('prepQueueJobs').querySelectorAll('details[open]')].map(e=>e.dataset.log));
  q('prepQueueJobs').innerHTML=status.items.map(job=>{const p=job.progress||{},elapsed=p.wall_seconds==null?'':Math.floor(p.wall_seconds/60)+'m '+Math.floor(p.wall_seconds%60)+'s',stage=p.stage_started_at?Math.max(0,Math.floor(Date.now()/1000-p.stage_started_at)):null;return `<article class="prep-task"><strong>${esc(job.title)} · ${esc(job.kind.replaceAll('_',' '))} · ${esc(job.status)}${job.position?' · queue '+job.position:''}</strong><span>${esc(job.reason||p.message||job.error||(job.status==='running'?'Running local processing':'Waiting for worker'))}${elapsed?' · '+elapsed:''}${stage!=null&&job.status==='running'?' · current stage '+stage+'s':''}</span>${p.device?`<span>${esc(p.device.toUpperCase())}${p.gpu_name?' · '+esc(p.gpu_name):''}${p.vram_bytes?' · '+(p.vram_bytes/1024**3).toFixed(1)+' GiB VRAM':''}</span>`:''}${p.measurements?.matched_words!=null?`<span>${p.measurements.matched_words} / ${p.measurements.total_words} words aligned; remaining positions are estimates.</span>`:''}${job.error&&job.status!=='waiting'?`<p>${esc(job.error)}</p>`:''}${['failed','interrupted'].includes(job.status)?`<button data-process-retry="${esc(job.id)}">Retry task</button>`:''}${p.log_tail?`<details data-log="${esc(job.id)}" ${opened.has(job.id)?'open':''}><summary>Worker log · ${esc(p.last_update||'')}</summary><pre>${esc(p.log_tail)}</pre></details>`:''}</article>`;}).join('')||'<p>No local processing jobs.</p>';
  for(const button of q('prepQueueJobs').querySelectorAll('[data-process-retry]'))button.onclick=handle(async()=>{await api(`/preparation/tasks/${button.dataset.processRetry}/retry`,{});await processingQueue();});
 }
 q('prepResumeQueue').onclick=handle(async()=>{await api('/preparation/processing/resume',{});await processingQueue();});
 function projectChoices(options){
  const picker=q('prepProject'),existing=new Map([...picker.options].map(option=>[option.value,option]));
  for(const [index,[id,label]] of options.entries()){
   const option=existing.get(id)||new Option(label,id);
   if(option.textContent!==label)option.textContent=label;
   if(picker.options[index]!==option)picker.insertBefore(option,picker.options[index]||null);
   existing.delete(id);
  }
  for(const option of existing.values())option.remove();
 }
 async function yamlDetails(p){
  const id=p.generation_id,key=id+':'+(p.yaml_sha256||'')+':'+libraryMode;
  if(yamlFor===key)return;
  const request=++yamlSequence,value=await api(`/preparation/projects/${encodeURIComponent(id)}/yaml`);
  if(request!==yamlSequence||id!==selected)return;
  q('prepYamlSource').textContent=(value.path||'Stored generation YAML')+(value.revision_id?' · saved Preparation revision':' · original metadata')+(value.editable?'. File edits are picked up automatically.':'. Published metadata is edited through Library.');
  q('prepYamlOriginal').textContent=value.original_yaml||'';
  q('prepYamlMetadata').innerHTML=Object.entries(value.metadata).filter(([key])=>!['lyrics','caption'].includes(key)).map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(typeof value==='object'?JSON.stringify(value):value??'—')}</dd></div>`).join('');
  q('prepYamlCaption').textContent=value.metadata.caption||'No caption stored';
  q('prepYamlLyrics').textContent=value.metadata.lyrics||'No lyrics stored';
  q('prepYamlRaw').textContent=value.yaml||'No original YAML stored';
  q('prepIdentity').hidden=libraryMode;
  if(!libraryMode){
   const catalogue=await api(`/preparation/projects/${encodeURIComponent(id)}/identity`);
   if(request!==yamlSequence||id!==selected)return;
   identity=catalogue;
   q('prepIdentitySong').replaceChildren(new Option('Choose master song',''),...identity.songs.map(r=>new Option(r.title+' · '+r.id,r.id)));
   const sid=identity.song_id||identity.suggested_song_id;
   if(sid&&!identity.songs.some(r=>r.id===sid))q('prepIdentitySong').append(new Option('Unlisted song ID '+sid,sid));
   q('prepIdentitySong').value=sid||'';identityVariants();q('prepSaveIdentity').disabled=false;
  }
  yamlFor=key;
 }
 async function refresh(){
  const sequence=++refreshSequence;
  const query=new URLSearchParams({focused:selected,published:String(libraryMode),flagged_only:String(flaggedOnly())});
  const result=await api('/preparation/projects?'+query);
  if(sequence!==refreshSequence)return;
  projects=result.projects;
  q('prepNotice').textContent=result.enabled?(projects.length?'':'Flag a reviewed recording for distribution to begin.'):'The shared platform database is required for Preparation.';
  const options=projects.map(p=>[p.generation_id,`${p.title} · ${p.style||p.cluster||''} · ${p.audio_id.slice(0,8)}${p.ready?'':' · promoting'}${!libraryMode&&p.status!=='selected'?' · flag cleared':''}`]);
  const signature=JSON.stringify(options);
  if(signature!==projectSignature){projectChoices(options);projectSignature=signature;}
  if(!projects.some(p=>p.generation_id===selected))selected=options[0]?.[0]||'';
  q('prepProject').value=selected;
  q('prepProject').disabled=!configured||!projects.length;
  const changedRecording=editing!==selected;
  if(changedRecording){
   timeline.pause();editing=selected;
   const draft=drafts.get(selected);
   loadMix(draft?.mix||{},draft?.stems,draft?.start);
   mixUndo=[];mixRedo=[];lastMix=JSON.stringify(readMix());historyButtons();
   requestedPreview=requestedMaster='';lastBrief=null;
   yamlFor='';++yamlSequence;identity=null;q('prepSaveIdentity').disabled=true;
   q('prepYamlSource').textContent='Loading recording details…';
   q('prepYamlCaption').textContent=q('prepYamlLyrics').textContent=q('prepYamlRaw').textContent='';
   q('prepWorkspace').inert=true;
   q('prepPrompt').value=q('prepDirection').value=q('prepArtworkFile').value='';
   q('prepRaw').pause();q('prepRaw').removeAttribute('src');q('prepRaw').load();setPreview(null);
  }
  const p=project();
  if(p?.ready&&!q('prepComplete').disabled&&!q('prepRaw').hasAttribute('src')){
   const source=changedRecording?'':timeline.artifact();
   q('prepRaw').src='/api/preparation/audio/'+encodeURIComponent(selected)+(source?'?'+new URLSearchParams({artifact:source}):'');
  }
  history();
  let songError=p?.yaml_error||'';
  if(p?.ready&&!songError){
   try{
    await timeline.update(p);
    if(sequence!==refreshSequence)return;
    await studio.update(selected,timeline.artifact());
    if(sequence!==refreshSequence)return;
    q('prepWorkspace').inert=!configured;
   }catch(error){if(sequence!==refreshSequence)return;songError=error.message;}
  }
  if(sequence!==refreshSequence)return;
  updateRenders(p);
  if(p&&!p.yaml_error){
   try{await yamlDetails(p);}catch(error){if(sequence!==refreshSequence)return;q('prepYamlSource').textContent=error.message;if(!songError)q('prepNotice').textContent=error.message;}
  }
  if(sequence!==refreshSequence)return;
  q('prepWorkspace').hidden=!p?.ready||Boolean(songError);
  if(songError){q('prepNotice').textContent=songError;q('prepYamlSource').textContent=songError;}
  else if(p?.ready&&!result.ffmpeg)q('prepNotice').textContent='Install FFmpeg on the host to render previews and master candidates.';
 }
 async function references(){const result=await api('/preparation/references');q('prepReferences').innerHTML=result.references.length?'<span>Reference images</span>'+result.references.map(r=>`<label><input type="checkbox" data-reference="${esc(r.id)}"> ${esc(r.name)}</label>`).join(''):'<p class="prep-note">Add reference images under _04_preparation/_references/_artwork, then reopen this tab.</p>';for(const input of root.querySelectorAll('[data-reference]'))input.checked=!!lastBrief?.reference_ids.includes(input.dataset.reference);}
 async function start(kind) {
  const id = selected, source = timeline.artifact();
  if (!id || submitting.has(kind)) return;
  submitting.add(kind);
  updateRenders(project());
  try {
   const result = await api(`/preparation/projects/${encodeURIComponent(id)}/jobs`, {
    kind, mix: mix(), start: Number(q('prepStart').value), seconds: Number(q('prepSeconds').value), input_artifact_id: source || null,
    stem_task_id: q('prepUseStems').checked ? workingStems(project())?.id : null,
   });
   if (selected !== id) return;
   if (kind === 'mix_preview' && source === timeline.artifact()) requestedPreview = result.id;
   if (kind === 'mix_master') requestedMaster = result.id;
   await refresh();
  } finally {
   submitting.delete(kind);
   updateRenders(project());
  }
 }
 q('prepImportArtwork').onclick = handle(async () => {
  const id = selected, input = q('prepArtworkFile'), file = input.files[0];
  if (!file || file.size > 10 * 1024 ** 2) throw Error('Choose an image smaller than 10 MiB');
  q('prepImportArtwork').disabled = true;
  try {
   const content = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(Error('Cannot read image'));
    reader.readAsDataURL(file);
   });
   const result = await api(`/preparation/projects/${encodeURIComponent(id)}/artwork/import`, {content});
   coverChoices.set(id, result.id);
   if (selected === id) {
    input.value = '';
    await refresh();
    timeline.artworkChanged();
    toast('Artwork attached.');
   }
  } finally {
   q('prepImportArtwork').disabled = false;
  }
 });
 q('prepComplete').onclick=handle(async()=>{
  if(!timeline.canSwitch())throw Error('Wait for the current song save to finish');
  timeline.releaseReady();
  const id=selected,sequence=openSequence,published=libraryMode,onBack=back;
  if(completing.has(id))return;
  const options={master_id:timeline.artifact()||null,artwork_id:q('prepReleaseArtwork').value||null,
   include_flac:q('prepIncludeFlac').checked,visibility:q('prepReleaseVisibility').value};
  const includeLyrics=q('prepReleaseLyrics').checked;
  timeline.pause();q('prepRaw').pause();q('prepRaw').removeAttribute('src');q('prepRaw').load();
  q('prepProcessed').pause();q('prepProcessed').removeAttribute('src');q('prepProcessed').load();
  completing.add(id);q('prepComplete').disabled=true;
  try{
   const editorial=await api(`/preparation/projects/${encodeURIComponent(id)}/editorial?`+new URLSearchParams(options.master_id?{artifact:options.master_id}:{}));
   if(includeLyrics&&(editorial.lyric_task_id||editorial.lyric_revision_missing)&&!editorial.approved)throw Error('Approve the saved lyric timings, or turn off their inclusion before completing');
   await api(published?`/releases/library/recordings/${encodeURIComponent(id)}/prepare`:`/releases/projects/${encodeURIComponent(id)}/finish`,
    {...options,timed_lyrics:includeLyrics&&editorial.approved});
   toast(published?'Updated Library release queued.':'Preparation queued. The completed package will appear in Publish.');
   if(selected===id&&sequence===openSequence){if(published)await onBack?.();else document.getElementById('publishingTab').click();}
  }finally{completing.delete(id);q('prepComplete').disabled=completing.has(selected);}
 });
 function identityVariants(){if(!identity)return;const sid=q('prepIdentitySong').value,variants=identity.variants.filter(r=>r.song_id===sid);q('prepIdentityVariant').replaceChildren(new Option('Base variant · 1','1'),...variants.filter(r=>r.id!=='1').map(r=>new Option(r.title+' · '+r.id,r.id)));if(sid===identity.song_id&&identity.variant_id){if(![...q('prepIdentityVariant').options].some(o=>o.value===identity.variant_id))q('prepIdentityVariant').append(new Option('Existing variant · '+identity.variant_id,identity.variant_id));q('prepIdentityVariant').value=identity.variant_id;}else if(sid===(identity.song_id||identity.suggested_song_id)&&identity.suggested_variant_id)q('prepIdentityVariant').value=identity.suggested_variant_id;}
 q('prepIdentitySong').onchange=identityVariants;
 q('prepSaveIdentity').onclick=handle(async()=>{if(!identity||!q('prepIdentitySong').value)throw Error('Choose a master song');await api(`/preparation/projects/${encodeURIComponent(selected)}/identity`,{song_id:Number(q('prepIdentitySong').value),variant_id:Number(q('prepIdentityVariant').value),expected_sha256:identity.sha256});yamlFor='';await refresh();toast('Preparation identity saved. Original generation metadata and reviews were retained.');});
 q('prepRefresh').onclick=handle(async()=>{await api('/preparation/sync',{});await refresh()});
 q('prepProject').onchange=handle(async()=>{if(!timeline.canSwitch()){q('prepProject').value=selected;throw Error('Wait for the current song save to finish before changing recordings');}timeline.pause();selected=q('prepProject').value;await refresh()});
 q('prepPreview').onclick=handle(()=>start('mix_preview'));q('prepMaster').onclick=handle(()=>start('mix_master'));q('prepVisuals').onclick=handle(()=>start('visuals'));
 q('prepClean').onclick = handle(async () => {
  submitting.add('cleanup_previews');
  setPreview(null);
  requestedPreview = '';
  updateRenders(project());
  try {
   const result = await api(`/preparation/projects/${encodeURIComponent(selected)}/cleanup-previews`, {});
   if (result.in_use) toast('Some previews are in use; stop playback and clear again.');
   await refresh();
  } finally {
   submitting.delete('cleanup_previews');
   updateRenders(project());
  }
 });
 q('prepReset').onclick = () => { loadMix({}); editMix(); };
 q('prepFromPlayhead').onclick = () => {
  const duration = project()?.duration || 0;
  q('prepStart').value = Math.max(0, Math.min(duration - .01, getTime())).toFixed(2);
  editMix();
 };
 q('prepRaw').onplay = () => q('prepProcessed').pause();
 q('prepProcessed').onplay = () => q('prepRaw').pause();
 q('prepAB').onclick = handle(async () => {
  const raw = q('prepRaw'), wet = q('prepProcessed');
  if (!wet.hasAttribute('src')) throw Error('Render a preview first');
  if (!wet.paused) {
   const time = wet.currentTime + previewStart;
   wet.pause();
   raw.currentTime = time;
   await raw.play();
  } else {
   const offset = raw.currentTime - previewStart;
   raw.pause();
   wet.currentTime = offset >= 0 && offset < (wet.duration || 30) ? offset : 0;
   await wet.play();
  }
 });
 q('prepBrief').onclick=handle(async()=>{const id=selected,result=await api(`/preparation/projects/${encodeURIComponent(id)}/artwork`,{family:q('prepFamily').value,direction:q('prepDirection').value,references:[...root.querySelectorAll('[data-reference]:checked')].map(e=>e.dataset.reference),seed:0});if(selected!==id)return;lastBrief=result;q('prepPrompt').value=lastBrief.prompt;await refresh()});
 q('prepBackLibrary').onclick=handle(async()=>{if(!timeline.canSwitch())throw Error('Wait for the current song save to finish before leaving');await back?.();});
 q('prepFlaggedOnly').onchange=handle(async()=>{
  const input=q('prepFlaggedOnly');
  if(!timeline.canSwitch()){
   input.checked=!input.checked;
   throw Error('Wait for the current song save to finish before changing the filter');
  }
  localStorage.setItem('gbr-preparation-flagged',String(input.checked));
  await Promise.all([refresh(),processingQueue()]);
 });
 async function pollProcessing() {
  if(polling||root.hidden||document.hidden)return;
  polling=true;
  try{const results=await Promise.allSettled([refresh(),processingQueue()]);for(const result of results)if(result.status==='rejected'&&!root.hidden)toast(result.reason.message);}
  catch(error){if(!root.hidden)toast(error.message);}
  finally{polling=false;}
 }
 function startPolling(){clearInterval(timer);timer=setInterval(pollProcessing,2000);}
 return {
  canLeave:()=>timeline.canSwitch(),
  async open(options={}){
   const sequence=++openSequence;
   ++refreshSequence;++yamlSequence;
   configured=false;
   q('prepWorkspace').inert=true;q('prepProject').disabled=true;
   startPolling();
   libraryMode=Boolean(options.published);
   back=options.onBack||null;
   if(options.published)selected=options.published;
   if (options.artwork_id && !coverChoices.has(selected)) coverChoices.set(selected, options.artwork_id);
   q('prepBackLibrary').hidden=!libraryMode;
   q('prepFlagFilter').hidden=libraryMode;
   root.querySelector('h1').textContent=libraryMode?'Edit published recording':'Preparation';
   q('prepComplete').textContent=libraryMode?'Update published recording':'Finish preparation → Publish queue';
   const [library, catalogue] = await Promise.all([
    api('/releases/library/settings'), effectPresets.size ? null : api('/preparation/effects'),
   ]);
   if(sequence!==openSequence)return;
   if (catalogue) showPresets(catalogue);
   const config = library.settings;
   q('prepReleaseVisibility').replaceChildren(...config.rooms.filter(r=>r.enabled).map(r=>new Option(r.name,r.id)));
   q('prepReleaseVisibility').value=options.room||config.default_room;
   q('prepIncludeFlac').checked=config.include_flac;
   q('prepReleaseLyrics').checked=config.timed_lyrics;
   configured=true;
   await Promise.all([refresh(),processingQueue()]);
   if(sequence!==openSequence)return;
   if(options.master_id)await timeline.chooseSource(options.master_id);
   if(projects.length)await references();
   if(sequence!==openSequence)return;
  },
  close(){
   ++openSequence;++refreshSequence;++yamlSequence;
   timeline.close();studio.close();clearInterval(timer);q('prepRaw').pause();q('prepProcessed').pause();
  },
 };
}
