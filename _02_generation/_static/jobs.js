import {experimentPublishing} from "/tools/generation/experiment-publishing.js";
const LABELS={completed:'Complete',prepared:'Queued',running:'Generating',publishing:'Finalising',cancel_requested:'Cancelling',cancelled:'Cancelled',succeeded:'Complete',failed:'Failed',waiting_worker:'Worker busy · waiting',paused:'Paused',queue_complete:'Queue finished',starting_worker:'Starting worker',comfy_queued:'Queued in ComfyUI',comfy_generating:'Generating in ComfyUI',conditioning:'Text conditioning',diffusion:'Diffusion',decode:'Decoding audio',write_flac:'Writing FLAC',downloading:'Downloading audio',cancel_after_generation:'Finishes current generation, then discards output',dedicated_generating:'Generating on pod',serverless_generating:'Generating serverless',reconnect:'Reconnect needed'};
const label=value=>LABELS[value]||String(value||'Waiting').replaceAll('_',' ');
const workerName=value=>value?.startsWith('local|')?'This PC':value?.replace(/^comfyui\|/,'ComfyUI · ').replace(/^runpod_dedicated\|/,'Pod · ').replace(/^runpod_serverless\|/,'Serverless · ')||'Next available worker';
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};
const time=value=>value?new Date(value).toLocaleString():'';
const model=value=>String(value||'Default').split(/[\\/]/).pop().replace(/\.(safetensors|gguf|ckpt)$/,'');

