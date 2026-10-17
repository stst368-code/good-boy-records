# Bulk YAML editor design

This tool is planned; no bulk editing command is enabled by this increment.
It will use the shared platform UI and database while remaining a reusable tool
under `_99_Tools`.

Choose directories, recursive inclusion, filename filters or catalogue selections.
Show a preview table of file, song/variant ID, selected values and validation
issues. Support adding/removing fields, renaming keys, changing values, deliberate
reordering, and ID repair against a catalogue. Path-based operations must separate
renaming a key from changing its value. Field removal and reordering require an
explicit operation: normal value edits preserve unrelated structure.

Build a reusable operation recipe, then preview exact diffs and per-file errors.
Detect rename collisions, duplicate keys, ambiguous selectors, unsupported aliases
or tags, and stale source hashes. A preview records a hash of every source file;
apply refuses files changed since preview. Exclude invalid files or cancel the
whole operation before writing.

Use syntax-aware byte patches and atomic per-file replacement. A batch journal
records before/after hashes, operation order, backups and completion state so an
interrupted apply is recoverable. Undo restores original bytes only when the
current file still matches the recorded post-edit hash. Multi-file edits are not
one filesystem transaction, so the UI must show partial completion clearly.

Default to writing edited copies to a chosen destination. Editing the writing
catalogue in place is a separate explicit apply choice with backups. This tool's
deliberate source editing does not change the generation runner's absolute rule
that original input YAMLs remain untouched.
