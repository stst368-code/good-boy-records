---
tab: Tracks and Storage
title: Track Storage, Naming and the Rest
order: 8
---

Getting myself to the point of writing this very page, I've generated more than 5,000 tracks.

The cassette on this page holds 10 tracks, with 6 versions on display, meaning less than 2% of
the songs generated actually made it through to your ears.

Throughout the process, continual refinement of track names was required, at first I just used
simple default names _ComfyUI_001 and so on... that did not last long.

Using ComfyUIs built in string management nodes and code within my .py orchestrator the final format
of the unreleased track names would bear all the relevant parameters.

Below is an example:

the-ball-beyond-the-gate-metal-death-metal-08_ECFG-1.60_TOPK-25_DUR-60s_CFG-1.60_STEP-33_SAMP-euler_SCHED-simple_ESEED-3332663505_SSEED-9959943502.flac

Doesn't roll off the tongue, but it gives all the distinction that'd ever be required when passing
the file name through .py scripts to analyse and sort.




Once a song is set for release, it moves into the Showcase.

The Showcase is our cassette, it has 11 sub-directories within it, one named after each song.
(Odd... I only see 10 different songs on the cassette... who has the power to investigate?)

Each song has 7 subdirectories within it, which combines the songs name and the genre variant.

With one extra (the 7th) tagged as '-atr', all-the-rest, this is the unreleased tab you can find
on each track, which contains a few other generations I thought were fun enough to make available
for my huge horde of eager fans.

Within each songs genre specific directory we have 5 files:
 - Two audio files:
	- song-name.flac - for the lossless and high bandwidth inclined
	- song-name.mp3 - for the low bandwidth afflicted
 - One album cover
	- song-name.png - the art work as seen in the player
 - One track yaml
	- song-name.yaml - 'the' song, contains all the information that would be required replicate production
 - One lyrics json
	- song-name.lyrics.json - the millisecond synced per word lyrics output from the Demucs/WhisperX workflow
