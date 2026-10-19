---
tab: Parameter Lab
title: Parameter Lab
order: 6
---

A lot of AI-generation controls sound enormously important when described in isolation. The useful question is simpler: **can you actually hear what they changed?**

This lab takes one known-good song and regenerates it as controlled matrices. Prompt, lyrics, duration and seed pair stay fixed while only the selected axes change. Move the patch lead between sockets while the track is playing and the player jumps to the same timestamp in the alternate generation, with a short crossfade.

The filename before the double underscore is deliberately ignored. A matrix cell is identified only by its suffix, such as `__euler_simple.mp3`, so the experiment remains valid if the source song or version name changes.

[matrix-suite]

### What the graph means

For numeric matrices such as CFG × steps, the graph marks the exact selected coordinates against every generated cell. DiT × text encoder is categorical. Sampler × scheduler shows a **normalised explanatory schedule shape**, not fabricated internal sigma values. Literal ComfyUI sigma traces would require instrumentation inside the generation workflow and are intentionally not invented here.

### Controlled comparison

These matrices are intended to expose both kinds of result: tiny numerical changes that audibly transform a track, and apparently dramatic model or parameter changes that produce surprisingly little difference. The important part is that the other variables are held constant, otherwise the comparison is just several songs wearing a lab coat.
