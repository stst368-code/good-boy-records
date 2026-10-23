"""Bounded-memory section novelty at short and longer time scales.

Compare descriptor averages on either side of a possible boundary. This is
the mean-distance form of a local novelty kernel; no full N-by-N similarity
matrix, neural model or extra runtime dependency is needed.
"""
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter
from scipy.signal import find_peaks


def descriptors(features):
    rows = []
    rich = all("spectrum_bands" in f for f in features)
    chroma = all("chroma" in f for f in features)
    for f in features:
        bands = np.maximum(f.get("spectrum_bands", f["bands"]), 0)
        bands = np.sqrt(bands / max(float(np.sum(bands)), 1e-12))
        row = list(bands * (1.1 if rich else .9))
        if chroma:
            pitch = np.maximum(f["chroma"], 0)
            row.extend(.65 * np.sqrt(pitch / max(float(np.sum(pitch)), 1e-12)))
        row.extend([np.log2(max(f["centroid_hz"], 30) / 30) / 6,
                    f["flatness"] * .6, np.clip(f["rms_db"], -80, 0) / 30,
                    np.clip(f["balance_db"], -30, 30) / 40,
                    (f["correlation"] or 0) * .25,
                    np.log1p(min(f["width_ratio"], 10)) * .15])
        rows.append(row)
    return np.nan_to_num(np.asarray(rows, dtype=float))


def structural_transitions(features, duration, sensitivity="balanced"):
    if len(features) < 8:
        return []
    x = descriptors(features)
    times = np.asarray([(f["start"] + f["end"]) / 2 for f in features])
    step = max(.05, float(np.median(np.diff(times))))
    cumulative = np.vstack([np.zeros(x.shape[1]), np.cumsum(x, axis=0)])
    scores = np.zeros(len(x))
    # Larger windows suppress individual beats; shorter windows retain drops
    # and brief arrangement changes. Prefix sums keep this O(N * dimensions).
    for seconds, weight in ((1.5, .35), (4., .65)):
        width = max(2, round(seconds / step))
        for i in range(2, len(x) - 2):
            lo, hi = max(0, i - width), min(len(x), i + width)
            before = (cumulative[i] - cumulative[lo]) / (i - lo)
            after = (cumulative[hi] - cumulative[i]) / (hi - i)
            scores[i] += weight * np.linalg.norm(after - before)
    scores = gaussian_filter1d(scores, .65, mode="nearest")
    width = max(5, round(24 / step)) | 1
    baseline = median_filter(scores, size=width, mode="nearest")
    deviation = median_filter(np.abs(scores - baseline), size=width, mode="nearest")
    floor, multiplier = {"sensitive": (.055, 1.2), "balanced": (.085, 1.8),
                         "conservative": (.13, 2.5)}[sensitivity]
    thresholds = np.maximum(floor, baseline + multiplier * deviation + floor * .25)
    peaks, _ = find_peaks(scores, height=thresholds, prominence=floor * .3,
                          distance=max(1, round(3 / step)))
    result = []
    for i in peaks:
        boundary = float(times[i] - step / 2)
        if boundary < 2 or boundary > duration - 2:
            continue
        result.append({"type": "structural_transition", "start": max(0., boundary - .5),
                       "end": min(duration, boundary + .5),
                       "severity": float(np.clip(scores[i] / 1.2, .05, 1)), "confidence": .65,
                       "measurements": {"boundary_seconds": boundary, "novelty": float(scores[i]),
                                        "threshold": float(thresholds[i]), "window_seconds": [1.5, 4.],
                                        "sensitivity": sensitivity,
                                        "features": "spectral_chroma" if "chroma" in features[0] else "cached_descriptors"}})
    return result
