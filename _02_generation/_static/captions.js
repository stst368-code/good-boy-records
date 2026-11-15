export function initCaptions({root,onChange}){
  let templates=[],groups={},includeNew=true,legacy=null;const controls=new Map();
  const toolbar=document.createElement('div');toolbar.className='generation-inline caption-pool-toolbar';
  const status=document.createElement('span');status.className='subtle small';status.setAttribute('role','status');
  const grid=document.createElement('div');grid.className='caption-clusters';
  for(const [label,enabled]of [['All clusters',true],['Clear clusters',false]]){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>{includeNew=enabled;for(const value of Object.values(groups))value.enabled=enabled;paint();onChange();};toolbar.append(b);}
  toolbar.append(status);root.append(toolbar,grid);
  function ids(){return templates.flatMap(t=>{const g=groups[t.cluster];return g?.enabled&&(!g.template_id||g.template_id===t.id||g.template_id==='@saved'&&g.template_ids.includes(t.id))?[t.id]:[];}).concat(Object.values(groups).filter(g=>g.enabled&&g.template_id&&g.template_id!=='@saved'&&!templates.some(t=>t.id===g.template_id)).map(g=>g.template_id));}
  function paint(){for(const [cluster,c]of controls){const g=groups[cluster];c.button.setAttribute('aria-pressed',String(g.enabled));c.row.classList.toggle('selected',g.enabled);for(const input of c.options)input.checked=!g.template_id||g.template_id===input.value||g.template_id==='@saved'&&g.template_ids.includes(input.value);
      c.summary.textContent=`Templates (${c.options.filter(input=>input.checked).length}/${c.options.length})`;}status.textContent=`${Object.values(groups).filter(g=>g.enabled).length} clusters · ${ids().length} templates in pool`;}
  function load(rows){
    templates=rows;controls.clear();grid.replaceChildren();
    const clusters=[...new Set(templates.map(t=>t.cluster))].sort();
    if(legacy){for(const cluster of clusters){const selected=templates.filter(t=>t.cluster===cluster&&(!legacy.genTemplate||t.id===legacy.genTemplate)&&(!legacy.genCaptionCluster||t.cluster.toLowerCase()===legacy.genCaptionCluster.toLowerCase())&&(!legacy.genCaptionStyle||t.style.toLowerCase()===legacy.genCaptionStyle.toLowerCase()));groups[cluster]={enabled:!!selected.length,template_id:selected.length===templates.filter(t=>t.cluster===cluster).length?'':'@saved',template_ids:selected.map(t=>t.id)};}includeNew=false;legacy=null;}
    for(const cluster of clusters){
      groups[cluster]||={enabled:includeNew,template_id:''};
      const row=document.createElement('div');row.className='caption-cluster';const button=document.createElement('button');button.type='button';button.className='caption-cluster-toggle';button.textContent=cluster;button.setAttribute('aria-label','Caption cluster '+cluster);
      button.onclick=()=>{includeNew=false;groups[cluster].enabled=!groups[cluster].enabled;if(groups[cluster].enabled&&groups[cluster].template_id==='@saved'&&!groups[cluster].template_ids.length)groups[cluster].template_id='';paint();onChange();};
      const versions=document.createElement('details');versions.className='caption-versions';const summary=document.createElement('summary');summary.textContent='Versions';summary.setAttribute('aria-label','Template versions for '+cluster);
      const items=templates.filter(t=>t.cluster===cluster);
      const options=items.map(t=>{
        const label=document.createElement('label');label.className='generation-check';
        const input=document.createElement('input');input.type='checkbox';input.value=t.id;
        label.append(input,`${t.style} · ${t.version||t.id}${t.song_id?' · song #'+t.song_id:''}`);
        versions.append(label);
        input.onchange=()=>{
          const selected=options.filter(option=>option.checked).map(option=>option.value);
          groups[cluster]={enabled:!!selected.length,template_id:selected.length===options.length?'':'@saved',template_ids:selected};
          paint();onChange();
        };
        return input;
      });
      const all=document.createElement('button');all.type='button';all.textContent='Select all templates';
      all.onclick=()=>{groups[cluster]={enabled:true,template_id:''};paint();onChange();};
      const clear=document.createElement('button');clear.type='button';clear.textContent='Clear templates';
      clear.onclick=()=>{groups[cluster]={enabled:false,template_id:'@saved',template_ids:[]};paint();onChange();};
      versions.prepend(summary,all,clear);row.append(button,versions);grid.append(row);
      controls.set(cluster,{row,button,options,summary});
    }
    paint();
  }
  return {load,ids,snapshot:()=>({include_new:includeNew,groups:structuredClone(groups)}),restore(saved,oldFields={}){if(saved){includeNew=!!saved.include_new;groups=structuredClone(saved.groups||{});}else if(oldFields.genTemplate||oldFields.genCaptionCluster||oldFields.genCaptionStyle)legacy=oldFields;if(templates.length)load(templates);}};
}
