# GBR Review: reviewer identity, independent queues and UI update

Please update the existing GBR Review application to support simultaneous reviewers with simple name-based identity and separate queues, and apply the UI changes below. Build and return an installable update ZIP with instructions.

This is for identity, not security. Preserve existing access controls and keep the interface simple.

## Reviewer selection

- When accessing the app without a selected identity, prompt for a name. Users can type any name or tap a previously used name from the database.
- Trim whitespace, reject blank names, and match existing names case-insensitively to avoid accidental duplicates.
- Remember the selected identity on that device, display it clearly, and provide a simple “Change user” control.
- No individual passwords, registration, email addresses or permissions are needed.

## Independent queues and reservations

- Each reviewer must have their own current track, queue position, Back history and skipped tracks. One reviewer’s actions must not change another’s queue.
- For now, only assign recordings that nobody has reviewed.
- Reserve the current recording and a small configurable queue ahead for each active reviewer. These reservations must be exclusive across reviewers, so their upcoming tracks cannot overlap.
- Allocate and renew reservations atomically on the server. Next-track preparation must use that reviewer’s reserved queue.
- Use the existing recording/audio identity for exclusion and reservations, so duplicate FLACs or different YAML provenance cannot bypass them.
- Reservations must expire after inactivity or disconnection, with active clients renewing them. Release unused reservations when changing identity or queue settings.
- Validate reservations and review state again at submission. Handle expired reservations or stale submissions clearly without overwriting another review.
- If all remaining tracks are temporarily reserved, distinguish that from “all tracks reviewed.”

## Database and migration

- Store a stable reviewer ID and display name, and attribute every new review to that reviewer.
- Preserve attribution through corrections and review history. Include reviewer identity in relevant exports and analytics views.
- Add a one-time, transactional migration attributing every existing review and historical review revision to **Simon**.
- Make the migration safe to rerun: it must not duplicate Simon or overwrite identities assigned to later reviews.
- Back up the database before migration. Preserve all existing scores, audio/YAML links, archive state, publishing flags and history.
- Back should edit only the selected reviewer’s own previous review.

## Future design note — do not implement now

Once every recording has at least one review, we may allow reviewers to assess recordings reviewed by other people but never previously reviewed by themselves. Independent ratings could then be aggregated to improve scoring.

Keep the data model compatible with that future workflow: distinguish a reviewer’s corrections from another reviewer’s independent assessment. Do not implement second-pass assignment, aggregation, consensus scores or changed archive-scoring rules in this update. When the first pass is complete, show that clearly and stop assigning tracks.

## Review UI and scoring guidance

Replace the review-row labels and add concise guidance:

| Row | Guidance |
| --- | --- |
| **Audio Quality** | Clarity, artifacts and glitching |
| **Composition** | How closely does it match the intended cluster/genre? |
| **Consistency** | Is it one unified song from start to finish? |
| **Realism** | Would the average person believe it was made by a human? |

Show the intended cluster/genre alongside Composition. Keep other generation settings hidden. If that metadata is missing, show “Genre not supplied” rather than inventing it.

Add these rating anchors to their respective rows:

| Row | 0 | 3 | 5 |
| --- | --- | --- | --- |
| Audio Quality | Pure electronic noise | Clear, but rough | Exceptional |
| Composition | Totally unrelated | Related spin-off genre | Exact match |
| Consistency | Bull in a china shop | Consistently inconsistent | One unified song |
| Realism | A baby would know | My mum wouldn’t know | Is this a trick song? |

Place each row’s guidance directly beneath its label. Keep the six wide 0–5 buttons, with short anchor captions beneath 0, 3 and 5, aligned with those buttons. Guidance must be visible on mobile without hovering or opening a tooltip. Adjust wording or layout where necessary for readability while preserving the meaning and informal tone. Leave 1, 2 and 4 as intermediate scores.

Realism means believable **human production**, including deliberately electronic, synthetic or chiptune music. It does not mean acoustic instrumentation or a naturally human-sounding voice.

Remove these unnecessary UI elements:

- “Four ratings. One listen.”
- “Saves after your fourth choice.”
- “Listen. Rate. Next.”
- The spectrogram zoom slider.
- The playback-speed selector.

Preserve automatic submission after the fourth rating, autoplay, wide mobile rating buttons, and the existing default playback speed. Retain useful spectrogram viewing and seeking.

## Validation and delivery

Preserve Fast mode, keyboard scoring, next-track preparation, mobile HTTP access and support for the renamed `review` package, alongside the behaviours specified above.

Test two simultaneous clients, exclusive queue reservations, duplicate-audio exclusion, reservation expiry/reconnection, isolated Back/Skip/filter behaviour, stale submissions, reviewer attribution, and migration reruns.

Check the revised layout at desktop and phone sizes, ensuring the guidance stays readable and rating buttons remain easy to tap.

Return the update ZIP, concise installation/usage instructions, and a separate list of remaining gaps. Clearly distinguish tested behaviour from physical Windows/phone checks that remain outstanding.
