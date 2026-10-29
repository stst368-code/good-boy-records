export function initPublishing({api,toast}){
 const root=document.getElementById('publishingView'),q=id=>document.getElementById(id);
 root.innerHTML=`<div class="prep-heading"><div><h1>Publish</h1><p>Completed preparations waiting to go live.</p></div><button id="pubRefresh">Refresh</button></div><p id="pubStatus" role="status"></p><p id="pubError" role="alert"></p><div id="publishQueue" class="library-list"></div><details><summary id="pubHistorySummary">Publication history</summary><div id="pubHistory"></div></details>`;
 let timer,busy=false,projects=[],settings=null,actions=Promise.resolve(),pending=0,refreshing=null;
 const requestErrors=new Map(),submitting=new Set();
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const action=work=>{pending++;busy=true;actions=actions.then(work).catch(error=>{q('pubError').textContent=error.message;if(q('pubConfig')?.open)q('pubConfigMessage').textContent=error.message;}).finally(()=>{pending--;busy=pending>0;});return actions;};
 const active=state=>state&&['queued','running'].includes(state.status);
 const failed=state=>state&&['failed','interrupted'].includes(state.status);
 const stateFor=p=>p.publication?.prepared_id===p.prepared_id?p.publication:null;
 const timestamp=value=>value?new Date(value).toLocaleString():'';
 function updateCard(p){
  const card=q('publishQueue').querySelector(`[data-pending="${p.prepared_id}"]`),state=stateFor(p),running=active(state),queuing=submitting.has(p.prepared_id);
  const progress=state?.progress||{},status=card.querySelector('[data-publication-status]');
  const label=queuing?'Queuing…':state?.status==='failed'?'Publication failed':state?.status==='interrupted'?'Interrupted':running&&progress.phase==='waiting_processing'?'Waiting for processing':state?.status==='running'?'Publishing':state?.status==='queued'?'Queued':'Ready to publish';
  const detail=state?[timestamp(state.completed_at||state.started_at||state.created_at),progress.message,progress.total?`${progress.completed||0} / ${progress.total} assets`:null].filter(Boolean).join(' · '):'';
  status.textContent=[label,detail].filter(Boolean).join(' · ');status.dataset.state=state?.status||'ready';
  card.querySelector('[data-publication-error]').textContent=requestErrors.get(p.prepared_id)||state?.error||'';
  const publish=card.querySelector('[data-publish]');publish.disabled=Boolean(running||queuing);publish.textContent=running||queuing?label:failed(state)?'Retry publication':'Publish recording';
  card.querySelector('[data-room]').disabled=Boolean(running||queuing);
  const back=card.querySelector('[data-return]');if(back)back.disabled=Boolean(running||queuing);
 }
 async function publish(pid,visibility){
  submitting.add(pid);requestErrors.delete(pid);q('pubError').textContent='';
  const card=q('publishQueue').querySelector(`[data-pending="${pid}"]`);
  if(card){const button=card.querySelector('[data-publish]');button.disabled=true;button.textContent='Queuing…';card.querySelector('[data-publication-status]').textContent='Queuing publication…';}
  try{await api(`/releases/${pid}/publish`,visibility?{visibility}:{});toast('Publication queued.');}
  catch(error){requestErrors.set(pid,error.message);throw error;}
  finally{submitting.delete(pid);await refresh();const p=projects.find(p=>p.prepared_id===pid);if(p)updateCard(p);}
 }
 function refresh(){if(!refreshing)refreshing=load().finally(()=>{refreshing=null;});return refreshing;}
 async function load(){
  const result=await api('/releases/projects');projects=result.projects;
  const rooms=settings?.rooms.filter(r=>r.enabled)||[{id:'public',name:'Main player'}];
  const existing=new Map([...q('publishQueue').querySelectorAll('[data-room]')].map(s=>[s.dataset.room,s.value]));
  const signature=JSON.stringify([projects.map(p=>[p.prepared_id,p.can_return]),rooms]);
  // Progress can refresh while auditioning audio without replacing the player
  // or resetting a room selection on each poll.
  if(q('publishQueue').dataset.signature!==signature){
   q('publishQueue').innerHTML=projects.map(p=>`<article class="library-card" data-pending="${escape(p.prepared_id)}"><div><h2>${escape(p.track.displayTitle)}</h2><p>${escape(p.track.genre?.parent_genre)} · ${escape(p.track.variant||'')}</p><audio controls preload="none" src="/api/releases/${encodeURIComponent(p.prepared_id)}/assets/${p.files.findIndex(f=>f.content_type==='audio/mpeg')}"></audio><p data-publication-status role="status"></p><p data-publication-error role="alert"></p></div><div class="library-card-actions"><label>Room<select data-room="${escape(p.prepared_id)}">${rooms.map(r=>`<option value="${escape(r.id)}">${escape(r.name)}</option>`).join('')}</select></label><button data-publish="${escape(p.prepared_id)}">Publish recording</button>${p.can_return?`<button class="secondary" data-return="${escape(p.prepared_id)}">Return to Preparation</button>`:''}</div></article>`).join('');
   q('publishQueue').dataset.signature=signature;
   for(const select of q('publishQueue').querySelectorAll('[data-room]')){const p=projects.find(p=>p.prepared_id===select.dataset.room);select.value=existing.get(select.dataset.room)||stateFor(p)?.visibility||p.options.visibility||settings?.default_room||'public';}
   for(const button of q('publishQueue').querySelectorAll('[data-publish]'))button.onclick=()=>{const room=q('publishQueue').querySelector(`[data-room="${button.dataset.publish}"]`).value;submitting.add(button.dataset.publish);updateCard(projects.find(p=>p.prepared_id===button.dataset.publish));return action(()=>publish(button.dataset.publish,room));};
   for(const button of q('publishQueue').querySelectorAll('[data-return]'))button.onclick=()=>action(async()=>{q('pubError').textContent='';await api(`/releases/${button.dataset.return}/discard`,{});toast('Returned to Preparation.');await refresh();});
  }
  for(const p of projects)updateCard(p);
  const counts={active:projects.filter(p=>active(stateFor(p))).length,failed:projects.filter(p=>failed(stateFor(p))).length};
  q('pubStatus').textContent=projects.length?`${projects.length} awaiting publication · ${counts.active} active · ${counts.failed} need attention`:'No completed preparations are waiting. Published recordings are in Library.';
  const history=result.releases.filter(r=>['queued','running','failed','interrupted'].includes(r.status));
  q('pubHistorySummary').textContent=`Publication history · ${history.length} active or failed jobs`;
  q('pubHistory').innerHTML=history.map(r=>`<article class="prep-task"><strong>${escape(r.title)} · ${escape(r.kind.replaceAll('_',' '))} · ${escape(r.status)}</strong><span>${escape(timestamp(r.created_at))} · ${escape(r.progress.message||r.progress.phase||'')}${r.progress.total?' · '+r.progress.completed+'/'+r.progress.total:''}</span>${r.error?`<p>${escape(r.error)}</p>`:''}${r.can_retry?`<button data-retry="${escape(r.prepared_id)}">Retry publication</button>`:''}</article>`).join('')||'<p>No active or failed jobs.</p>';
  for(const button of q('pubHistory').querySelectorAll('[data-retry]'))button.onclick=()=>action(()=>publish(button.dataset.retry));
 }
 q('pubRefresh').onclick=()=>action(refresh);
 const menu=document.createElement('details');menu.id='pubConfig';menu.innerHTML=`<summary>Publishing · R2 and website</summary><p class="subtle small">Already used the old uploader on this PC? Reuse its saved rclone connection.</p><button id="pubUseRclone" type="button">Use saved rclone r2 connection</button><p class="subtle small">Manual setup below is optional when using rclone. Keys stay on this device; blank key fields retain saved credentials.</p><div id="pubSettingsFields"></div><div class="actions"><button id="pubSaveConfig" type="button">Save publishing settings</button><button id="pubCheckConfig" type="button">Check public connection</button></div><p id="pubConfigMessage" role="status"></p>`;
 q('hostSettings').append(menu);
 const fields=[['endpoint','R2 endpoint or saved rclone connection'],['bucket','Public bucket'],['public_base','Public media URL'],['website_url','Website URL'],['profile','AWS profile (optional)'],['access_key','R2 Access Key ID (manual setup)'],['secret_key','R2 Secret Access Key (manual setup)']];
 for(const [key,label] of fields){const line=document.createElement('label');line.textContent=label;const input=document.createElement('input');input.id='pubConfig_'+key;input.type=['access_key','secret_key'].includes(key)?'password':'text';input.autocomplete='off';line.append(input);q('pubSettingsFields').append(line);}
 function showSettings(config){for(const [key] of fields)q('pubConfig_'+key).value=config.settings[key]||'';q('pubConfigMessage').textContent=config.settings.rclone_remote?`Using saved rclone connection ${config.settings.rclone_remote}. Its keys are read locally when publishing.`:config.credentials_available?'Credentials available.':'Use your existing rclone connection above, or enter the Cloudflare R2 endpoint and key pair.';}
 q('pubConfig_endpoint').placeholder='r2 or https://ACCOUNT_ID.r2.cloudflarestorage.com';
 q('pubConfig_profile').placeholder='Leave blank for rclone or R2 keys';
 menu.addEventListener('toggle',()=>{if(menu.open)action(async()=>showSettings(await api('/releases/settings')));});
 q('pubUseRclone').onclick=()=>action(async()=>{q('pubConfigMessage').textContent='Reading the saved r2 connection on this PC…';const body={remote:'r2',bucket:q('pubConfig_bucket').value.trim()||null,public_base:q('pubConfig_public_base').value.trim()||null,website_url:q('pubConfig_website_url').value.trim()||null};const config=await api('/releases/settings/rclone',body);showSettings(config);q('pubConfigMessage').textContent='Saved · using rclone r2. Run Check public connection.';});
 q('pubSaveConfig').onclick=()=>action(async()=>{q('pubConfigMessage').textContent='Saving publishing settings…';const body=Object.fromEntries(fields.map(([key])=>[key,q('pubConfig_'+key).value.trim()]));const config=await api('/releases/settings',body);showSettings(config);q('pubConfigMessage').textContent=config.settings.rclone_remote?`Saved · using rclone ${config.settings.rclone_remote}. Run the public connection check.`:'Saved. Run the public connection check.';});
 q('pubCheckConfig').onclick=()=>action(async()=>{q('pubConfigMessage').textContent='Checking upload, public URL and CORS…';const result=await api('/releases/check',{});q('pubConfigMessage').textContent='Ready · '+result.catalogue_url;});
 return {open:async()=>{settings=(await api('/releases/library/settings')).settings;await action(refresh);clearInterval(timer);timer=setInterval(()=>{if(!busy&&!root.querySelector('input:focus,select:focus,textarea:focus'))refresh().catch(error=>{q('pubError').textContent=error.message;});},4000);},close:()=>{clearInterval(timer);for(const audio of root.querySelectorAll('audio'))audio.pause();}};
}
