import hashlib
import json
import re
import threading
import time
from pathlib import Path
import numpy as np
import soundfile as sf
from fastapi.responses import FileResponse
from .util import dumps
from .indexer import digest


class PinnedPlaybackResponse(FileResponse):
    def __init__(self, playback, path, **kwargs):
        super().__init__(path, **kwargs)
        self.playback, self.pinned_path = playback, Path(path).resolve()
        with playback.lock:
            playback.pins[self.pinned_path] = playback.pins.get(self.pinned_path, 0) + 1

    async def __call__(self, scope, receive, send):
        try:
            return await super().__call__(scope, receive, send)
        finally:
            with self.playback.lock:
                count = self.playback.pins.get(self.pinned_path, 1) - 1
                if count:
                    self.playback.pins[self.pinned_path] = count
                else:
                    self.playback.pins.pop(self.pinned_path, None)


class Playback:
    def __init__(self, config, db, archiver):
        self.config, self.db, self.archiver = config, db, archiver
        self.lock = threading.RLock()
        self.pins = {}

    @property
    def cache_root(self):
        return self.config.data / 'cache' / 'playback'

    def remember(self, path, aid):
        timestamp = time.time()
        self.db.execute('''INSERT INTO playback_cache VALUES(?,?,?,?,?) ON CONFLICT(filename)
            DO UPDATE SET size=excluded.size,last_used=excluded.last_used''', (path.name, aid, path.stat().st_size, timestamp, timestamp))

    def response(self, path, **kwargs):
        return PinnedPlaybackResponse(self, path, **kwargs)

    def cache_report(self):
        with self.lock:
            files = [p for p in self.cache_root.iterdir() if p.is_file() and not p.is_symlink()] if self.cache_root.exists() else []
            known = {r['filename'] for r in self.db.all('SELECT filename FROM playback_cache')}
            return {'files': len(files), 'bytes': sum(p.stat().st_size for p in files),
                    'wav_files': sum(p.suffix == '.wav' for p in files), 'wav_bytes': sum(p.stat().st_size for p in files if p.suffix == '.wav'),
                    'untracked_files': sum(p.name not in known for p in files), 'automatic_cleanup': False}


    def cleanup(self, older_than_days=7, dry_run=True):
        if not 1 <= older_than_days <= 3650:
            raise ValueError('Keep at least one day of playback cache')
        result = {'eligible_files': 0, 'eligible_bytes': 0, 'removed_files': 0, 'removed_bytes': 0,
                  'protected_files': 0, 'unavailable_or_changed_source': 0, 'errors': [], 'dry_run': dry_run}
        cutoff = time.time() - older_than_days * 86400
        verified = {}
        for row in self.db.all('SELECT * FROM playback_cache WHERE last_used<?', (cutoff,)):
            if not re.fullmatch(r'[a-f0-9]{64}\.(?:wav|flac)', row['filename']):
                continue
            path = self.cache_root / row['filename']
            if path.is_symlink() or not path.is_file():
                continue
            # Hashing originals can take time; do not hold the render/stream lock.
            if row['audio_id'] not in verified:
                source = self.archiver.available(row['audio_id'])
                media = self.db.one("SELECT sha256 FROM media_files WHERE audio_id=? AND path=? AND storage_state IN ('input','archived')", (row['audio_id'], str(source))) if source else None
                try:
                    verified[row['audio_id']] = bool(source and media and digest(source) == media['sha256'])
                except OSError:
                    verified[row['audio_id']] = False
            if not verified[row['audio_id']]:
                result['unavailable_or_changed_source'] += 1
                continue
            with self.lock:
                current = self.db.one('SELECT last_used FROM playback_cache WHERE filename=?', (row['filename'],))
                reserved = self.db.one('SELECT 1 FROM queue_reservations WHERE audio_id=? AND expires_at>?', (row['audio_id'], time.time()))
                if self.pins.get(path.resolve()) or reserved or (current and current['last_used'] >= cutoff):
                    result['protected_files'] += 1
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                size = path.stat().st_size
                result['eligible_files'] += 1
                result['eligible_bytes'] += size
                if not dry_run:
                    try:
                        path.unlink()
                        self.db.execute('DELETE FROM playback_cache WHERE filename=?', (row['filename'],))
                        result['removed_files'] += 1
                        result['removed_bytes'] += size
                    except OSError:
                        result['errors'].append('A cached file is in use or cannot be removed; it was retained.')
        result['untracked_files_retained'] = self.cache_report()['untracked_files']
        return result

    def render(self, generation_id, mode="full", format="flac", start=None, end=None, segment_id=None, match_lufs=None, analysis_id=None):
        g = self.db.one("SELECT * FROM generations WHERE id=?", (generation_id,))
        if not g:
            raise KeyError("Recording not found")
        audio = self.db.one("SELECT * FROM audio WHERE id=?", (g["audio_id"],))
        source = self.archiver.available(g["audio_id"])
        if not source:
            raise FileNotFoundError("Recording unavailable")
        if mode == "full" and format == "flac" and match_lufs is None:
            return source
        analysis = self.db.one("SELECT * FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (g["audio_id"],))
        if analysis_id:
            analysis = self.db.one("SELECT * FROM analyses WHERE id=? AND audio_id=?", (analysis_id, g["audio_id"]))
            if not analysis:
                raise ValueError("Analysis does not belong to this recording")
        if segment_id:
            s = self.db.one("SELECT * FROM segments WHERE id=? AND audio_id=?", (segment_id, g["audio_id"]))
            if not s:
                raise ValueError("Segment not found")
            ranges = [(s["start"], s["end"])]
        elif mode in ("fast", "quick", "standard"):
            if not analysis:
                raise ValueError("Sampling is still being prepared; Full playback is available")
            segments = self.db.all("SELECT * FROM segments WHERE analysis_id=? AND mode=? ORDER BY ordinal", (analysis["id"], mode))
            ranges = [(s["start"], s["end"]) for s in segments]
            if not ranges:
                raise ValueError("This sampling mode needs retuned analysis. Use Retune zones in Settings, or another mode.")
        elif mode == "range":
            if start is None or end is None or not 0 <= start < end <= audio["duration"] + .01:
                raise ValueError("Invalid range")
            ranges = [(start, min(end, audio["duration"]))]
        elif mode == "full":
            ranges = [(0, audio["duration"])]
        else:
            raise ValueError("Unknown playback mode")
        # Full lossless WAV does not depend on an analysis revision or snippet fade.
        independent = mode == 'full' and match_lufs is None
        key = hashlib.sha256(dumps([g["audio_id"], None if independent else analysis["id"] if analysis else None, ranges, format, match_lufs, 0 if independent else self.config.values["fade_ms"]]).encode()).hexdigest()
        target = self.config.data / "cache" / "playback" / (key + "." + format)
        with self.lock:
            if target.exists():
                self.remember(target, g['audio_id'])
                return target
            if independent:
                # Reuse identifiable old full-track copies without renaming,
                # rewriting or deleting them. Earlier keys included analysis/fade.
                revisions = [r['id'] for r in self.db.all('SELECT id FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC', (g['audio_id'],))] + [None]
                for revision in revisions:
                    for old_fade in dict.fromkeys((self.config.values['fade_ms'], 35)):
                        old_key = hashlib.sha256(dumps([g['audio_id'], revision, ranges, format, None, old_fade]).encode()).hexdigest()
                        legacy = self.cache_root / (old_key+'.'+format)
                        if legacy.is_file() and not legacy.is_symlink():
                            self.remember(legacy, g['audio_id'])
                            return legacy
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".part")
            gain = 1.
            if match_lufs is not None:
                metrics = json.loads(analysis["metrics_json"]) if analysis else {}
                if metrics.get("integrated_lufs") is None:
                    raise ValueError("Loudness matching needs completed analysis with measurable audio")
                gain = 10 ** ((match_lufs - metrics["integrated_lufs"]) / 20)
            with sf.SoundFile(source) as src, sf.SoundFile(temporary, "w", samplerate=src.samplerate, channels=src.channels,
                                                        subtype=src.subtype, format="FLAC" if format == "flac" else "WAV") as dst:
                fade = round(src.samplerate * self.config.values["fade_ms"] / 1000) if mode != "full" else 0
                for beginning, ending in ranges:
                    src.seek(round(beginning * src.samplerate))
                    remaining = min(src.frames - src.tell(), round((ending - beginning) * src.samplerate))
                    total, offset = remaining, 0
                    while remaining > 0:
                        block = src.read(min(65536, remaining), dtype="float64", always_2d=True)
                        if not len(block):
                            break
                        positions = np.arange(len(block)) + offset
                        envelope = np.minimum(1., np.minimum(positions / max(fade, 1), (total - 1 - positions) / max(fade, 1))) if fade else np.ones(len(block))
                        dst.write(block * envelope[:, None] * gain)
                        remaining -= len(block)
                        offset += len(block)
            temporary.replace(target)
            self.remember(target, g['audio_id'])
        return target
