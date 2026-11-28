# Correct lyrics on the song timeline

**Follow** keeps the playhead in the zoomed view during playback. Switch it off
to inspect another part of the song. The readable word strip below the lanes
shows full words even when their timing blocks are only a few pixels wide.

## Direct editing

- Double-click a word: its end moves to the next timed word's start, and an
  inline text box opens. Type the replacement, then Enter to save or Escape to
  close the text box. The timing change can be undone separately.
- Double-click empty space in the lyric lane to insert words there. You can
  paste a phrase; its words share the available interval. **Words at playhead**
  and Enter on the focused timeline also open the text editor.
- All changes stay in the song version and local draft. The source audio and
  original writing YAML are preserved. Imported words follow cuts and repeats
  in the same way as original words.

## Select, nudge and repair a section

Ctrl-click (Command-click on macOS) adds or removes a word. Shift-click a second
word to select the timed words between it and the first. Selection works in the
timeline, readable word strip and full lyric text.

With the timeline focused:

- Left/Right moves the selected words by 0.05 seconds; Shift uses 0.25 seconds.
- Alt + Left/Right changes the selected words' length.
- Space pauses playback and selects the previous word during the first 20% of
  the current word; otherwise it selects the current word. Space again plays.
  Spaces typed inside text fields remain ordinary text.

The selection inspector has **Lay selected end to end** and a shared length
control. Resizing one selected word's edge gives every selected word the same
length. Group moves stop at the source audio boundaries.

Moving or resizing words checks their timings. **Checked by me** makes those
timings fixed anchors during alignment/refinement. Uncheck them if a new pass
should change them. Mark unsung words **Not sung** rather than deleting source
tokens, so earlier versions remain recoverable.

## Highlight part of a word

Select the word, open **Highlight parts / emphasis**, and enter its exact
spelling separated by pipes, e.g. `b|uil|t`. Set the parts, then adjust their
relative shares. Shares `1, 8, 1` spend 80% of the word's time on `uil`.

Parts keep their proportions when the word is resized. Saving annotations can
reuse an existing master; republish the updated lyric data to send the part
timings to the public player. The website animates each part without changing
the spelling of the word.

## Multiple languages

Writing's Song base and assistant drafts accept comma-separated language codes,
e.g. `en, ro, de`. The first is the default. Add inline markers to switch the
language for all subsequent words, including across lines:

```text
[verse]
{en} a house full of stories
{ro} o casă plină de povești
{en} another chapter begins
```

Markers stay in writing/provenance but are removed before MiniMax/ComfyUI sees
the lyrics. Existing unmarked generation text is passed through unchanged.
Preparation also lets you set the language of selected words and enter markers
when inserting a phrase.

The controller accepts up to 64 languages, with no two-language or ten-language
limit. Alignment uses the language-specific models available in the installed
WhisperX version, loading one at a time. An unsupported language produces a
visible warning while other languages can complete. Japanese/Chinese character
results are mapped back to the supplied tokens.

## Refinement after corrections

Correct a few anchor words around the troublesome section, leave them checked,
then choose **Refine around corrections**. The current lyric version is saved
and used as the exact transcript. The pass aligns unchecked words in bounded
windows between checked anchors and language changes.

Existing verified vocal stems are reused, locally or on a paired preparation
PC; this skips separation and Whisper transcription when those stems are
available. The original alignment also gets a known-text pass for missing or
uncertain words.

Automatic results never replace edits made after the pass was submitted.
Refinement is an alignment aid, not a guarantee: unsung text, overlapping
vocals, long musical gaps and extreme delivery can still need manual correction.
No model weights are trained from your edits.
