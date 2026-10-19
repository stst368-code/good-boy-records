---
tab: Music Workflow
title: Native MiniMax generation workflow
order: 4
---

The GBR app selects YAMLs, validates song/variant IDs and style pairs, then freezes
copies and effective controls before dispatch. Caption and lyrics become text
conditioning; the native MiniMax diffusion pipeline samples the music from it.
Workers may run locally, on dedicated RunPod or serverless. Cached conditioning
is reused only when its identity and model settings match.

Each completed generation contains a FLAC, its YAML recipe and a generation report.
Writing originals retain field order, formatting and literal lyric blocks. The
shared database records controls, hardware, timings and available memory/cost data.

Review combines four scores, optional positive/negative tags, a Reason, technical
checks and publish/bookmark/reject controls. It moves verified bundles out of the
generated inbox into the review archive. Earlier public recordings made with
ComfyUI retain their original generation metadata.
