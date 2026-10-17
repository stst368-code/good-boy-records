# GBR Review 1.2.0 — specification review and remaining gaps

The supplied multi-reviewer/UI specification is implemented as an in-place update. No requested first-pass feature is intentionally left unfinished. Second-pass assignment and aggregation remain deferred as the document requires.

## Requirement coverage

| Requirement | Delivered behavior |
| --- | --- |
| Simple identity | Choose an existing name or type one; trim/reject blanks; case-insensitive matching; stable ID, device memory, visible name and Change user. Existing shared access controls remain. |
| Independent work | Separate current item, order, filters, previous review and skips for each reviewer. Back/corrections and Selection Reassess edit only that person's own reviews. |
| Exclusive reservations | Server transactions reserve current audio plus configurable ahead (default 2). Decoded audio identity prevents overlap through duplicate FLACs or different YAML. |
| Active/expired queues | Ten-second renewal; default 180-second lease; identity/filter changes release owned reservations. Next preparation uses the owned queue. Expiry/conflicting submission disables stale rating and offers Resume queue. |
| First pass only | Never-reviewed audio only; temporarily reserved work and completed first pass have different messages. Completion stops assignment. |
| Attribution/migration | IDs/names in relevant datasets/views; pre-migration SQLite snapshot; transactional/idempotent Simon attribution for legacy reviews/history; scores, provenance, archive and publishing preserved. |
| Future-compatible model | Distinct assessment ID per recording/reviewer, with correction revisions. Existing global revision and archive rules retained; no second-pass or consensus policy enabled. |
| Guidance and anchors | All four specified row labels/questions; actual intended cluster/genre or Genre not supplied; 0/3/5 captions beneath corresponding buttons; electronic/chiptune human-production note. |
| Simpler controls | Three redundant slogans and zoom/pan/speed controls removed. Default 1× playback, Spectrum viewing/seek and range selection remain. |
| Existing behavior | Fourth-score save, autoplay, Fast/keyboard, next preparation, cached tuning, HTTP mobile and renamed `review` package retained. |

## Tested behavior

**81 backend tests passed on Python 3.12/Linux.** Tests include simultaneous server allocation, exclusive audio reservations, duplicate provenance, expiry and reconnect, isolated Back/Skip/filter/name changes, stale submissions, review/history exports, first-pass completion, migration snapshots, preservation, reruns and injected-failure rollback. Existing archive, exact-byte cross-filesystem transfer, authentication and analysis tests remain passing.

**Three Chromium integration scripts passed.** They cover independent browser storage/identities, separate queues, remembered names, changes of reviewer, Back/Skip/filter isolation, stale rating recovery and attribution, as well as existing playback/autoplay/Fast/keyboard/WAV/selection/A-B and next-request failure recovery. Layout checks at 1280 px desktop and 390/320 px phone widths confirm visible guidance, anchor alignment, no horizontal overflow and 24 rating targets at least 44 px wide and 48 px high. No application JavaScript errors were observed. Python compilation and JavaScript syntax checks passed.

The updater is tested on copied temporary installations under both `gbr_review` and `review`: payload integrity, configuration/data/media preservation, consistent SQLite snapshot, wrapper boot, active-app refusal, corrupt-payload refusal and rollback after an injected write failure. The backend suite also passes after installation into the renamed package.

Nonsecure HTTP login/playback uses a local request-forwarding harness to emulate a non-localhost browser origin. All audio/database fixtures are synthetic. These checks do not prove behavior on the user's physical Windows machine or phone.

## Remaining gaps and operational limits

1. **Physical Windows/phone acceptance remains.** Native `.bat` execution, actual Windows source/archive drives and filesystem behavior, firewall/LAN reachability, physical iOS/Android autoplay, lock-screen/Bluetooth controls and sleep/wake have not been tested here. `docs/TESTING.md` supplies a short acceptance sequence. Ordinary phone UI access still uses HTTP without certificate setup.
2. **Real-corpus calibration remains.** No real FLAC/YAML songs were attached to this specification. Intended cluster/genre parsing and transition accuracy should be checked on representative outputs. Signal detection can omit or over-identify musical changes; sampling cannot establish quality throughout omitted material.
3. **Large-corpus multi-reviewer throughput is unmeasured.** The historical efficiency benchmarks are retained as version-1.1 measurements. Real 1,000/6,000-track storage/network throughput with concurrent reviewers was not benchmarked. Analysis/archive work still uses one background worker; cache retention has no automatic cleanup policy.
4. **External consumers need compatibility checks.** The existing GBR importer was not supplied. Attribution fields are additive, dataset JSON now reports schema version 2, and custom queue/scoring API clients must send `X-GBR-Reviewer` after selecting a name. Check importer field assumptions and avoid counting corrections or inherited YAML provenance as independent ratings.
5. **Identity and connectivity limits are intentional.** Names provide attribution without individual permissions. Anyone already allowed into the app can choose any name. The same name on multiple devices shares one queue. Clearing device storage prompts name selection again. There is no offline review/draft synchronization; a suspended or disconnected client can lose reservations and must resume before saving stale scores.
6. **Readable phone guidance needs vertical scrolling.** Captions use adjacent empty score-column space, aligned with 0/3/5, to avoid breaking words. All four rows may exceed one phone screen; the UI preserves wide buttons and visible guidance rather than hiding text in tooltips.
7. **Database rollback needs matching state.** Once migrated, restoring only pre-update code is incompatible. Restoring an old database discards newer reviews and does not undo archive moves. Preserve the current database/archive and use matching snapshots for a deliberate rollback. The installer does not automatically replace user data.

The deferred second-pass/aggregation feature is outside this update's scope, not a failed requirement. Publishing intent remains shared and archive scoring retains the existing equal-weight mean and half-up bucket rules.
