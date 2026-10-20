export function initWriting({api,toast,onPublished}) {
  const root = document.getElementById('writingView'), settings = document.getElementById('writingSettingsDialog');
  root.innerHTML = `<div class="section-heading writing-heading"><h1>Writing</h1><button id="writeSettings" type="button">Writing model settings</button></div>
  <div class="writing-grid"><section class="generation-panel"><h2>Brief</h2>
    <label>Operation<select id="writeKind"><option value="new">New song</option><option value="lyrics">Rewrite lyrics</option><option value="caption">Rewrite caption</option><option value="both">Rewrite lyrics & caption</option><option value="translate">Translate lyrics</option></select></label>
    <label>Direction<textarea id="writeBrief" rows="6" placeholder="Story, characters, style, language, changes to make…"></textarea></label>
    <div class="generation-fields"><label>Language<input id="writeLanguage" value="en"></label><label>Cluster<input id="writeCluster"></label><label>Style<input id="writeStyle"></label></div>
    <div id="writeSources" hidden><div class="generation-inline"><input id="writeSearch" type="search" placeholder="Find a writing YAML" aria-label="Find a writing YAML"><button id="writeScan" type="button">Reload</button></div><div id="writeFiles" class="generation-files"></div><p id="writeCount" class="subtle small"></p></div>
    <details><summary>Reference context</summary><label class="generation-check"><input id="writeLore" type="checkbox" checked>Relevant lore</label><label class="generation-check"><input id="writeStyles" type="checkbox" checked>Caption style references</label><label class="generation-check"><input id="writeLyricsRefs" type="checkbox" checked>Lyric references when available</label><label>Caption reference IDs <span class="subtle">(up to three, comma-separated)</span><input id="writeTemplateIds" list="writeTemplateList"><datalist id="writeTemplateList"></datalist></label></details>
    <button id="writeGenerate" class="primary" type="button">Create draft</button><p id="writeMessage" class="small" role="status"></p>
  </section><section class="generation-panel"><div class="writing-heading"><h2>Drafts</h2><button id="writeRefresh" type="button">Refresh</button></div><label>Writing task<select id="writeTasks"><option value="">No drafts yet</option></select></label><p id="writeStatus" class="subtle small" role="status"></p>
    <div id="writeDraft" hidden><div class="writing-metadata"><label>Title<input id="writeTitle"></label><label>Language<input id="writeDraftLanguage"></label><label>Explicit<select id="writeExplicit"><option value="">Unspecified</option><option value="no">No</option><option value="yes">Yes</option></select></label><label>Cluster<input id="writeDraftCluster"></label><label>Style<input id="writeDraftStyle"></label></div>
    <div class="writing-texts"><label>Lyrics<textarea id="writeLyrics" rows="17" spellcheck="false"></textarea></label><label>Caption<textarea id="writeCaption" rows="17" spellcheck="false"></textarea></label></div><label>Story<textarea id="writeStory" rows="2"></textarea></label>
    <div class="generation-inline"><button id="writeSave" type="button">Save draft edits</button><label id="writeOriginalWrap">Original recipe<select id="writeOriginal"><option value="keep">Keep original · create a variant</option><option value="archive_replace">Archive original · replace active recipe</option></select></label><button id="writePublish" class="primary" type="button">Add to Built</button></div>
    <details><summary>YAML changes & provenance</summary><pre id="writeDiff"></pre><pre id="writeProvenance"></pre><textarea id="writeYaml" rows="12" readonly aria-label="Draft YAML"></textarea></details>
    <details><summary>Save caption as an LLM style template</summary><div class="generation-inline"><input id="writeTemplateName" placeholder="Unique template ID" aria-label="New template ID"><button id="writeTemplateSave" type="button">Save template</button></div></details></div>
    <div class="generation-inline"><button id="writeRetry" type="button" hidden>Retry as new draft</button><button id="writeCancel" type="button" hidden>Cancel queued request</button></div>
  </section></div>`;
  settings.innerHTML = `<div class="dialog-heading"><h2>Writing model · llama.cpp</h2><button id="writeSettingsClose" class="close" aria-label="Close writing settings">×</button></div><label>Server base URL<input id="writeEndpoint" placeholder="http://127.0.0.1:8080"></label><div class="generation-inline"><button id="writeQuery" type="button">Query models</button></div><label>Loaded model<select id="writeModel"><option value="">Use worker's loaded model</option></select></label><div class="form-grid"><label>Temperature<input id="writeTemperature" type="number" step="0.05" min="0" max="2"></label><label>Top-p<input id="writeTopP" type="number" min="0.01" max="1" step="0.05"></label><label>Maximum output tokens<input id="writeMaxTokens" type="number" min="256" max="32768" step="1"></label></div><p id="writeBinaryFolder" class="subtle small"></p><p class="subtle small">Connect a running llama-server. Rewrites stay in drafts until you add them to Built. Reference folders may be empty.</p><button id="writeSettingsSave" class="primary" type="button">Save writing settings</button><p id="writeSettingsStatus" class="small" role="status"></p>`;
  const $ = id => document.getElementById(id);
  const fields = {writeTitle:'title',writeDraftLanguage:'language',writeExplicit:'explicit',writeDraftCluster:'cluster',writeDraftStyle:'style',writeLyrics:'lyrics',writeCaption:'caption',writeStory:'story'};
  const state = {rows:[],selected:new Set(),tasks:[],current:null,dirty:false,loaded:false,timer:null};
  const guard = fn => async (...args) => {try {await fn(...args);} catch(error) {toast(error.message);$('writeMessage').textContent=error.message;}};
  function mode() {$('writeSources').hidden=$('writeKind').value==='new';}
  let previousKind='new';
  function files() {
    $('writeFiles').replaceChildren();
    const query=$('writeSearch').value.toLowerCase();
    for (const row of state.rows.filter(r=>r.file.toLowerCase().includes(query))) {
      const label=document.createElement('label');label.className='generation-file';
      const check=document.createElement('input');check.type='checkbox';check.checked=state.selected.has(row.file);check.setAttribute('aria-label','Select writing '+row.file);
      check.onchange=()=>{if(check.checked)state.selected.add(row.file);else state.selected.delete(row.file);$('writeCount').textContent=state.selected.size+' selected';};
      const text=document.createElement('span');text.textContent=row.file;label.append(check,text);$('writeFiles').append(label);
    }
    $('writeCount').textContent=state.selected.size+' selected';
  }
  async function scan() {state.rows=(await api('/writing/catalogue')).inputs;files();}
  function clean() {if(state.dirty)throw new Error('Save your draft edits before changing drafts or publishing.');}
  function paint(task) {
    state.current=task;state.dirty=false;$('writeTasks').value=task.id;
    $('writeStatus').textContent=task.status+(task.error?' · '+task.error:'')+(task.source?' · '+task.source:'')+(task.warnings.length?' · '+task.warnings.join(' '):'');
    $('writeDraft').hidden=!['ready','publishing','published'].includes(task.status);
    for(const [id,key] of Object.entries(fields)) {const value=task.values[key];$(id).value=key==='explicit'&&typeof value==='boolean'?(value?'yes':'no'):value??'';$(id).disabled=task.status!=='ready';}
    $('writeSave').disabled=task.status!=='ready';$('writePublish').disabled=task.status==='published';$('writePublish').textContent=task.status==='publishing'?'Finish interrupted publication':task.status==='published'?'Added to Built':'Add to Built';
    $('writeOriginalWrap').hidden=!task.source;$('writeOriginal').disabled=task.status!=='ready';
    $('writeDiff').textContent=task.diff;$('writeYaml').value=task.yaml;$('writeProvenance').textContent=JSON.stringify(task.details,null,2);
    $('writeRetry').hidden=task.active||!['failed','running','queued'].includes(task.status);$('writeCancel').hidden=task.status!=='queued';
    $('writeTemplateSave').disabled=!['ready','published'].includes(task.status);
  }
  async function openTask(id) {clean();paint(await api('/writing/tasks/'+id));}
  async function refresh() {
    state.tasks=(await api('/writing/tasks')).tasks;
    $('writeTasks').replaceChildren(new Option('Choose a draft',''),...state.tasks.map(t=>new Option(`${t.title} · ${t.kind} · ${t.status}`,t.id)));
    $('writeTasks').value=state.current?.id||'';
    const latest=state.tasks.find(t=>t.id===state.current?.id);
    if(latest&&!state.dirty&&(['queued','running','publishing'].includes(state.current.status)||latest.status!==state.current.status)) await openTask(latest.id);
  }
  async function openSettings() {
    settings.showModal();$('writeSettingsStatus').textContent='Loading…';
    for(const node of settings.querySelectorAll('input,select,button:not(#writeSettingsClose)'))node.disabled=true;
    try {
    const s=await api('/writing/settings');
    for(const [id,key] of [['writeEndpoint','endpoint'],['writeTemperature','temperature'],['writeTopP','top_p'],['writeMaxTokens','max_tokens']])$(id).value=s[key];
    $('writeModel').replaceChildren(new Option("Use worker's loaded model",''));if(s.model)$('writeModel').append(new Option(s.model,s.model));$('writeModel').value=s.model;
    $('writeBinaryFolder').textContent='Local binaries: '+s.binary_directory;$('writeSettingsStatus').textContent='';
    } finally {for(const node of settings.querySelectorAll('input,select,button:not(#writeSettingsClose)'))node.disabled=false;}
  }
  async function saveSettings() {return api('/writing/settings',{endpoint:$('writeEndpoint').value.trim(),model:$('writeModel').value,temperature:Number($('writeTemperature').value),top_p:Number($('writeTopP').value),max_tokens:Number($('writeMaxTokens').value)});}
  $('writeSettings').onclick=guard(openSettings);$('writeSettingsClose').onclick=()=>settings.close();
  $('writeSettingsSave').onclick=guard(async()=>{await saveSettings();$('writeSettingsStatus').textContent='Writing model settings saved.';});
  $('writeQuery').onclick=guard(async()=>{await saveSettings();const result=await api('/writing/query',{});const chosen=$('writeModel').value;$('writeModel').replaceChildren(new Option("Use worker's loaded model",''),...result.models.map(m=>new Option(m,m)));if(result.models.includes(chosen))$('writeModel').value=chosen;$('writeSettingsStatus').textContent='Connected · '+result.models.join(', ');});
  $('writeKind').onchange=()=>{if(previousKind==='new'&&$('writeKind').value!=='new'&&$('writeLanguage').value==='en')$('writeLanguage').value='';if($('writeKind').value==='new'&&!$('writeLanguage').value)$('writeLanguage').value='en';previousKind=$('writeKind').value;mode();};$('writeSearch').oninput=files;$('writeScan').onclick=guard(scan);$('writeRefresh').onclick=guard(refresh);
  $('writeTasks').onchange=guard(async()=>{const id=$('writeTasks').value;try{if(id)await openTask(id);}catch(error){$('writeTasks').value=state.current?.id||'';throw error;}});
  for(const id of Object.keys(fields))$(id).oninput=()=>{state.dirty=true;$('writeStatus').textContent='Unsaved draft edits';};
  $('writeGenerate').onclick=guard(async()=>{
    clean();$('writeGenerate').disabled=true;$('writeMessage').textContent='Queueing writing request…';
    try {
      const kind=$('writeKind').value;
      const result=await api('/writing/tasks',{kind,brief:$('writeBrief').value.trim(),files:kind==='new'?[]:[...state.selected],language:$('writeLanguage').value.trim(),cluster:$('writeCluster').value.trim(),style:$('writeStyle').value.trim(),use_lore:$('writeLore').checked,use_styles:$('writeStyles').checked,use_lyrics:$('writeLyricsRefs').checked,template_ids:$('writeTemplateIds').value.split(',').map(s=>s.trim()).filter(Boolean)});
      await refresh();await openTask(result.tasks[0]);$('writeMessage').textContent=result.tasks.length+' writing request(s) queued.';
    } finally {$('writeGenerate').disabled=false;}
  });
  $('writeSave').onclick=guard(async()=>{
    if(!state.current)return;const changes={};
    for(const [id,key] of Object.entries(fields)){let old=state.current.values[key];if(key==='explicit'&&typeof old==='boolean')old=old?'yes':'no';if($(id).value!==String(old??''))changes[key]=$(id).value;}
    if(!Object.keys(changes).length){state.dirty=false;return;}
    paint(await api('/writing/tasks/'+state.current.id+'/edit',{revision:state.current.revision,changes}));await refresh();toast('Draft edits saved.');
  });
  $('writePublish').onclick=guard(async()=>{clean();if(!state.current)return;$('writePublish').disabled=true;try{paint(await api('/writing/tasks/'+state.current.id+'/publish',{revision:state.current.revision,mode:$('writeOriginal').value}));await refresh();await scan();onPublished?.('track');toast('Recipe added to Built.');}finally{$('writePublish').disabled=state.current?.status==='published';}});
  $('writeTemplateSave').onclick=guard(async()=>{clean();const result=await api('/writing/tasks/'+state.current.id+'/template',{template_id:$('writeTemplateName').value.trim()});onPublished?.('template');toast('LLM template saved: '+result.id);await openTask(state.current.id);});
  $('writeRetry').onclick=guard(async()=>{const result=await api('/writing/tasks/'+state.current.id+'/action',{action:'retry'});await refresh();await openTask(result.tasks[0]);});
  $('writeCancel').onclick=guard(async()=>{paint(await api('/writing/tasks/'+state.current.id+'/action',{action:'cancel'}));await refresh();});
  window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
  return {openSettings,close:()=>clearInterval(state.timer),async open(){
    if(!state.loaded){await scan();const templates=await api('/generation/templates');$('writeTemplateList').replaceChildren(...templates.templates.map(t=>new Option(t.cluster+' / '+t.style+' · '+t.source,t.id)));state.loaded=true;}
    await refresh();mode();clearInterval(state.timer);state.timer=setInterval(()=>{if(root.hidden){clearInterval(state.timer);return;}refresh().catch(error=>toast(error.message));},2500);
  }};
}
