const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp = seconds => `${Math.floor(seconds/60)}:${(seconds%60).toFixed(1).padStart(4,'0')}`;
export function initMasteringStudio({root, api, toast, getTime, seek, play, pause, isPlaying, previewStart, onSelection, onChange}) {
 const host=document.createElement('div');host.className='mastering-studio';
 host.innerHTML=`
  <div class="studio-transport"><button id="mixPlay">Play / pause</button><label class="prep-checkbox"><input id="mixLoop" type="checkbox">Loop selection</label><output id="mixTime">0:00</output><label>Zoom<select id="mixZoom"><option value="1">Fit track</option><option value="2">2×</option><option value="4">4×</option><option value="8">8×</option><option value="16">16×</option></select></label><button id="mixUndo">Undo mix</button><button id="mixRedo">Redo mix</button></div>
  <p id="mixWaveStatus" class="subtle small" role="status">Select a recording to load its waveform.</p>
  <canvas id="mixWave" height="220" tabindex="0" role="application" aria-label="Mastering waveform. Drag to select a section; click to seek. Arrow keys seek."></canvas>
  <input id="mixScroll" type="range" min="0" max="0" value="0" step=".05" aria-label="Waveform view start">
  <div class="studio-selection"><label>Selection start / s<input id="mixStart" type="number" min="0" step=".01" value="0"></label><label>End / s<input id="mixEnd" type="number" min="0" step=".01" value="0"></label><button id="mixIn">In at playhead</button><button id="mixOut">Out at playhead</button><button id="mixListen">Listen to selection</button><button id="mixPreviewSelection">Preview this section</button></div>
  <p class="subtle small">Drag a section, add an effect, then choose Selection on that effect. Draw strength changes in Automation. Audio and lyric timing stay fixed.</p>`;
 root.querySelector('.prep-audio-pair').before(host);
 const q=id=>host.querySelector('#'+id),canvas=q('mixWave');
 let waveform=null,key='',ticket=0,drag=null,frame=null,lastPaint=0,undoAction=()=>{},redoAction=()=>{};
 const selection=()=>({start:Number(q('mixStart').value),end:Number(q('mixEnd').value)});
 const duration=()=>waveform?.duration||0;
 const width=()=>duration()/Number(q('mixZoom').value)||1;
 const start=()=>Number(q('mixScroll').value);
 const clamp=value=>Math.max(0,Math.min(duration(),value));
 const selectedRange=()=>{const range=selection();if(range.end<=range.start)throw Error('Select a section on the waveform first');const fade=Math.min(.1,(range.end-range.start)/4);return {...range,fade_in:fade,fade_out:fade};};
 function selected(a,b) {q('mixStart').value=clamp(Math.min(a,b)).toFixed(2);q('mixEnd').value=clamp(Math.max(a,b)).toFixed(2);draw();}
 function draw() {
  if(!waveform||!canvas.clientWidth)return;
  const ratio=Math.min(2,window.devicePixelRatio||1),w=canvas.clientWidth,h=220;
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==h*ratio){canvas.width=Math.round(w*ratio);canvas.height=h*ratio;}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#0a1118';ctx.fillRect(0,0,w,h);
  const from=start(),span=width(),channels=Math.min(2,waveform.channels);
  ctx.strokeStyle='#293a48';ctx.fillStyle='#9cafbf';ctx.font='11px ui-monospace,monospace';
  for(let i=0;i<=6;i++){const x=i*w/6;ctx.beginPath();ctx.moveTo(x,22);ctx.lineTo(x,h);ctx.stroke();ctx.fillText(stamp(from+i*span/6),Math.min(w-45,x+3),15);}
  const peaks=waveform.peaks;
  for(let ch=0;ch<channels;ch++){
   const center=28+(h-30)/channels*(ch+.5),scale=(h-40)/channels/2;
   ctx.strokeStyle=ch?'#64c8a0':'#e7b55e';ctx.beginPath();
   for(let x=0;x<w;x++){const a=Math.floor((from+x/w*span)/duration()*peaks.length),b=Math.min(peaks.length,Math.max(a+1,Math.ceil((from+(x+1)/w*span)/duration()*peaks.length)));let low=0,high=0;for(let i=a;i<b;i++){low=Math.min(low,peaks[i]?.[ch]?.[0]||0);high=Math.max(high,peaks[i]?.[ch]?.[1]||0);}ctx.moveTo(x,center-low*scale);ctx.lineTo(x,center-high*scale);}
   ctx.stroke();
  }
  const range=selection(),left=(range.start-from)/span*w,right=(range.end-from)/span*w;
  ctx.fillStyle='#7cabda28';ctx.fillRect(left,23,right-left,h-23);ctx.strokeStyle='#8abbe9';ctx.strokeRect(left,23,right-left,h-23);
  const head=(getTime()-from)/span*w;ctx.strokeStyle='#fff';ctx.beginPath();ctx.moveTo(head,23);ctx.lineTo(head,h);ctx.stroke();
 }
 function loop() {
  if(!q('mixLoop').checked||!isPlaying())return;
  const range=selection();if(range.end<=range.start||getTime()<range.end-.015)return;
  const processed=root.querySelector('#prepProcessed');
  if(!processed.paused&&range.start>=previewStart()&&range.end<=previewStart()+processed.duration)processed.currentTime=range.start-previewStart();
  else seek(range.start);
 }
 function tick(time) {
  if(root.hidden){frame=null;return;}
  loop();if(time-lastPaint>45){lastPaint=time;q('mixTime').value=stamp(getTime());q('mixPlay').textContent=isPlaying()?'Pause':'Play';draw();}
  frame=requestAnimationFrame(tick);
 }
 function view(){q('mixScroll').max=Math.max(0,duration()-width());q('mixScroll').value=Math.min(start(),Number(q('mixScroll').max));draw();}
 const pointerTime=event=>clamp(start()+(event.clientX-canvas.getBoundingClientRect().left)/canvas.clientWidth*width());
 canvas.onpointerdown=event=>{if(event.button!==0||!waveform)return;canvas.focus();drag={x:event.clientX,time:pointerTime(event)};canvas.setPointerCapture(event.pointerId);};
 canvas.onpointermove=event=>{if(drag&&Math.abs(event.clientX-drag.x)>4)selected(drag.time,pointerTime(event));};
 canvas.onpointerup=event=>{if(!drag)return;if(Math.abs(event.clientX-drag.x)<=4)seek(pointerTime(event));else selected(drag.time,pointerTime(event));drag=null;canvas.releasePointerCapture(event.pointerId);};
 canvas.onpointercancel=()=>{drag=null;};
 canvas.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();seek(clamp(getTime()+(event.key==='ArrowLeft'?-1:1)*(event.shiftKey?1:.1)));}if(event.code==='Space'){event.preventDefault();q('mixPlay').click();}};
 q('mixZoom').onchange=()=>{q('mixScroll').max=Math.max(0,duration()-width());q('mixScroll').value=Math.max(0,Math.min(duration()-width(),getTime()-width()/2));view();};q('mixScroll').oninput=draw;
 for(const id of ['mixStart','mixEnd'])q(id).onchange=()=>selected(Number(q('mixStart').value),Number(q('mixEnd').value));
 q('mixIn').onclick=()=>selected(getTime(),selection().end);q('mixOut').onclick=()=>selected(selection().start,getTime());
 q('mixPlay').onclick=async()=>{try{if(isPlaying())pause();else{const range=selection();if(q('mixLoop').checked&&(getTime()<range.start||getTime()>=range.end))seek(range.start);await play();}}catch(error){toast(error.message);}};
 for(const audio of [root.querySelector('#prepRaw'),root.querySelector('#prepProcessed')])audio.addEventListener('ended',()=>{
  const range=selection();if(!q('mixLoop').checked||range.end<=range.start)return;
  const offset=audio.id==='prepProcessed'?previewStart():0;
  if(range.start>=offset&&range.end<=offset+audio.duration){audio.currentTime=range.start-offset;audio.play().catch(error=>toast(error.message));}
 });
 q('mixListen').onclick=async()=>{try{const range=selectedRange();seek(range.start);q('mixLoop').checked=true;await play();}catch(error){toast(error.message);}};
 q('mixPreviewSelection').onclick=()=>{try{onSelection(selectedRange());}catch(error){toast(error.message);}};
 q('mixUndo').onclick=()=>undoAction();q('mixRedo').onclick=()=>redoAction();
 new ResizeObserver(draw).observe(canvas);
 function decorate(row,effect,preset) {
  const extra=document.createElement('div');extra.className='studio-effect-options';
  const range=effect.range;
  extra.innerHTML=`<div class="studio-effect-scope"><span>${range?stamp(range.start)+' – '+stamp(range.end):'Whole track'}</span><button type="button" data-scope="selection">Selection</button><button type="button" data-scope="all">Whole track</button></div>
   <div class="studio-parameters">${(preset.parameters||[]).map(p=>`<label>${esc(p.label)}<input data-parameter="${esc(p.id)}" type="number" min="${p.min}" max="${p.max}" step="${p.step}" value="${effect.parameters?.[p.id]??p.default}"></label>`).join('')}${range?`<label>Fade in / s<input data-fade="fade_in" type="number" min="0" step=".05" value="${range.fade_in||0}"></label><label>Fade out / s<input data-fade="fade_out" type="number" min="0" step=".05" value="${range.fade_out||0}"></label>`:''}</div>
   <details class="studio-automation"><summary>Automation · ${effect.automation?.length||0} points</summary><p class="subtle small">Click to add a point. Drag it to change the strength over time.</p><canvas height="100" tabindex="0" aria-label="${esc(preset.name)} strength automation"></canvas><div data-points></div><div class="studio-effect-scope"><button type="button" data-curve="rise">Fade effect in</button><button type="button" data-curve="fall">Fade effect out</button><button type="button" data-curve="clear">Clear points</button></div></details>`;
  row.append(extra);
  const bounds=()=>({start:effect.range?.start||0,end:effect.range?.end||duration()});
  function curvePaint(){
   const c=extra.querySelector('canvas'),w=c.clientWidth||500;c.width=w*2;c.height=200;const ctx=c.getContext('2d');ctx.scale(2,2);ctx.fillStyle='#0b141d';ctx.fillRect(0,0,w,100);ctx.strokeStyle='#304557';for(let i=0;i<5;i++){ctx.beginPath();ctx.moveTo(0,i*25);ctx.lineTo(w,i*25);ctx.stroke();}
   const b=bounds(),points=effect.automation||[];ctx.strokeStyle='#f2bf68';ctx.fillStyle='#f2bf68';ctx.beginPath();
   if(!points.length){ctx.moveTo(0,0);ctx.lineTo(w,0);}
   points.forEach((p,i)=>{const x=(p.time-b.start)/(b.end-b.start)*w,y=100-p.value;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();
   for(const p of points){ctx.beginPath();ctx.arc((p.time-b.start)/(b.end-b.start)*w,100-p.value,4,0,Math.PI*2);ctx.fill();}
  }
  function points(){
   extra.querySelector('summary').textContent='Automation · '+(effect.automation?.length||0)+' points';
   extra.querySelector('[data-points]').innerHTML=(effect.automation||[]).map((p,i)=>`<div class="studio-point"><label>Time / s<input type="number" step=".01" min="0" value="${p.time}" data-point="${i}" data-key="time"></label><label>Strength / %<input type="number" min="0" max="100" value="${p.value}" data-point="${i}" data-key="value"></label><button type="button" data-delete="${i}" aria-label="Remove automation point">×</button></div>`).join('');
   curvePaint();
  }
  extra.oninput=event=>{
   event.stopPropagation();const input=event.target;
   if(input.dataset.parameter){effect.parameters||={};effect.parameters[input.dataset.parameter]=Number(input.value);}
   else if(input.dataset.fade)effect.range[input.dataset.fade]=Number(input.value);
   else if(input.dataset.point!=null){effect.automation[Number(input.dataset.point)][input.dataset.key]=Number(input.value);curvePaint();return;}
   onChange();
  };
  extra.onchange=event=>{if(event.target.dataset.point!=null){effect.automation.sort((a,b)=>a.time-b.time);points();onChange();}};
  extra.onclick=event=>{
   const button=event.target.closest('button');if(!button)return;event.stopPropagation();
   try {
    if(button.dataset.scope){if(button.dataset.scope==='all')delete effect.range;else effect.range=selectedRange();onChange();return decorateAgain();}
    if(button.dataset.curve){
     const b=bounds();effect.automation=button.dataset.curve==='clear'?[]:[{time:b.start,value:button.dataset.curve==='rise'?0:100},{time:b.end,value:button.dataset.curve==='rise'?100:0}];
    }
    if(button.dataset.delete!=null)effect.automation.splice(Number(button.dataset.delete),1);
    points();onChange();
   }catch(error){toast(error.message);}
  };
  function decorateAgain(){extra.remove();decorate(row,effect,preset);}
  const curve=extra.querySelector('canvas');let moving=null,curveBefore=null;
  const valueAt=event=>{const rect=curve.getBoundingClientRect(),b=bounds();return {time:Math.round((b.start+Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*(b.end-b.start))*100)/100,value:Math.round(Math.max(0,Math.min(100,100-(event.clientY-rect.top)/rect.height*100)))};};
  curve.onpointerdown=event=>{
   if(event.button!==0||!duration())return;event.preventDefault();effect.automation||=[];
   curveBefore=structuredClone(effect.automation);
   const p=valueAt(event),b=bounds();moving=effect.automation.find(point=>Math.abs(point.time-p.time)<(b.end-b.start)*.025&&Math.abs(point.value-p.value)<12);
   if(!moving){if(effect.automation.length>=32)return;moving=p;effect.automation.push(moving);}
   effect.automation.sort((a,b)=>a.time-b.time);curve.setPointerCapture(event.pointerId);points();
  };
  curve.onpointermove=event=>{if(!moving)return;Object.assign(moving,valueAt(event));effect.automation.sort((a,b)=>a.time-b.time);curvePaint();};
  curve.onpointerup=event=>{if(!moving)return;const point=moving;moving=null;effect.automation=effect.automation.filter(p=>p===point||Math.abs(p.time-point.time)>.005).sort((a,b)=>a.time-b.time);curve.releasePointerCapture(event.pointerId);points();onChange();};
  const cancelCurve=()=>{if(!moving)return;moving=null;effect.automation=curveBefore;points();};
  curve.onpointercancel=cancelCurve;curve.onlostpointercapture=cancelCurve;
  extra.querySelector('details').ontoggle=curvePaint;
  points();
 }
 return {
  range:selectedRange, decorate,
  history(undo,redo,back,forward){q('mixUndo').disabled=!undo;q('mixRedo').disabled=!redo;undoAction=back;redoAction=forward;},
  async update(gid,artifact='') {
   const next=gid+':'+artifact;if(next===key){if(!frame&&!root.hidden)frame=requestAnimationFrame(tick);return;}
   key=next;const current=++ticket;waveform=null;q('mixWaveStatus').textContent='Loading waveform…';
   try{
    const data=await api('/preparation/projects/'+encodeURIComponent(gid)+'/waveform?'+new URLSearchParams(artifact?{artifact}:{}));
    if(current!==ticket)return;waveform=data;selected(0,Math.min(30,data.duration));view();
    q('mixWaveStatus').textContent=data.channels+' channel'+(data.channels===1?'':'s')+' · '+data.sample_rate+' Hz · peak '+(data.sample_peak_dbfs==null?'−∞':data.sample_peak_dbfs.toFixed(1))+' dBFS';
    if(!frame&&!root.hidden)frame=requestAnimationFrame(tick);
   }catch(error){if(current===ticket){key='';q('mixWaveStatus').textContent=error.message;}}
  },
  close(){if(frame)cancelAnimationFrame(frame);frame=null;pause();}
 };
}
