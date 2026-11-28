// Source-word operations are shared by dragging, keyboard nudges and the dock.
const bound=(x,a,b)=>Math.max(a,Math.min(b,x));
export function sourceTime(project,time){
 let at=0;
 for(const section of project.segments){
  const length=section.kind==='gap'?section.duration:section.end-section.start;
  if(time>=at&&time<=at+length&&section.kind==='audio'&&section.mode==='normal')return section.start+time-at;
  at+=length;
 }
 return null;
}
export function selectedWords(project,ids){return project.words.filter(w=>ids.has(w.id)&&!w.omitted&&w.start!=null&&w.end!=null);}
export function checked(word){word.confirmed=true;word.estimated=false;word.edited=true;}
export function nudgeWords(project,ids,delta,total){
 const items=selectedWords(project,ids);if(!items.length)return;
 delta=bound(delta,-Math.min(...items.map(w=>w.start)),total-Math.max(...items.map(w=>w.end)));
 for(const word of items){word.start+=delta;word.end+=delta;checked(word);}
 project.approved=false;
}
export function resizeWords(project,ids,length,total,activeId=null,newStart=null){
 const items=selectedWords(project,ids);if(!items.length)return;
 if(!Number.isFinite(length)||length<=0)throw Error('Enter a positive word length.');
 const maximum=Math.min(...items.map(w=>total-(w.id===activeId&&newStart!=null?newStart:w.start)));
 if(maximum<=0)throw Error('These words must start before the end of the audio.');
 length=bound(length,Math.min(.02,maximum),maximum);
 for(const word of items){if(word.id===activeId&&newStart!=null)word.start=newStart;word.end=word.start+length;checked(word);}
 project.approved=false;
}
export function packWords(project,ids,total){
 const items=selectedWords(project,ids).sort((a,b)=>a.start-b.start),lengths=items.map(w=>w.end-w.start);
 if(!items.length)return;
 let at=items[0].start;
 if(at+lengths.reduce((a,b)=>a+b,0)>total+.00001)throw Error('These words will not fit before the end of the audio. Shorten them first.');
 items.forEach((word,i)=>{word.start=at;word.end=at+lengths[i];at=word.end;checked(word);});project.approved=false;
}
export function pauseWord(items,time){
 const current=items.findIndex(w=>time>=w.start&&time<w.end);
 if(current>=0)return items[current>0&&time-items[current].start<.2*(items[current].end-items[current].start)?current-1:current];
 return items.filter(w=>w.start<=time).at(-1)||items[0]||null;
}
export function wordParts(text,split,weights=null){
 const labels=split.split('|').map(s=>s.trim());
 if(labels.some(s=>!s)||labels.join('')!==text)throw Error('Separate parts with |, keeping the exact spelling of the word.');
 if(labels.length>100)throw Error('Use up to 100 highlight parts.');
 const values=weights||labels.map(s=>Array.from(s).length);
 if(values.length!==labels.length||values.some(v=>!Number.isFinite(v)||v<=0))throw Error('Each highlight part needs a positive length.');
 const total=values.reduce((a,b)=>a+b,0);let at=0;
 return labels.map((label,i)=>{const start=at;at+=values[i]/total;return {text:label,start,end:i===labels.length-1?1:at};});
}
export function insertWords(project,text,start,end,reference=null,language='en',replaceId=null){
 let current=reference?.language||language.split(/[,\s]+/)[0]||'en';const tokens=[];
 for(const piece of text.split(/(\{[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,8})?\})/g)){
  if(/^\{[a-zA-Z]{2,3}(?:-[a-zA-Z]{2,8})?\}$/.test(piece)){current=piece.slice(1,-1).toLowerCase();continue;}
  for(const value of piece.trim().split(/\s+/).filter(Boolean))tokens.push({text:value,language:current});
 }
 if(!tokens.length||tokens.some(w=>w.text.length>200))throw Error('Enter short lyric words.');
 if(tokens.length>200||end-start<.02)throw Error('Choose a longer range for these lyrics.');
 const slot=(end-start)/tokens.length,line=reference?.line??('manual_'+crypto.randomUUID().slice(0,8));
 const made=tokens.map(({text,language},i)=>({id:'manual_'+crypto.randomUUID().replaceAll('-',''),text,source_text:text,
  manual:true,line,section:reference?.section||'',
  language,start:start+i*slot,end:start+(i+1)*slot,
  confirmed:true,estimated:false,edited:true,omitted:false,confidence:null}));
 if(replaceId){
  const index=project.words.findIndex(w=>w.id===replaceId),original=project.words[index];
  if(index<0)throw Error('This lyric word is no longer present.');
  made[0]={...original,...made[0],id:original.id,source_text:original.source_text,manual:original.manual||false,parts:null};
  project.words.splice(index,1,...made);
 }else{
  let index=project.words.findIndex(w=>w.start!=null&&w.start>=start);
  if(index<0)index=project.words.length;project.words.splice(index,0,...made);
 }
 project.language=[...new Set([...(project.language||'en').split(',').map(s=>s.trim()),...made.map(w=>w.language)])].join(', ');
 project.approved=false;return made;
}
export function followStart(time,start,span,total){
 if(span>=total-.01)return 0;
 if(time<start+span*.08||time>start+span*.88)return bound(time-span*.18,0,Math.max(0,total-span));
 return start;
}
