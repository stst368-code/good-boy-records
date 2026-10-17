"""Read writing catalogues without repairing or modifying their source files."""
from dataclasses import asdict, dataclass, field
import csv
from pathlib import Path, PureWindowsPath
import re
import unicodedata

from .yaml_document import YamlDocument, YamlError


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    severity: str = "error"


@dataclass
class Input:
    path: Path
    song_id: str | None
    variant_id: str | None
    metadata: dict
    issues: list[Issue] = field(default_factory=list)

    def summary(self):
        return {"path": str(self.path), "song_id": self.song_id, "variant_id": self.variant_id,
                "issues": [asdict(i) for i in self.issues]}


def identifier(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value or "")) or int(value) < 1:
        raise ValueError("expected a positive whole-number ID")
    return str(int(value))


def title_key(value):
    return re.sub(r"[\W_]+", "-", unicodedata.normalize("NFKC", str(value)).casefold()).strip("-")


def read_csv(path, required, encoding="utf-8-sig"):
    with Path(path).open(encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path}: missing columns {sorted(required - set(reader.fieldnames or ()))!r}")
        rows = []
        for number, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"{path}: malformed CSV row {number}")
            if any(value.strip() for value in row.values()):
                rows.append({key: value.strip() for key, value in row.items()})
        return rows


class Catalogue:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.inputs = []
        files = [p.resolve() for p in self.root.rglob("*")
                 if p.is_file() and p.suffix.lower() in {".yaml", ".yml"} and p.name != "track-template.yaml"
                 and p.resolve().is_relative_to(self.root)]
        by_name = {}
        for path in files:
            if path.is_relative_to(self.root):
                by_name.setdefault(path.name.casefold(), []).append(path)
        for name, variant in (("track_list.csv", False), ("variant_list.csv", True)):
            required = {"song_id", "filename"} | ({"variant_id"} if variant else set())
            for number, row in enumerate(read_csv(self.root / name, required), 2):
                problems = []
                ids = []
                for field_name, value in (("song_id", row["song_id"]), ("variant_id", row.get("variant_id", "1"))):
                    try:
                        ids.append(identifier(value))
                    except ValueError as exc:
                        ids.append(None)
                        problems.append(Issue("catalogue_id", f"{name}:{number} {field_name}: {exc}"))
                filename = row["filename"].replace("\\", "/")
                candidate = self.root / filename
                if not filename or PureWindowsPath(filename).drive or ".." in Path(filename).parts or not candidate.resolve().is_relative_to(self.root):
                    problems.append(Issue("catalogue_path", f"{name}:{number} requires a relative filename inside _Built"))
                    candidate = self.root / f"invalid-catalogue-row-{name}-{number}.yaml"
                else:
                    matches = by_name.get(Path(filename).name.casefold(), []) if len(Path(filename).parts) == 1 else [candidate.resolve()] if candidate.is_file() else []
                    if len(matches) > 1:
                        problems.append(Issue("ambiguous_filename", f"More than one YAML matches {filename!r}"))
                    if matches:
                        candidate = matches[0]
                self.inputs.append(Input(candidate.resolve(), *ids, row, problems))
        grouped_ids, grouped_paths = {}, {}
        masters = {i.song_id for i in self.inputs if i.variant_id == "1"}
        master_titles = {i.song_id: [i.path.stem, i.metadata.get("song_name", "")]
                         for i in self.inputs if i.variant_id == "1"}
        for item in self.inputs:
            grouped_ids.setdefault((item.song_id, item.variant_id), []).append(item)
            grouped_paths.setdefault(item.path, []).append(item)
            if item.variant_id != "1" and item.song_id not in masters:
                item.issues.append(Issue("missing_master_track", "Variant has no song in track_list.csv"))
            if item.variant_id != "1":
                item.metadata["_master_titles"] = master_titles.get(item.song_id, [])
        for group in grouped_ids.values():
            if len(group) > 1:
                for item in group:
                    item.issues.append(Issue("duplicate_catalogue_id", "Song/variant pair occurs more than once in the catalogues"))
        for group in grouped_paths.values():
            if len(group) > 1:
                for item in group:
                    item.issues.append(Issue("duplicate_catalogue_path", "Several catalogue rows point at the same YAML"))
        registered = set(grouped_paths)
        self.inputs.extend(Input(p, None, None, {}, [Issue("uncatalogued_file", "YAML is absent from both catalogues")])
                           for p in sorted(set(files) - registered))

    def select(self, *, all_inputs=False, songs=(), albums=(), files=(), order="selection"):
        selected, seen = [], set()
        def add(items):
            for item in items:
                identity = (item.path, item.song_id, item.variant_id)
                if identity not in seen:
                    selected.append(item)
                    seen.add(identity)
        if all_inputs:
            add(self.inputs)
        for query in songs:
            matches = [i for i in self.inputs if str(i.song_id) == str(query) or title_key(i.path.stem) == title_key(query)]
            if not matches:
                raise ValueError(f"No song matches {query!r}")
            add(matches)
        for album in albums:
            matches = [i for i in self.inputs if i.metadata.get("album_name", "").casefold() == album.casefold()]
            if not matches:
                raise ValueError(f"No album matches {album!r}")
            add(matches)
        for filename in files:
            path = Path(filename)
            matches = [i for i in self.inputs if i.path == path.resolve() or i.path == (self.root / path).resolve()
                       or len(path.parts) == 1 and i.path.name.casefold() == path.name.casefold()]
            if not matches:
                raise ValueError(f"No catalogue input matches {filename!r}")
            add(matches)
        if not selected:
            raise ValueError("Select --all, --song, --album or --file")
        if order == "filename":
            selected.sort(key=lambda i: (i.path.name.casefold(), str(i.path)))
        elif order == "album":
            positions = []
            for item in selected:
                try:
                    position = int(item.metadata.get("album_position", ""))
                    if position < 1:
                        raise ValueError()
                except ValueError as exc:
                    raise ValueError(f"{item.path.name}: album ordering requires a positive album_position") from exc
                positions.append((item.metadata.get("album_name", "").casefold(), position, item.path.name.casefold(), item))
            selected = [row[-1] for row in sorted(positions, key=lambda row: row[:-1])]
        elif order != "selection":
            raise ValueError("Unknown selection order")
        return selected


