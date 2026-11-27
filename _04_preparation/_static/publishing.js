export function initPublishing({api,toast,onPlay,onListChange}){
 const root=document.getElementById('publishingView'),q=id=>document.getElementById(id);
 root.innerHTML=`<div class="prep-heading"><div><h1>Publish</h1><p>Completed preparations waiting to go live.</p></div><button id="pubRefresh">Refresh</button></div><p id="pubStatus" role="status"></p><p id="pubError" role="alert"></p><div id="publishQueue" class="library-list"></div><details><summary id="pubHistorySummary">Publication history</summary><div id="pubHistory"></div></details>`;
 let timer,busy=false,projects=[],recordings=new Map(),settings=null,actions=Promise.resolve(),pending=0,refreshing=null,viewVersion=0;
 const requestErrors=new Map(),submitting=new Set();
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const action=work=>{pending++;busy=true;actions=actions.then(work).catch(error=>{q('pubError').textContent=error.message;if(q('pubConfig')?.open)q('pubConfigMessage').textContent=error.message;}).finally(()=>{pending--;busy=pending>0;});return actions;};
 const active=state=>state&&['queued','running'].includes(state.status);
 const failed=state=>state&&['failed','interrupted'].includes(state.status);
 const stateFor=p=>p.publication?.prepared_id===p.prepared_id?p.publication:null;
 const timestamp=value=>value?new Date(value).toLocaleString():'';
 const recordingKey=project=>project.audio_id||project.prepared_id;
 const songKey=project=>project.track.song_id ? 'song:'+project.track.song_id : 'recording:'+recordingKey(project);
 function preview(project) {
  const index = project.files.findIndex(file=>file.content_type==='audio/mpeg');
  if (index < 0) return null;
  const id = project.prepared_id;
  return {id,key:`publish:${id}`,source:'publishing',title:project.track.displayTitle,
          subtitle:'Publish preview',duration:project.track.duration,
          url:`/api/releases/${encodeURIComponent(id)}/assets/${index}`};
 }

 function groupBy(items, key) {
  const groups = new Map();
  for (const item of items) {
   const id = key(item);
   if (!groups.has(id)) groups.set(id, []);
   groups.get(id).push(item);
  }
  return groups;
 }

 function recordingDetails(project, sameAudio) {
  const {track, audio_id: audioId, generation_id: generationId} = project;
  const minutes = Number.isFinite(track.duration) ? Math.max(0, Math.floor(track.duration)) : null;
  return [
   track.variant || track.genre?.child_genre || track.genre?.parent_genre,
   track.variant_id && `Variant ${track.variant_id}`,
   minutes !== null ? `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2,'0')}` : null,
   audioId && `Recording ${audioId.slice(0,8)}`,
   sameAudio && generationId && `Generation ${generationId.slice(0,8)}`,
  ].filter(Boolean).map(escape).join(' · ');
 }

 function renderCard(project, rooms, sharedTitle, sameAudio) {
  const id = escape(project.prepared_id);
  const title = project.track.displayTitle;
  const entry = preview(project);
  const player = entry ? `<button type="button" class="listen-play publish-preview" data-preview="${id}" data-title="${escape(title)}" aria-label="Play ${escape(title)}"><span class="play-symbol" aria-hidden="true"></span></button>` : '';
  return `<article class="library-card" data-pending="${id}" ${entry ? `data-play-key="${escape(entry.key)}"` : ''}>${player}<div>
    ${title===sharedTitle ? '' : `<h2>${escape(title)}</h2>`}
    <p class="publish-recording-meta">${recordingDetails(project,sameAudio)}</p>
    <p data-publication-status role="status"></p><p data-publication-error role="alert"></p><p data-archive-status hidden></p>
   </div><div class="library-card-actions"><label>Room<select data-room="${id}">
    ${rooms.map(room=>`<option value="${escape(room.id)}">${escape(room.name)}</option>`).join('')}
   </select></label><button data-publish="${id}">Publish recording</button>
   <button class="secondary" data-retry-archive="${id}" hidden>Retry archive move</button>
   ${project.can_return ? `<button class="secondary" data-return="${id}">Return to Preparation</button>` : ''}
   </div></article>`;
 }

 function renderSong(group, rooms, expanded) {
  const title = group.length > 1 ? group[0][0].track.displayTitle : null;
  const heading = title ? `<header class="publish-song-heading"><h2>${escape(title)}</h2><span>${group.length} recordings</span></header>` : '';
  const cards = group.map(([primary,...alternatives])=>{
   const id = recordingKey(primary);
   const copies = alternatives.length ? `<details class="publish-alternatives" data-alternatives="${escape(id)}" ${expanded.has(id) ? 'open' : ''}>
     <summary>${alternatives.length} more preparation${alternatives.length===1 ? '' : 's'} of this recording</summary>
     ${alternatives.map(project=>renderCard(project,rooms,title,true)).join('')}
    </details>` : '';
   return `<div data-recording="${escape(id)}">${renderCard(primary,rooms,title,Boolean(alternatives.length))}${copies}</div>`;
  }).join('');
  return `<section class="publish-song" data-song="${escape(songKey(group[0][0]))}">${heading}<div class="publish-recordings">${cards}</div></section>`;
 }

 function reconcileQueue(markup){
  const template=document.createElement('template');template.innerHTML=markup;
  const focused=document.activeElement;
  const key=node=>{
   for(const attribute of ['data-song','data-recording','data-pending','data-alternatives','data-room','data-preview','data-publish','data-return','data-retry-archive','data-publication-status','data-publication-error','data-archive-status']){
    if(node.hasAttribute(attribute))return node.tagName+':'+attribute+':'+node.getAttribute(attribute);
   }
   return node.tagName+':'+(node.tagName==='OPTION'?node.value:node.className);
  };
  function merge(parent,next){
   const needed=new Map();
   for(const child of next.children){const id=key(child);needed.set(id,(needed.get(id)||0)+1);}
   for(const child of [...parent.children]){const id=key(child);if(needed.get(id)){needed.set(id,needed.get(id)-1);}else child.remove();}
   const available=new Map();
   for(const child of parent.children){const id=key(child);if(!available.has(id))available.set(id,[]);available.get(id).push(child);}
   const retained=new Set();let anchor=parent.firstElementChild;
   for(const fresh of [...next.children]){
    const current=available.get(key(fresh))?.shift();
    const child=current||fresh;retained.add(child);
    if(current){
     for(const attribute of [...current.attributes]){
      if(attribute.name!=='open'&&!fresh.hasAttribute(attribute.name))current.removeAttribute(attribute.name);
     }
     for(const attribute of fresh.attributes){
      if(attribute.name!=='open'&&current.getAttribute(attribute.name)!==attribute.value)current.setAttribute(attribute.name,attribute.value);
     }
     if(fresh.childElementCount)merge(current,fresh);
     else if(current.textContent!==fresh.textContent)current.textContent=fresh.textContent;
    }
    if(child!==anchor)parent.insertBefore(child,anchor);
    anchor=child.nextElementSibling;
   }
   for(const child of [...parent.children])if(!retained.has(child))child.remove();
  }
  merge(q('publishQueue'),template.content);
  if(focused?.isConnected&&document.activeElement!==focused)focused.focus({preventScroll:true});
 }

 function updateCard(p){
  const card=q('publishQueue').querySelector(`[data-pending="${p.prepared_id}"]`),state=stateFor(p),running=active(state),queuing=submitting.has(p.prepared_id);
  const blocked = recordings.get(recordingKey(p)).some(other=>other!==p&&(submitting.has(other.prepared_id)||active(other.publication)));
  const locked = Boolean(running||queuing||blocked);
  const progress=state?.progress||{},status=card.querySelector('[data-publication-status]');
  const requestError=requestErrors.get(p.prepared_id);
  const label=queuing?'Queuing…':blocked?'Another preparation is publishing':requestError?'Publication blocked':state?.status==='failed'?'Publication failed':state?.status==='interrupted'?'Interrupted':running&&progress.phase==='waiting_processing'?'Waiting for processing':state?.status==='running'?'Publishing':state?.status==='queued'?'Queued':p.archive?'Waiting for archive':'Ready to publish';
  const detail=state?[timestamp(state.completed_at||state.started_at||state.created_at),progress.message,progress.total?`${progress.completed||0} / ${progress.total} assets`:null].filter(Boolean).join(' · '):'';
  status.textContent=[label,detail].filter(Boolean).join(' · ');status.dataset.state=state?.status||'ready';
  card.querySelector('[data-publication-error]').textContent=requestError||state?.error||'';
  const archiveStatus=card.querySelector('[data-archive-status]');
  archiveStatus.hidden=!p.archive;
  archiveStatus.textContent=p.archive?.status==='failed'?p.archive.error||'Archive move failed':p.archive?'Archive move '+p.archive.status:'';
  const retryArchive=card.querySelector('[data-retry-archive]');
  retryArchive.hidden=p.archive?.status!=='failed';
  retryArchive.disabled=locked;
  const publish=card.querySelector('[data-publish]');publish.disabled=locked||Boolean(p.archive);publish.textContent=running||queuing?label:failed(state)||requestError?'Retry publication':'Publish recording';
  card.querySelector('[data-room]').disabled=locked;
  const back=card.querySelector('[data-return]');if(back)back.disabled=locked;
 }
 async function publish(pid,visibility){
  submitting.add(pid);requestErrors.delete(pid);q('pubError').textContent='';
  const card=q('publishQueue').querySelector(`[data-pending="${pid}"]`);
  if(card){const button=card.querySelector('[data-publish]');button.disabled=true;button.textContent='Queuing…';card.querySelector('[data-publication-status]').textContent='Queuing publication…';}
  try{await api(`/releases/${pid}/publish`,visibility?{visibility}:{});toast('Publication queued.');}
  catch(error){requestErrors.set(pid,error.message);if(!card)throw error;}
  finally{submitting.delete(pid);await refresh();const p=projects.find(p=>p.prepared_id===pid);if(p)updateCard(p);}
 }
 function refresh(){
  if(!refreshing){const flight=load().finally(()=>{if(refreshing===flight)refreshing=null;});refreshing=flight;}
  return refreshing;
 }
 async function load(){
  const version=viewVersion;
  if(!settings){const result=await api('/releases/library/settings');if(version!==viewVersion)return;settings=result.settings;}
  const result=await api('/releases/projects');
  if(version!==viewVersion)return;
  projects=result.projects;
  recordings=groupBy(projects,recordingKey);
  const songs=groupBy(recordings.values(),group=>songKey(group[0]));
  const rooms=settings?.rooms.filter(r=>r.enabled)||[{id:'public',name:'Main player'}];
  const existing=new Map([...q('publishQueue').querySelectorAll('[data-room]')].map(s=>[s.dataset.room,s.value]));
  const signature=JSON.stringify([projects.map(p=>[p.prepared_id,p.can_return,recordingKey(p),songKey(p)]),rooms]);
  // Progress can refresh while auditioning audio without replacing the player
  // or resetting a room selection on each poll.
  if(q('publishQueue').dataset.signature!==signature){
   const expanded = new Set([...q('publishQueue').querySelectorAll('[data-alternatives][open]')].map(details=>details.dataset.alternatives));
   reconcileQueue([...songs.values()].map(group=>renderSong(group,rooms,expanded)).join(''));
   q('publishQueue').dataset.signature=signature;
   for(const button of q('publishQueue').querySelectorAll('[data-preview]'))button.onclick=()=>{
    const project = projects.find(p=>p.prepared_id===button.dataset.preview);
    const items = [...recordings.entries()].map(([id,group])=>
      preview(id===recordingKey(project) ? project : group[0])).filter(Boolean);
    Promise.resolve(onPlay(items,items.findIndex(item=>item.id===project.prepared_id))).catch(error=>toast(error.message));
   };
   for(const select of q('publishQueue').querySelectorAll('[data-room]')){const p=projects.find(p=>p.prepared_id===select.dataset.room);select.value=existing.get(select.dataset.room)||stateFor(p)?.visibility||p.options.visibility||settings?.default_room||'public';}
   for(const button of q('publishQueue').querySelectorAll('[data-publish]'))button.onclick=()=>{
    const room=q('publishQueue').querySelector(`[data-room="${button.dataset.publish}"]`).value;
    submitting.add(button.dataset.publish);
    const project=projects.find(p=>p.prepared_id===button.dataset.publish);
    for(const related of recordings.get(recordingKey(project)))updateCard(related);
    return action(()=>publish(button.dataset.publish,room));
   };
   for(const button of q('publishQueue').querySelectorAll('[data-return]'))button.onclick=()=>action(async()=>{q('pubError').textContent='';await api(`/releases/${button.dataset.return}/discard`,{});toast('Returned to Preparation.');await refresh();});
   for(const button of q('publishQueue').querySelectorAll('[data-retry-archive]'))button.onclick=()=>action(async()=>{
    button.disabled=true;
    try{
     await api(`/releases/${button.dataset.retryArchive}/retry-archive`,{});
     requestErrors.delete(button.dataset.retryArchive);
     toast('Archive move queued.');
     await refresh();
    } finally {
     if(button.isConnected)button.disabled=false;
    }
   });
   onListChange();
  }
  for(const p of projects)updateCard(p);
  const groups = [...recordings.values()];
  const counts={active:groups.filter(group=>group.some(p=>active(p.publication))).length,
                failed:groups.filter(group=>group.some(p=>failed(stateFor(p))||p.archive?.status==='failed'||requestErrors.has(p.prepared_id))).length};
  const status = groups.length ? `${groups.length} awaiting publication · ${counts.active} active · ${counts.failed} need attention` :
    'No completed preparations are waiting. Published recordings are in Library.';
  q('pubStatus').textContent = result.already_published ? status+` · ${result.already_published} matching recordings already in Library` : status;
  const visiblePreviews=new Set(projects.map(p=>p.prepared_id));
  const jobs=groupBy(result.releases,r=>JSON.stringify([r.kind,r.prepared_id||r.generation_id||r.id]));
  const history=[...jobs.values()].map(group=>group[0]).filter(r=>
    ['queued','running','failed','interrupted'].includes(r.status)&&
    !(r.kind==='release_publish'&&visiblePreviews.has(r.prepared_id)));
  q('pubHistorySummary').textContent=`Publication history · ${history.length} active or failed jobs`;
  q('pubHistory').innerHTML=history.map(r=>`<article class="prep-task"><strong>${escape(r.title)} · ${escape(r.kind.replaceAll('_',' '))} · ${escape(r.status)}</strong><span>${escape(timestamp(r.created_at))} · ${escape(r.progress.message||r.progress.phase||'')}${r.progress.total?' · '+r.progress.completed+'/'+r.progress.total:''}</span>${r.error?`<p>${escape(r.error)}</p>`:''}${r.can_retry?`<button data-retry="${escape(r.id)}">${r.kind==='release_prepare'?'Retry preparation':'Retry publication'}</button>`:''}</article>`).join('')||'<p>No active or failed jobs.</p>';
  for(const button of q('pubHistory').querySelectorAll('[data-retry]'))button.onclick=()=>{
   button.disabled=true;
   action(async()=>{
    q('pubError').textContent='';
    try{await api(`/releases/tasks/${encodeURIComponent(button.dataset.retry)}/retry`,{});toast('Release retry queued.');await refresh();}
    finally{if(button.isConnected)button.disabled=false;}
   });
  };
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
 return {
  open:async()=>{
   ++viewVersion;settings=null;refreshing=null;clearInterval(timer);
   timer=setInterval(()=>{if(!document.hidden&&!root.hidden)refresh().catch(error=>{if(!root.hidden)q('pubError').textContent=error.message;});},4000);
   await action(refresh);
  },
  close(){++viewVersion;clearInterval(timer);}
 };
}
