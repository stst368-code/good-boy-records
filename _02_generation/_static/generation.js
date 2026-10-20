import {workerLabel} from "/tools/generation/workers.js";
export function initGeneration({api,toast,browse,configureWorkers}) {
  const root=document.getElementById('generationView');
  root.innerHTML=`
  <div class="section-heading"><h1>Generation</h1><p class="subtle">Select songs, adjust the stages you want to test, then check the batch.</p></div>
  <div class="generation-grid">
  <section class="generation-panel">
    <h2>Tracks</h2><label>Writing catalogue<div class="generation-inline"><input id="genRoot"><button id="genBrowse" type="button">Browse</button><button id="genScan" type="button">Load</button></div></label>
    <div class="generation-inline"><label class="flex">Find a song or file<input id="genSearch" type="search"></label><label>Album<select id="genAlbum"><option value="">All albums</option></select></label></div>
    <div class="generation-inline"><button id="genSelectVisible" type="button">Select shown</button><button id="genClear" type="button">Clear</button><span id="genSelected" class="subtle small"></span></div><div id="genFiles" class="generation-files" aria-label="Writing inputs"></div>
    <details><summary>Selection and order</summary><label>Run order<select id="genOrder"><option value="selection">Selection order</option><option value="filename">Filename</option><option value="album">Album track order</option></select></label><label class="generation-check"><input id="genLowRated" type="checkbox">Only tracks with low saved ratings</label><div class="generation-fields"><label>Mean rating at or below<input id="genRatingMax" type="number" min="0" max="5" step="0.1" value="3"></label><label>Review cluster<input id="genRatingCluster" placeholder="Any cluster"></label><label>Review style<input id="genRatingStyle" placeholder="Any style"></label></div></details>
    <div class="generation-modules">
    <div class="generation-stage-column"><section class="generation-module"><h2>1 · Models</h2><label>Encoder / DiT selection<select id="genModelsMode"><option value="yaml">YAML, then worker defaults / available models</option><option value="worker" selected>Worker defaults / available models</option><option value="pairs">Test listed encoder + DiT pairs</option></select></label><div id="genModelPairs" class="gen-pairs"></div><button id="genAddModelPair" type="button">+ Encoder / DiT pair</button></section>
    <section class="generation-module"><h2>2 · Text conditioning</h2><div id="genEncodingControls" class="generation-fields"></div><label>Caption template<input id="genTemplate" list="genTemplateList" placeholder="Optional template ID"><datalist id="genTemplateList"></datalist></label><div class="generation-fields"><label>Caption source<select id="genCaptionMode"><option value="random">Random templates</option><option value="yaml">YAML caption</option><option value="catalogue">Catalogue style</option></select></label><label>Captions per track<input id="genCaptionCount" type="number" min="1" max="1000" value="1"></label></div><details><summary>Caption pool</summary><div class="generation-fields"><label>Cluster<input id="genCaptionCluster" placeholder="Any cluster"></label><label>Style<input id="genCaptionStyle" placeholder="Any style"></label></div></details></section>
    </div><div class="generation-stage-column"><section class="generation-module"><h2>3 · Diffusion</h2><label>Sampler / scheduler selection<select id="genSamplingMode"><option value="yaml">YAML, then worker defaults / available choices</option><option value="worker" selected>Worker defaults / available choices</option><option value="pairs">Test listed sampler + scheduler pairs</option></select></label><div id="genSamplerPairs" class="gen-pairs"></div><button id="genAddSamplerPair" type="button">+ Sampler / scheduler pair</button><div id="genDiffusionControls" class="generation-fields"></div></section>
    <section class="generation-module"><h2>4 · Queue & output</h2><div class="generation-fields"><label>Generations per combination<input id="genRepetitions" type="number" min="1" max="10000" value="1"></label><label>Combinations across stages<select id="genStrategy"><option value="grid">Test every combination</option><option value="sample">Sample random combinations</option></select></label><label id="genSamplesLabel" hidden>Random combinations<input id="genSamples" type="number" min="1" max="10000" value="1"></label><label>Repeated-run seeds<select id="genSeedPolicy"><option value="preserve">Use seed controls (automatic if blank)</option><option value="randomize">Force new seeds per generation</option></select></label><label>Conditioning cache<select id="genCache"><option value="prefer">Reuse when supported</option><option value="require">Require cached encodings (native)</option><option value="disabled">Encode every time (native)</option></select></label><label>Output grouping<select id="genLayout"><option value="single">Single inbox</option><option value="track">By track</option><option value="album">By album</option></select></label></div><p id="genEstimate" class="subtle small" role="status"></p><p class="subtle small">Each row keeps its encoder/DiT or sampler/scheduler paired. Ranges combine across stages. Blank selections inherit YAML, then each worker's defaults and available models.</p><p id="genOutput" class="subtle small"></p></section>
    </div></div><button id="genPreview" class="primary" type="button">Check selected tracks</button>
  </section>
  <section class="generation-panel generation-monitor"><h2>Workers</h2><label>Run on<select id="genWorker"><option value="auto">All available workers · shared queue</option></select></label><div class="generation-inline"><button id="genWorkerSettings" type="button">Worker settings</button><button id="genRefreshWorkers" type="button">Refresh availability</button></div><p id="genConnectionStatus" class="subtle small" role="status"></p><section id="genPlan" class="generation-plan" hidden><h2>Batch check</h2><p id="genPlanSummary"></p><div id="genPlanRows"></div><label class="generation-check"><input id="genDropErrors" type="checkbox">Drop errored inputs and run valid tracks</label><button id="genStart" class="primary" type="button">Generate checked tracks</button><button id="genCancelPreview" type="button">Cancel batch</button></section><h2>Jobs</h2><div class="generation-inline"><button id="genRefreshJobs" type="button">Refresh</button><button id="genClearLogs" type="button">Clear log view</button><button id="genRestoreLogs" type="button">Show full logs</button></div><div id="genJobs" class="generation-jobs"></div></section></div>`;
  const $=id=>document.getElementById(id);
  const state={rows:[],selected:new Set(),catalogueRoot:null,scanSequence:0,workers:[],resolved:null,token:null,preview:null,loaded:false,poll:null,busy:false,revision:0,logCursors:JSON.parse(sessionStorage.getItem('gbr-generation-log-cursors')||'{}'),tasks:[]};
  const guarded=fn=>(...args)=>Promise.resolve(fn(...args)).catch(error=>toast(error.message));
  const numericFields=[['encoder_cfg','genEncoderCfg','Encoder CFG',1.5,'genEncodingControls'],['top_k','genTopK','Top-k',50,'genEncodingControls'],['duration','genDuration','Maximum seconds',360,'genEncodingControls'],['encoder_seed','genEncoderSeed','Encoder seed','auto','genEncodingControls'],['sampler_steps','genSteps','Steps',30,'genDiffusionControls'],['sampler_cfg','genCfg','Diffusion CFG',1.7,'genDiffusionControls'],['sampler_seed','genSamplerSeed','Diffusion seed','auto','genDiffusionControls']];
  for(const [key,id,label,value,container] of numericFields){
    const seed=key.endsWith('_seed'),box=document.createElement('div');box.className='gen-control';
    box.innerHTML=`<label>${label}<select id="${id}Mode" aria-label="${label} source"><option value="yaml">YAML / worker</option><option value="worker">Worker default</option><option value="fixed">Fixed value</option><option value="list">List of values</option><option value="range">Range</option></select></label><input id="${id}" aria-label="${label} value" ${seed?'type="text"':'type="number" step="any"'} value="${value}" hidden><input id="${id}List" aria-label="${label} values" placeholder="${seed?'auto, 123, 456':'1.2, 1.5, 1.8'}" hidden><div id="${id}Range" class="gen-range" hidden><input aria-label="${label} range start" placeholder="Start" inputmode="decimal"><input aria-label="${label} range stop" placeholder="Stop" inputmode="decimal"><input aria-label="${label} range step" placeholder="Step" inputmode="decimal"></div>`;
    $(container).append(box);
    $(id+'Mode').onchange=()=>{const mode=$(id+'Mode').value;$(id).hidden=mode!=='fixed';$(id+'List').hidden=mode!=='list';$(id+'Range').hidden=mode!=='range';invalidate();};
  }
  const pairs=[{name:'Models',container:'genModelPairs',mode:'genModelsMode',button:'genAddModelPair',fields:[['text_encoder','Text encoder','text_encoders'],['dit','DiT','diffusion_models']]},{name:'Sampling',container:'genSamplerPairs',mode:'genSamplingMode',button:'genAddSamplerPair',fields:[['sampler','Sampler','samplers'],['scheduler','Scheduler','schedulers']]}];
  function modelChoices(key){
    const relevant=state.workers.filter(w=>w.enabled&&($('genWorker').value==='auto'||w.id===$('genWorker').value));
    const values=relevant.flatMap(w=>w.status?.models?.[key]||[]);
    return [...new Set(values)].sort();
  }
  function refreshChoices(){for(const group of pairs)for(const row of $(group.container).children)for(const [key,label,list] of group.fields){const select=row.querySelector(`[data-field="${key}"]`),old=select.value,values=modelChoices(list);select.replaceChildren(new Option('Worker default','auto'),...values.map(v=>new Option(v,v)));if(old&&old!=='auto'&&!values.includes(old))select.append(new Option('Unavailable · '+old,old));select.value=old||'auto';}}
  function addPair(group,values={}){
    const row=document.createElement('div');row.className='gen-pair-row';
    for(const [key,label,list] of group.fields){const field=document.createElement('label');field.textContent=label;const select=document.createElement('select');select.dataset.field=key;select.setAttribute('aria-label',label+' combination');select.append(new Option('Worker default','auto'),...modelChoices(list).map(v=>new Option(v,v)));select.value=values[key]||'auto';field.append(select);row.append(field);}
    const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remove combination');remove.onclick=()=>{row.remove();invalidate();};row.append(remove);row.onchange=()=>{$(group.mode).value='pairs';invalidate();};$(group.container).append(row);
  }
  for(const group of pairs){addPair(group);$(group.button).onclick=()=>{addPair(group);$(group.mode).value='pairs';invalidate();};$(group.mode).onchange=invalidate;}
  function connection(){return state.resolved?.connection||state.workers.find(w=>w.id===$('genWorker').value)?.connection||{backend:'automatic'};}
  function parameters(){const out={};for(const [key,id] of numericFields)if($(id+'Mode').value==='fixed'){const raw=$(id).value.trim();if(raw==='')throw Error('Enter a value or choose YAML / worker.');out[key]=key.endsWith('_seed')?raw:Number(raw);}return out;}
  function sources(){const out={};for(const [key,id] of numericFields){const mode=$(id+'Mode').value;if(mode==='fixed')out[key]='control';if(mode==='worker')out[key]='worker';}for(const group of pairs)if($(group.mode).value==='worker')for(const [key] of group.fields)out[key]='worker';return out;}
  function experiments(){
    const sweeps={},groups=[];
    for(const [key,id] of numericFields){const mode=$(id+'Mode').value;if(mode==='list'){if(!$(id+'List').value.trim())throw Error('Enter the values for '+key);sweeps[key]=$(id+'List').value.trim();}if(mode==='range'){const values=[...$(id+'Range').querySelectorAll('input')].map(e=>e.value.trim());if(values.some(v=>v===''))throw Error('Complete the start, stop and step for '+key);sweeps[key]=Object.fromEntries(['start','stop','step'].map((v,i)=>[v,values[i]]));}}
    for(const group of pairs)if($(group.mode).value==='pairs'){const rows=[...$(group.container).children].map(row=>Object.fromEntries([...row.querySelectorAll('select')].map(e=>[e.dataset.field,e.value])));if(!rows.length)throw Error('Add a '+group.name.toLowerCase()+' combination.');groups.push(rows);}
    return {sweeps,groups,repetitions:Number($('genRepetitions').value),strategy:$('genStrategy').value,samples:Number($('genSamples').value),seed_policy:$('genSeedPolicy').value};
  }
  function preferences(){return {worker_id:$('genWorker').value,input_root:$('genRoot').value,parameters:parameters(),layout:$('genLayout').value};}
  function invalidate(){state.revision++;state.token=null;state.preview=null;$('genPlan').hidden=true;estimate();}
  function estimate(){for(const group of pairs)$(group.container).hidden=$(group.mode).value!=='pairs';try{const e=experiments();let n=e.groups.reduce((n,g)=>n*g.length,1);for(const value of Object.values(e.sweeps))n*=typeof value==='object'?Math.max(0,Math.floor((Number(value.stop)-Number(value.start))/Number(value.step)+1+1e-8)):value.split(',').length;if(e.strategy==='sample')n=e.samples;const captions=$('genTemplate').value.trim()||$('genCaptionMode').value!=='random'?1:Number($('genCaptionCount').value);$('genEstimate').textContent=`Estimated ${state.selected.size*captions*n*e.repetitions} generations · ${state.selected.size} tracks × ${captions} captions × ${n} combinations × ${e.repetitions} repeats. Each job runs once on a compatible worker.`;}catch{$('genEstimate').textContent='Complete the selected ranges and combinations to estimate the batch.';}$('genSamplesLabel').hidden=$('genStrategy').value!=='sample';}
  async function save(){return api('/generation/preferences',preferences());}
  async function reloadWorkers(refresh=false){const current=$('genWorker').value;state.workers=(await api('/generation/workers'+(refresh?'?refresh=true':''))).workers;$('genWorker').replaceChildren(new Option('All available workers · shared queue','auto'),...state.workers.map(w=>new Option(workerLabel(w),w.id)));$('genWorker').value=state.workers.some(w=>w.id===current)?current:'auto';state.resolved=null;refreshChoices();invalidate();}
  async function chooseWorker(){state.resolved=null;invalidate();if($('genWorker').value==='auto'){$('genConnectionStatus').textContent='Enabled online workers share this queue. Each worker uses its own compatible models; busy workers join after their current job.';refreshChoices();return;}const id=$('genWorker').value,entry=await api('/generation/workers/query',{worker_id:id});if($('genWorker').value!==id)return;state.workers=state.workers.map(w=>w.id===id?entry:w);state.resolved=entry;refreshChoices();$('genConnectionStatus').textContent=entry.status?.error||workerLabel(entry);}
  function visible() {
    const search = $("genSearch").value.trim().toLowerCase(),
      album = $("genAlbum").value;
    return state.rows.filter(
      (row) =>
        (!album || row.album === album) &&
        (!search ||
          `${row.title} ${row.relative} ${row.song_id} ${row.variant_id}`
            .toLowerCase()
            .includes(search)),
    );
  }
  function paintFiles() {
    const shown = visible();
    $("genFiles").replaceChildren();
    for (const row of shown) {
      const label = document.createElement("label");
      label.className = "generation-file";
      const check = document.createElement("input");
      check.type = "checkbox";
      check.checked = state.selected.has(row.relative);
      check.onchange = () => {
        if (check.checked) state.selected.add(row.relative);
        else state.selected.delete(row.relative);
        invalidate();
        paintSelected();
      };
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = row.title;
      const detail = document.createElement("small");
      detail.textContent = `${row.relative} · song ${row.song_id || "?"} / variant ${row.variant_id || "?"}${row.issues.length ? " · catalogue issue" : ""}`;
      text.append(title, detail);
      label.append(check, text);
      $("genFiles").append(label);
    }
    paintSelected();
  }
  function paintSelected() {
    estimate();
    $("genSelected").textContent =
      `${state.selected.size} selected · ${visible().length} shown / ${state.rows.length}`;
  }
  function clearCatalogue() {
    state.scanSequence++;
    state.catalogueRoot = null;
    state.rows = [];
    state.selected = new Set();
    $("genAlbum").replaceChildren(new Option("All albums", ""));
    invalidate();
    paintFiles();
  }
  async function scan() {
    clearCatalogue();
    const sequence = state.scanSequence, requestedRoot = $("genRoot").value.trim();
    const result = await api(
      "/generation/catalogue?" +
        new URLSearchParams({ root: requestedRoot }),
    );
    if (sequence !== state.scanSequence || $("genRoot").value.trim() !== requestedRoot) return;
    $("genRoot").value = result.root;
    state.catalogueRoot = result.root;
    state.rows = result.inputs;
    state.selected = new Set();
    $("genAlbum").replaceChildren(new Option("All albums", ""));
    for (const album of result.albums)
      $("genAlbum").append(new Option(album, album));
    invalidate();
    paintFiles();
  }
  function paintPlan() {
    const plan = state.preview;
    $("genPlan").hidden = !plan;
    if (!plan) return;
    $("genPlanSummary").textContent =
      `${plan.ready} ready · ${plan.generation_count ?? plan.ready} generations · ${plan.invalid} errored · ${plan.workers?.length || 1} worker(s). Seeds and source copies will be frozen before dispatch.`;
    $("genPlanRows").replaceChildren();
    for (const row of plan.inputs) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${row.status === "ready" ? "Ready" : "Error"} · ${row.path.split(/[\\/]/).pop()}`;
      const text = document.createElement("pre");
      text.textContent = [
        ...row.issues.map((issue) => `${issue.severity}: ${issue.message}`),
        JSON.stringify(
          { controls: row.effective, from: row.provenance, generations: row.generation_count, experiments: row.experiments },
          null,
          2,
        ),
      ].join("\n");
      details.append(summary, text);
      $("genPlanRows").append(details);
    }
    $("genDropErrors").closest("label").hidden = plan.invalid === 0;
    $("genStart").disabled =
      !plan.ready || Boolean(plan.invalid && !$("genDropErrors").checked);
  }
  async function preview() {
    if (!state.catalogueRoot || $("genRoot").value.trim() !== state.catalogueRoot)
      throw new Error("Load the writing catalogue before checking tracks.");
    if (!state.selected.size)
      throw new Error("Select one or more writing YAMLs first.");
    const loadedRoot = state.catalogueRoot, files = [...state.selected];
    const requestedWorker = $('genWorker').value;
    await save();
    if (loadedRoot !== state.catalogueRoot || $("genRoot").value.trim() !== loadedRoot)
      throw new Error("Writing folder changed. Load it again before checking tracks.");
    const body = {
      input_root: loadedRoot,
      worker_id: requestedWorker,
      files,
      order: $("genOrder").value,
      parameters: parameters(),
      parameter_priority: "yaml",
      parameter_sources:sources(),
      template_id: $("genTemplate").value.trim() || null,
      caption_mode: $("genCaptionMode").value,
      caption_count: $("genTemplate").value.trim() || $("genCaptionMode").value !== "random" ? 1 : Number($("genCaptionCount").value),
      caption_cluster: $("genCaptionCluster").value.trim() || null,
      caption_style: $("genCaptionStyle").value.trim() || null,
      experiments: experiments(),
      encoding_cache: $("genCache").value,
      layout: $("genLayout").value,
      rating_filter: $("genLowRated").checked
        ? {
            threshold: Number($("genRatingMax").value),
            cluster: $("genRatingCluster").value.trim() || null,
            style: $("genRatingStyle").value.trim() || null,
          }
        : null,
    };
    const revision = state.revision;
    const result = await api("/generation/preview", body);
    if (revision !== state.revision || loadedRoot !== state.catalogueRoot || $("genRoot").value.trim() !== loadedRoot) return;
    state.preview = result;
    state.token = state.preview.token;
    $("genDropErrors").checked = false;
    paintPlan();
  }
  async function jobs() {
    const expanded = new Set(
      [...$("genJobs").querySelectorAll("details[open]")].map(
        (item) => item.dataset.task,
      ),
    );
    const result = await api("/generation/tasks");
    state.tasks=result.tasks;
    $("genJobs").replaceChildren();
    for (const task of result.tasks) {
      const card = document.createElement("article");
      card.className = "generation-job";
      const heading = document.createElement("h3");
      heading.textContent = `${task.batch_id ? "Batch " + task.batch_id.slice(0, 8) : "Worker check"} · ${task.status.replaceAll("_", " ")}`;
      const detail = document.createElement("p");
      detail.className = "subtle small";
      const runs = task.runs || [];
      const progress=task.batch_progress||{complete:runs.filter(r=>r.status==="succeeded").length,total:runs.length};
      detail.textContent = `${task.connection.backend.replaceAll("_", " ")}${task.connection.address ? " · " + task.connection.address : ""}${progress.total ? " · " + progress.complete + " / " + progress.total + " complete" : ""}`;
      card.append(heading, detail);
      const time=document.createElement("p");time.className="subtle small";time.textContent="Queued "+new Date(task.created_at*1000).toLocaleString()+(task.updated_at?" · Updated "+new Date(task.updated_at).toLocaleString():"");card.append(time);
      if (task.error) {
        const error = document.createElement("p");
        error.className = "error";
        error.textContent = task.error;
        card.append(error);
      }
      if (task.report?.message) {
      const message = document.createElement("p");
      message.className = "subtle small";
      message.textContent = task.report.message;
      card.append(message);
    }
    const hardware = task.report?.hardware;
      if (hardware) {
        const info = document.createElement("p");
        info.className = "subtle small";
        info.textContent = `${hardware.gpu_name || "GPU unavailable"} · ${hardware.usable_vram_bytes ? (hardware.usable_vram_bytes / 1024 ** 3).toFixed(1) + " GiB VRAM" : "VRAM not reported"}`;
        card.append(info);
      }
      const actions = document.createElement("div");
      actions.className = "generation-inline";
      const options = [];
      if (task.active && task.batch_id && task.status !== "cleaning")
        options.push(["pause", "Pause after this song"]);
      if (!task.active) {
        if (!task.batch_id) options.push(["continue", "Run check again"]);
        else if (runs.some((run) => run.status === "running")) {
          if (task.connection.backend !== "local")
            options.push(["resume", "Reconnect to existing job"]);
          else {
            const note = document.createElement("p");
            note.className = "subtle small";
            note.textContent =
              "The local controller stopped. Check its log and recover the interrupted attempt before retrying.";
            card.append(note);
          }
        } else if (runs.some((run) => run.status === "failed"))
          options.push(["retry", "Retry failed tracks"]);
        else if (runs.some((run) => run.status === "prepared"))
          options.push(["continue", "Continue queue"]);
        if (task.cleanup_pending)
          options.push(["cleanup", "Clean temporary files"]);
      }
      for (const [action, label] of options) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = label;
        button.onclick = guarded(async () => {
          await api(`/generation/tasks/${task.id}`, { action });
          await jobs();
        });
        actions.append(button);
      }
      card.append(actions);
      if (task.outputs?.length) {
        const location = document.createElement("p");
        location.className = "generation-output-path";
        location.textContent =
          task.outputs.find((item) => item.kind === "audio")?.uri || "";
        card.append(location);
      }
      if (task.log) {
        const log = document.createElement("details");
        log.dataset.task = task.id;
        log.open = expanded.has(task.id);
        const summary = document.createElement("summary");
        summary.textContent = "Progress log";
        const text = document.createElement("pre");
        const cursor=state.logCursors[task.id]||0,bytes=new TextEncoder().encode(task.log);
        text.textContent = new TextDecoder().decode(bytes.slice(Math.max(0,cursor-(task.log_start||0)))) || 'Log view cleared. New events will appear here.';
        const clear=document.createElement('button');clear.type='button';clear.textContent='Clear this log';clear.onclick=()=>{state.logCursors[task.id]=task.log_end;sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.logCursors));jobs().catch(e=>toast(e.message));};log.append(clear);
        log.append(summary, text);
        card.append(log);
      }
      $("genJobs").append(card);
    }
    if (!result.tasks.length)
      $("genJobs").textContent = "No generation jobs yet.";
  }
  $('genClearLogs').onclick=guarded(async()=>{for(const task of state.tasks)state.logCursors[task.id]=task.log_end||0;sessionStorage.setItem('gbr-generation-log-cursors',JSON.stringify(state.logCursors));await jobs();});
  $('genRestoreLogs').onclick=guarded(async()=>{state.logCursors={};sessionStorage.removeItem('gbr-generation-log-cursors');await jobs();});
  $('genWorker').onchange = guarded(chooseWorker);
  $('genWorkerSettings').onclick = guarded(configureWorkers);
  $('genRefreshWorkers').onclick = guarded(async () => {await reloadWorkers(true); await chooseWorker();});
  $("genBrowse").onclick = guarded(() =>
    browse((path) => {
      $("genRoot").value = path;
      invalidate();
    }),
  );
  $("genScan").onclick = guarded(scan);
  $("genSearch").oninput = paintFiles;
  $("genAlbum").onchange = paintFiles;
  $("genSelectVisible").onclick = () => {
    for (const row of visible()) state.selected.add(row.relative);
    invalidate();
    paintFiles();
  };
  $("genClear").onclick = () => {
    state.selected.clear();
    invalidate();
    paintFiles();
  };
  $("genPreview").onclick = guarded(async () => {
    if ($('genPreview').disabled) return;
    $('genPreview').disabled = true;
    $('genConnectionStatus').textContent = 'Checking worker availability…';
    try {await preview();} catch (error) {$('genConnectionStatus').textContent = error.message; throw error;} finally {$('genPreview').disabled = false;}
  });
  $("genDropErrors").onchange = paintPlan;
  $("genCancelPreview").onclick = invalidate;
  $("genRefreshJobs").onclick = guarded(jobs);
  $("genStart").onclick = guarded(async () => {
    if (!state.token || state.busy) return;
    state.busy = true;
    $("genStart").disabled = true;
    try {
      await api("/generation/start", {
        token: state.token,
        skip_errors: $("genDropErrors").checked,
      });
      invalidate();
      await jobs();
      toast("Batch queued. You can continue reviewing.");
    } finally {
      state.busy = false;
    }
  });
  $("genRoot").addEventListener("input", clearCatalogue);
  for (const element of root.querySelectorAll("input,select,textarea"))
    if (
      ![
        "genSearch",
        "genAlbum",
        "genWorker",
        "genDropErrors",
      ].includes(element.id)
    )
      element.addEventListener("input", invalidate);
  return {
    reloadWorkers,
    writingChanged(kind) {if(kind==='track')state.catalogueDirty=true;state.templatesDirty=true;invalidate();},
    async open() {
      if (!state.loaded) {
        const prefs = await api("/generation/preferences");
        $("genRoot").value = prefs.input_root;
        $("genOutput").textContent =
          `Inbox: ${prefs.output_root}. Each generation keeps its FLAC, completed YAML, source and report together until review.`;
        await reloadWorkers(true);
        if (state.workers.some(w => w.id === prefs.worker_id)) $('genWorker').value = prefs.worker_id;
        $("genLayout").value = prefs.layout;
        const templates = await api("/generation/templates");
        for (const item of templates.templates) {
          const option = new Option(
            `${item.cluster} / ${item.style} · ${item.source || ""}`,
            item.id,
          );
          $("genTemplateList").append(option);
        }
        state.loaded = true;
        await chooseWorker();
        if (!prefs.shared_database_ready)
          toast(
            "Generation needs the shared platform database. Open GBR through _97_app.",
          );
        await scan();
        state.catalogueDirty=false;state.templatesDirty=false;
      }
      if(state.catalogueDirty){await scan();state.catalogueDirty=false;}
      if(state.templatesDirty){const result=await api('/generation/templates');$('genTemplateList').replaceChildren(...result.templates.map(t=>new Option(t.cluster+' / '+t.style+' · '+t.source,t.id)));state.templatesDirty=false;}
      await jobs();
      clearInterval(state.poll);
      state.poll = setInterval(() => {
        if (!root.hidden) jobs().catch(() => {});
      }, 4000);
    },
  };
}
