import {selectChoices} from '/static/choices.js';

export function initAuthoring({root,api,toast,onPublished,refreshDrafts}) {
  const assistant=root.querySelector('.writing-grid');
  assistant.id='writingAssistant';assistant.hidden=true;
  const nav=document.createElement('div');nav.className='writing-tabs';nav.setAttribute('role','tablist');nav.setAttribute('aria-label','Writing tools');
  const base=document.createElement('section');base.id='writingBase';base.className='generation-panel writing-base';
  base.innerHTML=`<div class="writing-heading"><h2>Song base</h2><span class="subtle small">Lyrics and track registration</span></div>
    <label>Start from<select id="baseSource"><option value="">New song</option></select></label>
    <div class="writing-base-grid"><label>Song name<input id="baseTitle" maxlength="500" autocomplete="off"></label><label class="base-lyrics">Lyrics<textarea id="baseLyrics" rows="18" spellcheck="false"></textarea></label>
    <div class="base-metadata"><label>Writing / author<input id="baseAuthor" value="gbr"></label><label>Inspiration artist<input id="baseInspirationAuthor"></label><label>Song inspiration<input id="baseInspirationTrack"></label><label>Album<input id="baseAlbum" list="writingAlbums" autocomplete="off"><datalist id="writingAlbums"></datalist></label><label>Album position<input id="basePosition" type="number" min="1" step="1"></label></div></div>
    <div class="writing-heading"><span class="subtle small">Saves lowercase, removes blank lines and hyphenates the song name.</span><button id="baseSave" class="primary" type="button">Save new song</button></div><p id="baseStatus" role="status" class="small"></p>`;
  const caption=document.createElement('section');caption.id='writingCaptions';caption.hidden=true;
  caption.innerHTML=`<div class="generation-panel"><div class="writing-heading"><h2>Caption builder</h2><button id="captionClear" type="button">Clear caption</button></div>
    <div class="caption-meta"><label>Cluster<input id="captionCluster" list="writingClusters" autocomplete="off" required><datalist id="writingClusters"></datalist></label><label>Version (optional)<input id="captionVersion"></label><label>Song link<select id="captionSong"><option value="">Unlinked · available for any song</option></select></label></div>
    <p class="subtle small">Choose reference sections to copy into the editable boxes. Duplicate cluster/version names receive -2, -3, …</p></div>
    <div id="captionSections" class="caption-sections"></div><div class="generation-panel writing-heading"><span id="captionStatus" role="status" class="small"></span><button id="captionSave" type="button" class="primary">Save custom caption</button></div>`;
  root.insertBefore(nav,assistant);root.insertBefore(base,assistant);root.insertBefore(caption,assistant);
  const panels=[base,caption,assistant];const names=['Base','Captions','Model assistant'];
  panels.forEach((panel,index)=>{panel.setAttribute('role','tabpanel');const button=document.createElement('button');button.type='button';button.textContent=names[index];button.id='writingMode'+index;button.setAttribute('role','tab');button.setAttribute('aria-controls',panel.id);button.setAttribute('aria-selected',String(index===0));panel.setAttribute('aria-labelledby',button.id);
    button.onclick=()=>{panels.forEach((p,i)=>{p.hidden=i!==index;nav.children[i].setAttribute('aria-selected',String(i===index));});};
    button.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?2:(index+(event.key==='ArrowRight'?1:2))%3;nav.children[next].click();nav.children[next].focus();}};nav.append(button);});
  const $=id=>document.getElementById(id);
  let options=null,source=null,baseRequest=null,captionRequest=null;
  const changed=()=>{baseRequest=null;};
  const captionChanged=()=>{captionRequest=null;};
  for(const node of base.querySelectorAll('input,textarea'))node.addEventListener('input',changed);
  for(const node of caption.querySelectorAll('input,select'))node.addEventListener('input',captionChanged);
  function guard(button,status,fn){return async()=>{button.disabled=true;status.textContent='Saving…';try{await fn();}catch(error){status.textContent=error.message;toast(error.message);}finally{button.disabled=false;}};}
  function key(){return crypto.randomUUID().replaceAll('-','');}
  function tracks(){return (options?.tracks||[]).filter(r=>r.song_id);}
  function buildOptions(){
    const selected=$('baseSource').value;
    $('baseSource').replaceChildren(new Option('New song',''),...tracks().map(t=>new Option(`${t.title} · #${t.song_id} / ${t.variant_id}`,t.file)));$('baseSource').value=selected;
    $('writingAlbums').replaceChildren(...options.albums.map(value=>new Option(value,value)));
    $('writingClusters').replaceChildren(...options.clusters.map(value=>new Option(value,value)));
    selectChoices($('captionSong'),tracks().filter(t=>t.variant_id==='1').map(t=>({value:t.song_id,label:t.title+' · #'+t.song_id})),{empty:'Unlinked · available for any song'});
  }
  $('baseSource').onchange=async()=>{
    const file=$('baseSource').value;source=null;baseRequest=null;$('baseSave').disabled=true;
    try{
      if(file){const loaded=await api('/writing/base?file='+encodeURIComponent(file));if($('baseSource').value!==file)return;source=loaded;}
      const metadata=source?.metadata||{},values=source?.values||{};
      $('baseTitle').value=values.title||metadata.song_name||'';$('baseLyrics').value=values.lyrics||'';
      for(const [id,name,fallback] of [['baseAuthor','author','gbr'],['baseInspirationAuthor','inspiration_author',''],['baseInspirationTrack','inspiration_track',''],['baseAlbum','album_name',''],['basePosition','album_position','']])$(id).value=metadata[name]||fallback;
      $('baseSave').textContent=file?'Save as new variant':'Save new song';$('baseStatus').textContent=file?'The selected source is preserved. Saving creates the next variant.':'';
    }catch(error){$('baseStatus').textContent=error.message;toast(error.message);}finally{$('baseSave').disabled=!!file&&!source;}
  };
  $('baseSave').onclick=guard($('baseSave'),$('baseStatus'),async()=>{
    if($('baseSource').value&&!source)throw new Error('Reload the selected track before saving');
    const request=baseRequest||{request_id:key(),file:$('baseSource').value,source_sha256:source?.sha256||'',title:$('baseTitle').value,lyrics:$('baseLyrics').value,
      author:$('baseAuthor').value,inspiration_author:$('baseInspirationAuthor').value,inspiration_track:$('baseInspirationTrack').value,album_name:$('baseAlbum').value,album_position:$('basePosition').value?Number($('basePosition').value):null};
    baseRequest=request;const result=await api('/writing/base',request);
    $('baseTitle').value=result.values.title;$('baseLyrics').value=result.values.lyrics;
    // Keep the request and saved result together; editing creates a new request.
    options=await api('/writing/authoring');buildOptions();await refreshDrafts();onPublished?.('track');
    $('baseStatus').textContent=`Saved #${result.publication.song_id} · variant ${result.publication.variant_id} · ${result.publication.target}`;toast('Song saved to Built and the catalogue.');
  });
  function fields(){return Object.fromEntries([...caption.querySelectorAll('[data-caption-field]')].map(node=>[node.dataset.captionField,node.value]));}
  function sections(){
    if($('captionSections').children.length)return;
    for(const section of options.sections){
      const panel=document.createElement('section');panel.className='generation-panel';const heading=document.createElement('h3');heading.textContent=section.name;panel.append(heading);
      for(const field of section.fields){
        if(field.key==='primary'){const title=document.createElement('h4');title.textContent='Instrument Lifecycle Description (Primary/Secondary Layering)';panel.append(title);}
        const group=document.createElement('div');group.className='caption-field';const label=document.createElement('label');label.textContent=field.label;
        const text=document.createElement('textarea');text.id='caption-'+field.key;text.rows=4;text.dataset.captionField=field.key;label.htmlFor=text.id;label.append(text);text.oninput=captionChanged;
        const references=document.createElement('div');references.className='caption-reference';
        const search=document.createElement('input');search.type='search';search.placeholder='Search reference examples';search.setAttribute('aria-label','Search '+field.label+' examples');
        const pick=document.createElement('select');pick.setAttribute('aria-label',field.label+' reference');pick.append(new Option('Load a reference…',''));
        let choices=[],serial=0,timer;
        async function query(){const id=++serial;try{const result=await api('/writing/caption-examples?field='+field.key+'&q='+encodeURIComponent(search.value));if(id!==serial)return;choices=result.examples;pick.replaceChildren(new Option(result.total?`Choose example (${result.total}${result.total>100?' · first 100':''})`:'No matching labelled examples',''),...choices.map((r,i)=>new Option(r.cluster+' / '+r.version+' · '+r.text.slice(0,90),String(i))));}catch(error){pick.replaceChildren(new Option(error.message,''));}}
        search.oninput=()=>{clearTimeout(timer);timer=setTimeout(query,300);};pick.onfocus=()=>{if(!choices.length)query();};
        pick.onchange=()=>{if(pick.value==='')return;text.value=choices[Number(pick.value)].text;captionChanged();pick.value='';text.focus();};
        references.append(search,pick);group.append(label,references);panel.append(group);
      }
      $('captionSections').append(panel);
    }
  }
  $('captionClear').onclick=()=>{for(const node of caption.querySelectorAll('[data-caption-field],input'))node.value='';$('captionSong').value='';captionChanged();$('captionStatus').textContent='';};
  $('captionSave').onclick=guard($('captionSave'),$('captionStatus'),async()=>{
    const request=captionRequest||{request_id:key(),cluster:$('captionCluster').value,version:$('captionVersion').value,song_id:$('captionSong').value,fields:fields()};captionRequest=request;
    const result=await api('/writing/captions',request);options=await api('/writing/authoring');buildOptions();await refreshDrafts();onPublished?.('template');
    $('captionStatus').textContent='Saved custom caption: '+result.id+(request.song_id?' · linked to song #'+request.song_id:' · unlinked pool');toast('Custom caption saved.');
  });
  return {async open(){options=await api('/writing/authoring');buildOptions();sections();}};
}