export function initJobs({root,api,toast,onQueued}){
  const state={purpose:'production',sorts:new Map(),open:new Set(),pages:new Map(),texts:new Map(),logs:new Map(),busy:new Set(),jobs:[],sequence:0,cursors:JSON.parse(sessionStorage.getItem('gbr-generation-log-cursors')||'{}')};
  const guard=fn=>async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};
  const publication=experimentPublishing({api,toast,refresh});
  const button=(text,fn,cls='')=>{const b=node('button',cls,text);b.type='button';b.onclick=guard(fn);return b;};
  function drawer(key,title){const e=node('details','job-drawer');e.dataset.key=key;e.open=state.open.has(key);e.append(node('summary','',title));e.addEventListener('toggle',()=>{if(!e.isConnected)return;if(e.open)state.open.add(key);else state.open.delete(key);});return e;}
  function metadata(run){const p=run.parameters;return [['Encoder',model(p.text_encoder)],['DiT',model(p.dit)],['Sampling',p.sampler+' / '+p.scheduler],['Encoder CFG',p.encoder_cfg],['Diffusion CFG',p.sampler_cfg],['Top-k',p.top_k],['Steps',p.sampler_steps],['Seconds',p.duration]].map(([k,v])=>k+': '+v).join(' · ');}
  async function loadRuns(id,more=false,sequence=null){
    const old=state.pages.get(id),sort=state.sorts.get(id)||'created';let data;
    if(more){const next=await api(`/generation/jobs/${id}?offset=${old?.runs.length||0}&limit=100&sort=${sort}`);data={...next,runs:[...(old?.runs||[]),...next.runs]};}
    else{const count=Math.max(100,old?.runs.length||100),parts=await Promise.all(Array.from({length:Math.ceil(count/200)},(_,i)=>api(`/generation/jobs/${id}?offset=${i*200}&limit=${Math.min(200,count-i*200)}&sort=${sort}`)));data={...parts.at(-1),offset:0,runs:parts.flatMap(p=>p.runs)};}
    if(sequence==null||sequence===state.sequence)state.pages.set(id,data);
  }
  function textDrawer(batch,run,kind){
    const key=`${batch.id}/${run.id}/${kind}`,e=drawer(key,kind==='caption'?'Caption':'Lyrics'),pre=node('pre','job-song-text');e.dataset.kind=kind;e.dataset.attempt=run.attempt?.id||'queued';e.append(pre);
    const load=guard(async()=>{if(!e.open)return;const cacheKey=batch.id+'/'+run.id+'/'+e.dataset.attempt;if(!state.texts.has(cacheKey))state.texts.set(cacheKey,await api(`/generation/jobs/${batch.id}/runs/${run.id}/text`));pre.textContent=state.texts.get(cacheKey)[kind]||'No '+kind+' supplied.';});
    e.loadContent=load;e.addEventListener('toggle',load);if(e.open)load();return e;
  }
  function parameterList(parameters){const dl=node('dl','job-parameters');for(const [key,value]of Object.entries(parameters))dl.append(node('dt','',key),node('dd','',String(value)));return dl;}
  function cancelButton(batch,run){const b=button('Cancel',async()=>{if(state.busy.has(run.id))return;state.busy.add(run.id);b.disabled=true;try{await api(`/generation/jobs/${batch.id}/runs/${run.id}/cancel`,{});await refresh();}finally{state.busy.delete(run.id);}} ,'job-cancel');b.disabled=state.busy.has(run.id);b.setAttribute('aria-label','Cancel '+run.title+' configuration '+run.id.slice(0,8));return b;}
  function experimentResult(batch,run){
    const panel=node('section','experiment-result');panel.signature=JSON.stringify([run.observations,run.output]);
    const latest=run.observations?.at(-1),seconds=v=>v==null?'Not measured':v.toFixed(2)+' s',memory=v=>v==null?'Not measured':(v/1024**3).toFixed(2)+' GiB';
    if(latest){const measurements=node('dl','experiment-measurements');for(const [name,value] of [['Total',seconds(latest.wall_seconds)],['Conditioning',seconds(latest.conditioning_seconds)],[latest.diffusion_seconds==null&&latest.comfy_execution_seconds!=null?'Comfy execution':'Diffusion',seconds(latest.diffusion_seconds??latest.comfy_execution_seconds)],['Encoding cache',latest.cache_status||'Not reported'],['GPU',latest.gpu_name||'Not reported'],['Peak allocated',memory(latest.peak_allocated_bytes)],['Peak device use',memory(latest.peak_observed_device_bytes)],['Cost',latest.estimated_cost==null?'Not priced':latest.estimated_cost+' '+(latest.currency||'')]])measurements.append(node('dt','',name),node('dd','',value));panel.append(measurements);
      const full=drawer(batch.id+'/'+run.id+'/measurements',`Attempts & measurements · ${run.observations.length}`),pre=node('pre','job-song-text');pre.textContent=JSON.stringify(run.observations,null,2);full.append(pre);panel.append(full);}
    const output=run.output||{},actions=node('div','experiment-actions');
    if(output.available){const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=`/api/generation/experiments/${run.id}/audio`;audio.setAttribute('aria-label','Listen to experiment '+run.title);panel.append(audio);}
    const action=(kind,title)=>{const b=button(title,async()=>{if(state.busy.has(run.id))return;if(kind==='delete-output'&&!confirm('Delete this experiment’s output files? Timings, hardware data and encoding caches will be kept.'))return;state.busy.add(run.id);b.disabled=true;try{await api(`/generation/experiments/${run.id}/${kind}`,{});await refresh();toast(kind==='promote'?'Moved to the review inbox. Original timings and provenance retained.':'Output files deleted. Measurements and encoding caches retained.');}finally{state.busy.delete(run.id);b.disabled=false;}});b.disabled=state.busy.has(run.id);return b;};
    if(run.status==='succeeded'&&['saved','promoting'].includes(output.state))actions.append(action('promote',output.state==='promoting'?'Retry send to review':'Send to review'));
    if(run.status==='succeeded'&&['saved','deleting'].includes(output.state))actions.append(action('delete-output',output.state==='deleting'?'Retry file deletion':'Delete output files'));
    if(output.state==='promoted')actions.append(node('span','subtle small','Sent to review · '+time(output.promoted_at)));
    if(output.state==='deleted')actions.append(node('span','subtle small','Output files deleted · measurements retained'));
    panel.append(actions);return panel;
  }
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
    const config=drawer(batch.id+'/'+run.id+'/config','All parameters');config.append(parameterList(run.parameters));details.append(config);e.append(details);if(batch.purpose==='experiment')e.append(experimentResult(batch,run));return e;
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
  function assignmentControls(batch){
    const box=node('div','job-assignment');if(!batch.counts.prepared)return box;
    const label=node('label','job-auto-workers'),auto=node('input');auto.type='checkbox';auto.checked=!!batch.automatic_workers;auto.setAttribute('aria-label','Automatically add available workers to batch '+batch.id.slice(0,8));
    auto.onchange=guard(async()=>{auto.disabled=true;try{await api(`/generation/jobs/${batch.id}/auto-workers`,{enabled:auto.checked});await refresh();}finally{auto.disabled=false;}});label.append(auto,node('span','','Automatically join new workers'));box.append(label);
    const choices=node('select');choices.setAttribute('aria-label','Additional worker for batch '+batch.id.slice(0,8));choices.append(new Option('Choose another worker…',''));
    for(const worker of batch.available_workers||[])choices.append(new Option(workerName(worker.id),worker.id));
    const join=button('Add worker',async()=>{if(!choices.value)return;join.disabled=true;join.textContent='Checking worker…';try{const result=await api(`/generation/jobs/${batch.id}/workers`,{worker_id:choices.value});toast(result.status==='already_assigned'?'Worker already assigned.':`Worker added · ${result.eligible} compatible queued configurations`);await refresh();}finally{join.textContent='Add worker';join.disabled=!choices.value;}});join.disabled=true;choices.onchange=()=>{join.disabled=!choices.value;};
    if(batch.available_workers?.length){const row=node('div','generation-inline job-add-worker');row.append(choices,join);box.append(row);}
    const note=node('span','subtle small','Queued configurations only; active songs stay with their worker.');box.append(note);
    for(const notice of batch.worker_notices||[])box.append(node('p','subtle small',workerName(notice.worker_id)+': '+notice.message));return box;
  }
  function diagnosticDrawer(batch){
    const key=batch.id+'/diagnostics',e=drawer(key,'Technical log'),body=node('div','job-diagnostics');e.append(body);
    const load=guard(async()=>{
      if(!e.open)return;
      if(!state.logRequest)state.logRequest=api('/generation/tasks').finally(()=>{state.logRequest=null;});
      const result=await state.logRequest,tasks=result.tasks.filter(t=>t.batch_id===batch.id);state.logs.set(batch.id,tasks);
      const retained=new Set(tasks.map(t=>t.id));for(const section of body.children)if(!retained.has(section.dataset.task))section.remove();
      for(const task of tasks){
        let section=body.querySelector(`[data-task="${task.id}"]`);
        if(!section){
          section=node('section');section.dataset.task=task.id;const title=node('div','generation-inline');
          title.append(node('strong','',workerName(task.worker_id||task.connection.backend+'|'+(task.connection.address||''))),button('Clear log view',async()=>{state.cursors[task.id]=state.logs.get(batch.id).find(t=>t.id===task.id)?.log_end||0;sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.cursors));await load();}),button('Show full log',async()=>{delete state.cursors[task.id];sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.cursors));await load();}));
          section.append(title,node('pre'));body.append(section);
        }
        const bytes=new TextEncoder().encode(task.log||''),shown=new TextDecoder().decode(bytes.slice(Math.max(0,(state.cursors[task.id]||0)-(task.log_start||0))))||'Log view cleared. New events will appear here.',pre=section.querySelector('pre');
        if(pre.textContent!==shown){const follow=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-8;pre.textContent=shown;if(follow)pre.scrollTop=pre.scrollHeight;}
      }
    });
    e.refreshLog=load;e.addEventListener('toggle',load);if(e.open)load();return e;
  }
  function card(batch){
    const e=node('article','job-batch '+batch.section);e.dataset.batch=batch.id;
    const c=batch.counts,header=node('div','job-batch-heading'),title=node('div');title.append(node('h3','',`${batch.purpose==='experiment'?'Experiment':'Batch'} ${batch.experiment_label||batch.id.slice(0,8)}`),node('span','subtle small',time(batch.created_at)));header.append(title,node('span','job-status '+batch.status,label(batch.status)));e.append(header);
    const completed=c.succeeded||0,ended=completed+(c.cancelled||0)+(c.failed||0),bar=node('progress','job-batch-progress');bar.max=batch.total||1;bar.value=ended;bar.setAttribute('aria-label','Configurations resolved');e.append(bar);
    const counts=node('div','job-counts');counts.append(node('span','complete',`${completed} / ${batch.total} complete`));for(const [name,n]of [['generating',(c.running||0)+(c.publishing||0)],['queued',c.prepared||0],['cancelled',(c.cancelled||0)+(c.cancel_requested||0)],['failed',c.failed||0]])if(n)counts.append(node('span',name,`${n} ${name}`));e.append(counts,workerControls(batch),assignmentControls(batch));
    const tools=node('div','experiment-batch-tools');
    if(batch.purpose==='experiment'){const caption=node('label','','Order results'),sort=node('select');sort.setAttribute('aria-label','Order experiment results');for(const [name,value] of [['Generation order','created'],['Steps · low to high','steps'],['Total time · fastest first','time']])sort.append(new Option(name,value));sort.value=state.sorts.get(batch.id)||'created';sort.onchange=guard(async()=>{state.sorts.set(batch.id,sort.value);state.pages.delete(batch.id);await loadRuns(batch.id);render();});caption.append(sort);tools.append(caption);}
    const download=node('a','experiment-export','Export performance CSV');download.href=`/api/generation/jobs/${batch.id}/performance.csv`;tools.append(download);e.append(tools);if(batch.purpose==='experiment')e.append(publication.controls(batch));
    const runs=drawer(batch.id+'/runs',`Tracks & configurations · ${batch.total}`);runs.classList.add('job-track-list');
    const page=state.pages.get(batch.id);if(page){for(const run of page.runs)runs.append(runRow(batch,run));if(page.has_more)runs.append(button('Load more configurations',async()=>{await loadRuns(batch.id,true);render();},'job-more'));}else runs.append(node('p','subtle small','Expand to load this batch.'));
    runs.addEventListener('toggle',guard(async()=>{if(runs.open&&!state.pages.has(batch.id)){await loadRuns(batch.id);render();}}));e.append(runs,diagnosticDrawer(batch));return e;
  }
  function updateCard(existing,batch){
    existing.className='job-batch '+batch.section;
    if(batch.purpose==='experiment')publication.update(existing.querySelector('.comparison-publishing'),batch);
    const badge=existing.querySelector('.job-batch-heading .job-status');badge.className='job-status '+batch.status;badge.textContent=label(batch.status);
    const bar=existing.querySelector('.job-batch-progress'),c=batch.counts;bar.max=batch.total||1;bar.value=(c.succeeded||0)+(c.cancelled||0)+(c.failed||0);
    const counts=existing.querySelector('.job-counts');counts.replaceChildren(node('span','complete',`${c.succeeded||0} / ${batch.total} complete`));for(const [name,n]of [['generating',(c.running||0)+(c.publishing||0)],['queued',c.prepared||0],['cancelled',(c.cancelled||0)+(c.cancel_requested||0)],['failed',c.failed||0]])if(n)counts.append(node('span',name,`${n} ${name}`));
    const fingerprint=JSON.stringify(batch.workers,(key,value)=>key==='updated_at'?undefined:value);
    if(existing.dataset.workers!==fingerprint){existing.querySelector('.job-workers').replaceWith(workerControls(batch));existing.dataset.workers=fingerprint;}
    const assignment=JSON.stringify([!!batch.counts.prepared,batch.automatic_workers,batch.available_workers,batch.worker_notices]);if(existing.assignmentSignature!==assignment){existing.querySelector('.job-assignment').replaceWith(assignmentControls(batch));existing.assignmentSignature=assignment;}
    const list=existing.querySelector('.job-track-list'),page=state.pages.get(batch.id);
    if(page){list.querySelector(':scope>p')?.remove();const old=new Map([...list.querySelectorAll(':scope>.job-run')].map(e=>[e.dataset.run,e]));let cursor=list.querySelector(':scope>.job-run')||list.querySelector(':scope>.job-more');for(const run of page.runs){const row=old.get(run.id);if(!row){list.insertBefore(runRow(batch,run),cursor);continue;}if(row!==cursor)list.insertBefore(row,cursor);else cursor=row.nextElementSibling;old.delete(run.id);row.className='job-run '+run.status;const status=row.querySelector('.job-status');status.className='job-status '+run.status;status.textContent=label(run.status);row.querySelector('.job-configuration').textContent=metadata(run);row.querySelector('.job-worker-name').textContent=(run.worker?workerName(run.worker):run.status==='prepared'?'Next available worker':run.status==='cancelled'?'Not dispatched':'Worker not recorded');row.querySelector('.job-stage').textContent=label(run.stage);
      const signature=JSON.stringify(run.parameters);if(row.parameterSignature!==signature){row.querySelector('.job-parameters').replaceWith(parameterList(run.parameters));row.parameterSignature=signature;}
      if(batch.purpose==='experiment'){const panel=row.querySelector('.experiment-result'),signature=JSON.stringify([run.observations,run.output]);if(!panel)row.append(experimentResult(batch,run));else if(panel.signature!==signature)panel.replaceWith(experimentResult(batch,run));}
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
  async function refresh(){const sequence=++state.sequence,data=await api('/generation/jobs?purpose='+state.purpose);if(sequence!==state.sequence)return;state.jobs=data.jobs;for(const job of data.jobs)if(state.open.has(job.id+'/runs'))await loadRuns(job.id,false,sequence);if(sequence!==state.sequence)return;render();await Promise.all([...root.querySelectorAll('details[open]')].filter(e=>e.refreshLog).map(e=>e.refreshLog()));onQueued?.(data);return data;}
  return {refresh,setPurpose(purpose){if(state.purpose===purpose)return;state.purpose=purpose;state.sequence++;state.jobs=[];root.replaceChildren();},hasActive:()=>state.jobs.some(j=>j.section==='active')};
}
