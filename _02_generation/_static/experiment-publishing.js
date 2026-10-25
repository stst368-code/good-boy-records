/* Public comparisons share R2 settings with publishing, but never enter review. */
const node=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e;};

export function experimentPublishing({api,toast,refresh}){
  const guard=fn=>async(...args)=>{try{await fn(...args);}catch(e){toast(e.message);}};
  const button=(title,fn)=>{const b=node('button','',title);b.type='button';b.onclick=guard(fn);return b;};
  function update(box,batch){
    if(!box)return;
    const signature=JSON.stringify(batch.publications||[]);
    if(box.signature===signature)return;box.signature=signature;
    const history=box.querySelector('.comparison-publications');history.replaceChildren();
    for(const p of batch.publications||[]){
      const line=node('div','comparison-publication');line.append(node('strong','',p.title),node('span','subtle small',p.status));
      if(p.progress?.total)line.append(node('span','subtle small',`${p.progress.phase} · ${p.progress.completed||0} / ${p.progress.total}`));
      if(p.error)line.append(node('p','error small',p.error));
      if(p.url&&p.status==='published'){const link=node('a','','Open website comparison');link.href=p.url;link.target='_blank';link.rel='noopener noreferrer';line.append(link);}
      for(const [action,title] of p.status==='failed'||p.status==='interrupted'?[['retry','Retry upload']]:p.status==='published'?[['withdraw','Remove from website index']]:[]){
        const b=button(title,async()=>{b.disabled=true;try{await api(`/generation/experiment-publications/${p.id}/${action}`,{});await refresh();}finally{b.disabled=false;}});line.append(b);
      }
      history.append(line);
    }
  }
  function controls(batch){
    const box=node('details','job-drawer comparison-publishing');box.append(node('summary','','Publish comparison to website'));
    const body=node('div','comparison-form'),message=node('p','subtle small','Load matched sets to publish a listening comparison using the existing R2 settings.');message.setAttribute('role','status');body.append(message);
    const fields=node('div','generation-fields');
    const field=(label,type='select')=>{const holder=node('label','',label),input=node(type);input.setAttribute('aria-label',label);holder.append(input);fields.append(holder);return input;};
    const parameter=field('Varied parameter'),group=field('Matched set'),title=field('Public comparison title','input'),slot=field('Website position'),format=field('Public audio format');
    title.maxLength=160;title.value=batch.experiment_label||'Generation comparison';
    format.append(new Option('Lossless FLAC · preserve the audio','flac'),new Option('MP3 · smaller download','mp3'));
    body.append(fields,node('p','subtle small','Uploads this matched set’s audio and public parameter/timing summaries. Source YAMLs, private comments and worker addresses stay local.'));
    const count=node('p','subtle small');body.append(count);
    let options=null,busy=false;
    const selected=()=>options?.parameters.find(p=>p.id===parameter.value)?.groups.find(g=>g.id===group.value);
    function paintCount(){const g=selected();count.textContent=g?`${g.count} complete settings · ${(g.bytes/1024**2).toFixed(1)} MiB of source audio. All other generation controls and the source are matched.`:'No matched set selected.';publish.disabled=busy||!g||!options?.r2;}
    function paintGroups(){
      const axis=options.parameters.find(p=>p.id===parameter.value);
      group.replaceChildren();
      for(const g of axis?.groups||[]){const c=g.controls;group.append(new Option(`${g.count} settings · ${g.min}–${g.max} · encoder ${c.text_encoder||'default'} · seeds ${c.encoder_seed||'?'} / ${c.sampler_seed||'?'}`,g.id));}
      slot.replaceChildren(new Option('Comparison link only',''));
      for(const t of options.targets.filter(t=>t.parameter===parameter.value))slot.append(new Option('How it’s made · '+t.title,t.id));
      if(slot.options.length===2)slot.selectedIndex=1;
      paintCount();
    }
    parameter.onchange=paintGroups;group.onchange=paintCount;
    const publish=button('Publish comparison to R2',async()=>{
      if(!selected()||busy)return;
      busy=true;paintCount();message.textContent='Queuing public comparison…';
      try{await api(`/generation/jobs/${batch.id}/publish-comparison`,{parameter:parameter.value,group:group.value,title:title.value.trim(),slot:slot.value,format:format.value});message.textContent='Upload queued. The website will load the published set from R2.';await refresh();}
      finally{busy=false;paintCount();}
    });publish.disabled=true;
    const reload=button('Reload matched sets',load);body.append(publish,reload);box.append(body,node('div','comparison-publications'));
    async function load(){
      reload.disabled=true;message.textContent='Finding completed runs with matching controls…';
      try{options=await api(`/generation/jobs/${batch.id}/comparison-options`);const old=parameter.value;parameter.replaceChildren(...options.parameters.map(p=>new Option(p.label,p.id)));if(options.parameters.some(p=>p.id===old))parameter.value=old;else if(options.parameters.some(p=>p.id==='sampler_steps'))parameter.value='sampler_steps';
        fields.hidden=!options.parameters.length;
        message.textContent=!options.parameters.length?'No matched sweep is ready. Complete at least two settings with the same source, caption, other controls and seeds.':!options.r2?'Configure R2 under Settings → Publishing before uploading.':'Select the matched set and website position, then publish.';
        paintGroups();
      }finally{reload.disabled=false;}
    }
    box.addEventListener('toggle',guard(async()=>{if(box.open&&!options)await load();}));
    update(box,batch);return box;
  }
  return {controls,update};
}
