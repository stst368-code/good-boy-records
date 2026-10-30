import {app} from '../../scripts/app.js';
const frames=new Set();
function frame(container){const el=document.createElement('iframe');el.src='/gbr/workspace';el.title='GBR workspace';el.style.cssText='width:100%;height:100%;min-height:540px;border:0;background:#12191f';container.replaceChildren(el);frames.add(el);return el;}
function open(payload){let dialog=document.getElementById('gbr-workspace-dialog');if(!dialog){dialog=document.createElement('dialog');dialog.id='gbr-workspace-dialog';dialog.style.cssText='width:96vw;height:92vh;max-width:1900px;padding:0;background:#12191f;color:#ead9b8;border:1px solid #75603a';const close=document.createElement('button');close.textContent='Back to graph';close.onclick=()=>{dialog.querySelector('iframe')?.contentWindow.postMessage({type:'gbr-pause'},location.origin);dialog.close();};const body=document.createElement('div');body.style.height='calc(100% - 40px)';dialog.append(close,body);document.body.append(dialog);frame(body);}dialog.showModal();const el=dialog.querySelector('iframe');const send=()=>el.contentWindow.postMessage({type:'gbr-plan',plan:payload},location.origin);if(el.dataset.ready)send();else el.addEventListener('load',()=>{el.dataset.ready='1';send();},{once:true});}
function set(node,name,value){const widget=node.widgets?.find(w=>w.name===name);if(widget){widget.value=value;widget.callback?.(value);}}
function graph(data){
 const song=LiteGraph.createNode('GBRSong'),caption=LiteGraph.createNode('GBRCaption'),plan=LiteGraph.createNode('GBRGenerationPlan');
 if(!song||!caption||!plan)throw Error('GBR nodes are not loaded; restart ComfyUI');
 const right=Math.max(0,...app.graph._nodes.map(n=>n.pos[0]+n.size[0]))+70;
 for(const [node,x,y] of [[song,right,70],[caption,right,480],[plan,right+600,70]]){app.graph.add(node);node.pos=[x,y];}
 for(const [key,value] of Object.entries(data.song))set(song,key,value);
 for(const [key,value] of Object.entries({...data.caption.fields,cluster:data.caption.cluster,style:data.caption.style}))set(caption,key,value);
 set(plan,'worker_id',data.worker_id||'auto');set(plan,'purpose',data.purpose||'experiment');
 for(const [key,value] of Object.entries(data.parameters||{}))set(plan,({sampler_steps:'steps',sampler_cfg:'diffusion_cfg'})[key]||key,value);
 song.connect(0,plan,0);caption.connect(0,plan,1);app.graph.setDirtyCanvas(true,true);
}
app.registerExtension({
 name:'GBR.Workspace',
 commands:[{id:'GBR.OpenWorkspace',label:'GBR workspace',function:()=>open()}],
 menuCommands:[{path:['GBR'],commands:['GBR.OpenWorkspace']}],
 bottomPanelTabs:[{id:'GBR.Workspace',title:'GBR',type:'custom',render:frame}],
 setup(){window.addEventListener('message',event=>{if(event.origin!==location.origin||![...frames].some(f=>f.contentWindow===event.source))return;if(event.data?.type==='gbr-create-graph'){try{graph(event.data.plan);event.source.postMessage({type:'gbr-graph-result',ok:true},location.origin);}catch(error){event.source.postMessage({type:'gbr-graph-result',error:error.message},location.origin);}}});},
 beforeRegisterNodeDef(nodeType,nodeData){if(!['GBRSong','GBRCaption','GBRGenerationPlan','GBRReviewTarget'].includes(nodeData.name))return;
  const created=nodeType.prototype.onNodeCreated;nodeType.prototype.onNodeCreated=function(){created?.apply(this,arguments);this.addWidget('button','Open GBR workspace',null,()=>open(this.properties?.gbrPlan));};
  if(nodeData.name==='GBRGenerationPlan'){const executed=nodeType.prototype.onExecuted;nodeType.prototype.onExecuted=function(message){executed?.apply(this,arguments);this.properties=this.properties||{};this.properties.gbrPlan=message.gbr_plan?.[0];};}
  if(nodeData.name==='GBRReviewTarget'){const executed=nodeType.prototype.onExecuted;nodeType.prototype.onExecuted=function(message){executed?.apply(this,arguments);if(message.gbr_recording?.[0])open({recording:message.gbr_recording[0]});};}
 }
});
