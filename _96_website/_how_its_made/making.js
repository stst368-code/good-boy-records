/* Content is data, never executable HTML. Edit tour.yaml; build.py validates
   the graph and stages its public JSON and explicitly referenced media. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const SVG = 'http://www.w3.org/2000/svg';
  let data, stage, selected, graphObserver;
  const el = (tag, cls, text) => {const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;};
  const svg = (tag, attrs={}) => {const n=document.createElementNS(SVG,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  const status = text => {$('pageStatus').textContent=text;};
  function source(value) {
    if (typeof value !== 'string') throw Error('Missing media address');
    if (value.startsWith('showcase:')) return new URL(value.slice(9), window.GBR_SHOWCASE_ROOT || new URL('../showcase/',location.href)).href;
    const url=new URL(value,location.href);
    if(url.protocol!=='https:' && url.origin!==location.origin)throw Error('Unsupported media address');
    if(url.username||url.password)throw Error('Unsupported media credentials');
    return url.href;
  }
  function navigate(stageId,nodeId,focus=false) {
    const hash='#'+encodeURIComponent(stageId)+(nodeId?'/'+encodeURIComponent(nodeId):'');
    if(location.hash!==hash)history.pushState(null,'',hash);
    renderRoute();
    if(focus)$('stepDetail').scrollIntoView({block:'start',behavior:'auto'});
  }
  function route() {
    let parts=[];try{parts=location.hash.slice(1).split('/').map(decodeURIComponent);}catch(_){}
    const s=data.stages.find(s=>s.id===parts[0])||data.stages[0];
    return {stage:s,node:s.nodes.find(n=>n.id===parts[1])||s.nodes.find(n=>n.id===s.entry)};
  }
  function drawWheel() {
    const wheel=svg('svg',{viewBox:'0 0 520 520',role:'tablist','aria-label':'Production stages'});
    const count=data.stages.length,sector=360/count;
    const point=(r,a)=>[260+r*Math.cos(a*Math.PI/180),260+r*Math.sin(a*Math.PI/180)];
    data.stages.forEach((s,i)=>{
      const mid=-90+i*sector,start=mid-sector/2+2,end=mid+sector/2-2;
      const a=point(224,start),b=point(224,end),c=point(102,end),d=point(102,start);
      const g=svg('g',{role:'tab','aria-controls':'stageWorkspace','aria-label':`${i+1}. ${s.title}`,'aria-selected':s.id===stage.id,tabindex:s.id===stage.id?0:-1,'data-stage':s.id,class:'wheel-segment'});
      g.style.setProperty('--sector',s.colour);
      if(s.id===stage.id)g.style.transform=`translate(${Math.cos(mid*Math.PI/180)*10}px,${Math.sin(mid*Math.PI/180)*10}px)`;
      g.append(svg('path',{d:`M${a} A224 224 0 0 1 ${b} L${c} A102 102 0 0 0 ${d} Z`}));
      const p=point(164,mid),number=svg('text',{x:p[0],y:p[1]-1,class:'sector-index'});number.textContent=String(i+1).padStart(2,'0');g.append(number);
      const label=svg('text',{x:p[0],y:p[1]+23});label.textContent=s.short||s.title;g.append(label);
      g.addEventListener('click',()=>navigate(s.id));
      g.addEventListener('keydown',e=>{
        let next=i;
        if(['ArrowRight','ArrowDown'].includes(e.key))next=(i+1)%count;
        else if(['ArrowLeft','ArrowUp'].includes(e.key))next=(i+count-1)%count;
        else if(e.key==='Home')next=0;else if(e.key==='End')next=count-1;
        else if(e.key==='Enter'||e.key===' '){e.preventDefault();navigate(s.id);return;}else return;
        e.preventDefault();navigate(data.stages[next].id);$('wheel').querySelector('[aria-selected=true]').focus();
      });
      wheel.append(g);
    });
    wheel.append(svg('circle',{cx:260,cy:260,r:88,class:'wheel-core'}));
    const title=svg('text',{x:260,y:255,class:'wheel-center-title'});title.textContent='G✦BR';wheel.append(title);
    const sub=svg('text',{x:260,y:278,class:'wheel-center-sub'});sub.textContent='THE PROCESS';wheel.append(sub);
    $('wheel').replaceChildren(wheel);
    $('stageLegend').textContent=`${String(data.stages.indexOf(stage)+1).padStart(2,'0')} / ${stage.title.toUpperCase()} · OPEN`;
  }
  function levels() {
    const depth=new Map([[stage.entry,0]]);
    const visit=id=>{const node=stage.nodes.find(n=>n.id===id);for(const edge of node.next||[]){const d=depth.get(id)+1;if(!depth.has(edge.to)||depth.get(edge.to)<d){depth.set(edge.to,d);visit(edge.to);}}};
    visit(stage.entry);
    const rows=[];for(const node of stage.nodes){const d=depth.get(node.id)||0;(rows[d]??=[]).push(node);}return rows;
  }
  function drawGraph() {
    const graph=$('processGraph');graph.replaceChildren(svg('svg',{'aria-hidden':true}));
    levels().forEach(row=>{
      const line=el('div','process-row');line.style.setProperty('--columns',row.length);
      row.forEach((node)=>{
        const button=el('button',`step-node kind-${node.kind||'step'}`);button.type='button';button.dataset.node=node.id;
        button.setAttribute('aria-pressed',String(node.id===selected.id));button.setAttribute('aria-controls','stepDetail');
        button.append(el('small','',`${String(stage.nodes.indexOf(node)+1).padStart(2,'0')} / ${node.kind==='decision'?'DECISION':node.kind==='output'?'OUTPUT':'STEP'}`),el('strong','',node.title),el('p','',node.summary));
        button.addEventListener('click',()=>navigate(stage.id,node.id,matchMedia('(max-width:1350px)').matches));line.append(button);
      });graph.append(line);
    });
    graphObserver?.disconnect();graphObserver=new ResizeObserver(drawEdges);graphObserver.observe(graph);requestAnimationFrame(drawEdges);
  }
  function drawEdges() {
    if(!stage)return;const graph=$('processGraph'),drawing=graph.querySelector('svg');if(!drawing)return;
    const bounds=graph.getBoundingClientRect(),scale=bounds.width/graph.clientWidth||1;
    drawing.setAttribute('viewBox',`0 0 ${graph.clientWidth} ${graph.clientHeight}`);drawing.replaceChildren();
    const defs=svg('defs'),marker=svg('marker',{id:'arrow',viewBox:'0 0 10 10',refX:8,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});marker.append(svg('path',{d:'M0 0 L10 5 L0 10 Z',fill:'context-stroke'}));defs.append(marker);drawing.append(defs);
    const ancestors=new Set([selected.id]);let changed=true;while(changed){changed=false;for(const n of stage.nodes)if(!ancestors.has(n.id)&&(n.next||[]).some(e=>ancestors.has(e.to))){ancestors.add(n.id);changed=true;}}
    for(const node of stage.nodes){for(const edge of node.next||[]){
      const from=graph.querySelector(`[data-node="${node.id}"]`).getBoundingClientRect(),to=graph.querySelector(`[data-node="${edge.to}"]`).getBoundingClientRect();
      const x1=(from.left+from.width/2-bounds.left)/scale,y1=(from.bottom-bounds.top)/scale,x2=(to.left+to.width/2-bounds.left)/scale,y2=(to.top-bounds.top)/scale,m=(y1+y2)/2;
      const active=ancestors.has(node.id)&&ancestors.has(edge.to);
      drawing.append(svg('path',{d:`M${x1} ${y1} C${x1} ${m},${x2} ${m},${x2} ${y2-5}`,fill:'none',stroke:active?stage.colour:'#53644a','stroke-width':active?2:1.3,'marker-end':'url(#arrow)'}));
      if(edge.label){const label=svg('text',{x:(x1+x2)/2,y:m-5,'text-anchor':'middle',class:'edge-label'});label.textContent=edge.label;drawing.append(label);}
    }}
  }
  function media(block) {
    const fig=el('figure','media-block');let asset;
    if(block.kind==='image'){asset=el('img');asset.alt=block.alt;asset.loading='lazy';}
    else {asset=el(block.kind);asset.controls=true;asset.preload='none';if(block.kind==='video'){asset.playsInline=true;if(block.poster)asset.poster=source(block.poster);}}
    asset.src=source(block.src);
    asset.addEventListener('error',()=>{if(fig.querySelector('.media-error'))return;const error=el('p','media-error','This example could not be loaded. ');const link=el('a','','Open media');link.href=asset.src;link.target='_blank';link.rel='noopener noreferrer';error.append(link);fig.append(error);});
    fig.append(asset);if(block.caption)fig.append(el('figcaption','',block.caption));return fig;
  }
  function experiment(block) {
    const box=el('section','playground');box.dataset.experiment=block.id;
    box.append(el('span','experiment-count',`${block.samples.length} / ${Math.round((block.max-block.min)/block.step)+1} EXAMPLES AVAILABLE`),el('h4','',block.title));
    const top=el('div','experiment-reading'),label=el('label','',block.parameter),reading=el('output');const slider=el('input');slider.type='range';slider.min=block.min;slider.max=block.max;slider.step=block.step;slider.id='experiment-'+block.id;label.htmlFor=slider.id;top.append(label,reading);box.append(top,slider);
    const ends=el('div','range-ends');ends.append(el('span','',String(block.min)),el('span','',String(block.max)));box.append(ends);
    const message=el('p','experiment-state');message.setAttribute('role','status');const audio=el('audio','experiment-audio');audio.controls=true;audio.preload='metadata';audio.hidden=true;audio.addEventListener('error',()=>{if(audio.hasAttribute('src'))message.textContent='This listening clip could not be loaded. Choose another setting or try again.';});box.append(message,audio);
    const compare=el('div','experiment-compare'),select=el('select');select.setAttribute('aria-label','Compare with '+block.parameter);
    for(const sample of block.samples){const option=el('option','',`${sample.value} ${block.parameter}`);option.value=sample.value;select.append(option);}
    const a=el('button','','A'),b=el('button','','B');a.type=b.type='button';compare.append(el('span','','Compare with'),select,a,b);compare.hidden=block.samples.length<2;box.append(compare);
    const base=el('p','fixed-conditions',block.conditions);box.append(base);
    let primary=block.samples[0]?.value??block.min,current=primary,load=0;
    slider.value=primary;
    function audition(value,preserve=false){
      const sample=block.samples.find(s=>s.value===Number(value)),wasPlaying=!audio.paused,time=preserve?audio.currentTime:0,token=++load;
      current=Number(value);audio.pause();audio.onloadedmetadata=null;reading.value=current;reading.textContent=current;
      a.textContent='A · '+primary;b.textContent='B · '+(select.value||'—');
      a.setAttribute('aria-pressed',String(!!sample&&current===primary));b.setAttribute('aria-pressed',String(!!sample&&current===Number(select.value)));
      a.disabled=b.disabled=block.samples.length<2||!block.samples.some(s=>s.value===primary);select.disabled=block.samples.length<2;
      if(!sample){audio.hidden=true;audio.removeAttribute('src');audio.load();message.textContent=block.samples.length?'No audio has been published for this setting.':'The listening comparison is ready to fill. Matched clips have not been published yet.';return;}
      audio.hidden=false;audio.src=source(sample.audio);audio.onloadedmetadata=()=>{if(token!==load)return;if(time)audio.currentTime=Math.min(time,Math.max(0,(Number.isFinite(audio.duration)?audio.duration:time)-.05));if(preserve&&wasPlaying)audio.play().catch(()=>{message.textContent='Press play to listen to this setting.';});};audio.load();
      message.textContent=sample.label||`${block.parameter}: ${current}. ${sample.notes||'Listen, then switch at the same playback position.'}`;
      a.setAttribute('aria-pressed',String(current===primary));b.setAttribute('aria-pressed',String(current===Number(select.value)));
    }
    slider.addEventListener('input',()=>{primary=Number(slider.value);audition(primary,true);});
    select.addEventListener('change',()=>{b.textContent='B · '+select.value;});
    a.addEventListener('click',()=>audition(primary,true));b.addEventListener('click',()=>audition(Number(select.value),true));
    if(block.samples.length>1)select.value=String(block.samples[1].value);audition(primary);
    return box;
  }
  function blocks(items,target) {
    for(const block of items||[]){
      if(block.type==='paragraph')target.append(el('p','',block.text));
      else if(block.type==='heading')target.append(el('h4','',block.text));
      else if(block.type==='note')target.append(el('div','block-note',block.text));
      else if(block.type==='code'){const pre=el('pre'),code=el('code','',block.text);pre.append(code);target.append(pre);}
      else if(block.type==='media')target.append(media(block));
      else if(block.type==='experiment')target.append(experiment(block));
      else if(block.type==='links'){const list=el('ul','resource-links');for(const item of block.items){const row=el('li'),link=el('a','',item.label);link.href=source(item.url);link.target='_blank';link.rel='noopener noreferrer';row.append(link);if(item.description)row.append(el('small','',item.description));list.append(row);}target.append(list);}
      else if(block.type==='stage-link'){const button=el('button','quiet-button',block.label);button.type='button';button.onclick=()=>{if($('topicDialog').open)$('topicDialog').close();navigate(block.stage,block.node);};target.append(button);}
      else if(block.type==='topic-link'){const button=el('button','quiet-button',block.label);button.type='button';button.onclick=()=>openTopic(block.topic);target.append(button);}
      else if(block.type==='analytics-link'){const link=el('a','quiet-button',block.label);link.href='../analytics/';link.onclick=e=>{if($('topicDialog').open)$('topicDialog').close();if(parent!==window){e.preventDefault();parent.postMessage({type:'gbr:open-analytics'},location.origin);}};target.append(link);}
    }
  }
  function drawDetail(){
    document.querySelectorAll('.step-node').forEach(n=>n.setAttribute('aria-pressed',String(n.dataset.node===selected.id)));
    const detail=$('stepDetail');detail.querySelectorAll('audio,video').forEach(a=>a.pause());detail.replaceChildren();detail.scrollTop=0;
    const tools=el('div','step-tools');tools.append(el('span','',`${stage.short||stage.title} / ${String(stage.nodes.indexOf(selected)+1).padStart(2,'0')}`));const share=el('button','','Link to this step');share.type='button';share.onclick=async()=>{try{await navigator.clipboard.writeText(location.href);status('Step link copied');share.textContent='Copied';}catch(_){status('Step address: '+location.href);share.textContent=location.hash;}};tools.append(share);detail.append(tools);
    const title=el('h3','',selected.title);title.id='detailTitle';detail.append(title);blocks(selected.body,detail);
    if(selected.next?.length){const footer=el('div','detail-output');footer.append(el('p','eyebrow',selected.kind==='decision'?'CHOOSE A ROUTE':'FOLLOW THE PROCESS'));const next=el('div','next-steps');for(const edge of selected.next){const node=stage.nodes.find(n=>n.id===edge.to),button=el('button','',edge.label||node.title);button.type='button';button.onclick=()=>navigate(stage.id,node.id);next.append(button);}footer.append(next);detail.append(footer);}
    status(`${stage.title}: ${selected.title}`);requestAnimationFrame(drawEdges);
  }
  function renderRoute(){
    const next=route(),changed=next.stage!==stage;stage=next.stage;selected=next.node;document.documentElement.style.setProperty('--stage',stage.colour);
    if(changed){drawWheel();const header=$('stageHeader');header.replaceChildren(el('span','eyebrow',`STAGE ${String(data.stages.indexOf(stage)+1).padStart(2,'0')} / ${data.stages.length}`));const title=el('h2','',stage.title);title.id='stageTitle';header.append(title,el('p','',stage.summary));const io=el('div','stage-io');for(const item of stage.delivers||[])io.append(el('span','',item));header.append(io);drawGraph();}
    drawDetail();
  }
  function openTopic(id){const topic=data.topics.find(t=>t.id===id);if(!topic)return;const target=$('topicContent');target.querySelectorAll('audio,video').forEach(a=>a.pause());target.replaceChildren(el('span','eyebrow','UNDER THE HOOD'));const title=el('h2','',topic.title);title.id='topicTitle';target.append(title);blocks(topic.body,target);$('topicDialog').scrollTop=0;if(!$('topicDialog').open)$('topicDialog').showModal();}
  function back(event){if(parent!==window){event.preventDefault();parent.postMessage({type:'gbr:close-making'},location.origin);}}
  document.querySelector('.back-player').onclick=back;document.querySelector('.making-brand').onclick=back;
  $('closeTopic').onclick=()=>$('topicDialog').close();$('topicDialog').addEventListener('close',()=>{$('topicContent').querySelectorAll('audio,video').forEach(a=>a.pause());});
  document.addEventListener('play',event=>{for(const player of document.querySelectorAll('audio,video'))if(player!==event.target)player.pause();},true);
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('topicDialog').open&&parent!==window){event.preventDefault();parent.postMessage({type:'gbr:close-making'},location.origin);}});
  addEventListener('popstate',()=>data&&renderRoute());addEventListener('hashchange',()=>data&&renderRoute());
  fetch('tour.json',{cache:'no-cache'}).then(r=>{if(!r.ok)throw Error('Content request failed');return r.json();}).then(payload=>{
    if(payload.format!=='gbr-making-v1'||!payload.stages?.length)throw Error('Invalid process content');data=payload;document.querySelector('.stage-deck>.eyebrow').textContent=`ONE PROCESS / ${data.stages.length} STAGES`;
    for(const topic of data.topics){const button=el('button','',topic.short||topic.title);button.type='button';button.onclick=()=>openTopic(topic.id);$('topicButtons').append(button);}renderRoute();
  }).catch(error=>{$('makingMain').replaceChildren(el('div','error-state','The process guide could not load. Please refresh or return to the record player.'));console.error(error);});
})();
