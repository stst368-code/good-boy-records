import {selectChoices} from "/static/choices.js";
export function workerLabel(worker) {
  const c = worker.connection;
  const name = {local: 'Native · this PC', comfyui: 'ComfyUI', runpod_dedicated: 'Pod', runpod_serverless: 'Serverless'}[c.backend];
  const s = worker.status;
  return `${name}${c.address ? ' · ' + c.address : ''}${worker.enabled === false ? ' · disabled' : s?.online ? s.busy ? ' · busy' : ' · idle' : s?.online === false ? ' · offline' : ' · unchecked'}`;
}

export function initWorkers({api, toast, onChange, configureWriting}) {
  const root = document.getElementById('workerSettingsDialog');
  root.innerHTML = `<div class="dialog-heading"><h2>Worker settings</h2><button id="workersClose" class="close" aria-label="Close worker settings">×</button></div><button id="workerWritingSettings" type="button">Writing model · llama.cpp</button>
  <div class="generation-inline"><label class="flex">Saved worker<select id="workerSaved"></select></label><button id="workerNew" type="button">Add worker</button><button id="workersRefresh" type="button">Query all</button></div>
  <div class="generation-grid"><section class="generation-panel">
    <label>Worker type<select id="workerBackend"><option value="local">Native · this PC</option><option value="comfyui">ComfyUI · local or pod URL</option><option value="runpod_dedicated">RunPod dedicated · SSH</option><option value="runpod_serverless">RunPod Serverless</option></select></label>
    <label id="workerAddressWrap">Worker address<input id="workerAddress"></label>
    <div id="workerKeyWrap"><label>Private SSH key<select id="workerKeyChoice"></select></label><label id="workerCustomKey" hidden>Other SSH key file<input id="workerKey"></label></div>
    <label id="workerPythonWrap">Native worker Python <span class="subtle">(optional)</span><input id="workerPython"></label>
    <label id="workerMemoryWrap">Memory mode<select id="workerMemory"><option value="auto">Automatic</option><option value="low_vram">CPU offload</option><option value="resident_stage">Active stage on GPU</option></select></label>
    <label id="workerSetupWrap" class="generation-check"><input id="workerSetup" type="checkbox" checked>Prepare native worker automatically</label>
    <label class="generation-check"><input id="workerEnabled" type="checkbox" checked>Include in Automatic selection</label>
    <details id="workerLauncher"><summary>Local ComfyUI start file</summary><label>Absolute start-file path<input id="workerStartFile" placeholder="E:\\ComfyUI_windows_portable\\run_nvidia_gpu.bat"></label><label>Arguments <span class="subtle">(JSON list, optional)</span><input id="workerStartArgs" placeholder='["--listen", "127.0.0.1"]'></label><label>Working folder <span class="subtle">(blank uses start-file folder)</span><input id="workerStartCwd"></label><label>Python for a .py start file <span class="subtle">(optional)</span><input id="workerStartPython"></label><label class="generation-check"><input id="workerAutoStart" type="checkbox">Auto-start when a generation needs this worker</label></details>
    <div id="workerServerless" hidden><label>RunPod API key <span class="subtle">(session only)</span><input id="workerApiKey" type="password" autocomplete="new-password"></label><details><summary>S3 / R2 result transfer</summary><label>Endpoint<input id="workerStorageEndpoint"></label><label>Bucket<input id="workerStorageBucket"></label><label>Region<input id="workerStorageRegion" value="auto"></label><label>Access key<input id="workerStorageAccess" type="password" autocomplete="new-password"></label><label>Secret key<input id="workerStorageSecret" type="password" autocomplete="new-password"></label></details></div>
    <div class="generation-inline"><button id="workerSave" class="primary" type="button">Save worker</button><button id="workerDelete" type="button">Remove</button></div>
  </section><section class="generation-panel"><h2>Discovery & defaults</h2><div class="generation-inline"><button id="workerQuery" type="button">Query models & GPU</button><button id="workerStart" type="button">Start / query</button><button id="workerPrepare" type="button">Prepare worker</button></div>
    <p id="workerStatus" class="subtle small" role="status"></p><p id="workerGpu" class="small"></p><p id="workerSupport" class="subtle small"></p>
    <div class="generation-fields"><label>Encoder default<select id="workerEncoder"></select></label><label>DiT default<select id="workerDit"></select></label><label>Sampler default<select id="workerSampler"></select></label><label>Schedule default<select id="workerScheduler"></select></label><label>Encoder CFG<input id="workerEncoderCfg" type="number" min="0" step="any" placeholder="Generation setting"></label><label>Top-k<input id="workerTopK" type="number" min="1" step="1" placeholder="Generation setting"></label><label>Diffusion CFG<input id="workerCfg" type="number" min="0" step="any" placeholder="Generation setting"></label><label>Diffusion steps<input id="workerSteps" type="number" min="1" step="1" placeholder="Generation setting"></label><label>Maximum seconds<input id="workerDuration" type="number" min="0.04" max="360" step="any" placeholder="Generation setting"></label></div>
    <p class="subtle small">Defaults fill untouched Generation controls. YAML priority and explicit experiment values still apply. Models and hardware are queried again when jobs start.</p><p id="workerVae" class="subtle small"></p><p id="workerMessage" class="small" role="status"></p>
  </section></div>`;
  const $ = id => document.getElementById(id);
  let sshKeys=[];
  function keyChoices(){selectChoices($('workerKeyChoice'),[...sshKeys,{value:'@other',label:'Other key file…'}],{empty:'SSH default identity',value:$('workerKey').value});$('workerCustomKey').hidden=true;}
  $('workerKeyChoice').onchange=()=>{$('workerCustomKey').hidden=$('workerKeyChoice').value!=='@other';$('workerKey').value=$('workerKeyChoice').value==='@other'?'':$('workerKeyChoice').value;};
  const fields = {workerEncoder:'text_encoder',workerDit:'dit',workerSampler:'sampler',workerScheduler:'scheduler',workerEncoderCfg:'encoder_cfg',workerTopK:'top_k',workerCfg:'sampler_cfg',workerSteps:'sampler_steps',workerDuration:'duration'};
  let entries = [], current = null, busy = false;
  const guarded = fn => async () => {
    if (busy) return;
    busy = true; $('workerMessage').textContent = 'Working…';
    for (const id of ['workerSave','workerDelete','workerQuery','workerStart','workerPrepare','workersRefresh','workerNew','workerSaved','workerBackend']) $(id).disabled = true;
    try {await fn(); $('workerMessage').textContent = '';}
    catch(error) {$('workerMessage').textContent = error.message; toast(error.message);}
    finally {busy = false; for (const id of ['workerSave','workerDelete','workerQuery','workerStart','workerPrepare','workersRefresh','workerNew','workerSaved','workerBackend']) $(id).disabled = false;}
  };
  function localComfy() {
    if ($('workerBackend').value !== 'comfyui') return false;
    if (!$('workerAddress').value.trim()) return true;
    try {const host = new URL($('workerAddress').value.trim()).hostname; return host === 'localhost' || host === '[::1]' || /^127\.[0-9.]+$/.test(host);} catch {return false;}
  }
  function mode() {
    const type = $('workerBackend').value;
    $('workerAddressWrap').hidden = type === 'local';
    $('workerKeyWrap').hidden = type !== 'runpod_dedicated';
    $('workerPythonWrap').hidden = type !== 'local';
    $('workerMemoryWrap').hidden = type === 'comfyui';
    $('workerSetupWrap').hidden = ['comfyui','runpod_serverless'].includes(type);
    $('workerLauncher').hidden = !localComfy();
    $('workerStart').hidden = !localComfy();
    $('workerPrepare').hidden = ['comfyui','runpod_serverless'].includes(type);
    $('workerServerless').hidden = type !== 'runpod_serverless';
    $('workerAddress').placeholder = {comfyui:'http://127.0.0.1:8188',runpod_dedicated:'ssh://root@IP:PORT',runpod_serverless:'RunPod endpoint ID'}[type] || '';
  }
  function choices(id, values, selected) {
    const node = $(id); node.replaceChildren(new Option('Use Generation setting', ''));
    for (const value of values || []) node.append(new Option(value, value));
    if (selected && ![...node.options].some(o => o.value === String(selected))) node.append(new Option(selected, selected));
    node.value = selected || '';
  }
  function report(status = {}) {
    $('workerStatus').textContent = status.online === true ? `${status.busy ? 'Online · busy' : 'Online · idle'}${status.queue_pending ? ' · '+status.queue_pending+' queued' : ''}` : status.error || 'Query to discover supported models and hardware.';
    const gpu = status.hardware;
    $('workerGpu').textContent = gpu ? `${gpu.gpu_name || 'GPU not reported'}${gpu.usable_vram_bytes ? ' · '+(gpu.usable_vram_bytes / 1024**3).toFixed(1)+' GiB VRAM' : ''}${gpu.free_vram_bytes_at_start != null ? ' · '+(gpu.free_vram_bytes_at_start / 1024**3).toFixed(1)+' GiB free' : ''}` : status.hardware_pending ? 'Serverless hardware is measured when a job starts.' : '';
    $('workerSupport').textContent = status.missing_nodes?.length ? 'Missing nodes: '+status.missing_nodes.join(', ') : $('workerBackend').value === 'comfyui' && status.online ? `MiniMax Music3 · graph cache · ${status.capabilities?.verified_output_cleanup ? 'verified output cleanup available' : 'install gbr_bridge.py in ComfyUI/custom_nodes for output cleanup'}${status.capabilities?.checkpoint_sha256 ? ' · checkpoint hashing available' : ''}` : '';
    $('workerVae').textContent = status.models?.vaes?.length ? 'VAE: '+status.models.vaes.join(', ') : '';
    const models = status.models || {};
    for (const [id, key] of [['workerEncoder','text_encoders'],['workerDit','diffusion_models'],['workerSampler','samplers'],['workerScheduler','schedulers']]) choices(id, models[key], $(id).value);
  }
  function fill(entry = null) {
    current = entry?.id || null;
    const c = entry?.connection || {backend:'comfyui', memory_profile:'auto', auto_setup:true};
    $('workerBackend').value = c.backend; $('workerAddress').value = c.address || ''; $('workerKey').value = c.identity_file || '';keyChoices();
    $('workerPython').value = c.worker_python || ''; $('workerMemory').value = c.memory_profile || 'auto'; $('workerSetup').checked = c.auto_setup !== false; $('workerEnabled').checked = entry?.enabled !== false;
    const launcher = entry?.launcher || {};
    for (const [id,key] of [['workerStartFile','path'],['workerStartCwd','cwd'],['workerStartPython','python']]) $(id).value = launcher[key] || '';
    $('workerStartArgs').value = launcher.args?.length ? JSON.stringify(launcher.args) : ''; $('workerAutoStart').checked = !!launcher.auto_start;
    for (const [id,key] of Object.entries(fields)) {
      if ($(id).tagName === 'SELECT') choices(id, [], entry?.defaults?.[key]);
      else $(id).value = entry?.defaults?.[key] ?? '';
    }
    $('workerSaved').value = current || ''; mode(); report(entry?.status);
  }
  async function load(refresh = false) {
    entries = (await api('/generation/workers'+(refresh ? '?refresh=true' : ''))).workers;
    $('workerSaved').replaceChildren(new Option('New worker', ''), ...entries.map(e => new Option(workerLabel(e), e.id)));
    $('workerSaved').value = current || '';
    await onChange?.();
  }
  function body() {
    const type = $('workerBackend').value, defaults = {};
    for (const [id,key] of Object.entries(fields)) if ($(id).value !== '') defaults[key] = $(id).tagName === 'SELECT' ? $(id).value : Number($(id).value);
    return {previous_id:current, connection:{backend:type,address:type === 'local' ? '' : $('workerAddress').value.trim(),identity_file:type === 'runpod_dedicated' ? $('workerKey').value.trim() : '',worker_python:type === 'local' ? $('workerPython').value.trim() : '',memory_profile:$('workerMemory').value,auto_setup:$('workerSetup').checked},
      enabled:$('workerEnabled').checked, defaults,
      launcher:localComfy() ? {path:$('workerStartFile').value.trim(),args:$('workerStartArgs').value.trim() ? JSON.parse($('workerStartArgs').value) : [],cwd:$('workerStartCwd').value.trim(),python:$('workerStartPython').value.trim(),auto_start:$('workerAutoStart').checked} : {},
      storage_endpoint:$('workerStorageEndpoint').value.trim(),storage_bucket:$('workerStorageBucket').value.trim(),storage_region:$('workerStorageRegion').value.trim() || 'auto',
      secrets:{api_key:$('workerApiKey').value,s3_access_key:$('workerStorageAccess').value,s3_secret_key:$('workerStorageSecret').value}};
  }
  async function save() {
    const entry = await api('/generation/workers', body()); current = entry.id;
    for (const id of ['workerApiKey','workerStorageAccess','workerStorageSecret']) $(id).value = '';
    await load(); return entry;
  }
  $('workersClose').onclick = () => root.close();
  $('workerWritingSettings').onclick = () => {root.close();configureWriting?.();};
  $('workerBackend').onchange = () => {mode(); for (const id of Object.keys(fields)) {if ($(id).tagName === 'SELECT') choices(id, [], '');} report();};
  $('workerAddress').oninput = mode;
  $('workerSaved').onchange = () => fill(entries.find(e => e.id === $('workerSaved').value));
  $('workerNew').onclick = () => fill();
  $('workerSave').onclick = guarded(async () => {await save(); toast('Worker saved.');});
  $('workersRefresh').onclick = guarded(async () => {await load(true); const entry = entries.find(e => e.id === current); if (entry) report(entry.status);});
  const query = activate => guarded(async () => {
    await save(); const entry = await api('/generation/workers/query', {worker_id:current, activate}); report(entry.status); await load();
  });
  $('workerQuery').onclick = query(false); $('workerStart').onclick = query(true);
  $('workerPrepare').onclick = guarded(async () => {await save(); await api('/generation/workers/prepare', {worker_id:current}); toast('Worker preparation started. Progress appears in Generation jobs.');});
  $('workerDelete').onclick = guarded(async () => {if (!current) return; await api('/generation/workers/remove', {worker_id:current}); current = null; await load(); fill();});
  return {async open() {
    root.showModal();
    const controls=[...root.querySelectorAll('input,select,button:not(#workersClose)')];controls.forEach(node=>node.disabled=true);$('workerStatus').textContent='Loading workers…';
    try {
    const prefs = await api('/generation/preferences');
    sshKeys=prefs.key_candidates||[];
    for (const [id,key] of [['workerStorageEndpoint','storage_endpoint'],['workerStorageBucket','storage_bucket'],['workerStorageRegion','storage_region']]) $(id).value = prefs[key] || '';
    $('workerApiKey').placeholder = prefs.has_api_key ? 'Configured for this session' : 'RUNPOD_API_KEY is also supported';
    $('workerStorageSecret').placeholder = prefs.has_storage_key ? 'Configured for this session' : '';
    await load(); fill(entries.find(e => e.id === current) || entries.at(-1));
    } finally {controls.forEach(node=>node.disabled=false);}
  }};
}
