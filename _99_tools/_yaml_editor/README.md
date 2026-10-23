# YAML editor

Open the YAML editor tab in GBR. Scan a directory, select files, build edit
operations, preview the exact diffs, then save. Files with errors can be excluded
explicitly. The last 20 completed saves retain byte-exact undo backups;
interrupted saves remain available for recovery.

**Add missing fields from template** reads the default
`_01_writing/_built/track-template.yaml` or another selected YAML. Leave the field
list blank to add every missing template field, or enter a subset such as
`explicit, duration`. New fields keep the template values unless defaults are
supplied. Enable the default-text option for a shared value such as `no`, or
enter per-field JSON such as `{"explicit":"no","duration":360}`. Existing values
are never overwritten. Template ordering is enabled by default and can be
switched off. Yes/no text is safely quoted in YAML.

**Reorder fields** uses a selected template's key order by default; manual lists
are also supported. Reordering does not add missing fields. Fields absent from
the template keep their positions. Comments, multiline lyrics, quotes, BOMs and
line endings survive edits outside the fields expressly being changed.

Set/add, rename, remove, catalogue ID repair, duplicate-key repair and individual
text repair remain available. Preview tokens freeze the template and edits.
Source edits after preview block saving. Undo refuses to overwrite subsequent
external edits. Generation never uses this editor to change writing originals.

## Historical generation cleanup

`py -3.12 _97_app/run_gbr.py cleanup-generations preview` inventories indexed
recordings in the configured input/archive folders and `_02_generation/_generated`.
It prints every rename/YAML diff and a saved plan path. Use `--root PATH` (repeatable)
for a smaller batch, `--template PATH` for another YAML template, and `--model-map PATH`
for explicit aliases, e.g. `{"text_encoder":{"exact old name":"exact current name"}}`.

Close GBR before `cleanup-generations apply "PLAN_PATH"`. Errors stop apply unless
`--skip-errors` explicitly excludes the failed recordings. `cleanup-generations undo
"PLAN_PATH"` restores the batch, including after an interruption; changed files or
database rows are retained rather than overwritten. Once satisfied, `cleanup-generations
forget "PLAN_PATH"` removes the rollback journal. The audit stays in the shared DB;
there is no extra database or permanent duplicate audio.

Names become `catalogue-title__pcmhash12.flac` with corresponding YAMLs. Template
fields/defaults are added and ordered, extra fields/comments retained, known legacy
field aliases renamed, and literal `\n`/`\r\n` plus obvious `/n` separators repaired
only within lyrics/captions. Existing line breaks, URLs, BOMs and line endings survive.
Generation time is populated only from a recorded generation timestamp, never guessed
from file modification time. Exact known checkpoint basenames gain `.safetensors`;
precision-only native model descriptors stay as recorded unless explicitly mapped.

Audio bytes/embedded original tags, review IDs/revisions/tags/reasons, frozen requests,
source snapshots and writing inputs are preserved. The same DB records receive current
paths, effective metadata and verified artifact checksums. Unindexed files, conflicting
IDs/aliases, duplicate active copies and missing companions are reported for repair/scan,
not deleted or assigned speculative metadata. Original embedded tags remain provenance;
the completed sidecar is the effective metadata used by Review.

