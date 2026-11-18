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
    $('noiseCard').classList.remove('playing');
    $('trackCard').classList.remove('playing');
  }

  async function toggle(which) {
    if (playing === which && !audio.paused) {
      audio.pause();
      status.textContent = 'Paused';
      resetControls();
      playing = null;
      return;
    }
    const source = which === 'noise' ? window.GBR_EMPTY_LATENT_AUDIO : current?.source;
    if (!source) { status.textContent = 'Audio unavailable'; return; }
    audio.pause();
    audio.src = source;
    playing = which;
    resetControls();
    $(which === 'noise' ? 'noiseCard' : 'trackCard').classList.add('playing');
    const label = which === 'noise' ? 'decoded empty latent' : songTitle.textContent;
    status.textContent = 'Loading ' + label + '…';
    try {
      await audio.play();
      status.textContent = 'Playing ' + label;
      $(which === 'noise' ? 'noisePlay' : 'trackPlay').textContent = 'Ⅱ Pause';
    } catch (error) {
      playing = null;
      resetControls();
      status.textContent = 'Playback could not start. Try pressing Play again.';
    }
  }

  noiseButton.addEventListener('click', () => toggle('noise'));
  trackButton.addEventListener('click', () => toggle('track'));
  shuffle.addEventListener('click', selectSong);
  audio.addEventListener('timeupdate', () => {
    const value = Number.isFinite(audio.duration) && audio.duration > 0
      ? Math.min(100, audio.currentTime / audio.duration * 100) : 0;
    progress.style.width = value + '%';
  });
  audio.addEventListener('ended', () => {
    resetControls();
    status.textContent = 'Finished';
    playing = null;
    progress.style.width = '0%';
  });
  audio.addEventListener('error', () => {
    if (playing) {
      status.textContent = 'This recording could not be loaded.';
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
    })
    .catch(() => {
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
