const panels=[
 ['sounds','Sounds'],['effects','Effects'],['lyrics','Lyrics'],['release','Artwork & release'],
 ['queue','Processing'],['versions','Versions'],['details','Track details'],
];

// Keep one editor, transport and playhead. Existing tools are docked below it,
// retaining their state and handlers instead of building competing editors.
export function initWorkbench(root,timeline){
 const find=selector=>root.querySelector(selector),editor=find('.song-editor');
 const dock=document.createElement('div');dock.className='workbench-dock';
 const tabs=document.createElement('div');tabs.className='workbench-tabs';tabs.role='tablist';tabs.setAttribute('aria-label','Timeline tools');
 const body=document.createElement('div');body.className='workbench-body';
 const content=document.createElement('div');content.className='workbench-content';
 const inspector=find('.song-inspector');inspector.classList.add('workbench-inspector');
 body.append(content,inspector);dock.append(tabs,body);
 const footer=find('.song-render');editor.insertBefore(dock,footer);
 const panelNodes=new Map();
 const flatten=node=>{
  if(node.tagName!=='DETAILS')return node;
  const replacement=document.createElement('section');
  replacement.id=node.id;replacement.className=node.className;
  for(const status of node.querySelectorAll(':scope > summary [id]'))replacement.append(status);
  for(const child of [...node.childNodes])if(child.nodeName!=='SUMMARY')replacement.append(child);
  node.replaceWith(replacement);return replacement;
 };
 const sources={
  sounds:find('.song-library'),effects:find('[aria-labelledby="prepMasteringHeading"]'),
  lyrics:flatten(find('.song-lyrics')),release:find('.prep-artwork'),
  queue:flatten(find('#prepQueue')),versions:flatten(find('.song-versions')),
  details:flatten(find('#prepYamlDetails')),
 };
 const releaseOptions=find('.prep-complete'),finish=find('#prepComplete');
 const releaseFields=document.createElement('div');releaseFields.className='workbench-release-options';
 releaseOptions.querySelector('h2').remove();
 for(const node of [...releaseOptions.children])if(node!==finish)releaseFields.append(node);
 sources.release.append(releaseFields);
 releaseOptions.remove();
 const history=flatten(find('#prepHistoryHeading'));sources.queue.append(history);
 for(const [name,label] of panels){
  const tab=document.createElement('button');tab.type='button';tab.id='workbenchTab-'+name;tab.role='tab';
  tab.textContent=label;tab.dataset.pane=name;tab.setAttribute('aria-controls','workbenchPane-'+name);
  const panel=document.createElement('section');panel.id='workbenchPane-'+name;panel.role='tabpanel';
  panel.setAttribute('aria-labelledby',tab.id);panel.append(sources[name]);
  tabs.append(tab);content.append(panel);panelNodes.set(name,panel);
 }
 find('#prepMasteringHeading').textContent='Effects on this timeline';
 const queueHeading=document.createElement('h3');queueHeading.textContent='Processing & workers';
 sources.queue.prepend(queueHeading);
 const workers=document.createElement('section');workers.className='workbench-workers';
 workers.innerHTML='<div class="workbench-worker-heading"><strong>Preparation PCs</strong><button id="prepPairWorker" type="button">Pair another PC</button><button id="prepUnpairWorker" type="button" hidden>Disconnect workers</button></div><p id="prepWorkerStatus" role="status">A paired PC can align songs while this PC generates.</p><div id="prepWorkers"></div><div id="prepPairGuide" hidden><p>On the other PC, pull main and install processing once. For the GTX 1060 use:</p><code>py -3.12 _97_app/run_gbr.py setup-processing --cuda cu126</code><label>Main GBR LAN address<input id="prepWorkerServer" placeholder="http://192.168.1.10:8765"></label><p>Then run this command on that PC. Paste the pairing key at its hidden prompt.</p><pre id="prepWorkerCommand"></pre><label>Pairing key<input id="prepWorkerKey" type="password" readonly autocomplete="off"></label><button id="prepCopyWorkerKey" type="button">Copy key</button><span id="prepWorkerCopyStatus" role="status"></span><p>Keep the worker running when that PC is available. It pauses pickup while its GPU or ComfyUI is busy. Existing workers need a replacement key if you create a new one.</p></div>';
 sources.queue.insertBefore(workers,find('#prepResumeQueue'));
 footer.querySelector('div').remove();footer.prepend(finish);
 find('.song-bottom').remove();find('.prep-studio').remove();
 find('#prepVisuals').parentElement.hidden=true;
 const heading=find('.song-heading');heading.querySelector('.song-eyebrow').remove();
 heading.querySelector('h2').classList.add('workbench-sr');
 const art=document.createElement('button');art.type='button';art.className='workbench-art';
 art.setAttribute('aria-label','Artwork and release settings');art.innerHTML='<img alt="" hidden><span>GBR</span>';
 heading.prepend(art);
 const projectBar=find('.prep-project-bar');projectBar.classList.add('workbench-recording');
 heading.insertBefore(projectBar,heading.querySelector('.song-save'));
 const pageHeading=find('.prep-page-heading');pageHeading.querySelector('h1').classList.add('workbench-sr');
 heading.append(pageHeading.querySelector('.prep-header-actions'));
 root.insertBefore(heading,find('#prepNotice'));
 // The page title remains for Library mode and screen readers.
 pageHeading.classList.add('workbench-sr');
 const alignment=find('#songAlignmentStatus');find('.song-view').append(alignment);
 find('#songFit').textContent='↔';find('#songFit').setAttribute('aria-label','Fit whole song');
 find('#songLyricState').classList.add('workbench-sr');
 const gestures=document.createElement('div');gestures.className='workbench-gestures';gestures.role='group';gestures.setAttribute('aria-label','Selected music effects');
 const gestureSelect=find('#songGesture');gestureSelect.closest('label').hidden=true;
 for(const [mode,label] of [['scratch','Scratch'],['stop','Record stop'],['start','Record start'],['stutter','Stutter'],['reverse','Reverse']]){
  const button=document.createElement('button');button.type='button';button.className='secondary';
  button.textContent=label;button.setAttribute('aria-label','Apply '+label.toLowerCase()+' to selected music');
  button.onclick=()=>{gestureSelect.value=mode;gestureSelect.dispatchEvent(new Event('change',{bubbles:true}));};
  gestures.append(button);
 }
 find('.song-tools').insertBefore(gestures,find('#songAddLight'));
 const queueButton=document.createElement('button');queueButton.type='button';queueButton.className='workbench-queue';
 queueButton.textContent='Processing';queueButton.setAttribute('aria-controls','workbenchPane-queue');
 find('.song-transport').append(queueButton);queueButton.append(find('#prepQueueStatus'));
 const updateArt=()=>{
  const cover=find('#prepCoverImage'),thumb=art.querySelector('img'),url=cover.getAttribute('src');
  thumb.hidden=!url;art.querySelector('span').hidden=Boolean(url);
  if(url&&thumb.getAttribute('src')!==url)thumb.src=url;
 };
 new MutationObserver(updateArt).observe(find('#prepCoverImage'),{attributes:true,attributeFilter:['src']});updateArt();
 const setPane=(name,focus=false)=>{
  if(!panelNodes.has(name))name='sounds';
  root.dataset.prepPane=name;
  for(const tab of tabs.children){
   const active=tab.dataset.pane===name;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;
   panelNodes.get(tab.dataset.pane).hidden=!active;if(active&&focus)tab.focus();
  }
  try{localStorage.setItem('gbr-preparation-pane',name);}catch{}
 };
 tabs.onclick=event=>{const tab=event.target.closest('[role=tab]');if(tab)setPane(tab.dataset.pane);};
 tabs.onkeydown=event=>{
  const index=panels.findIndex(([name])=>name===root.dataset.prepPane);
  const next={ArrowRight:(index+1)%panels.length,ArrowLeft:(index+panels.length-1)%panels.length,Home:0,End:panels.length-1}[event.key];
  if(next!=null){event.preventDefault();setPane(panels[next][0],true);}
 };
 art.onclick=()=>setPane('release');queueButton.onclick=()=>setPane('queue');
 root.addEventListener('click',event=>{if(event.target.closest('[data-object="revealEffect"]'))setPane('effects');});
 let initial='sounds';try{initial=localStorage.getItem('gbr-preparation-pane')||initial;}catch{}
 setPane(initial);root.classList.add('timeline-workbench');
 return {setPane};
}
