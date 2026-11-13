export const formatTime = seconds => {
  const time = Number.isFinite(seconds) ? Math.max(0, seconds) : 0;
  return `${Math.floor(time / 60)}:${String(Math.floor(time % 60)).padStart(2, '0')}`;
};

export function initPlayer({toast, onChange}) {
  const get = id => document.getElementById(id);
  const audio = get('audio');
  const progress = get('progress');
  const volume = get('volume');
  let current = null;
  let pendingSeek = null;
  let wantsPlayback = false;
  let stepping = null;
  let format = localStorage.getItem('gbr-format') === 'wav' ? 'wav' : 'flac';
  const session = navigator.mediaSession;

  volume.value = localStorage.getItem('gbr-volume') ?? 50;
  audio.volume = Number(volume.value) / 100;
  const continuous = get('playContinuously');
  continuous.checked = localStorage.getItem('gbr-listen-continuous') !== 'false';
  continuous.onchange = () => localStorage.setItem('gbr-listen-continuous', String(continuous.checked));

  function seek(time) {
    if (!Number.isFinite(time) || !current) return;
    if (Number.isFinite(audio.duration)) audio.currentTime = Math.max(0, Math.min(time, audio.duration));
    else pendingSeek = Math.max(0, time);
  }

  function syncProgress() {
    const total = Number.isFinite(audio.duration) ? audio.duration : current?.duration || 0;
    const position = pendingSeek ?? audio.currentTime;
    get('elapsed').textContent = formatTime(position);
    get('duration').textContent = formatTime(total);
    progress.value = total ? Math.min(1000, position / total * 1000) : 0;
    progress.disabled = !Number.isFinite(audio.duration);
    if (session) {
      try {
        session.setPositionState(total > 0 ? {duration: total, position: Math.min(position, total), playbackRate: audio.playbackRate} : undefined);
      } catch {}
    }
  }

  function canStep(direction) {
    if (!current) return false;
    const {queue, index} = current;
    return Boolean(queue && (direction < 0 ? index > 0 : index + 1 < queue.items.length || queue.loadMore));
  }

  function refresh() {
    const playing = Boolean(current && !audio.paused);
    get('play').disabled = !current;
    get('play').dataset.playing = String(playing);
    get('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
    get('playerPrevious').disabled = !current || stepping || (!canStep(-1) && !audio.currentTime);
    get('playerNext').disabled = stepping || !canStep(1);
    get('compatibility').disabled = typeof current?.url !== 'function';
    get('compatibility').textContent = typeof current?.url === 'string' ? 'MP3' : format.toUpperCase();
    if (session) session.playbackState = current ? (playing ? 'playing' : 'paused') : 'none';
    onChange(current, playing);
  }

  function updateTrack() {
    get('playerTitle').textContent = current?.title || 'Nothing playing';
    get('playerContext').textContent = current?.subtitle || '';
    get('playerTitle').title = current?.title || '';
    if (session && typeof MediaMetadata !== 'undefined') {
      session.metadata = current ? new MediaMetadata({
        title: current.title,
        artist: 'Good Boy Records',
        album: current.subtitle,
        artwork: [{src: '/static/icon-192.png', sizes: '192x192', type: 'image/png'}],
      }) : null;
    }
    syncProgress();
    refresh();
  }

  function pause() {
    wantsPlayback = false;
    audio.pause();
  }

  async function play() {
    const entry = current;
    if (!entry) return;
    wantsPlayback = true;
    try {
      await audio.play();
    } catch (error) {
      if (entry !== current || error.name === 'AbortError' || audio.error) return;
      wantsPlayback = false;
      throw error;
    }
  }

  function load(entry, {autoplay = false, position = entry?.position || 0} = {}) {
    stepping = null;
    if (current) current.position = pendingSeek ?? audio.currentTime;
    pause();
    current = entry;
    pendingSeek = position || null;
    if (entry) audio.src = typeof entry.url === 'function' ? entry.url(format) : entry.url;
    else audio.removeAttribute('src');
    audio.load();
    updateTrack();
    if (autoplay) return play();
  }

  function toggle() {
    if (!audio.paused) return pause();
    return play();
  }

  function playQueue(queue, index) {
    return load({...queue.items[index], queue, index}, {autoplay: true, position: 0});
  }

  async function step(direction) {
    if (!current || stepping) return;
    if (direction < 0 && (audio.currentTime > 3 || !canStep(-1))) {
      seek(0);
      return;
    }
    if (!canStep(direction)) return;
    const entry = current;
    stepping = entry;
    refresh();
    try {
      const {queue, index} = entry;
      const nextIndex = index + direction;
      while (nextIndex === queue.items.length && queue.loadMore && stepping === entry) {
        await queue.loadMore();
      }
      if (stepping === entry && queue.items[nextIndex]) await playQueue(queue, nextIndex);
    } finally {
      if (stepping === entry) {
        stepping = null;
        refresh();
      }
    }
  }

  const handle = fn => (...args) => Promise.resolve(fn(...args)).catch(error => toast(error.message));
  audio.addEventListener('loadedmetadata', () => {
    if (pendingSeek !== null) seek(pendingSeek);
    pendingSeek = null;
    syncProgress();
  });
  for (const event of ['play', 'pause', 'emptied', 'loadedmetadata']) audio.addEventListener(event, refresh);
  audio.addEventListener('timeupdate', () => {
    syncProgress();
    // Restart becomes available once the first track has started.
    get('playerPrevious').disabled = !current || stepping || (!canStep(-1) && !audio.currentTime);
  });
  audio.addEventListener('ended', handle(async () => {
    wantsPlayback = false;
    syncProgress();
    refresh();
    if (continuous.checked && current?.queue && current.source !== 'review') await step(1);
  }));
  audio.addEventListener('error', handle(async () => {
    if (!audio.error || !current) return;
    if (format === 'flac' && typeof current.url === 'function') {
      format = 'wav';
      localStorage.setItem('gbr-format', format);
      const autoplay = wantsPlayback;
      toast('Using lossless WAV for playback compatibility.');
      await load(current, {autoplay, position: pendingSeek ?? audio.currentTime});
    } else {
      wantsPlayback = false;
      toast('Playback unavailable. Check Settings for file issues.');
    }
  }));

  // The mixing station and experiment previews can still audition their own sources.
  document.addEventListener('play', event => {
    if (!(event.target instanceof HTMLAudioElement)) return;
    if (event.target === audio) {
      for (const preview of document.querySelectorAll('audio')) if (preview !== audio) preview.pause();
    } else {
      pause();
    }
  }, true);

  get('play').onclick = handle(toggle);
  get('playerPrevious').onclick = handle(() => step(-1));
  get('playerNext').onclick = handle(() => step(1));
  progress.oninput = () => {
    seek(Number(progress.value) / 1000 * audio.duration);
    syncProgress();
  };
  volume.oninput = () => {
    audio.volume = Number(volume.value) / 100;
    localStorage.setItem('gbr-volume', volume.value);
  };
  get('compatibility').onclick = handle(async () => {
    if (typeof current?.url !== 'function') return;
    format = format === 'flac' ? 'wav' : 'flac';
    localStorage.setItem('gbr-format', format);
    await load(current, {autoplay: !audio.paused, position: pendingSeek ?? audio.currentTime});
  });
  if (session) {
    const actions = {
      play: handle(play),
      pause,
      stop: () => {pause(); seek(0);},
      seekbackward: details => seek(audio.currentTime - (details.seekOffset || 10)),
      seekforward: details => seek(audio.currentTime + (details.seekOffset || 10)),
      seekto: details => seek(details.seekTime),
      previoustrack: handle(() => step(-1)),
      nexttrack: handle(() => step(1)),
    };
    for (const [name, handler] of Object.entries(actions)) {
      try { session.setActionHandler(name, handler); } catch {}
    }
  }
  updateTrack();
  return {
    get current() { return current; },
    get format() { return format; },
    position: entry => entry === current ? (pendingSeek ?? audio.currentTime) : (entry?.position || 0),
    load, pause, toggle, seek, playQueue, refresh, updateTrack,
  };
}
