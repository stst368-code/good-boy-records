"""Streaming measurements, conservative anomaly heuristics and segment selection."""
import json
import math
import uuid
from pathlib import Path
import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import lfilter, resample_poly
from PIL import Image
from .transitions import structural_transitions
from .triage import technical_triage
from .util import atomic_text, dumps, now

VERSION = "gbr-signal-1.2.0"
EPS = 1e-12


def dbfs(x):
    return float(20 * np.log10(max(float(x), EPS)))


def finite(x):
    return float(x) if np.isfinite(x) else None


def loudness(energies, channels):
    weights = np.array([1, 1, 1, 1.41, 1.41][:channels]) if channels <= 5 else np.ones(channels)
    e = np.asarray(energies)
    if len(e) < 4:
        return None, [], None
    weighted = e @ weights
    blocks = np.convolve(weighted, np.ones(4) / 4, mode="valid")
    levels = -0.691 + 10 * np.log10(blocks + EPS)
    absolute = blocks[levels > -70]
    integrated = None
    if len(absolute):
        relative = -0.691 + 10 * np.log10(absolute.mean() + EPS) - 10
        gated = blocks[(levels > -70) & (levels > relative)]
        if len(gated):
            integrated = finite(-0.691 + 10 * np.log10(gated.mean() + EPS))
    short = np.convolve(weighted, np.ones(30) / 30, mode="valid") if len(weighted) >= 30 else []
    short = -0.691 + 10 * np.log10(np.asarray(short) + EPS)
    # EBU-style absolute and relative gates; 10th–95th percentile LRA.
    gated_short = short[(short > -70) & (short > (integrated - 20 if integrated is not None else -70))]
    lra = finite(np.percentile(gated_short, 95) - np.percentile(gated_short, 10)) if len(gated_short) >= 2 else None
    return integrated, short.tolist(), lra


