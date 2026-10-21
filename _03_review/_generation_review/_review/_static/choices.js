// Shared, catalogue-backed controls. Empty filter selection means unrestricted.
export function toggleChoices(root,{label,any='Any',onChange=()=>{},max=Infinity,count='total'}={}){
  let rows=[],selected=new Set();root.classList.add('choice-panel');
  const heading=document.createElement('div');heading.className='choice-heading';
  const name=document.createElement('strong');name.textContent=label;
  const reset=document.createElement('button');reset.type='button';reset.className='quiet-button';reset.textContent=any;
  const summary=document.createElement('span');summary.className='subtle small';summary.setAttribute('role','status');
  const list=document.createElement('div');list.className='choice-list';list.setAttribute('role','group');list.setAttribute('aria-label',label);
  heading.append(name,summary,reset);root.append(heading,list);
  function paint(){
    reset.setAttribute('aria-pressed',String(!selected.size));summary.textContent=selected.size?selected.size+' selected':'';
    for(const button of list.querySelectorAll('button')){button.setAttribute('aria-pressed',String(selected.has(button.dataset.value)));button.disabled=!selected.has(button.dataset.value)&&selected.size>=max;}
  }
  function render(){
    list.replaceChildren();const known=new Set(rows.map(r=>r.value));
    for(const row of [...rows,...[...selected].filter(v=>!known.has(v)).map(value=>({value,label:value+' · unavailable'}))]){
      const b=document.createElement('button');b.type='button';b.className='choice-toggle';b.dataset.value=row.value;
      b.setAttribute('aria-label',label+': '+(row.label||row.value));b.textContent=row.label||row.value;
      if(row[count]!==undefined){const n=document.createElement('span');n.className='choice-count';n.textContent=row[count];b.append(n);}
      b.onclick=()=>{selected.has(row.value)?selected.delete(row.value):selected.add(row.value);paint();onChange();};list.append(b);
    }
    if(!list.children.length){const empty=document.createElement('span');empty.className='subtle small';empty.textContent='No choices in this catalogue';list.append(empty);}
    paint();
  }
  reset.onclick=()=>{selected.clear();render();onChange();};
  return {values:()=>[...selected],load(values,chosen){rows=values.map(v=>typeof v==='string'?{value:v}:v);if(chosen!==undefined)selected=new Set(chosen);render();},
    set(values){selected=new Set(values);render();},legacy(text){return rows.filter(r=>r.value.toLowerCase().includes(text.toLowerCase())).map(r=>r.value);}};
}

export function selectChoices(select,values,{empty='Any',value=select.value,keep=true}={}){
  const rows=values.map(v=>typeof v==='string'?{value:v,label:v}:v),seen=new Set();
  select.replaceChildren(new Option(empty,''));
  for(const row of rows)if(row.value&&!seen.has(row.value)){seen.add(row.value);select.append(new Option(row.label||row.value,row.value));}
  if(value&&!seen.has(value)&&keep)select.append(new Option(value+' · saved value',value));
  select.value=seen.has(value)||keep?value:'';
}

// ISO 639-1 vocabulary; names come from the browser's language data.
export function languageChoices(known=[]){
  const codes='aa ab ae af ak am an ar as av ay az ba be bg bh bi bm bn bo br bs ca ce ch co cr cs cu cv cy da de dv dz ee el en eo es et eu fa ff fi fj fo fr fy ga gd gl gn gu gv ha he hi ho hr ht hu hy hz ia id ie ig ii ik io is it iu ja jv ka kg ki kj kk kl km kn ko kr ks ku kv kw ky la lb lg li ln lo lt lu lv mg mh mi mk ml mn mr ms mt my na nb nd ne ng nl nn no nr nv ny oc oj om or os pa pi pl ps pt qu rm rn ro ru rw sa sc sd se sg si sk sl sm sn so sq sr ss st su sv sw ta te tg th ti tk tl tn to tr ts tt tw ty ug uk ur uz ve vi vo wa wo xh yi yo za zh zu'.split(' ');
  const names=new Intl.DisplayNames(['en'],{type:'language'});
  return [...new Set([...codes,...known].filter(Boolean))].map(value=>{let label;try{label=names.of(value);}catch{label=value;}return {value,label:`${label} (${value})`};}).sort((a,b)=>a.label.localeCompare(b.label));
}
