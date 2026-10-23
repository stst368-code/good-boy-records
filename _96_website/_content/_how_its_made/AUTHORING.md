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

The public 1–50 step experiment intentionally has **no invented audio**. Add real matched clips to its `samples` list; slider positions without a clip remain unavailable. A and B switch a single audio player at the same timestamp; switching a paused player keeps it paused. The UI exposes how many settings have actual audio.

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

Numeric experiments can use fractional increments, for example CFG 1.0–2.0 in steps of 0.1. IDs must be unique across experiments. Sample values must lie on that range, without duplicates. Use R2 HTTPS URLs for larger audio banks so media need not live in Git. The fixed conditions are editorial information: the guide cannot prove that an audio file was generated under those controls, so verify each recipe before adding it.

Link directly to a stage or node with `how-its-made/#generation/sampling`. Keyboard arrows move around the wheel; Enter/Space selects. The back button works standalone and when the guide is embedded in the player. Browser tests cover branches, deep links, media, A/B position preservation, missing samples and mobile/4K layouts.
