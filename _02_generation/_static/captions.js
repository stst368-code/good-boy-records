export function initCaptions({root,onChange}){
  let templates=[],groups={},includeNew=true,legacy=null;const controls=new Map();
  const toolbar=document.createElement('div');toolbar.className='generation-inline caption-pool-toolbar';
  const status=document.createElement('span');status.className='subtle small';status.setAttribute('role','status');
  const grid=document.createElement('div');grid.className='caption-clusters';
  for(const [label,enabled]of [['All clusters',true],['Clear clusters',false]]){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=()=>{includeNew=enabled;for(const value of Object.values(groups))value.enabled=enabled;paint();onChange();};toolbar.append(b);}
  toolbar.append(status);root.append(toolbar,grid);
  function ids(){return templates.flatMap(t=>{const g=groups[t.cluster];return g?.enabled&&(!g.template_id||g.template_id===t.id||g.template_id==='@saved'&&g.template_ids.includes(t.id))?[t.id]:[];}).concat(Object.values(groups).filter(g=>g.enabled&&g.template_id&&g.template_id!=='@saved'&&!templates.some(t=>t.id===g.template_id)).map(g=>g.template_id));}
  function paint(){for(const [cluster,c]of controls){const g=groups[cluster];c.button.setAttribute('aria-pressed',String(g.enabled));c.row.classList.toggle('selected',g.enabled);c.select.value=g.template_id||'';}status.textContent=`${Object.values(groups).filter(g=>g.enabled).length} clusters · ${ids().length} templates in pool`;}
  function load(rows){
    templates=rows;controls.clear();grid.replaceChildren();
    const clusters=[...new Set(templates.map(t=>t.cluster))].sort();
    if(legacy){for(const cluster of clusters){const selected=templates.filter(t=>t.cluster===cluster&&(!legacy.genTemplate||t.id===legacy.genTemplate)&&(!legacy.genCaptionCluster||t.cluster.toLowerCase()===legacy.genCaptionCluster.toLowerCase())&&(!legacy.genCaptionStyle||t.style.toLowerCase()===legacy.genCaptionStyle.toLowerCase()));groups[cluster]={enabled:!!selected.length,template_id:selected.length===templates.filter(t=>t.cluster===cluster).length?'':'@saved',template_ids:selected.map(t=>t.id)};}includeNew=false;legacy=null;}
    for(const cluster of clusters){
      groups[cluster]||={enabled:includeNew,template_id:''};
      const row=document.createElement('div');row.className='caption-cluster';const button=document.createElement('button');button.type='button';button.className='caption-cluster-toggle';button.textContent=cluster;button.setAttribute('aria-label','Caption cluster '+cluster);
      button.onclick=()=>{includeNew=false;groups[cluster].enabled=!groups[cluster].enabled;paint();onChange();};
      const versions=document.createElement('details');versions.className='caption-versions';const summary=document.createElement('summary');summary.textContent='Versions';summary.setAttribute('aria-label','Template versions for '+cluster);
      const select=document.createElement('select');select.setAttribute('aria-label','Template version for '+cluster);select.append(new Option('All styles / versions',''));
      const items=templates.filter(t=>t.cluster===cluster);for(const t of items)select.append(new Option(`${t.style} · ${t.version||t.id} · ${t.source||'template'}${t.song_id?' · song #'+t.song_id:''}`,t.id));
      const old=groups[cluster];if(old.template_id==='@saved')select.append(new Option('Saved selection · '+old.template_ids.length+' versions','@saved'));else if(old.template_id&&!items.some(t=>t.id===old.template_id))select.append(new Option('Unavailable · '+old.template_id,old.template_id));
      select.onchange=()=>{groups[cluster].template_id=select.value;groups[cluster].enabled=true;paint();onChange();};versions.append(summary,select);row.append(button,versions);grid.append(row);controls.set(cluster,{row,button,select});
    }
    paint();
  }
  return {load,ids,snapshot:()=>({include_new:includeNew,groups:structuredClone(groups)}),restore(saved,oldFields={}){if(saved){includeNew=!!saved.include_new;groups=structuredClone(saved.groups||{});}else if(oldFields.genTemplate||oldFields.genCaptionCluster||oldFields.genCaptionStyle)legacy=oldFields;if(templates.length)load(templates);}};
}
