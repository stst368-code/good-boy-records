export function initPublishing({api,toast}){
 const root=document.getElementById('publishingView');
 root.innerHTML=`<div class="prep-heading"><div><h1>Publishing</h1><p class="subtle">Preview a release, then publish its verified web assets.</p></div><div><button id="pubResearch">Publish research snapshot</button> <button id="pubRefresh">Refresh</button></div></div><p id="pubStatus" role="status"></p><div class="prep-grid"><section><h2>Release preview</h2><label>Recording<select id="pubProject"></select></label><label>Working audio<select id="pubMaster"></select></label><label>Artwork<select id="pubArtwork"></select></label><label><input type="checkbox" id="pubFlac" checked> Include lossless FLAC</label><label><input type="checkbox" id="pubLyrics"> Include approved word timings</label><button id="pubPrepare">Prepare preview</button><p class="subtle small">No artwork selected uses the GBR sleeve. Word timings must be approved for the selected audio in Preparation.</p></section><section><h2>Release history</h2><div id="pubHistory"></div></section></div>`;
 const q=id=>document.getElementById(id);let projects=[],timer,busy=false,pending=Promise.resolve();
 const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function action(work){pending=pending.then(async()=>{busy=true;q('pubStatus').textContent='';try{await work();}catch(error){q('pubStatus').textContent=error.message;if(q('pubConfig')?.open)q('pubConfigMessage').textContent=error.message;}finally{busy=false;}});return pending;}
 function choices(){const project=projects.find(p=>p.generation_id===q('pubProject').value);q('pubMaster').replaceChildren(new Option('Prepared original',''),...(project?.masters||[]).map(a=>new Option('Master · '+a.created_at,a.id)));q('pubArtwork').replaceChildren(new Option('GBR sleeve',''),...(project?.artworks||[]).map(a=>new Option(a.kind+' · '+a.id.slice(0,8),a.id)));q('pubPrepare').disabled=!project;}
 async function refresh(){const result=await api('/releases/projects');const selected=q('pubProject').value;projects=result.projects;
  q('pubProject').replaceChildren(...projects.map(p=>new Option(p.title+' · '+(p.style||p.cluster||''),p.generation_id)));
  if(projects.some(p=>p.generation_id===selected))q('pubProject').value=selected;
  if(selected!==q('pubProject').value||!q('pubMaster').options.length)choices();
  const live=new Set();let position=0;
  for(const item of result.releases){live.add(item.id);let row=q('pubHistory').querySelector(`[data-release="${item.id}"]`);const signature=JSON.stringify([item,projects.map(p=>[p.generation_id,p.status]),result.releases.filter(r=>r.prepared_id===item.id).map(r=>[r.id,r.status,r.progress.phase])]);
   if(row?.dataset.signature===signature){position++;continue;}
   if(!row){row=document.createElement('article');row.className='prep-task';row.dataset.release=item.id;}else row.replaceChildren();row.dataset.signature=signature;
   const name=document.createElement('strong');name.textContent=item.title;row.append(name);
   const status=document.createElement('span');status.textContent=`${new Date(item.created_at).toLocaleString()} · ${item.kind==='release_prepare'?'Preview':'Publication'} · ${['cancelled','failed','interrupted'].includes(item.status)?item.status:item.progress.phase||item.status}${item.progress.total?' · '+item.progress.completed+'/'+item.progress.total:''}`;row.append(status);
   if(item.error){const error=document.createElement('p');error.textContent=item.error;row.append(error);}
   if(item.kind==='release_prepare'&&item.status==='done'){
    const published=result.releases.some(r=>r.prepared_id===item.id&&r.progress.phase==='published');
    const active=result.releases.some(r=>r.prepared_id===item.id&&['queued','running'].includes(r.status));
    if(!published){const audio=document.createElement('audio');audio.controls=true;audio.preload='none';audio.src=`/api/releases/${encodeURIComponent(item.id)}/assets/0`;row.append(audio);
     const details=document.createElement('details');const summary=document.createElement('summary');summary.textContent='Public metadata and files';details.append(summary);const pre=document.createElement('pre');pre.textContent=JSON.stringify({track:item.track,files:item.files.map(f=>({key:f.key,bytes:f.size}))},null,2);details.append(pre);row.append(details);
     const button=document.createElement('button');button.textContent=active?'Publishing…':'Publish this preview';button.disabled=active;button.onclick=()=>action(async()=>{await api(`/releases/${encodeURIComponent(item.id)}/publish`,{});toast('Publication queued.');await refresh();});row.append(button);
     const discard=document.createElement('button');discard.textContent='Discard preview';discard.className='secondary';discard.disabled=active;discard.onclick=()=>action(async()=>{await api(`/releases/${encodeURIComponent(item.id)}/discard`,{});await refresh();});row.append(discard);}
   }
   if(item.progress.url){const link=document.createElement('a');link.href=item.progress.url;link.target='_blank';link.rel='noopener';link.textContent='Open published track';row.append(link);
    if(projects.some(p=>p.generation_id===item.generation_id&&p.status==='published')){const withdraw=document.createElement('button');withdraw.textContent='Withdraw from website';withdraw.className='secondary';withdraw.onclick=()=>action(async()=>{await api(`/releases/${encodeURIComponent(item.prepared_id)}/withdraw`,{});toast('Removed from the public catalogue.');await refresh();});row.append(withdraw);}}
   if(q('pubHistory').children[position]!==row)q('pubHistory').insertBefore(row,q('pubHistory').children[position]||null);position++;
  }
  for(const row of q('pubHistory').children)if(!live.has(row.dataset.release))row.remove();
  if(!projects.length)q('pubStatus').textContent='Flag a reviewed track for distribution and finish its move to Preparation.';
 }
 q('pubResearch').onclick=()=>action(async()=>{const result=await api('/releases/research',{});toast('Published research for '+result.recordings+' recordings.');});q('pubProject').onchange=choices;q('pubRefresh').onclick=()=>action(refresh);
 q('pubPrepare').onclick=()=>action(async()=>{await api(`/releases/projects/${encodeURIComponent(q('pubProject').value)}/prepare`,{master_id:q('pubMaster').value||null,artwork_id:q('pubArtwork').value||null,include_flac:q('pubFlac').checked,timed_lyrics:q('pubLyrics').checked});toast('Preparing web assets.');await refresh();});
 const menu=document.createElement('details');menu.id='pubConfig';menu.innerHTML=`<summary>Publishing · R2 and website</summary><p class="subtle small">Credentials stay on this device. Blank key fields retain saved credentials.</p><div id="pubSettingsFields"></div><div class="actions"><button id="pubSaveConfig" type="button">Save publishing settings</button><button id="pubCheckConfig" type="button">Check public connection</button></div><p id="pubConfigMessage" role="status"></p>`;
 q('hostSettings').append(menu);
 const fields=[['endpoint','R2 S3 API endpoint'],['bucket','Public bucket'],['public_base','Public media URL'],['website_url','Website URL'],['profile','AWS profile (optional)'],['access_key','R2 access key'],['secret_key','R2 secret key']];
 for(const [key,label] of fields){const line=document.createElement('label');line.textContent=label;const input=document.createElement('input');input.id='pubConfig_'+key;input.type=['access_key','secret_key'].includes(key)?'password':'text';input.autocomplete='off';line.append(input);q('pubSettingsFields').append(line);}
 menu.addEventListener('toggle',()=>{if(menu.open)action(async()=>{const config=await api('/releases/settings');for(const [key] of fields)q('pubConfig_'+key).value=config.settings[key]||'';q('pubConfigMessage').textContent=config.credentials_available?'Credentials available.':'Enter R2 credentials here, select an AWS profile, or use GBR_R2_ACCESS_KEY_ID / GBR_R2_SECRET_ACCESS_KEY environment variables.';});});
 q('pubSaveConfig').onclick=()=>action(async()=>{const body=Object.fromEntries(fields.map(([key])=>[key,q('pubConfig_'+key).value.trim()]));await api('/releases/settings',body);q('pubConfig_access_key').value=q('pubConfig_secret_key').value='';q('pubConfigMessage').textContent='Saved. Run the public connection check.';});
 q('pubCheckConfig').onclick=()=>action(async()=>{q('pubConfigMessage').textContent='Checking upload, public URL and CORS…';const result=await api('/releases/check',{});q('pubConfigMessage').textContent='Ready · '+result.catalogue_url;});
 return {open:async()=>{await action(refresh);clearInterval(timer);timer=setInterval(()=>{if(!busy)action(refresh);},4000);},close:()=>{clearInterval(timer);timer=null;}};
}