def measure(path, folder, sensitivity="balanced"):
    folder.mkdir(parents=True, exist_ok=True)
    info = sf.info(path)
    sr, channels, frames = info.samplerate, info.channels, info.frames
    if not frames:
        raise ValueError("Empty recording")
    bins = min(4096, frames)
    peaks = np.zeros((bins, min(channels, 2)), dtype=np.float64)
    sumsq = np.zeros(channels)
    peak, true_peak, clips, k_energy = 0.0, 0.0, 0, []
    meter = pyln.Meter(sr)
    filters = [(f.b, f.a, np.zeros((max(len(f.a), len(f.b)) - 1, channels))) for f in meter._filters.values()]
    offset, remainder, previous = 0, np.empty((0, channels)), np.empty((0, channels))
    # One original-rate pass for exact RMS, K-weighted energy and 4x true peak.
    with sf.SoundFile(path) as f:
        for chunk in f.blocks(blocksize=sr * 10, dtype="float64", always_2d=True):
            peak = max(peak, float(np.max(np.abs(chunk))))
            threshold = 1 - 1 / (2 ** max(1, int(''.join(c for c in info.subtype if c.isdigit()) or 16) - 1))
            clips += int(np.count_nonzero(np.abs(chunk) >= threshold))
            sumsq += np.sum(chunk * chunk, axis=0)
            up = resample_poly(np.concatenate([previous, chunk]), 4, 1, axis=0)
            true_peak = max(true_peak, float(np.max(np.abs(up))))
            previous = chunk[-32:]
            indices = np.minimum(((np.arange(len(chunk)) + offset) * bins // frames), bins - 1)
            starts = np.r_[0, np.flatnonzero(np.diff(indices)) + 1]
            maximum = np.maximum.reduceat(np.abs(chunk[:, :peaks.shape[1]]), starts, axis=0)
            peaks[indices[starts]] = np.maximum(peaks[indices[starts]], maximum)
            filtered = chunk
            for index, (b, a, state) in enumerate(filters):
                filtered, state = lfilter(b, a, filtered, axis=0, zi=state)
                filters[index] = b, a, state
            filtered = np.concatenate([remainder, filtered])
            hop = max(1, round(sr * .1))
            n = len(filtered) // hop
            if n:
                k_energy.extend(np.mean(filtered[:n * hop].reshape(n, hop, channels) ** 2, axis=1))
            remainder = filtered[n * hop:]
            offset += len(chunk)
    integrated, short, lra = loudness(k_energy, channels)
    features, spectrum, last_shape = [], [], None
    hop = max(1, sr // 2)
    processing_sr = min(sr, 48000)
    # Use one frequency grid for the whole recording. SoundFile emits a
    # shorter final block; deriving nfft from that block changes the number
    # of spectral bins and makes adjacent-window flux comparisons fail.
    nfft = 2 ** math.ceil(math.log2(max(2048, processing_sr)))
    freq = np.fft.rfftfreq(nfft, 1 / processing_sr)
    spectral_max = min(20000, sr / 2)
    edges = np.geomspace(30, max(31, spectral_max), 73)
    band_masks = [(freq >= edges[j]) & (freq < edges[j + 1]) for j in range(72)]
    tonal = (freq >= 65) & (freq <= min(4000, processing_sr / 2))
    pitch_classes = np.round(69 + 12 * np.log2(np.maximum(freq[tonal], 1) / 440)).astype(int) % 12
    with sf.SoundFile(path) as f:
        for i, chunk in enumerate(f.blocks(blocksize=sr, overlap=sr - hop, dtype="float32", always_2d=True)):
            if not len(chunk):
                continue
            start = i * hop / sr
            reduced = resample_poly(chunk, processing_sr // math.gcd(sr, processing_sr), sr // math.gcd(sr, processing_sr), axis=0)
            amplitude = np.abs(np.fft.rfft(reduced * np.hanning(len(reduced))[:, None], n=nfft, axis=0))
            # Average channel power so antiphase stereo does not cancel the
            # descriptors used to locate musical sections.
            power = np.mean(amplitude ** 2, axis=1) + EPS
            centroid = float(np.sum(freq * power) / np.sum(power))
            flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
            shape = power / np.sum(power)
            flux = float(np.sum(np.maximum(shape - last_shape, 0))) if last_shape is not None else 0
            last_shape = shape
            bands = np.array([float(np.mean(power[mask])) if np.any(mask) else EPS for mask in band_masks])
            band_energy = np.array([float(np.sum(power[mask])) for mask in band_masks])
            pitch = np.bincount(pitch_classes, weights=power[tonal], minlength=12)
            spectrum.append(10 * np.log10(bands + EPS))
            rms_ch = np.sqrt(np.mean(chunk.astype(float) ** 2, axis=0) + EPS)
            rms = float(np.sqrt(np.mean(chunk.astype(float) ** 2)))
            correlation, width, balance = None, 0., 0.
            if channels >= 2:
                left, right = chunk[:, 0].astype(float), chunk[:, 1].astype(float)
                correlation = finite(np.corrcoef(left, right)[0, 1]) if np.std(left) > 1e-7 and np.std(right) > 1e-7 else None
                width = float(np.sqrt(np.mean(((left - right) / 2) ** 2)) / (np.sqrt(np.mean(((left + right) / 2) ** 2)) + 1e-8))
                balance = dbfs(rms_ch[0]) - dbfs(rms_ch[1])
            features.append({"start": start, "end": min(info.duration, start + len(chunk) / sr),
                             "rms_db": dbfs(rms), "centroid_hz": centroid, "flatness": flatness, "flux": flux,
                             "balance_db": balance, "correlation": correlation, "width_ratio": width,
                             "spike": float(np.max(np.abs(np.diff(chunk, axis=0)))) if len(chunk) > 1 else 0.,
                             "spectrum_bands": (band_energy / max(float(np.sum(band_energy)), EPS)).tolist(),
                             "chroma": (pitch / max(float(np.sum(pitch)), EPS)).tolist(),
                             "bands": [float(np.sum(shape[(freq >= lo) & (freq < hi)])) for lo, hi in [(0, 250), (250, 4000), (4000, processing_sr / 2 + 1)]]})
    events = detect(features, info.duration, peak, sensitivity)
    correlation = [x["correlation"] for x in features if x["correlation"] is not None]
    metrics = {
        "sample_peak_dbfs": dbfs(peak), "true_peak_dbtp_4x": dbfs(true_peak),
        "clipped_samples": clips, "clipped_fraction": clips / (frames * channels),
        "rms_dbfs": dbfs(np.sqrt(sumsq.sum() / (frames * channels))),
        "channel_rms_dbfs": [dbfs(x) for x in np.sqrt(sumsq / frames)], "integrated_lufs": integrated,
        "loudness_range_lu": lra, "short_term_lufs": [finite(x) for x in short], "short_term_step_seconds": .1,
        "spectral_centroid_hz": float(np.mean([x["centroid_hz"] for x in features])),
        "spectral_flatness": float(np.mean([x["flatness"] for x in features])),
        "spectral_flux": float(np.mean([x["flux"] for x in features])),
        "frequency_distribution": np.mean([x["bands"] for x in features], axis=0).tolist(),
        "stereo_correlation": float(np.mean(correlation)) if correlation else None,
        "stereo_width_ratio": float(np.mean([x["width_ratio"] for x in features])),
        "channel_balance_db": float(np.mean([x["balance_db"] for x in features])),
        "mono_compatibility": "possible_phase_cancellation" if correlation and np.mean(correlation) < -.25 else "no_strong_cancellation_detected",
        "event_count": len(events), "duration": info.duration, "sample_rate": sr, "channels": channels,
        "transition_method": "multi_window_novelty_v1", "transition_sensitivity": sensitivity,
        "limitations": ["True peak is a 4x polyphase estimate, not a certified broadcast meter.",
                         "Structural and defect events are heuristics; intentional music can trigger them.",
                         "Spectral preview covers 30 Hz to min(20 kHz, source Nyquist); original playback is unaltered."]}
    spect = np.array(spectrum).T
    low, high = np.percentile(spect, [10, 99])
    norm = np.clip((spect - low) / max(high - low, 1), 0, 1)
    # Dark background, warm gold highlights, no external charting dependency.
    rgb = np.stack([18 + norm * 225, 23 + norm * 145, 29 + norm * 30], axis=2).astype(np.uint8)
    image = Image.fromarray(rgb[::-1], "RGB")
    if image.width > 1600:
        image = image.resize((1600, 72), Image.Resampling.BOX)
    image.resize((image.width, 180), Image.Resampling.NEAREST).save(folder / "spectrogram.png")
    preview = {"duration": info.duration, "channels": channels, "waveform": np.round(peaks, 5).T.tolist(),
               "features": features, "frequency_axis": {"min_hz": 30, "max_hz": spectral_max, "scale": "log"}}
    atomic_text(folder / "preview.json", dumps(preview))
    return metrics, events, preview


def detect(features, duration, peak, sensitivity="balanced"):
    rms = np.array([f["rms_db"] for f in features])
    flux = np.array([f["flux"] for f in features])
    flat = np.array([f["flatness"] for f in features])
    events = []
    flux_cutoff = max(.35, float(np.percentile(flux, 90)))
    noise_cutoff = max(.18, float(np.median(flat)) * 4)

    def add(kind, i, severity, confidence, values=None):
        f = features[i]
        # Merge adjacent events of the same type to avoid marker overload.
        previous = next((e for e in reversed(events) if e["type"] == kind and f["start"] <= e["end"] + .55), None)
        if previous:
            previous["end"] = f["end"]
            previous["severity"] = max(previous["severity"], severity)
            return
        events.append({"type": kind, "start": f["start"], "end": f["end"],
                       "severity": float(np.clip(severity, 0, 1)), "confidence": confidence,
                       "measurements": values or {k: f[k] for k in ("rms_db", "flatness", "centroid_hz", "flux")}, "algorithm": VERSION})
    for i, f in enumerate(features):
        if f["rms_db"] < -55:
            add("quiet_passage" if i < 2 or f["end"] > duration - 1 else "possible_dropout", i, .5, .65)
        if i and abs(rms[i] - rms[i - 1]) > 16:
            add("energy_change", i, abs(rms[i] - rms[i - 1]) / 40, .6)
        if f["flux"] > flux_cutoff:
            add("texture_transition", i, f["flux"], .7)
        if f["flatness"] > noise_cutoff and f["rms_db"] > -35:
            add("possible_noise_degradation", i, min(1, f["flatness"] * 2), .45)
        if f["bands"][2] > .65 and f["rms_db"] > -40:
            add("high_frequency_activity", i, f["bands"][2], .45)
        if f["spike"] > .8:
            add("transient_irregularity", i, min(f["spike"] / 2, 1), .45)
        if f["correlation"] is not None and f["correlation"] < -.45:
            add("possible_phase_cancellation", i, -f["correlation"], .7)
        if abs(f["balance_db"]) > 15 and f["rms_db"] > -40:
            add("channel_divergence", i, min(abs(f["balance_db"]) / 30, 1), .55)
        if i and features[i - 1]["width_ratio"] > .25 and f["width_ratio"] < .015:
            add("stereo_collapse", i, .55, .45)
    if rms[0] > -20:
        add("abrupt_opening", 0, .35, .35)
    if rms[-1] > -20:
        add("possible_abrupt_ending", len(features) - 1, .4, .35)
    if peak > .999:
        add("possible_clipping", int(np.argmax(rms)), .5, .6, {"sample_peak": peak})
    # Repeated texture: sufficiently separated windows with almost equal descriptors.
    signatures = np.array([[f["centroid_hz"] / 8000, f["flatness"], *f["bands"]] for f in features])
    for i in range(0, len(features), 8):
        if i < 24 or rms[i] < -45:
            continue
        distances = np.linalg.norm(signatures[:i - 20] - signatures[i], axis=1)
        j = int(np.argmin(distances))
        if distances[j] < .012 and abs(rms[i] - rms[j]) < 3:
            add("repeated_texture", i, .1, .4, {"similar_to_seconds": features[j]["start"], "descriptor_distance": float(distances[j])})
    for kind, index in [("energy_peak", int(np.argmax(rms))), ("low_energy", int(np.argmin(rms)))]:
        add(kind, index, .1, .9)
    for event in structural_transitions(features, duration, sensitivity):
        event["algorithm"] = VERSION
        events.append(event)
    return sorted(events, key=lambda e: (e["start"], e["type"]))


def select_segments(preview, events, target, mode):
    duration, fs = preview["duration"], preview["features"]
    if duration <= target or (mode != "fast" and duration <= target + 2):
        return [{"start": 0., "end": duration, "reason": "complete_short_recording"}]
    count = 6 if mode == "standard" else 5
    length = target / count
    rms = np.array([f["rms_db"] for f in fs])
    starts = np.array([f["start"] for f in fs])
    median_rms = float(np.median(rms))
    transitions = sorted((e for e in events if e["type"] == "structural_transition"),
                         key=lambda e: e["measurements"].get("novelty", e["severity"]), reverse=True)
    defects = sorted((e for e in events if e["type"].startswith("possible_") or
                      e["type"] in ("transient_irregularity", "channel_divergence")),
                     key=lambda e: e["severity"] * e["confidence"], reverse=True)
    candidates = [(0., "opening"), (duration - length, "ending")]
    # Musical boundaries receive a real part of the listening budget, ahead
    # of generic energy extrema. Include context on both sides of the point.
    candidates += [((e["start"] + e["end"] - length) / 2, "transition")
                   for e in transitions[:3 if mode == "standard" else 2]]
    candidates += [((e["start"] + e["end"] - length) / 2, "point_of_interest") for e in defects[:1]]
    candidates += [(fs[int(np.argmax(rms))]["start"] - length / 2, "high_energy"),
                   (fs[int(np.argmin(rms))]["start"] - length / 2, "low_energy")]
    candidates += [(f["start"] - length / 2, "transition")
                   for f in sorted(fs, key=lambda f: f["flux"], reverse=True)[:12]]
    candidates += [(f["start"] - length / 2, "representative")
                   for f in sorted(fs, key=lambda f: abs(f["rms_db"] - median_rms))]
    chosen = []
    for start, reason in candidates:
        start = float(np.clip(start, 0, duration - length))
        if any(start < c["end"] + .5 and start + length > c["start"] - .5 for c in chosen):
            continue
        current = fs[min(len(fs) - 1, int(np.searchsorted(starts, start + length / 2)))]
        if reason == "representative" and any(abs(current["rms_db"] - c.get("rms", -999)) < .15 and
                                              abs(current["centroid_hz"] - c.get("centroid", -999)) < 10 for c in chosen):
            continue
        chosen.append({"start": start, "end": start + length, "reason": reason,
                       "rms": current["rms_db"], "centroid": current["centroid_hz"]})
        if len(chosen) == count:
            break
    if len(chosen) < count:
        for start in np.linspace(0, duration - length, count * 8):
            if not any(start < c["end"] + .01 and start + length > c["start"] - .01 for c in chosen):
                chosen.append({"start": float(start), "end": float(start + length), "reason": "coverage"})
            if len(chosen) == count:
                break
    return [{"start": c["start"], "end": c["end"], "reason": c["reason"]} for c in sorted(chosen, key=lambda c: c["start"])]


class Analyzer:
    def __init__(self, config, db, archiver):
        self.config, self.db, self.archiver = config, db, archiver

    def run(self, audio_id):
        path = self.archiver.available(audio_id)
        if not path:
            raise FileNotFoundError("Audio unavailable for analysis")
        analysis_id = uuid.uuid4().hex
        folder = self.config.data / "cache" / audio_id / analysis_id
        metrics, events, preview = measure(path, folder, self.config.values["transition_sensitivity"])
        return self.store(audio_id, metrics, events, preview, folder / "preview.json", folder / "spectrogram.png")

    def retune(self, audio_id):
        previous = self.db.one("SELECT * FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (audio_id,))
        if not previous or not Path(previous["preview_path"]).is_file() or not Path(previous["spectrogram_path"]).is_file():
            return self.run(audio_id)
        preview = json.loads(Path(previous["preview_path"]).read_text())
        metrics = json.loads(previous["metrics_json"])
        events = detect(preview["features"], preview["duration"], 10 ** (metrics["sample_peak_dbfs"] / 20), self.config.values["transition_sensitivity"])
        metrics.update(event_count=len(events), transition_method="multi_window_novelty_v1", transition_sensitivity=self.config.values["transition_sensitivity"], retuned_from_analysis_id=previous["id"], feature_source_version=metrics.get("feature_source_version", previous["version"]))
        return self.store(audio_id, metrics, events, preview, previous["preview_path"], previous["spectrogram_path"])

    def store(self, audio_id, metrics, events, preview, preview_path, spectrogram_path):
        metrics = {**metrics, "technical_triage": technical_triage(metrics, events)}
        analysis_id = uuid.uuid4().hex
        with self.db.transaction() as c:
            c.execute("INSERT INTO analyses VALUES(?,?,?,?,?,?,?)", (analysis_id, audio_id, VERSION, now(), dumps(metrics), str(preview_path), str(spectrogram_path)))
            for e in events:
                c.execute("INSERT INTO audio_events VALUES(?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, analysis_id, audio_id, e["type"], e["start"], e["end"], e["severity"], e["confidence"], dumps(e["measurements"]), VERSION))
            for mode in ("fast", "quick", "standard"):
                for i, segment in enumerate(select_segments(preview, events, self.config.values[mode + "_seconds"], mode)):
                    c.execute("INSERT INTO segments VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, analysis_id, audio_id, mode, i, segment["start"], segment["end"], dumps({"reason": segment["reason"], "target_seconds": self.config.values[mode + "_seconds"]})))
            c.execute("UPDATE audio SET analysis_state='ready' WHERE id=?", (audio_id,))
        return analysis_id

