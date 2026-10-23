// Both polarities are always direct choices. This component never submits a review.
export class ReviewTags {
  constructor(root, clear) {
    this.root=root;this.clear=clear;this.definitions=[];this.selected=new Map();this.disabled=true;
    clear.addEventListener('click',()=>{if(this.disabled)return;this.selected.clear();this.render();});
  }
  configure(definitions){this.definitions=definitions;this.render();}
  load(tags=[]){this.selected=new Map(tags.map(tag=>[tag.tag_id,tag.polarity]));this.render();}
  value(){return [...this.selected].map(([tag_id,polarity])=>({tag_id,polarity})).sort((a,b)=>a.tag_id.localeCompare(b.tag_id));}
  setDisabled(disabled){this.disabled=disabled;this.paint();}
  render(){
    this.root.replaceChildren();const groups=new Map();
    for(const tag of this.definitions){
      if(!tag.active&&!this.selected.has(tag.id))continue;
      let group=groups.get(tag.category);
      if(!group){group=document.createElement('section');group.className='tag-category';group.setAttribute('role','group');group.setAttribute('aria-label',tag.category);const title=document.createElement('h3');title.textContent=tag.category;group.append(title);groups.set(tag.category,group);this.root.append(group);}
      const row=document.createElement('div');row.className='review-tag';row.dataset.tagId=tag.id;row.title=tag.description+(tag.active?'':' (inactive; saved on this review)');
      const label=document.createElement('span');label.className='review-tag-label';label.textContent=tag.label;
      for(const [polarity,symbol] of [['negative','−'],['positive','+']]){
        const button=document.createElement('button');button.type='button';button.textContent=symbol;button.dataset.polarity=polarity;
        button.setAttribute('aria-label',`${tag.category} · ${tag.label}: ${polarity}`);button.title=`${polarity==='positive'?'Positive':'Negative'}: ${tag.description}`;
        button.addEventListener('click',()=>{if(this.disabled||(!tag.active&&this.selected.get(tag.id)!==polarity))return;if(this.selected.get(tag.id)===polarity)this.selected.delete(tag.id);else this.selected.set(tag.id,polarity);this.render();const replacement=[...this.root.querySelectorAll('.review-tag')].find(row=>row.dataset.tagId===tag.id)?.querySelector(`[data-polarity="${polarity}"]`);replacement?.focus({preventScroll:true});});
        if(polarity==='negative')row.append(button,label);else row.append(button);
      }
      group.append(row);
    }
    this.paint();
  }
  paint(){
    this.clear.disabled=this.disabled||!this.selected.size;
    const active=new Map(this.definitions.map(tag=>[tag.id,tag.active]));
    for(const row of this.root.querySelectorAll('.review-tag'))for(const button of row.querySelectorAll('button')){
      const selected=this.selected.get(row.dataset.tagId)===button.dataset.polarity;
      button.setAttribute('aria-pressed',String(selected));button.disabled=this.disabled||(!active.get(row.dataset.tagId)&&!selected);
    }
  }
}

export function savedTags(parent, tags){
  const list=typeof tags==='string'?JSON.parse(tags):tags||[];
  if(!list.length)return;
  const wrap=document.createElement('div');wrap.className='saved-tags';wrap.setAttribute('aria-label','Saved observations');
  for(const tag of list){const chip=document.createElement('span');chip.className=`saved-tag ${tag.polarity}`;chip.textContent=`${tag.polarity==='positive'?'+':'−'} ${tag.category} · ${tag.label}${tag.active?'':' (inactive)'}`;wrap.append(chip);}
  parent.append(wrap);
}
