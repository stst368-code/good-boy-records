const LABELS={completed:'Complete',prepared:'Queued',running:'Generating',publishing:'Finalising',cancel_requested:'Cancelling',cancelled:'Cancelled',succeeded:'Complete',failed:'Failed',waiting_worker:'Worker busy · waiting',paused:'Paused',queue_complete:'Queue finished',starting_worker:'Starting worker',comfy_queued:'Queued in ComfyUI',comfy_generating:'Generating in ComfyUI',conditioning:'Text conditioning',diffusion:'Diffusion',decode:'Decoding audio',write_flac:'Writing FLAC',downloading:'Downloading audio',cancel_after_generation:'Finishes current generation, then discards output',dedicated_generating:'Generating on pod',serverless_generating:'Generating serverless',reconnect:'Reconnect needed'};
const label=value=>LABELS[value]||String(value||'Waiting').replaceAll('_',' ');
const workerName=value=>value?.startsWith('local|')?'This PC':value?.replace(/^comfyui\|/,'ComfyUI · ').replace(/^runpod_dedicated\|/,'Pod · ').replace(/^runpod_serverless\|/,'Serverless · ')||'Next available worker';
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};
const time=value=>value?new Date(value).toLocaleString():'';
const model=value=>String(value||'Default').split(/[\\/]/).pop().replace(/\.(safetensors|gguf|ckpt)$/,'');

