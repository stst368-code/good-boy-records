# Audio analysis and sampling

Analysis runs in one background thread, separately from HTTP requests and review submission. Measurements are cached by audio identity and analysis revision. Scanning does not repeatedly decode unchanged FLACs. Preview responses send at most 4096 peak bins per displayed channel, neutral events and selected listening ranges; full numeric feature data stays on the server.

## Measurements

| Area | Implemented measurement / event |
| --- | --- |
| Peaks | Sample peak, exact full-scale sample count/fraction, 4× polyphase oversampled true-peak estimate, adjacent-sample transient irregularity. |
| Loudness | Original-rate RMS per channel and overall; streaming K-weighting, absolute/relative-gated integrated LUFS; 3-second short-term loudness and gated percentile loudness range. |
| Spectrum | One-second windows every half second, centroid, flatness, normalised positive spectral flux, low/mid/high-band energy, 72 logarithmic spectral bands and twelve-bin chroma (pitch classes). Channel spectra use mean power, preserving antiphase stereo information. |
| Silence | Quiet windows, interior possible dropouts, energy jumps/collapse, potentially abrupt opening/ending. |
| Stereo | Channel RMS balance, Pearson correlation, mid/side width ratio, possible phase cancellation, sudden stereo collapse, divergence. |
| Structure | Multi-window spectral/chroma/stereo/energy section novelty, repeated descriptor patterns, energy extremes and sustained unusual/noise-like windows. |

Frequency analysis retains content up to 24 kHz for higher-rate input; the spectrogram displays 30 Hz through min(20 kHz, source Nyquist). For rates above 48 kHz, spectral analysis is resampled to 48 kHz. Native playback, hashes and peak/loudness measurements retain the original recording rate.

True peak is a useful estimate, not a certified ITU meter. Loudness has been checked against pyloudnorm on synthetic stereo audio. LRA is an EBU-style percentile estimate, not an independently certified compliance implementation. Stereo/mono music is the primary tested use case; multichannel loudness/phase interpretation needs additional reference-channel validation.

Defect/structure detectors are conservative heuristics, not learned musical judgments. A loud distorted metal passage, deliberate silence, a dramatic genre transition or stereo effect can legitimately trigger an event. Confidence values express heuristic strength and are not calibrated probabilities. Audio analysis cannot determine whether an ending was intentionally abrupt or whether a sampler caused instability.

## Event storage and reviewer neutrality

Each event stores an ID, audio ID, analysis ID, type, start/end seconds, severity, confidence, supporting measurements and algorithm/version. Consecutive events of the same type are merged. Analysis revisions are retained, and new analyses create new events and sample plans.

The review-facing preview deliberately removes type, severity, confidence and numeric supporting measurements. Every event is drawn with the same neutral dot. Clicking a dot plays its surrounding passage. Detailed events are available only through SQLite/predefined exports, independent of the rating interface.

## Section transitions

The previous large flux/energy-jump thresholds remain useful anomaly cues but miss quieter instrumentation or harmony changes. Section detection now compares descriptor means before and after each candidate using 1.5-second and 4-second windows. A smoothed novelty curve, local median/MAD baseline, minimum prominence and three-second spacing suppress individual beat fluctuations. The prefix-sum calculation uses bounded linear descriptor storage instead of a full track-by-track similarity matrix. Sensitivity controls the threshold, not the human score.

This supports short drops and longer arrangement changes. Gradual builds, small melodic changes, very brief sections and repeated choruses can still be missed. Boundary precision is approximately the half-second feature hop and smoothing context; markers are listening aids rather than a beat/bar grid. Fresh analysis adds chroma and finer band energies; old caches can be retuned from their existing descriptors.

## Snippet selection

The algorithm constructs candidates from the opening, ending, strongest section transitions, unusual sections, maximum/minimum energy and representative windows, in that priority. It centres each chosen range around the candidate, checks context and overlap, and avoids near-identical middle descriptors where possible. Uniform recordings use non-overlapping fallback coverage rather than repeated selection of the same section.

Fast defaults to five two-second sections, Quick to five four-second sections and Standard to six sections totalling fifty seconds. Up to two structural changes (three for Standard) receive priority after the opening and ending. At most one anomaly candidate then competes for remaining slots. All budgets are configurable. Playback follows the selected sections in chronological order. If a track is shorter than the budget, it plays once rather than duplicating passages to reach the target. Fixed positions are used for opening/ending context and uniform-signal fallback, not as the sole selection method.

All snippets are rendered at the source sample rate/channel count and PCM subtype, with configurable short fade-in/fade-out edges. The source FLAC remains unchanged. A session is one continuous FLAC/WAV file, so it keeps playing without requiring a background timer to seek between snippets. Individual snippet buttons and manually selected passages use the same rendering path.

## A/B loudness matching

Both comparison recordings need completed analysis and measurable integrated loudness. Their labels are randomly assigned A/B without model settings. The shared LUFS target is at most −18 LUFS and is reduced as needed to leave at least 1 dB of estimated true-peak headroom in both recordings. The comparison files are derived playback assets; the originals and their quality ratings are not modified. Relative preferences are stored separately with the chosen generation and target.

## Cache lifecycle

Initial analysis cost depends on duration, sample rate and disk speed. One worker intentionally limits disk/CPU contention and prevents simultaneous archive mutation. Full playback and reviewing remain available while analysis is pending. Failed analysis is logged, and Reanalyse audio retries it.

Changing sampling durations or transition sensitivity schedules cached retuning. Retuning preserves measured loudness/peaks, original files and historical reviews, appends a new analysis/event/segment revision, and references the earlier preview assets. Missing assets trigger full analysis. Full Reanalyse audio additionally rebuilds spectral/chroma features. Playback pins its analysis revision so retuning does not change the ranges in an ongoing listening session. The next track can be selected and rendered ahead without advancing or reviewing it.

Retuning and reanalysis retain historical records. The application does not automatically expire historical analysis or rendered comparison files; storage can grow with repeated reanalysis.

With the server stopped, deleting `data/cache/` removes only derived previews/playback assets. At the next startup, missing current previews are regenerated while full playback remains available; historical measurements stay in SQLite. Do not delete `data/recovery/`, the database, or the archive as part of cache cleaning.
