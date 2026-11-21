const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function initLab({root, api, toast, context, openExperiments}) {
 root.innerHTML = `
  <header class="generation-queue-heading"><div><h2>Quick lab</h2><p>Try an idea in a short clip, listen, adjust, repeat.</p></div><button id="labHistory">All experiments</button></header>
  <div class="lab-layout">
   <section class="generation-panel">
    <div class="generation-fields"><label>Run on<select id="labWorker"></select></label><label>Start from a song<select id="labSource"><option value="">Blank trial</option></select></label></div>
    <label>Trial name<input id="labTitle" maxlength="160" value="Caption test"></label>
    <div class="lab-text"><label>Caption<textarea id="labCaption" rows="10" maxlength="20000" placeholder="Describe the sound you want to test"></textarea></label><label>Lyric excerpt<textarea id="labLyrics" rows="10" maxlength="40000" placeholder="[verse]&#10;A few lines to test phrasing, rhyme or delivery"></textarea></label></div>
    <div class="generation-fields"><label>Clip length<select id="labDuration"><option>10</option><option selected>20</option><option>30</option><option>45</option><option>60</option></select></label><label>Steps<input id="labSteps" value="30" placeholder="30 or 30,35"></label><label>Encoder seed<input id="labEncoderSeed" value="12345" inputmode="numeric"></label><label>Diffusion seeds<input id="labSamplerSeed" value="12345" placeholder="12345 or 12345,67890"></label></div>
    <div class="generation-inline"><button id="labRandom">New seed pair</button><button id="labCopy">Copy caption</button><span class="subtle small">Seed lists stay exact, including 64-bit values.</span></div>
    <p class="subtle small">Keep the clip length and seeds fixed for A/B tests. A short clip tests the idea; a longer generation can develop differently. Up to 16 variations per trial.</p>
    <div class="generation-inline"><button id="labCheck" class="primary">Check trial</button><button id="labRun" class="primary" disabled>Queue trial</button></div>
    <p id="labStatus" role="status">Draft saved on this device. Trials stay out of the production review queue.</p>
   </section>
   <section class="generation-panel lab-results"><div class="generation-queue-heading"><h2>Listen & compare</h2><button id="labRefresh">Refresh trials</button></div>
    <div class="lab-decks"><div><strong>A</strong><span id="labALabel">Choose a result</span><audio id="labA" controls preload="none"></audio></div><div><strong>B</strong><span id="labBLabel">Choose a result</span><audio id="labB" controls preload="none"></audio></div></div>
    <div id="labResults" aria-live="polite"></div>
   </section>
  </div>`;
 const q = id => root.querySelector('#'+id);
 const ids = ['labTitle','labCaption','labLyrics','labDuration','labSteps','labEncoderSeed','labSamplerSeed'];
 const savedKey = 'gbr-quick-lab-v1';
 let token = '', revision = 0, busy = false, sourceLoading = false, sequence = 0, timer, sourcePath = '', inputRoot = '', baseline = {};
 const results = new Map();
 const handle = fn => async () => { try { await fn(); } catch (error) { q('labStatus').textContent = error.message; } };
 try { const saved=JSON.parse(localStorage.getItem(savedKey)||'{}'); for(const id of ids) if(saved[id]!=null)q(id).value=saved[id];sourcePath=saved.sourcePath||'';baseline=saved.parameters||{}; } catch {}
 function changed() {
  revision++; token=''; q('labRun').disabled=true;
  q('labRun').textContent='Queue trial';
  localStorage.setItem(savedKey, JSON.stringify({...Object.fromEntries(ids.map(id=>[id,q(id).value])),sourcePath,parameters:baseline}));
  q('labStatus').textContent='Draft saved. Check the trial when you are ready.';
 }
 for(const id of ids)q(id).addEventListener('input',changed);
 q('labWorker').onchange=changed;
 q('labRandom').onclick=()=>{
  for(const id of ['labEncoderSeed','labSamplerSeed']){const seed=crypto.getRandomValues(new Uint32Array(2));q(id).value=((BigInt(seed[0])<<32n)|BigInt(seed[1])).toString();}
  changed();
 };
 q('labCopy').onclick=handle(async()=>{await navigator.clipboard.writeText(q('labCaption').value);toast('Caption copied.');});
 q('labHistory').onclick=openExperiments;
 q('labSource').onchange=handle(async()=>{
  const ticket=++sequence, path=q('labSource').value;
  const previousPath=sourcePath,previousParameters=baseline,before=Object.fromEntries(ids.map(id=>[id,q(id).value]));
  sourcePath=path;baseline={};changed();sourceLoading=Boolean(path);q('labCheck').disabled=busy||sourceLoading;
  if(!path)return;
  q('labStatus').textContent='Loading the writing recipe…';
  try{
   const source=await api('/generation/lab/source?'+new URLSearchParams({root:inputRoot,path}));
   if(ticket!==sequence)return;
   baseline=source.parameters;
   for(const [key,id] of [['title','labTitle'],['caption','labCaption'],['lyrics','labLyrics']])if(q(id).value===before[id])q(id).value=source.fields[key]||'';
   for(const [key,id] of [['encoder_seed','labEncoderSeed'],['sampler_seed','labSamplerSeed']])if(q(id).value===before[id]&&source.parameters[key]&&source.parameters[key]!=='auto')q(id).value=source.parameters[key];
   changed();
  }catch(error){if(ticket===sequence){sourcePath=previousPath;baseline=previousParameters;q('labSource').value=previousPath;}throw error;}
  finally{if(ticket===sequence){sourceLoading=false;q('labCheck').disabled=busy;}}
 });
 function body() {
  const seeds=q('labSamplerSeed').value.trim(), encoder=q('labEncoderSeed').value.trim(), steps=q('labSteps').value.trim();
  const inherited=Object.fromEntries(Object.entries(baseline).filter(([key])=>!['caption','cluster','style'].includes(key)));
  return {input_root:inputRoot, purpose:'experiment',experiment_label:('Quick lab · '+q('labTitle').value).slice(0,160),
   worker_id:q('labWorker').value,lab:{title:q('labTitle').value,caption:q('labCaption').value,lyrics:q('labLyrics').value,source_path:sourcePath||null},
   parameters:{...inherited,duration:Number(q('labDuration').value),encoder_seed:encoder},parameter_priority:'form',
   experiments:{sweeps:{sampler_seed:seeds,sampler_steps:steps},seed_policy:'preserve'},
   caption_mode:'yaml',caption_count:1,layout:'single',encoding_cache:'prefer'};
 }
 q('labCheck').onclick=handle(async()=>{
  if(busy)return;busy=true; const current=revision;q('labCheck').disabled=true;q('labStatus').textContent='Checking worker and recipe…';
  try{
   const checked=await api('/generation/preview',body());if(current!==revision)return;
   const issues=checked.inputs.flatMap(input=>input.issues||[]).filter(issue=>issue.severity==='error');
   if(checked.invalid||!checked.ready)throw Error(issues.map(issue=>issue.message).join(' · ')||'This trial is not ready');
   token=checked.token;q('labRun').disabled=false;q('labRun').textContent='Queue '+checked.generation_count+' short clip'+(checked.generation_count===1?'':'s');
   q('labStatus').textContent=checked.generation_count+' clips checked · '+q('labDuration').value+' seconds each. Ready to queue.';
  }finally{busy=false;q('labCheck').disabled=sourceLoading;}
 });
 q('labRun').onclick=handle(async()=>{
  if(!token||busy)return;busy=true;q('labRun').disabled=true;
  try{await api('/generation/start',{token,skip_errors:false});token='';q('labStatus').textContent='Trial queued. Your draft is ready for the next variation.';await refresh();}
  finally{busy=false;}
 });
 function deck(which,run,batch) {
  const audio=q('lab'+which), other=q('lab'+(which==='A'?'B':'A'));
  other.pause(); audio.src='/api/generation/experiments/'+encodeURIComponent(run.id)+'/audio';
  q('lab'+which+'Label').textContent=batch.experiment_label.replace('Quick lab · ','')+' · seed '+run.parameters.sampler_seed+' · '+run.parameters.sampler_steps+' steps';
 }
 for(const id of ['labA','labB']){
  q(id).onplay=()=>q(id==='labA'?'labB':'labA').pause();
  q(id).onerror=()=>toast('This trial audio is unavailable. Open All experiments to inspect or recover it.');
 }
 async function refresh() {
  const jobs=(await api('/generation/jobs?purpose=experiment')).jobs.filter(job=>job.lab).slice(0,6);
  const loaded=await Promise.all(jobs.map(async batch=>{
   const old=results.get(batch.id);
   if(old&&old.batch.status===batch.status&&batch.section!=='active')return old;
   const data=await api('/generation/jobs/'+batch.id+'?limit=16');
   const entry={batch,runs:data.runs};results.set(batch.id,entry);return entry;
  }));
  const progress=run=>run.steps>0?`<progress max="${Number(run.steps)}" value="${Math.max(0,Math.min(run.steps,Number(run.step)||0))}" aria-label="Generation steps"></progress><small>${Number(run.step)||0} / ${Number(run.steps)} ${escape(run.progress_unit||'steps')} · ${escape(run.node_label||'')}</small>`:'';
  const time=value=>value?new Date(value).toLocaleString():'';
  q('labResults').innerHTML=loaded.map(({batch,runs})=>`<section class="lab-trial"><h3>${escape(batch.experiment_label.replace('Quick lab · ',''))}</h3>${runs.map(run=>`<div class="lab-result" data-run="${escape(run.id)}"><div><strong>${escape(run.phase==='prepared'?'Queued':run.phase||run.status)}</strong>${progress(run)}<p>${escape(run.parameters.sampler_seed)} · ${escape(run.parameters.sampler_steps)} steps · ${escape(run.parameters.duration)}s</p>${run.error?`<p>${escape(run.error)}</p>`:''}<small>${run.started_at?'Started '+escape(time(run.started_at)): 'Waiting for worker'}${run.completed_at?' · Finished '+escape(time(run.completed_at)):''}</small></div><div>${run.output?.available?'<button data-deck="A">A</button><button data-deck="B">B</button>':''}<button data-reuse>Load recipe</button></div></div>`).join('')}</section>`).join('')||'<p class="subtle">Your short trials will appear here.</p>';
  for(const button of q('labResults').querySelectorAll('button')){
   const id=button.closest('[data-run]').dataset.run;
   const entry=loaded.find(item=>item.runs.some(run=>run.id===id)),run=entry.runs.find(item=>item.id===id);
   if(button.dataset.deck)button.onclick=()=>deck(button.dataset.deck,run,entry.batch);
   else button.onclick=handle(async()=>{
    const text=await api('/generation/jobs/'+entry.batch.id+'/runs/'+id+'/text');
    baseline=run.parameters;sourcePath=entry.batch.lab_source_path||'';q('labSource').value=sourcePath;
    q('labCaption').value=text.caption;q('labLyrics').value=text.lyrics;q('labTitle').value=entry.batch.experiment_label.replace('Quick lab · ','');
    for(const [key,target] of [['duration','labDuration'],['sampler_steps','labSteps'],['encoder_seed','labEncoderSeed'],['sampler_seed','labSamplerSeed']])q(target).value=run.parameters[key];
    changed();
   });
  }
 }
 q('labRefresh').onclick=handle(async()=>{results.clear();await refresh();});
 return {
  async open() {
   const state=context();inputRoot=state.inputRoot;
   const previous=q('labWorker').value;
   q('labWorker').innerHTML='<option value="auto">Any available worker</option>'+state.workers.filter(w=>w.enabled).map(w=>`<option value="${escape(w.id)}">${escape(workerLabel(w))}</option>`).join('');
   if([...q('labWorker').options].some(o=>o.value===previous))q('labWorker').value=previous;
   const catalogue=await api('/generation/catalogue?'+new URLSearchParams({root:inputRoot}));
   q('labSource').innerHTML='<option value="">Blank trial</option>'+catalogue.inputs.filter(item=>item.exists).map(item=>`<option value="${escape(item.path)}">${escape(item.title)}</option>`).join('');
   q('labSource').value=sourcePath;
   await refresh();clearInterval(timer);timer=setInterval(()=>{if(!root.hidden&&!document.hidden&&!busy)refresh().catch(()=>{});},4000);
  },
  close(){clearInterval(timer);q('labA').pause();q('labB').pause();}
 };
}
import {workerLabel} from './workers.js';
