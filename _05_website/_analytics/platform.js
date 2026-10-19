'use strict';
// Public projections only. The static site never opens the app or its database.
const REVIEW_METRICS = [['average','Four-score average'],['audio_quality','Audio quality'],['composition','Composition'],['consistency','Consistency'],['realism','Realism']];
let researchSources={},researchSource='archive',researchMetric='average';
const isPlatform=()=>researchSource==='platform';
const rated=r=>Number.isFinite(score(r));
const observed=v=>v!==null&&v!==undefined&&v!==''&&Number.isFinite(Number(v));
const gib=v=>observed(v)?fix(Number(v)/2**30,2):'—';

function installResearch(archive,current){
 researchSources={archive};if(current)researchSources.platform=current;
 const controls=document.createElement('div');controls.className='research-source';
 controls.innerHTML=`<label>RESEARCH SOURCE<select id="researchSource">${current?'<option value="platform">GBR shared platform · four scores</option>':''}<option value="archive">Porto archive · single grade</option></select></label><label id="responseLabel">REVIEW RESPONSE<select id="reviewResponse">${REVIEW_METRICS.map(([id,label])=>`<option value="${id}">${label}</option>`).join('')}</select></label><p id="researchScope"></p>`;
 document.querySelector('.page-head').after(controls);
 const tagControls=document.createElement('div');tagControls.className='research-tag-controls';tagControls.id='tagControls';
 tagControls.innerHTML='<label>OBSERVATION<select id="reviewTag"><option value="">All tags / untagged</option></select></label><label>POLARITY<select id="tagPolarity"><option value="">Either polarity</option><option value="negative">Negative</option><option value="positive">Positive</option></select></label>';
 document.querySelector('.filter-grid').append(tagControls);
 const nav=document.createElement('button');nav.type='button';nav.dataset.view='tags';nav.id='tagsNav';
 nav.innerHTML='<span class="idx">05</span><span>Review observations<small>Positive / negative tags</small></span><span class="arrow">↗</span>';
 document.getElementById('nav').append(nav);
 nav.onclick=()=>{view='tags';page=0;render()};
 document.getElementById('researchSource').onchange=e=>chooseResearch(e.target.value);
 document.getElementById('reviewResponse').onchange=e=>{researchMetric=e.target.value;refreshResearch()};
 for(const id of ['reviewTag','tagPolarity'])document.getElementById(id).onchange=apply;
 chooseResearch(current?'platform':'archive');
}

function chooseResearch(source){
 researchSource=source;researchMetric='average';
 for(const id of ['search','song','cluster','captionSource','grade','reviewTag','tagPolarity'])document.getElementById(id).value='';
 document.getElementById('researchSource').value=source;document.getElementById('reviewResponse').value='average';
 document.getElementById('responseLabel').hidden=!isPlatform();
 document.getElementById('tagControls').hidden=!isPlatform();document.getElementById('tagsNav').hidden=!isPlatform();
 if(!isPlatform()&&view==='tags')view='archive';
 const tags=document.getElementById('reviewTag');tags.replaceChildren(new Option('All tags / untagged',''));
 for(const tag of researchSources[source].tag_definitions||[])tags.append(new Option(`${tag.category} / ${tag.label}${tag.active?'':' [inactive]'}`,tag.id));
 const grade=document.getElementById('grade');grade.replaceChildren(new Option('All scores / unreviewed',''));
 for(let i=5;i>=0;i--)grade.append(new Option(isPlatform()?(i===5?'5':`${i} to <${i+1}`):`${i} / 5`,String(i)));
 captionFilter='all';
 refreshResearch();
}

function refreshResearch(){
 const data=researchSources[researchSource];
 rows=data.generations.map(r=>({...r,...(r.review_metrics||{}),
  vram_gib:observed(r.usable_vram_bytes)?r.usable_vram_bytes/2**30:null,
  peak_vram_gib:observed(r.peak_observed_device_bytes)?r.peak_observed_device_bytes/2**30:null,
  quality_score:isPlatform()?(researchMetric==='average'?r.quality_score:r.review_metrics[researchMetric]):r.quality_score}));
 fillOptions();
 const date=new Date(data.generated_at).toLocaleDateString('en-GB');
 document.getElementById('dataStamp').textContent=(isPlatform()?'PLATFORM / ':'ARCHIVE / ')+date+' · 0–5 RATINGS';
 document.getElementById('sideCount').textContent=rows.length+' RECORDINGS';
 document.getElementById('researchScope').textContent=isPlatform()?
  'Latest revision per reviewer. Each PCM recording counts once; scores average current reviewers. Exported '+date+'.':
  'Historical single listening grade, 0–5. This archive is kept separate from the four-score review model.';
 // Select observed cohorts rather than retaining Porto's particular sampler defaults.
 const first=rows.find(r=>r.sampler&&r.scheduler&&r.text_encoder&&r.dit);
 if(first){simState.pair=first.sampler+' / '+first.scheduler;simState.text_encoder=first.text_encoder;simState.dit=first.dit}
 apply();
}

