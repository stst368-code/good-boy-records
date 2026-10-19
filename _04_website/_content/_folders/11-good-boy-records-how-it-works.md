---
title: Good Boy Records — How It Works
order: 11
---
# Good Boy Records — How It Works

> Write a song → describe how it should sound → encode the words → generate the music → listen → keep the good ones → analyse and prepare them → publish them to Good Boy Records.

## The three layers

The system makes more sense when three things stay separate.

### Core generation

The minimum story: caption + lyrics → text encoder → conditioning → sampler/music model → audio.

### Generation harness

YAML jobs, Python scheduling, local/cloud workers and conditioning caching make generation easier to automate and distribute. They are conveniences around the core workflow, not prerequisites for understanding it.

### Good Boy Records

Accepted recordings are analysed, prepared, catalogued and published into the player. Each recording has a generation identity, associated song/variant IDs and review history in the shared database. The public player retains its existing catalogue IDs and share links.

## Multimedia examples

Knowledge articles support embedded media. This is where controlled listening tests can live later.

Example syntax for an audio comparison:

```text
:::audio
src: showcase:path/to/example.mp3
label: 20 sampling steps
caption: Same source, seed and settings; only the step count changes.
:::
```

Images can use normal Markdown image syntax or the richer `:::image` block. The intention is simple: show it, explain it briefly, let people hear or see it, then offer the technical detail.

## Publishing architecture

An explicit catalogue refresh reads the curated showcase into `_04_website/_data/catalogue.json`. Ordinary builds use committed metadata instead of scanning a partial checkout. Media remains on R2 under the existing `showcase/` keys. GitHub Pages serves only the public `_04_website` build, independently of the local app and database.