export function initJobs({root,api,toast,onQueued}){
  const state={open:new Set(),pages:new Map(),texts:new Map(),logs:new Map(),busy:new Set(),jobs:[],sequence:0,cursors:JSON.parse(sessionStorage.getItem('gbr-generation-log-cursors')||'{}')};
  const guard=fn=>async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};
  const button=(text,fn,cls='')=>{const b=node('button',cls,text);b.type='button';b.onclick=guard(fn);return b;};
  function drawer(key,title){const e=node('details','job-drawer');e.dataset.key=key;e.open=state.open.has(key);e.append(node('summary','',title));e.addEventListener('toggle',()=>{if(!e.isConnected)return;if(e.open)state.open.add(key);else state.open.delete(key);});return e;}
  function metadata(run){const p=run.parameters;return [['Encoder',model(p.text_encoder)],['DiT',model(p.dit)],['Sampling',p.sampler+' / '+p.scheduler],['Encoder CFG',p.encoder_cfg],['Diffusion CFG',p.sampler_cfg],['Top-k',p.top_k],['Steps',p.sampler_steps],['Seconds',p.duration]].map(([k,v])=>k+': '+v).join(' · ');}
  async function loadRuns(id,more=false,sequence=null){
    const old=state.pages.get(id);let data;
    if(more){const next=await api(`/generation/jobs/${id}?offset=${old?.runs.length||0}&limit=100`);data={...next,runs:[...(old?.runs||[]),...next.runs]};}
    else{const count=Math.max(100,old?.runs.length||100),parts=await Promise.all(Array.from({length:Math.ceil(count/200)},(_,i)=>api(`/generation/jobs/${id}?offset=${i*200}&limit=${Math.min(200,count-i*200)}`)));data={...parts.at(-1),offset:0,runs:parts.flatMap(p=>p.runs)};}
    if(sequence==null||sequence===state.sequence)state.pages.set(id,data);
  }
  function textDrawer(batch,run,kind){
    const key=`${batch.id}/${run.id}/${kind}`,e=drawer(key,kind==='caption'?'Caption':'Lyrics'),pre=node('pre','job-song-text');e.dataset.kind=kind;e.dataset.attempt=run.attempt?.id||'queued';e.append(pre);
    const load=guard(async()=>{if(!e.open)return;const cacheKey=batch.id+'/'+run.id+'/'+e.dataset.attempt;if(!state.texts.has(cacheKey))state.texts.set(cacheKey,await api(`/generation/jobs/${batch.id}/runs/${run.id}/text`));pre.textContent=state.texts.get(cacheKey)[kind]||'No '+kind+' supplied.';});
    e.loadContent=load;e.addEventListener('toggle',load);if(e.open)load();return e;
  }
  function parameterList(parameters){const dl=node('dl','job-parameters');for(const [key,value]of Object.entries(parameters))dl.append(node('dt','',key),node('dd','',String(value)));return dl;}
  function cancelButton(batch,run){const b=button('Cancel',async()=>{if(state.busy.has(run.id))return;state.busy.add(run.id);b.disabled=true;try{await api(`/generation/jobs/${batch.id}/runs/${run.id}/cancel`,{});await refresh();}finally{state.busy.delete(run.id);}} ,'job-cancel');b.disabled=state.busy.has(run.id);b.setAttribute('aria-label','Cancel '+run.title+' configuration '+run.id.slice(0,8));return b;}
  function runRow(batch,run){
    const e=node('article','job-run '+run.status);e.dataset.run=run.id;e.parameterSignature=JSON.stringify(run.parameters);
    const head=node('div','job-run-head'),title=node('div');title.append(node('strong','',run.title),node('span','job-run-id',`Song ${run.song_id} · variant ${run.variant_id} · ${run.id.slice(0,8)}`));
    head.append(title,node('span','job-status '+run.status,label(run.status)));
    if(run.can_cancel)head.append(cancelButton(batch,run));
    e.append(head,node('p','job-configuration',metadata(run)));
    const activity=node('div','job-run-activity');activity.append(node('span','job-worker-name',(run.worker?workerName(run.worker):run.status==='prepared'?'Next available worker':run.status==='cancelled'?'Not dispatched':'Worker not recorded')),node('span','job-stage',label(run.stage)));
    if(run.step!=null&&run.steps){activity.append(node('span','job-step',`${run.step} / ${run.steps} steps`));const p=node('progress','job-step-progress');p.max=run.steps;p.value=run.step;p.setAttribute('aria-label','Sampling steps');activity.append(p);}
    e.append(activity);if(run.message)e.append(node('p','job-run-note subtle small',run.message));if(run.error)e.append(node('p','error small',run.error));
    const details=node('div','job-run-drawers');details.append(textDrawer(batch,run,'caption'),textDrawer(batch,run,'lyrics'));
    const config=drawer(batch.id+'/'+run.id+'/config','All parameters');config.append(parameterList(run.parameters));details.append(config);e.append(details);return e;
  }
  function workerControls(batch){
    const area=node('div','job-workers');
    for(const w of batch.workers){const chip=node('div','job-worker '+(w.active?'online':''));chip.append(node('span','job-worker-dot'),node('span','job-worker-title',workerName(w.worker_id||w.connection.backend+'|'+(w.connection.address||''))),node('span','job-worker-state',label(w.status==='waiting_worker'?'waiting_worker':w.stage||w.status)));
      const actions=node('span','job-worker-actions');
      const action=(value,title)=>button(title,async()=>{await api('/generation/tasks/'+w.id,{action:value});await refresh();});
      if(w.active)actions.append(action('pause','Pause'));
      else if(['interrupted','running'].includes(w.status)&&w.connection.backend!=='local')actions.append(action('resume','Reconnect'));
      else if(batch.counts.failed)actions.append(action('retry','Retry failed'));
      else if(batch.counts.prepared)actions.append(action('continue','Continue'));
      if(w.cleanup_pending&&!w.active)actions.append(action('cleanup','Clean temporary files'));
      if(w.current_title)chip.append(node('span','job-current-title',w.current_title));chip.append(actions);if(w.error)chip.append(node('span','error small',w.error));area.append(chip);
    }return area;
  }
  function diagnosticDrawer(batch){
    const key=batch.id+'/diagnostics',e=drawer(key,'Technical log'),body=node('div','job-diagnostics');e.append(body);
    const load=guard(async()=>{if(!e.open)return;const result=await api('/generation/tasks');state.logs.set(batch.id,result.tasks.filter(t=>t.batch_id===batch.id));body.replaceChildren();for(const task of state.logs.get(batch.id)){const section=node('section'),title=node('div','generation-inline');title.append(node('strong','',workerName(task.worker_id||task.connection.backend+'|'+(task.connection.address||''))),button('Clear log view',async()=>{state.cursors[task.id]=task.log_end||0;sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.cursors));await load();}),button('Show full log',async()=>{delete state.cursors[task.id];sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.cursors));await load();}));const bytes=new TextEncoder().encode(task.log||'');const shown=new TextDecoder().decode(bytes.slice(Math.max(0,(state.cursors[task.id]||0)-(task.log_start||0))));section.append(title,node('pre','',shown||'Log view cleared. New events will appear here.'));body.append(section);}});
    e.addEventListener('toggle',load);if(e.open)load();return e;
  }
  function card(batch){
    const e=node('article','job-batch '+batch.section);e.dataset.batch=batch.id;
    const c=batch.counts,header=node('div','job-batch-heading'),title=node('div');title.append(node('h3','',`Batch ${batch.id.slice(0,8)}`),node('span','subtle small',time(batch.created_at)));header.append(title,node('span','job-status '+batch.status,label(batch.status)));e.append(header);
    const completed=c.succeeded||0,ended=completed+(c.cancelled||0)+(c.failed||0),bar=node('progress','job-batch-progress');bar.max=batch.total||1;bar.value=ended;bar.setAttribute('aria-label','Configurations resolved');e.append(bar);
    const counts=node('div','job-counts');counts.append(node('span','complete',`${completed} / ${batch.total} complete`));for(const [name,n]of [['generating',(c.running||0)+(c.publishing||0)],['queued',c.prepared||0],['cancelled',(c.cancelled||0)+(c.cancel_requested||0)],['failed',c.failed||0]])if(n)counts.append(node('span',name,`${n} ${name}`));e.append(counts,workerControls(batch));
    const runs=drawer(batch.id+'/runs',`Tracks & configurations · ${batch.total}`);runs.classList.add('job-track-list');
    const page=state.pages.get(batch.id);if(page){for(const run of page.runs)runs.append(runRow(batch,run));if(page.has_more)runs.append(button('Load more configurations',async()=>{await loadRuns(batch.id,true);render();},'job-more'));}else runs.append(node('p','subtle small','Expand to load this batch.'));
    runs.addEventListener('toggle',guard(async()=>{if(runs.open&&!state.pages.has(batch.id)){await loadRuns(batch.id);render();}}));e.append(runs,diagnosticDrawer(batch));return e;
  }
  function updateCard(existing,batch){
    existing.className='job-batch '+batch.section;
    const badge=existing.querySelector('.job-batch-heading .job-status');badge.className='job-status '+batch.status;badge.textContent=label(batch.status);
    const bar=existing.querySelector('.job-batch-progress'),c=batch.counts;bar.max=batch.total||1;bar.value=(c.succeeded||0)+(c.cancelled||0)+(c.failed||0);
    const counts=existing.querySelector('.job-counts');counts.replaceChildren(node('span','complete',`${c.succeeded||0} / ${batch.total} complete`));for(const [name,n]of [['generating',(c.running||0)+(c.publishing||0)],['queued',c.prepared||0],['cancelled',(c.cancelled||0)+(c.cancel_requested||0)],['failed',c.failed||0]])if(n)counts.append(node('span',name,`${n} ${name}`));
    const fingerprint=JSON.stringify(batch.workers,(key,value)=>key==='updated_at'?undefined:value);
    if(existing.dataset.workers!==fingerprint){existing.querySelector('.job-workers').replaceWith(workerControls(batch));existing.dataset.workers=fingerprint;}
    const list=existing.querySelector('.job-track-list'),page=state.pages.get(batch.id);
    if(page){list.querySelector(':scope>p')?.remove();const old=new Map([...list.querySelectorAll(':scope>.job-run')].map(e=>[e.dataset.run,e]));for(const run of page.runs){const row=old.get(run.id);if(!row){list.append(runRow(batch,run));continue;}old.delete(run.id);row.className='job-run '+run.status;const status=row.querySelector('.job-status');status.className='job-status '+run.status;status.textContent=label(run.status);row.querySelector('.job-configuration').textContent=metadata(run);row.querySelector('.job-worker-name').textContent=(run.worker?workerName(run.worker):run.status==='prepared'?'Next available worker':run.status==='cancelled'?'Not dispatched':'Worker not recorded');row.querySelector('.job-stage').textContent=label(run.stage);
      const signature=JSON.stringify(run.parameters);if(row.parameterSignature!==signature){row.querySelector('.job-parameters').replaceWith(parameterList(run.parameters));row.parameterSignature=signature;}
      for(const drawer of row.querySelectorAll('[data-kind]')){const attempt=run.attempt?.id||'queued';if(drawer.dataset.attempt!==attempt){drawer.dataset.attempt=attempt;drawer.loadContent();}}
      const activity=row.querySelector('.job-run-activity');activity.querySelectorAll('.job-step,.job-step-progress').forEach(e=>e.remove());if(run.step!=null&&run.steps){activity.append(node('span','job-step',`${run.step} / ${run.steps} steps`));const p=node('progress','job-step-progress');p.max=run.steps;p.value=run.step;p.setAttribute('aria-label','Sampling steps');activity.append(p);}
      if(!run.can_cancel)row.querySelector('.job-cancel')?.remove();else if(!row.querySelector('.job-cancel'))row.querySelector('.job-run-head').append(cancelButton(batch,run));
      let note=row.querySelector('.job-run-note');if(run.message){if(!note){note=node('p','job-run-note subtle small');activity.after(note);}note.textContent=run.message;}else note?.remove();
      let error=row.querySelector('.error');if(run.error){if(!error){error=node('p','error small');activity.after(error);}error.textContent=run.error;}else error?.remove();
    }for(const row of old.values())row.remove();list.querySelector(':scope>.job-more')?.remove();if(page.has_more)list.append(button('Load more configurations',async()=>{await loadRuns(batch.id,true);render();},'job-more'));}
  }
  function render(){
    const retained=new Set(state.jobs.map(j=>j.id));for(const card of root.querySelectorAll('[data-batch]'))if(!retained.has(card.dataset.batch))card.remove();
    for(const [key,title,empty]of [['active','Active jobs','No active or queued batches.'],['completed','Completed','No completed batches yet.'],['failed','Failed / cancelled','No failed or cancelled batches.']]){
      let section=root.querySelector(`[data-section="${key}"]`);if(!section){section=node('section','job-section');section.dataset.section=key;const heading=node('div','job-section-heading');heading.append(node('h2','',title),node('span','job-section-count'));section.append(heading,node('div','job-batches'));root.append(section);}
      const jobs=state.jobs.filter(j=>j.section===key),list=section.querySelector('.job-batches');section.querySelector('.job-section-count').textContent=jobs.length;
      if(jobs.length)list.querySelector('.job-empty')?.remove();else if(!list.querySelector('.job-empty'))list.append(node('p','job-empty',empty));
      for(const [index,job] of jobs.entries()){let existing=root.querySelector(`[data-batch="${job.id}"]`);if(existing)updateCard(existing,job);else existing=card(job);if(list.children[index]!==existing)list.insertBefore(existing,list.children[index]||null);}
    }
  }
  async function refresh(){const sequence=++state.sequence,data=await api('/generation/jobs');if(sequence!==state.sequence)return;state.jobs=data.jobs;for(const job of data.jobs)if(state.open.has(job.id+'/runs'))await loadRuns(job.id,false,sequence);if(sequence!==state.sequence)return;render();onQueued?.(data);return data;}
  return {refresh,hasActive:()=>state.jobs.some(j=>j.section==='active')};
}
