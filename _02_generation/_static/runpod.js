export function initRunpod({root,api,toast,onChange}) {
  const panel=document.createElement('section');panel.className='generation-panel runpod-account';
  panel.innerHTML=`<details id="runpodAccount"><summary>RunPod account & discovered pods</summary>
    <div class="runpod-config"><label>API key<input id="runpodKey" type="password" autocomplete="new-password" placeholder="Leave blank to keep the saved key"></label><label>Discovery interval (seconds)<input id="runpodInterval" type="number" value="30" min="10" max="3600"></label><label>Candidate HTTP ports<input id="runpodPorts" value="8188" placeholder="8188, 8190"></label><label>Native SSH private key<select id="runpodSsh"></select></label></div>
    <div class="generation-inline"><label class="generation-check"><input id="runpodEnabled" type="checkbox">Automatic discovery</label><label class="generation-check"><input id="runpodNative" type="checkbox" checked>Inspect native SSH workers</label><button id="runpodSave" type="button">Save account</button><button id="runpodTest" type="button">Test Account Connection</button><button id="runpodRefresh" type="button">Refresh Pods</button></div>
    <p id="runpodMessage" role="status" class="small"></p><div class="runpod-table-wrap"><table class="runpod-table"><thead><tr><th>Pod</th><th>Status</th><th>Worker</th><th>GPU</th><th>VRAM</th><th>Endpoint</th><th>Health</th><th>Queue</th><th>Enabled</th><th>Auto Allocate</th><th></th></tr></thead><tbody id="runpodRows"></tbody></table></div>
    <details id="runpodDiagnostics"><summary>Discovery status & diagnostics</summary><pre id="runpodProgress"></pre></details></details>`;
  root.insertBefore(panel,root.querySelector('.generation-inline'));
  const $=id=>document.getElementById(id);let timer=null,signature='',loaded=false;
  const stamp=value=>value?new Date(value*1000).toLocaleString():'—';
  const bytes=value=>typeof value==='number'?(value/1024**3).toFixed(1)+' GiB':'—';
  function textCell(row,value){const node=document.createElement('td');node.textContent=value??'—';row.append(node);return node;}
  async function paint(data){
    const p=data.progress;
    $('runpodMessage').textContent=(data.has_api_key?'Account key configured · ':'No account key configured · ')+p.operation+(p.current_pods.length?' · inspecting '+p.current_pods.join(', '):'');
    $('runpodProgress').textContent=JSON.stringify({...p,started_at:stamp(p.started_at),completed_at:stamp(p.completed_at),next_at:stamp(p.next_at)},null,2);
    const next=JSON.stringify(data.workers);
    if(signature===next)return;signature=next;
    const open=new Set([...$('runpodRows').querySelectorAll('details[open]')].map(d=>d.dataset.id));$('runpodRows').replaceChildren();
    for(const entry of data.workers){
      const pod=entry.runpod,status=entry.status||{},hardware=status.hardware||{};const row=document.createElement('tr');
      const cell=textCell(row,'');const details=document.createElement('details');details.dataset.id=entry.id;details.open=open.has(entry.id);
      const summary=document.createElement('summary');summary.textContent=pod.name+' · '+pod.pod_id;const info=document.createElement('pre');info.textContent=JSON.stringify({pod,hardware,system:status.system,models:status.models,capabilities:status.capabilities,missing_nodes:status.missing_nodes},null,2);details.append(summary,info);cell.append(details);
      textCell(row,pod.state+(pod.eligible?'':(' · '+(pod.service_state||'unavailable'))));textCell(row,pod.worker_type||'unknown');
      textCell(row,(hardware.gpu_name||pod.gpu_name||'Unknown')+(pod.gpu_count?' × '+pod.gpu_count:''));
      textCell(row,bytes(hardware.usable_vram_bytes)+' / '+bytes(hardware.free_vram_bytes_at_start)+' free');textCell(row,entry.connection?.address||'No validated endpoint');
      textCell(row,stamp(pod.last_health));textCell(row,status.online?(status.queue_running??0)+' running / '+(status.queue_pending??0)+' queued':'—');
      const enabled=document.createElement('input');enabled.type='checkbox';enabled.checked=entry.enabled;enabled.setAttribute('aria-label','Enable pod '+pod.pod_id);textCell(row,'').append(enabled);
      const auto=document.createElement('input');auto.type='checkbox';auto.checked=pod.auto_allocate!==false;auto.setAttribute('aria-label','Auto allocate pod '+pod.pod_id);textCell(row,'').append(auto);
      const change=async()=>{try{await api('/generation/runpod/worker',{worker_id:entry.id,enabled:enabled.checked,auto_allocate:auto.checked});signature='';await refresh();await onChange?.();}catch(error){toast(error.message);signature='';await refresh();}};enabled.onchange=change;auto.onchange=change;
      const inspect=document.createElement('button');inspect.type='button';inspect.textContent='Inspect';inspect.onclick=()=>action('/generation/runpod/inspect',{worker_id:entry.id});textCell(row,'').append(inspect);
      $('runpodRows').append(row);
    }
    if(!data.workers.length){const row=document.createElement('tr');const cell=textCell(row,'No RunPod pods discovered');cell.colSpan=11;$('runpodRows').append(row);}
    await onChange?.();
  }
  async function refresh(){await paint(await api('/generation/runpod'));}
  async function action(path,body={}){try{$('runpodMessage').textContent='Discovery requested…';await paint(await api(path,body));}catch(error){$('runpodMessage').textContent=error.message;toast(error.message);}}
  async function save(){
    const data=await api('/generation/runpod/settings',{api_key:$('runpodKey').value.trim(),enabled:$('runpodEnabled').checked,interval:Number($('runpodInterval').value),candidate_ports:$('runpodPorts').value.split(/[ ,]+/).filter(Boolean).map(Number),identity_file:$('runpodSsh').value,native:$('runpodNative').checked});
    $('runpodKey').value='';await paint(data);
  }
  $('runpodSave').onclick=async()=>{try{await save();toast('RunPod account saved.');}catch(error){$('runpodMessage').textContent=error.message;toast(error.message);}};
  $('runpodTest').onclick=async()=>{try{await save();await action('/generation/runpod/test');}catch(error){$('runpodMessage').textContent=error.message;toast(error.message);}};
  $('runpodRefresh').onclick=()=>action('/generation/runpod/refresh');
  root.addEventListener('close',()=>{clearInterval(timer);timer=null;});
  return {async open(keys=[]){
    const data=await api('/generation/runpod'),s=data.settings;
    $('runpodEnabled').checked=s.enabled;$('runpodInterval').value=s.interval;$('runpodPorts').value=s.candidate_ports.join(', ');$('runpodNative').checked=s.native;
    $('runpodSsh').replaceChildren(new Option('SSH default identity',''),...keys.map(k=>new Option(k,k)));
    if(s.identity_file&&!keys.includes(s.identity_file))$('runpodSsh').append(new Option(s.identity_file,s.identity_file));$('runpodSsh').value=s.identity_file;
    if(!loaded&&data.has_api_key)$('runpodAccount').open=true;loaded=true;await paint(data);
    clearInterval(timer);timer=setInterval(()=>{if(!root.open){clearInterval(timer);return;}refresh().catch(error=>{$('runpodMessage').textContent=error.message;});},2000);
  }};
}
