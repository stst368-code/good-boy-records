# Editing How it’s made

Edit `tour.yaml`, then run `python _96_website/build.py`. The normal website workflow validates and publishes the result on a push to `main`. Prose is plain text: HTML is never executed. Nothing in this guide runs a model or changes the production database.

## Structure and presentation

- `stages` is the ordered wheel. Change a title, colour or summary here; add/remove/reorder stages without editing JavaScript. Keep IDs stable because they are public deep links.
- Each stage has an `entry` and ordered `nodes`. Each node supplies `title`, `summary`, `body` and `next`. `kind: decision` changes its appearance; labelled outgoing edges show its alternatives. `kind: output` marks a hand-off.
- `next: [{to: encode, label: Native}, {to: comfy, label: ComfyUI}]` draws a split. Multiple inputs can join at the same target. Cycles are rejected; a `stage-link` block represents returning to an earlier stage. Every node must be reachable from `entry`.
- `topics` holds the supporting deep dives beneath the wheel. `short` is the button label. A `topic-link` block can open one from a step.
- Presentation lives in `_how_its_made/making.css`; data, rendering and build validation are separate. Desktop density is shared with the player via `display.js`.

## Body blocks

```yaml
body:
  - type: heading
    text: A useful subheading
  - type: paragraph
    text: A short explanation, in plain text.
  - type: note
    text: A distinction worth calling out.
  - type: code
    text: |
      sampler_steps: 30
      lyrics: |
        Keep literal newlines.
  - type: links
    items:
      - label: Primary source
        url: https://example.com/documentation
        description: Optional explanation.
  - type: stage-link
    stage: generation
    node: sampling
    label: Explore sampling →
  - type: topic-link
    topic: platform
    label: Explore the platform ↗
  - type: analytics-link
    label: Open the Observatory →
```

URLs may be HTTPS, `showcase:relative/path.mp3` (uses the existing public showcase host), or `media/example.mp4`. Put local media in `_content/_how_its_made/_media/`; only files explicitly referenced in the guide are staged. Images support PNG/JPEG/GIF/WebP; audio supports MP3/FLAC/WAV/OGG; video supports MP4/WebM. Images require `alt`. Audio/video do not autoplay; provide a caption and optional prose transcript. A `poster` image is optional for video. Media URLs with credentials, script/data URLs, symlinks and directory traversal are rejected.

```yaml
- type: media
  kind: image
  src: media/encoder.webp
  alt: A diagram showing caption and lyrics entering the encoder
  caption: Optional description; an animated GIF/WebP works here too.
- type: media
  kind: video
  src: media/alignment.mp4
  poster: media/alignment-poster.webp
  caption: Correcting a word alignment in the preparation page.
- type: media
  kind: audio
  src: media/steps-01.mp3
  caption: One sampling step, with the other controls held fixed.
```

## Controlled listening playgrounds

The sampling playground loads published experiment sets from R2. In the GBR app, open **Generation → Experiments**, expand **Publish comparison to website**, choose a matched set and publish it to the sampling-steps position. The page then offers the published sets, their actual ranges (including 1–100 steps), A/B playback and available generation measurements. It continues working when the studio app is closed and local experiment files are removed. No example audio is invented.

`GBR_EXPERIMENT_CATALOGUE` in `showcase-host.js` points to `gbr/v2/experiments/catalogue.json` in the configured public bucket. Audio and immutable manifests have separate experiment prefixes, outside the normal music catalogue. The browser verifies manifest checksums and rejects unsupported media URLs or malformed parameter grids. Failed loads show a reload control; absent samples remain unavailable.

Add these two properties to an experiment block to bind it to an app publishing position. Each `published_slot` must be unique. Supported `parameter_field` values are `sampler_steps`, `sampler_cfg`, `encoder_cfg`, `top_k` and `duration`:

```yaml
published_slot: sampling-steps
parameter_field: sampler_steps
```

The app discovers these positions from this document’s `tour.yaml`, so authors can add bindings without changing the review interface. A comparison can also be published with a direct link only. The published range, fixed controls and clip URLs replace the block’s fallback when a matching set is loaded. App-published sets are matched against the shared generation ledger; timing and memory data are reported per clip, without filling unknown values with zero.

Hand-authored sets remain supported through the `samples` list. A and B switch one audio player at the same timestamp; switching a paused player keeps it paused. The UI exposes how many settings have actual audio:

```yaml
- type: experiment
  id: sampling-steps
  title: What do 1–50 steps sound like?
  parameter: Sampling steps
  min: 1
  max: 50
  step: 1
  conditions: Same caption, lyrics, duration, seed pair, models, CFG, top-k and sampler/scheduler. Only sampling steps changes.
  samples:
    - value: 1
      audio: media/steps-01.mp3
      label: One step
      notes: Optional listening notes.
    - value: 2
      audio: media/steps-02.mp3
      label: Two steps
```

Numeric experiments can use fractional increments, for example CFG 1.0–2.0 in steps of 0.1. IDs must be unique across experiments. Sample values must lie on that range, without duplicates. Use R2 HTTPS URLs for larger audio banks so media need not live in Git. For manually authored clips, fixed conditions are editorial information: verify their recipes before adding them.

Link directly to a stage or node with `how-its-made/#generation/sampling`. Keyboard arrows move around the wheel; Enter/Space selects. The back button works standalone and when the guide is embedded in the player. Browser tests cover branches, deep links, media, A/B position preservation, missing samples and mobile/4K layouts.