class Styles:
    def __init__(self, path, encoding="utf-8-sig"):
        self.rows = read_csv(path, {"ID", "cluster", "style_primary", "template"}, encoding)
        self.by_id = {}
        for row in self.rows:
            if not row["ID"] or row["ID"] in self.by_id:
                raise ValueError("styles.csv contains a missing or duplicate template ID")
            self.by_id[row["ID"]] = row

    def candidates(self, cluster, style):
        return [row for row in self.rows if row["cluster"].casefold() == str(cluster).strip().casefold()
                and row["style_primary"].casefold() == str(style).strip().casefold()]

    def choose(self, cluster, style, template_id=None):
        matches = self.candidates(cluster, style)
        if template_id is not None:
            matches = [r for r in matches if r["ID"] == template_id]
        if len(matches) != 1:
            raise ValueError(f"Choose one template ID for ({cluster!r}, {style!r}); candidates: {[r['ID'] for r in matches]}")
        return matches[0]


def inspect_input(item):
    issues = list(item.issues)
    if not item.path.is_file():
        return None, issues + [Issue("missing_file", "Catalogue YAML was not found")]
    try:
        document = YamlDocument(item.path.read_bytes())
    except (OSError, YamlError) as exc:
        return None, issues + [Issue("invalid_yaml", str(exc))]
    values = document.values
    for name, expected in (("song_id", item.song_id), ("variant_id", item.variant_id)):
        try:
            actual = identifier(values.get(name))
        except ValueError:
            issues.append(Issue("missing_or_invalid_id", f"{name} must be a positive whole-number ID"))
        else:
            if expected is not None and actual != expected:
                issues.append(Issue("catalogue_id_mismatch", f"{name} is {actual}; catalogue expects {expected}"))
    if not isinstance(values.get("title"), str) or not values["title"].strip():
        issues.append(Issue("missing_title", "title must be nonempty text"))
    elif title_key(values["title"]) not in {title_key(v) for v in [item.path.stem, *item.metadata.get("_master_titles", [])]}:
        issues.append(Issue("title_filename_mismatch", "YAML title differs from its catalogue filename",
                            "error" if item.variant_id == "1" else "warning"))
    if not isinstance(values.get("lyrics"), str) or not values["lyrics"].strip():
        issues.append(Issue("missing_lyrics", "lyrics must be nonempty text"))
    if values.get("version") not in (None, "") and not values.get("style"):
        issues.append(Issue("legacy_version", "version was preserved and was not interpreted as style", "warning"))
    return document, issues