function researchMatches(r){
 if(!isPlatform())return true;
 const tag=document.getElementById('reviewTag').value,polarity=document.getElementById('tagPolarity').value;
 return (!tag&&!polarity)||(r.current_reviews||[]).some(review=>review.tags.some(t=>(!tag||t.tag_id===tag)&&(!polarity||t.polarity===polarity)));
}

function researchGrade(r,grade){
 if(!grade)return true;
 return isPlatform()?rated(r)&&score(r)>=Number(grade)&&score(r)<Number(grade)+1:String(score(r))===grade;
}

function reviewTags(){
 const definitions=researchSources.platform?.tag_definitions||[],result=[];
 for(const tag of definitions)for(const polarity of ['negative','positive']){
  const recordings=new Set(),reviews=[];
  for(const row of filtered)for(const review of row.current_reviews||[])if(review.tags.some(t=>t.tag_id===tag.id&&t.polarity===polarity)){
   recordings.add(row.id);reviews.push(review);
  }
  if(reviews.length)result.push({tag,polarity,n:recordings.size,reviews});
 }
 const current=filtered.flatMap(r=>r.current_reviews||[]),recorded=current.filter(r=>r.tags_recorded).length;
 const table=`<div class="plot"><table class="vault-table research-table"><thead><tr><th>OBSERVATION</th><th>POLARITY</th><th>RECORDINGS / REVIEWS</th>${REVIEW_METRICS.slice(1).map(([,label])=>`<th>${safe(label)}</th>`).join('')}</tr></thead><tbody>${result.map(({tag,polarity,n,reviews})=>`<tr><td>${safe(tag.category+' / '+tag.label)}${tag.active?'':' [inactive]'}</td><td class="tag-${polarity}">${polarity==='positive'?'+':'−'} ${polarity}</td><td>${n} / ${reviews.length}</td>${REVIEW_METRICS.slice(1).map(([key])=>`<td>${fix(average(reviews.map(r=>r.metrics[key])))}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
 return section('Review observations','Distinct recordings and current reviews are counted separately. Scores below describe the reviews carrying each observation.')+
 panel('Positive / negative vocabulary',`${current.length} CURRENT REVIEWS · ${recorded} WITH TAG CAPTURE`,result.length?table:'<div class="empty">No selected observations in this cohort.</div>',
 `${current.length-recorded} reviews predate tag capture; ${recorded} allow an explicit empty selection. An unselected tag expresses no judgement. Filter observations, then compare parameters, precision or one of the four responses.`,true);
}

function platformCompute(){
 const map=new Map();
 for(const row of filtered){
  const key=[row.gpu_name||'Unknown GPU',observed(row.usable_vram_bytes)?gib(row.usable_vram_bytes)+' GiB':'Unknown VRAM',row.execution_mode||'Unknown mode',row.text_encoder||'?',row.dit||'?',row.memory_profile||'Unknown memory profile',row.encoding_cache||'Unknown cache policy',row.cache_status||'Unknown cache result'].join(' / ');
  if(!map.has(key))map.set(key,[]);map.get(key).push(row);
 }
 const table=[...map].map(([key,items])=>{
  const ratedItems=items.filter(rated),times=items.map(r=>r.wall_seconds).filter(observed);
  // Complete run cost includes failed retries; missing/partial prices are never zero.
  const priced=items.filter(r=>r.attempts?.length&&r.attempts.every(a=>a.pricing_complete&&observed(a.estimated_cost_usd)&&a.gpu_name===r.gpu_name&&a.usable_vram_bytes===r.usable_vram_bytes&&a.execution_mode===r.execution_mode));
  const pricedReviews=priced.filter(rated),cost=pricedReviews.reduce((sum,r)=>sum+r.attempts.reduce((n,a)=>n+a.estimated_cost_usd,0),0),high=pricedReviews.filter(r=>score(r)>=4).length;
  const peak=items.map(r=>r.peak_observed_device_bytes).filter(observed),allocated=items.map(r=>r.peak_allocated_bytes).filter(observed);
  return `<tr><td>${safe(key)}</td><td>${items.length} / ${ratedItems.length}</td><td>${fix(average(ratedItems.map(score)))}</td><td>${fix(average(times),1)}<small> n=${times.length}</small></td><td>${peak.length?gib(Math.max(...peak)):'—'} / ${allocated.length?gib(Math.max(...allocated)):'—'}</td><td>${priced.length} / ${items.length}</td><td>${high?costMoney(cost/high):'—'}</td></tr>`;
 }).join('');
 return panel('Worker hardware and precision','DISTINCT RECORDINGS · MEASURED TELEMETRY',`<div class="plot"><table class="vault-table research-table"><thead><tr><th>GPU / CAPACITY / MODE / ENC / DiT / MEMORY / CACHE POLICY / RESULT</th><th>OUTPUTS / REVIEWED</th><th>MEAN RESPONSE</th><th>MEAN WALL SEC</th><th>PEAK DEVICE / ALLOCATOR GiB</th><th>COMPARABLY PRICED</th><th>EST. USD / SCORE ≥4</th></tr></thead><tbody>${table}</tbody></table></div>`,
 'Wall time and peaks describe the successful attempt. Device peaks are sampled and may include other processes; allocator peaks describe PyTorch. Prices include retries only when every attempt has a complete USD total and the same GPU/capacity/mode. Mixed hardware, partial prices and absent telemetry remain unknown for cost comparison. Score ≥4 is a measured threshold, independent of the publish flag.',true);
}

function platformSignal(){
 const metrics=[['sample_peak_dbfs','Sample peak / dBFS'],['true_peak_dbtp_4x','True peak / dBTP (4×)'],['clipped_samples','Clipped samples'],['rms_dbfs','RMS / dBFS'],['integrated_lufs','Integrated LUFS'],['loudness_range_lu','Loudness range / LU'],['spectral_centroid_hz','Spectral centroid / Hz'],['spectral_flatness','Spectral flatness'],['stereo_correlation','Stereo correlation'],['event_count','Flagged events']];
 const table=metrics.map(([key,label])=>{const values=filtered.map(r=>r.audio_analysis?.[key]).filter(observed);return `<tr><td>${label}</td><td>${values.length} / ${filtered.length}</td><td>${fix(average(values))}</td><td>${values.length?fix(Math.min(...values))+' – '+fix(Math.max(...values)):'—'}</td></tr>`}).join('');
 return section('Technical audio checks','The shared review analysis supplies measured loudness, peaks and spectral checks. Missing measurements remain absent.')+
 panel('Signal measurements','LATEST ANALYSIS PER PCM RECORDING',`<div class="plot"><table class="vault-table research-table"><thead><tr><th>MEASUREMENT</th><th>COVERAGE</th><th>MEAN</th><th>RANGE</th></tr></thead><tbody>${table}</tbody></table></div>`,
 'Technical flags help a human prioritise listening. These measurements do not establish musical quality or replace the four review scores.',true);
}

function researchDetail(row){
 if(!isPlatform())return '';
 const fields=[['SONG / VARIANT ID',[row.song_id,row.variant_id].map(x=>x??'—').join(' / ')],['GENERATION ID',row.generation_id],['GENERATED',row.generation_timestamp||row.generation_datetime],['GPU',row.gpu_name],['EXECUTION MODE',row.execution_mode],['VRAM / GiB',gib(row.usable_vram_bytes)],['PEAK DEVICE / GiB',gib(row.peak_observed_device_bytes)],...REVIEW_METRICS.slice(1).map(([key,label])=>[label.toUpperCase(),observed(row.review_metrics[key])?fix(row.review_metrics[key]):'—'])];
 const definitions=new Map(researchSources.platform.tag_definitions.map(t=>[t.id,t]));
 return `<div class="drawer-grid">${fields.map(([key,value])=>`<div class="drawer-field"><small>${safe(key)}</small><strong>${safe(value??'—')}</strong></div>`).join('')}</div><h3>Current review observations</h3>${row.current_reviews.map(r=>`<p>${safe(r.review_id.slice(0,8))} · revision ${r.revision} · ${r.tags.length?r.tags.map(t=>`<span class="tag-${t.polarity}">${t.polarity==='positive'?'+':'−'} ${safe(definitions.get(t.tag_id)?.label||t.tag_id)}</span>`).join(' · '):r.tags_recorded?'No tags selected':'Tag capture unavailable'}</p>`).join('')||'<p>Awaiting review.</p>'}`;
}
