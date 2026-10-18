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
