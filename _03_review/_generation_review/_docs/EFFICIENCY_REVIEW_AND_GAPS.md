# GBR Review — efficiency review and remaining gaps

Reviewed baseline: the application built in this conversation, including the 1.0.1 analysis/launcher fixes, and your supplied `GBR-Review-Autoplay-Patch(1).zip`. You confirmed clean operation and verified SQL output. This update preserves that review/archive model and your autoplay behavior.

## Findings and implemented improvements

| Finding | Change in version 1.1.0 |
| --- | --- |
| Large one-step flux/level thresholds miss quieter instrumentation, harmony and arrangement changes. | Added short/long-window structural novelty using spectral distribution, pitch classes, energy and stereo descriptors, with an adaptive local threshold and three sensitivity settings. |
| Generic energy/anomaly candidates can consume the listening budget before musical transitions. | Opening/ending and the strongest section changes now receive priority in Fast, Quick and Standard sampling. Next point plays local context. |
| Four mouse/touch choices and starting each track add repeated friction. | Preserved autoplay; added remembered modes and sequential keyboard scoring. Fast offers a ten-second overview. |
| Selecting a new track, fetching its preview and rendering snippets all occur at advancement. | Added a preview in the queue response and background preparation of the reserved next recording. Removed a duplicate mode/media load on advancement. |
| Queue selection reads large metadata fields for every eligible candidate. | The common queue path avoids those joins/JSON loads; metadata filtering is added only when needed. |
| Duration/detector tuning previously required decoding every recording again. | Cached retuning appends new event/sample plans while retaining measured values, review history and the existing preview assets. Full reanalysis remains available for richer new features. |
| Two keyboard handlers conflict after combining the interfaces. | Consolidated shortcuts. N skips; S selects Standard. A committed review is protected from resubmission while next-track loading recovers. |
| Certificate setup adds unnecessary work for ordinary LAN browsing. | Start-Mobile automatically enables password-protected HTTP, prints real phone URLs and generates an access code when no password exists. Existing passwords are retained. |

The structural detector compares average descriptors across 1.5- and 4-second windows, smooths the novelty curve and uses local median/MAD thresholds, prominence and three-second separation. Fresh analysis adds 72 spectral bands and twelve pitch classes. Antiphase stereo no longer cancels the frequency-analysis signal. Old caches can use the detector with their original, less detailed descriptors.

## Expected efficiency and measured scope

Your current **10–20 seconds per track** implies approximately:

| Remaining tracks | At 10 seconds each | At 20 seconds each |
| --- | --- | --- |
| 1,000 | 2.8 hours | 5.6 hours |
| Additional 5,000 | 13.9 hours | 27.8 hours |
| 6,000 total | 16.7 hours | 33.3 hours |

These figures exclude breaks and extra listening. Fast reduces the listening budget to ten seconds; autoplay, keyboard scoring and preparation reduce handling time. If ten seconds already represents the material you need to judge, the achievable saving will be mainly interaction overhead. **No end-to-end seconds-per-track improvement is promised without timing your actual workflow.** Four reliable human judgments still require listening and attention.

Synthetic Linux/Python 3.12 benchmarks:

| Check | Earlier path | Updated path |
| --- | --- | --- |
| Queue selection, 1,000 records | 12.81 ms median | 3.33 ms median |
| Queue selection, 6,000 records | 89.25 ms median | 20.05 ms median |
| Synthetic 90-second mono FLAC: full analysis vs cached retune | 0.425 s full analysis | 0.016 s median retune |

Queue figures use seven warmed selections and roughly 4.2 KB of metadata per record, with media availability stubbed. They exclude network, file-access and audio-rendering costs. Retune figures use three warmed runs at 16 kHz/PCM24; full analysis excludes indexing. These results demonstrate code-path savings and are **not measurements of your Windows machine, songs or Wi-Fi**.

Suggested workflow: retune existing caches; use Balanced initially; enable Autoplay; select Fast; enter four scores with number keys. Use Next point or Quick/Standard/Full for uncertain tracks. Use Skip to postpone a decision. For the later 5,000 recordings, initially add a representative folder batch, inspect its score distribution and listening effort, then decide whether to add the rest. PCM deduplication already avoids repeat reviews of identical recordings. There is no automatic judgment of whether an unreviewed recording is worth importing.

## Remaining gaps and issues

1. **Corpus calibration is outstanding.** The attachment supplies UI changes, not example songs with expected transition times. Tests detect quiet synthetic changes near 18/36 seconds and suppress steady pulsing false boundaries. This does not establish recall/precision on your songs. Representative missed boundaries, gradual transitions and intentional drops are needed for measured further tuning. Sensitive can recover weaker changes while adding false points.
2. **Sampling omits material.** Five two-second Fast excerpts provide limited context for composition, consistency and realism. Some transitions or defects will lie outside them. Promote uncertain decisions to longer listening. Ratings remain entirely human; no silent auto-scoring or auto-rejection was added.
3. **Existing caches have fewer descriptors.** Cached retuning works with 1.0.1 features, but finer spectral/chroma detection requires full Reanalyse audio. Reanalyse takes longer; it is not necessary merely to enable Fast. Historical analysis/cache files accumulate; automatic retention/cleanup remains absent.
4. **Actual latency is unmeasured.** The supplied changes contain no timing trace separating listening, queue responses, snippet rendering, archive I/O and network delay. A slower disk, archive verification, first-time analysis and Wi-Fi may still dominate. The serial archive worker and original-byte verification remain deliberate safeguards.
5. **Physical device/platform checks remain.** Backend and browser validation passed on Linux. Native Windows execution, real phone LAN reachability, iOS/Android lock-screen playback and Bluetooth were not tested. The nonsecure HTTP browser test used request forwarding to a temporary local server because this environment restricts direct private-IP connections. Authentication/remote restrictions were separately tested through simulated remote clients.
6. **Browser autoplay and PWA rules remain.** Autoplay can be denied until user interaction; the UI shows a tap-to-play message. Ordinary LAN HTTP works for the web UI without a certificate, but optional service workers/PWA installation require a secure origin. The generated LAN access code is password protection, not traffic encryption. Use a trusted network or optional HTTPS when appropriate.
7. **External analytics mode enumeration may need an update.** SQL/export consumers that restrict mode values to Quick/Standard/Full must accept `fast`. Existing ratings, bucket calculation, provenance and analytics views are retained. Your external importer was not supplied for integration testing.
8. **Only the supplied local modifications were reviewed.** The combined update includes the autoplay ZIP. Any other local code edits are not represented in the attachments; the installer backs up replaced files for comparison/recovery.

## Validation delivered

**61 backend tests passed**, including byte/provenance preservation, cached retuning without media decoding, subtle boundaries, antiphase stereo, read-only prefetch/reserved order, pinned sample revisions, tuning during active analysis, mobile code reuse and remote access restrictions. Desktop/mobile Chromium checks passed with no application JavaScript errors, covering scoring, corrections, selection/A-B, autoplay/Fast, keyboard actions, WAV fallback and recovery after a saved review's next request fails. Installer checks cover both package names, unchanged data/configuration, running-process refusal, corrupted-payload refusal and rollback.

Primary background references: [AudioLabs/FMP novelty-based segmentation](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C4/C4S4_NoveltySegmentation.html), [MDN secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts), [MDN media play/autoplay behavior](https://developer.mozilla.org/en-US/docs/Web/API/HTMLMediaElement/play). These explain the methods/browser constraints; the performance and validation figures above come from this update's local tests.
