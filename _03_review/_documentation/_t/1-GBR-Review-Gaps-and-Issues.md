# Gaps, issues and implementation decisions

The application implements the four phases of the supplied document: core review/archive, automated sampling/analysis, mobile and publishing selection, and structured analytics access. The items below distinguish specification ambiguities from practical limits and unverified integrations.

## Decisions required by ambiguities in the document

1. **“Identical FLAC” versus “identical recording”.** A raw FLAC SHA-256 changes when tags or compression change. The build stores both the exact container hash and a decoded PCM hash. Queue deduplication and shared audio assessment use PCM identity; byte-exact move verification uses the container/YAML hashes.

2. **Different YAMLs for the same recording.** The document requires separate provenance without duplicate audio but does not specify sidecar naming or whether the same audio should have different ratings. The build archives one FLAC, keeps one sidecar per distinct YAML hash, and shares the human assessment across identical audio. Additional YAMLs use `__p<YAML-hash>` names. `review_inherited` identifies shared assessments in exports. All raw metadata and source locations are retained.

3. **Metadata and genre contract.** No sample FLAC/YAML pair or formal embedded-tag schema was attached. The implementation supports adjacent `.yaml`, `.yml`, `.flac.yaml`/`.yml`, common structured FLAC tags, and the listed MiniMax parameter names. Sidecar values take precedence. Missing title falls back to the filename; missing intended genre is shown as **Genre unspecified**. Unknown nested metadata remains queryable as JSON, but cannot always be flattened or interpreted automatically. Real generation output should be checked before attributing analytics to fields recovered from nonstandard tags.

4. **One reviewer versus several devices.** The document specifies one shared queue/database and mobile access, but no separate users, reviewer identities or independent assignments. The build uses one persistent `main` reviewer session. Stale concurrent submissions are rejected. Independent multi-reviewer reliability studies would need reviewer accounts and assignment logic.

5. **Sampling coverage and assessment meaning.** “Full” does not establish that a person heard the entire recording, and snippets cannot demonstrate stability in every omitted passage. The build records actual played modes, approximate played seconds, selected ranges and a completion flag. It does not force a minimum listen or automatically infer human quality from the omitted audio.

## Practical limits

6. **Detector accuracy is not validated on your music.** Defect, repeat and structure events use signal heuristics. They can flag intentional distortion, silence, transitions or stereo effects. They are not certified judgments, genre-aware models or calibrated probability estimates. True peak and loudness range are engineering estimates. Source metadata is required to relate a suspected failure to a sampler/scheduler; audio alone cannot prove that cause. Human ratings remain independent.

7. **Mobile background/PWA constraints.** Media Session and continuous audio playback are implemented. The operating system can still suspend or stop a browser; physical iOS/Android, Bluetooth and car controls have not been tested here. Full PWA installation on a LAN generally requires a trusted HTTPS origin; plain HTTP LAN access still supports the browser UI. HTTPS certificate provisioning is not automated. The desktop host must remain awake and reachable.

8. **Platform packaging and device verification.** Delivery is a runnable Python application with source, static assets and Windows/Linux launchers. It is not a bundled Python runtime or Windows `.exe`. First installation downloads dependencies. Forty backend tests and desktop/mobile Chromium checks passed on Linux, including transfer between two distinct filesystems. Native Windows launch scripts, NTFS/exFAT crash behaviour, macOS and physical phone audio integrations remain to be checked on those systems.

9. **Existing GBR analytics importer is external.** SQLite views, CSV/Parquet/JSON exports, event/review history exports, SQL examples and a publishing manifest are provided. The existing GBR repository/schema was not supplied, so automatic merge into that site’s current analytics data pipeline has not been implemented or verified. The review database stays separate from `data/analytics.sqlite`.

10. **Storage and long-file throughput.** Derived previews/playback assets and historical analyses are retained. Reanalysis can grow storage, and there is no automatic cache-retention policy. One serial worker limits contention; very large libraries or multi-hour recordings have not been benchmarked. Full playback does not wait for analysis. Missing current preview caches regenerate at startup.

11. **Archive relocation and irrecoverable external deletion.** Current file locations and journal plans are absolute. Changing drive letters or moving an established archive needs a controlled database/path migration; the UI blocks casual archive-root changes after archiving begins. If an original is externally overwritten/deleted before a verified copy exists, hashes and SQLite cannot recreate its audio. Interrupted copies, destination conflicts and failed source deletions are handled without discarding completed reviews.

## Validation evidence

- **40 backend tests passed.** Covers all half-up thresholds, exact FLAC/YAML preservation, cross-filesystem copy/verification, duplicate/provenance records, reused filenames, invalid/missing/embedded YAML, corrupt audio, collisions, failed/recovered moves, interrupted journals, stale reviews, publishing history, authentication/CSRF/host checks, backup, loudness matching, sampling and CSV/Parquet/JSON exports.
- **Desktop/mobile Chromium integration passed.** Checks continuous navigation playback, waveform/manual range/spectrogram controls, 24 rating buttons, 50 px mobile target height, automatic save/advance, Back correction, skip, selection status, blind A/B playback and preference storage. No application JavaScript errors were observed.
- Tests used synthetic audio fixtures. No real song corpus, generation service or publishing service was connected.

Relevant browser/platform references: [MDN Media Session](https://developer.mozilla.org/en-US/docs/Web/API/Media_Session_API), [MDN secure contexts](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts), [Starlette file responses](https://www.starlette.dev/responses/), [SoundFile documentation](https://python-soundfile.readthedocs.io/en/latest/).
