'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const audio = $('storyAudio');
  const noiseButton = $('noisePlay');
  const trackButton = $('trackPlay');
  const shuffle = $('shuffleSong');
  const songTitle = $('songTitle');
  const songDescription = $('songDescription');
  const songArtwork = $('songArtwork');
  const status = $('playbackStatus');
  const progress = $('playbackProgress');
  let candidates = [];
  let current = null;
  let playing = null;
  let comparison = {a:null,b:null};
  let comparisonGroups = new Map();
  let evidenceMode = 'lyrics';
  let evidenceToken = 0;
  const captionCache = new Map();

  function waveform(holder, seed) {
    for (let i=0; i<68; i++) {
      const el = document.createElement('span');
      el.className = 'bar';
      const factor = seed === 'noise'
        ? .25 + .75 * Math.abs(Math.sin(i * 8.77) * Math.sin(i * 2.11 + 1))
        : .15 + .85 * Math.abs(Math.sin(i*.42 + 3)*Math.cos(i*.13));
      el.style.height = (8 + factor * 83) + '%';
      el.style.animationDelay = (-i * 37) + 'ms';
      holder.appendChild(el);
    }
  }
  waveform($('noiseWave'),'noise');
  waveform($('trackWave'),'track');

  function mediaUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return null;
    if (/^https:\/\//i.test(value)) return value;
    if (value.startsWith('showcase/')) {
      const root = window.GBR_SHOWCASE_ROOT;
      return typeof root === 'string' && /^https:\/\//i.test(root)
        ? root.replace(/\/+$/,'/') + value.slice('showcase/'.length)
        : '../' + value;
    }
    return null;
  }

  function infoFor(item) {
    const sources = item?.audio?.sources || {};
    const source = mediaUrl(sources.mp3) || mediaUrl(sources.flac);
    if (!source) return null;
    return { ...item, source };
  }

  function showSelection(item) {
    current = item;
    songTitle.textContent = item?.displayTitle || item?.title || 'Untitled recording';
    const styles = [item?.genre?.parent_genre, item?.genre?.child_genre].filter(Boolean);
    songDescription.textContent = styles.join(' / ') || item?.version || 'A finished GBR recording';
    const art = mediaUrl(item?.artwork_url);
    if (art) { songArtwork.src = art; songArtwork.hidden = false; }
    else { songArtwork.hidden = true; songArtwork.removeAttribute('src'); }
    trackButton.disabled = false;
    if (playing !== 'track') trackButton.textContent = '▶ Listen to the song';
  }

  function selectSong() {
    if (!candidates.length) return;
    const choices = candidates.length > 1 && current
      ? candidates.filter((item) => item.id !== current.id) : candidates;
    const next = choices[Math.floor(Math.random() * choices.length)];
    if (playing === 'track') {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      playing = null;
      resetControls();
    }
    showSelection(next);
    status.textContent = 'Ready to listen';
    progress.style.width = '0%';
  }

  function resetControls() {
    noiseButton.textContent = '▶ Listen to the latent';
    trackButton.textContent = '▶ Listen to the song';
    $('comparePlayA').textContent = '▶ Listen to A';
    $('comparePlayB').textContent = '▶ Listen to B';
    $('noiseCard').classList.remove('playing');
    $('trackCard').classList.remove('playing');
    $('compareCardA').classList.remove('playing');
    $('compareCardB').classList.remove('playing');
  }

  async function toggle(which) {
    if (playing === which && !audio.paused) {
      audio.pause();
      status.textContent = 'Paused';
      if (which.startsWith('compare')) $('recipePlayback').textContent = 'Paused';
      resetControls();
      playing = null;
      return;
    }
    const source = which === 'noise' ? window.GBR_EMPTY_LATENT_AUDIO
      : which === 'track' ? current?.source
      : which === 'compareA' ? comparison.a?.source
      : comparison.b?.source;
    if (!source) { status.textContent = 'Audio unavailable'; return; }
    audio.pause();
    audio.src = source;
    playing = which;
    resetControls();
    const card = {noise:'noiseCard',track:'trackCard',compareA:'compareCardA',compareB:'compareCardB'}[which];
    const button = {noise:'noisePlay',track:'trackPlay',compareA:'comparePlayA',compareB:'comparePlayB'}[which];
    $(card).classList.add('playing');
    const label = which === 'noise' ? 'decoded empty latent'
      : which === 'track' ? songTitle.textContent
      : (which === 'compareA' ? 'A: ' : 'B: ') + (which === 'compareA' ? comparison.a?.version : comparison.b?.version);
    status.textContent = 'Loading ' + label + '…';
    if (which.startsWith('compare')) $('recipePlayback').textContent = status.textContent;
    try {
      await audio.play();
      // Ignore stale play completions if the listener switched recordings.
      if (playing !== which) return;
      status.textContent = 'Playing ' + label;
      if (which.startsWith('compare')) $('recipePlayback').textContent = status.textContent;
      $(button).textContent = 'Ⅱ Pause';
    } catch (error) {
      if (playing !== which) return;
      playing = null;
      resetControls();
      status.textContent = 'Playback could not start. Try pressing Play again.';
      $('recipePlayback').textContent = status.textContent;
    }
  }


  // Chapter 01: use only public catalogue data and published track YAML.
  function option(select, value, label) {
    const node = document.createElement('option');
    node.value = value;
    node.textContent = label;
    select.appendChild(node);
  }
  function niceLabel(item) {
    return String(item?.genre?.child_genre || item?.version || item?.variant || 'Unlabelled version')
      .replaceAll('-', ' ');
  }
  function compareStyles(item) {
    return [
      item?.genre?.parent_genre,
      item?.genre?.child_genre,
      item?.model?.name
    ].filter(Boolean).join(' · ') || item?.version || 'Style not published';
  }
  function sameWords(a,b) {
    const x = a?.lyrics?.raw, y = b?.lyrics?.raw;
    return typeof x === 'string' && x.trim() && x === y;
  }
  function stopComparison() {
    if (playing === 'compareA' || playing === 'compareB') {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
      playing = null;
      resetControls();
      status.textContent = 'Ready to listen';
      progress.style.width = '0%';
    }
    $('recipePlayback').textContent = 'Choose A or B to hear the actual recordings. Only one recording plays at a time.';
  }
  function setVariants(group, first, second) {
    const choices = comparisonGroups.get(group) || [];
    if (choices.length < 2) return;
    stopComparison();
    for (const [letter, selectId] of [['a','recipeVariantA'],['b','recipeVariantB']]) {
      const select = $(selectId);
      select.replaceChildren();
      choices.forEach(item => option(select,item.id,niceLabel(item)));
      select.disabled = false;
    }
    comparison.a = first || choices[0];
    comparison.b = second || choices.find(item => item.id !== comparison.a.id) || choices[1];
    $('recipeVariantA').value = comparison.a.id;
    $('recipeVariantB').value = comparison.b.id;
    updateRecipe();
  }
  function showSettings(item, suffix) {
    const list = $('recipeSettings' + suffix);
    list.replaceChildren();
    const generation = item?.generation || {};
    const rows = [
      ['Model',item?.model?.name],
      ['Sampler',generation.sampler],
      ['Scheduler',generation.scheduler],
      ['Steps',generation.steps],
      ['Sampler CFG',generation.samplerCfg],
      ['Encoder CFG',generation.encoderCfg],
      ['Top K',generation.topK]
    ];
    for (const [name,value] of rows) {
      if (value === null || value === undefined || value === '') continue;
      const label = document.createElement('dt');
      label.textContent = name;
      const detail = document.createElement('dd');
      detail.textContent = String(value);
      list.appendChild(label);
      list.appendChild(detail);
    }
    if (!list.children.length) {
      const missing = document.createElement('dd');
      missing.textContent = 'Generation settings not published for this version.';
      list.appendChild(missing);
    }
  }
  function updateRecipe() {
    for (const [key,suffix] of [['a','A'],['b','B']]) {
      const item = comparison[key];
      $('recipeName' + suffix).textContent = item?.displayTitle || item?.title || 'Untitled';
      $('recipeStyle' + suffix).textContent = compareStyles(item);
      showSettings(item,suffix);
      $('comparePlay' + suffix).disabled = !item?.source;
    }
    $('recipeMatch').textContent = sameWords(comparison.a,comparison.b)
      ? 'Same published lyric text · different recorded versions'
      : 'Published lyric texts differ · other generation settings may differ too';
    showEvidence();
  }
  function sectionMarkers(item) {
    const raw = item?.lyrics?.raw || '';
    const matches = [...raw.matchAll(/^\s*\[([^\]\n]+)\]\s*$/gm)]
      .map(match => '[' + match[1].trim() + ']');
    return matches.length
      ? matches.join('  →  ') + '\n\n' + matches.length + ' marked sections in the published lyrics.'
      : 'No explicit [section] markers in the published lyrics.';
  }
  function extractCaption(yaml) {
    // Extract a top-level YAML block scalar when present. Do not infer captions from genre labels.
    const lines = yaml.replace(/\r\n/g,'\n').split('\n');
    const start = lines.findIndex(line => /^caption:\s*[|>][-+]?\s*$/.test(line));
    if (start < 0) return null;
    const section = [];
    let minIndent = Infinity;
    for (const line of lines.slice(start + 1)) {
      if (!line.trim()) {
        section.push(line);
        continue;
      }
      const leading = line.match(/^ */)[0].length;
      if (leading === 0) break;
      minIndent = Math.min(minIndent,leading);
      section.push(line);
    }
    if (!Number.isFinite(minIndent)) return null;
    const result = section.map(line => line.trim() ? line.slice(minIndent) : '').join('\n').trim();
    return result || null;
  }
  function publishedCaption(item) {
    const url = mediaUrl(item?.yamlUrl);
    if (!url) return Promise.resolve(null);
    if (captionCache.has(url)) return captionCache.get(url);
    const request = fetch(url, {cache:'force-cache'})
      .then(response => { if (!response.ok) throw new Error('Missing public YAML'); return response.text(); })
      .then(extractCaption)
      .catch(() => null);
    captionCache.set(url, request);
    return request;
  }
  function showEvidence() {
    const a = comparison.a, b = comparison.b;
    if (!a || !b) return;
    const token = ++evidenceToken;
    const area = {a:$('recipeEvidenceA'),b:$('recipeEvidenceB')};
    for(const tab of document.querySelectorAll('[data-evidence]')) {
      const selected = tab.dataset.evidence === evidenceMode;
      tab.classList.toggle('selected',selected);
      tab.setAttribute('aria-selected',String(selected));
    }
    for (const [key,suffix] of [['a','A'],['b','B']]) {
      const link = $('recipeYaml' + suffix);
      const url = mediaUrl(comparison[key]?.yamlUrl);
      link.hidden = evidenceMode !== 'caption' || !url;
      if (url) link.href = url;
      else link.removeAttribute('href');
    }
    if (evidenceMode === 'lyrics') {
      area.a.textContent = a?.lyrics?.raw || 'No lyrics in the published catalogue.';
      area.b.textContent = b?.lyrics?.raw || 'No lyrics in the published catalogue.';
      $('evidenceExplainer').textContent = 'Actual lyric text from the published GBR catalogue. Bracketed cues can steer performance, but are not guaranteed.';
    } else if (evidenceMode === 'structure') {
      area.a.textContent = sectionMarkers(a);
      area.b.textContent = sectionMarkers(b);
      $('evidenceExplainer').textContent = 'Section order is extracted from the bracketed markers in each recording’s real published lyrics, not from automatic audio analysis.';
    } else {
      area.a.textContent = 'Loading the published caption, if accessible…';
      area.b.textContent = 'Loading the published caption, if accessible…';
      $('evidenceExplainer').textContent = 'The public catalogue includes genre labels but not the full caption. The original caption is shown only if it can be read from the published YAML.';
      Promise.all([publishedCaption(a),publishedCaption(b)]).then(([first,second]) => {
        if (token !== evidenceToken) return;
        area.a.textContent = first || 'Full caption unavailable from public YAML. Published style: ' + compareStyles(a);
        area.b.textContent = second || 'Full caption unavailable from public YAML. Published style: ' + compareStyles(b);
      });
    }
  }
  function initChapterOne(tracks) {
    const groups = new Map();
    for(const item of tracks) {
      const group = item.composition || item.title;
      if (!group) continue;
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(item);
    }
    comparisonGroups = new Map([...groups.entries()].filter(([,items]) => items.length > 1)
      .sort((a,b) => (a[1][0]?.displayTitle || a[0]).localeCompare(b[1][0]?.displayTitle || b[0])));
    const songSelect = $('recipeSong');
    songSelect.replaceChildren();
    for (const [key,items] of comparisonGroups) {
      option(songSelect,key,(items[0]?.displayTitle || key) + ' · ' + items.length + ' versions');
    }
    if (!comparisonGroups.size) {
      songSelect.disabled = true;
      $('recipeMatch').textContent = 'No compositions with multiple published versions.';
      return;
    }
    songSelect.disabled = false;
    const defaultKey = comparisonGroups.has('paws-off-the-velvet')
      ? 'paws-off-the-velvet' : comparisonGroups.keys().next().value;
    songSelect.value = defaultKey;
    const choices = comparisonGroups.get(defaultKey);
    const a = choices.find(item => item.version === 'bluegrass') || choices[0];
    const b = choices.find(item => item.version === 'funk-pop' && sameWords(a,item))
      || choices.find(item => item.id !== a.id && sameWords(a,item))
      || choices.find(item => item.id !== a.id);
    setVariants(defaultKey,a,b);
    songSelect.addEventListener('change',() => {
      const key = songSelect.value;
      const items = comparisonGroups.get(key) || [];
      const pair = items.find((a,i) => items.slice(i+1).some(b => sameWords(a,b) && niceLabel(a)!==niceLabel(b)));
      const next = pair ? items.find(b => b.id !== pair.id && sameWords(pair,b) && niceLabel(pair)!==niceLabel(b)) : null;
      setVariants(key,pair||items[0],next||items.find(x=>x.id!==(pair||items[0])?.id));
    });
    for(const [key,suffix] of [['a','A'],['b','B']]) {
      $('recipeVariant' + suffix).addEventListener('change', () => {
        const items = comparisonGroups.get(songSelect.value) || [];
        const item = items.find(x => x.id === $('recipeVariant' + suffix).value);
        if (!item) return;
        stopComparison();
        comparison[key] = item;
        const other = key === 'a' ? 'b' : 'a';
        if (comparison[other]?.id === item.id) {
          comparison[other] = items.find(x => x.id !== item.id) || comparison[other];
          $('recipeVariant' + (other === 'a' ? 'A' : 'B')).value = comparison[other].id;
        }
        updateRecipe();
      });
    }
    for(const tab of document.querySelectorAll('[data-evidence]')) {
      tab.addEventListener('click', () => { evidenceMode=tab.dataset.evidence; showEvidence(); });
    }
  }

  noiseButton.addEventListener('click', () => toggle('noise'));
  $('comparePlayA').addEventListener('click', () => toggle('compareA'));
  $('comparePlayB').addEventListener('click', () => toggle('compareB'));
  trackButton.addEventListener('click', () => toggle('track'));
  shuffle.addEventListener('click', selectSong);
  audio.addEventListener('timeupdate', () => {
    const value = Number.isFinite(audio.duration) && audio.duration > 0
      ? Math.min(100, audio.currentTime / audio.duration * 100) : 0;
    progress.style.width = value + '%';
  });
  audio.addEventListener('ended', () => {
    if (playing?.startsWith('compare')) $('recipePlayback').textContent = 'Finished. Select A or B to listen again.';
    resetControls();
    status.textContent = 'Finished';
    playing = null;
    progress.style.width = '0%';
  });
  audio.addEventListener('error', () => {
    if (playing) {
      status.textContent = 'This recording could not be loaded.';
      if (playing.startsWith('compare')) $('recipePlayback').textContent = status.textContent;
      playing = null;
      resetControls();
    }
  });

  noiseButton.disabled = !window.GBR_EMPTY_LATENT_AUDIO;
  if (!window.GBR_EMPTY_LATENT_AUDIO) $('noiseMeta').textContent = 'Audio asset missing';

  fetch('../catalogue.json', { cache:'no-cache' })
    .then((response) => { if (!response.ok) throw new Error('Catalogue unavailable'); return response.json(); })
    .then((data) => {
      candidates = (Array.isArray(data.tracks) ? data.tracks : []).map(infoFor).filter(Boolean);
      if (!candidates.length) throw new Error('No playable showcase recordings');
      shuffle.disabled = false;
      selectSong();
      initChapterOne(candidates);
    })
    .catch(() => {
      $('recipeSong').innerHTML = '<option>Published catalogue unavailable</option>';
      songTitle.textContent = 'Showcase unavailable';
      songDescription.textContent = 'The published catalogue could not be loaded.';
      status.textContent = 'The latent audio is still available to play.';
    });

  const home = document.querySelector('.back');
  if (home && window.parent !== window) {
    home.addEventListener('click', (event) => {
      event.preventDefault();
      audio.pause();
      window.parent.postMessage({type:'gbr:close-observatory'}, window.location.origin);
    });
  }
  window.addEventListener('pagehide', () => audio.pause());
})();
