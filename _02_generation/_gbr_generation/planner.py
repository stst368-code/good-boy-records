"""Preflight and freeze immutable requests. This module never invokes a GPU worker."""
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import sqlite3
import sys
import tempfile
import uuid
from zoneinfo import ZoneInfo

from .catalogue import Issue, inspect_input, validate_template_format
from .parameters import SEEDS, execution_options, missing_parameters, output_changes, resolve
from .yaml_document import YamlDocument
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from _gbr_layout import manifest_paths, relocated_path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_99_tools" / "_workers_infdit"))
from _gbr_worker.contract import MODEL as NATIVE_MODEL, validate_job


def json_text(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write_new(path, raw):
    with Path(path).open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def publish_new_file(path, raw):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="gbr-output-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass
class CheckedInput:
    item: object
    document: YamlDocument | None
    issues: list
    effective: dict
    provenance: dict
    template: dict | None = None
    planned_runs: list | None = None

    @property
    def ready(self):
        return not any(i.severity == "error" for i in self.issues)

    def summary(self):
        return {**self.item.summary(), "issues": [asdict(i) for i in self.issues],
                "status": "ready" if self.ready else "invalid", "effective": dict(self.effective),
                "provenance": dict(self.provenance), "source_sha256": self.document.sha256 if self.document else None,
                "template": self.template, 'generation_count': len(self.planned_runs) if self.planned_runs is not None else 1,
                'experiments': self.planned_runs[:30] if self.planned_runs is not None else None}


def preflight(inputs, specification, styles=None, template_root=None, require_parameters=True):
    checked = []
    for item in inputs:
        document, issues = inspect_input(item)
        values, provenance, template = {}, {}, None
        if document is not None:
            try:
                values, provenance = resolve(document, specification)
                cluster, style = values.get("cluster"), values.get("style")
                if bool(cluster) != bool(style):
                    issues.append(Issue("incomplete_style_pair", "cluster and style must be supplied together"))
                if cluster and style:
                    if styles is None:
                        issues.append(Issue("missing_style_catalogue", "A cluster/style pair requires styles.csv validation"))
                    elif not styles.candidates(cluster, style):
                        issues.append(Issue("unknown_style_pair", "cluster/style is absent from styles.csv"))
                if specification.get("template_id") or not values.get("caption") and cluster and style and styles is not None:
                    row = styles.choose(cluster, style, specification.get("template_id"))
                    if template_root is None:
                        raise ValueError("Template selection requires --template-root")
                    root = Path(template_root).resolve(strict=True)
                    path = (root / row["template"].replace("\\", "/")).resolve(strict=True)
                    if not path.is_relative_to(root):
                        raise ValueError("Template path escapes the chosen template root")
                    raw = path.read_bytes()
                    caption = raw.decode("utf-8-sig").replace("\r\n", "\n")
                    if not caption.strip():
                        raise ValueError("The selected caption template is empty")
                    validate_template_format(caption, row.get("template_source"))
                    template = {"id": row["ID"], "sha256": sha(raw), "path": str(path),
                                "source": row.get("template_source")}
                    values, provenance = resolve(document, specification, {
                        "cluster": row["cluster"], "style": row["style_primary"], "caption": caption})
                    # A source caption winning precedence was not produced by this template.
                    if provenance.get("caption") != "template":
                        template = None
                if not values.get("caption") or not values.get("caption", "").strip():
                    issues.append(Issue("missing_caption", "Provide a caption or select a unique cluster/style template"))
                if require_parameters and missing_parameters(values):
                    issues.append(Issue("unresolved_parameters", "Choose values for: " + ", ".join(missing_parameters(values))))
                if values.get("model") == NATIVE_MODEL and not missing_parameters(values) and values.get("caption"):
                    validate_job({"protocol": 1, "run_id": "preflight", "parameters": values,
                        "lyrics": document.values.get("lyrics", ""), "execution": execution_options(specification),
                        "model_revision": specification.get("model_revision")},
                        allow_unfrozen_seeds=True, require_local=False)
                document.with_generation_values(output_changes(document, {k: v for k, v in values.items() if v != "auto"}))
            except (ValueError, OSError, UnicodeError) as exc:
                issues.append(Issue("invalid_request", str(exc)))
        checked.append(CheckedInput(item, document, issues, values, provenance, template))
    # Repeated identities in independently chosen files cannot become two tracks.
    identities = {}
    for record in checked:
        key = (record.item.song_id, record.item.variant_id)
        if None not in key:
            identities.setdefault(key, []).append(record)
    for records in identities.values():
        if len(records) > 1:
            for record in records:
                record.issues.append(Issue("duplicate_selected_id", "Several selected inputs have the same song/variant identity"))
    return checked


class BatchRejected(ValueError):
    def __init__(self, message, checked):
        super().__init__(message)
        self.report = [item.summary() for item in checked]


def open_database(path):
    path = Path(path).resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        if not conn.execute("SELECT 1 FROM platform_migration_report WHERE id=1").fetchone():
            raise ValueError("A verified shared database is required")
    except Exception:
        conn.close()
        raise
    return conn


def prepare_batch(checked, specification, workspace, database, *, input_root, error_policy="cancel", selection=None,
                  output_root=None, output_layout='single'):
    if error_policy not in {"cancel", "drop"}:
        raise ValueError("error_policy must be cancel or drop")
    invalid = [i for i in checked if not i.ready]
    ready = [i for i in checked if i.ready]
    if invalid and error_policy == "cancel":
        raise BatchRejected("Batch cancelled: fix invalid inputs or explicitly choose the drop policy", checked)
    if not ready:
        raise BatchRejected("No valid inputs remain; no batch was created", checked)
    if specification.get("request_id") and sum(len(r.planned_runs or [{}]) for r in ready) != 1:
        raise ValueError("A rerun request ID must identify one input")
    workspace = Path(workspace).resolve()
    if workspace.is_relative_to(Path(input_root).resolve()):
        raise ValueError("Batch workspace cannot be inside the source writing directory")
    if output_layout not in {'single', 'track', 'album'}:
        raise ValueError('Output layout must be single, track or album')
    if output_root is not None:
        output_root = Path(output_root).resolve()
        if output_root.is_relative_to(Path(input_root).resolve()):
            raise ValueError('Generated outputs cannot be inside the source writing directory')
    # Detect edits after preflight before creating files or recording a plan.
    for record in ready:
        if missing_parameters(record.effective):
            raise ValueError("Cannot prepare an input with unresolved generation parameters")
        if sha(record.item.path.read_bytes()) != record.document.sha256:
            raise ValueError(f"Source changed after preflight: {record.item.path}; revalidate the batch")
    with closing(open_database(database)) as conn:
        request_id = specification.get("request_id")
        if request_id and conn.execute("SELECT 1 FROM generation_runs WHERE request_id=?", (request_id,)).fetchone():
            raise ValueError("Rerun request already exists; load its frozen batch instead")
        parent = specification.get("parent_review_generation_id")
        if parent and not conn.execute("SELECT 1 FROM generations WHERE id=?", (parent,)).fetchone():
            raise ValueError("Parent review generation does not exist")
        workspace.mkdir(parents=True, exist_ok=True)
        batch_id = uuid.uuid4().hex
        target = workspace / batch_id
        staging = Path(tempfile.mkdtemp(prefix="gbr-plan-", dir=workspace))
        published = False
        try:
            (staging / "inputs").mkdir()
            (staging / "requests").mkdir()
            created = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            manifest = {"format_version": 1, "batch_id": batch_id, "status": "prepared", "created_at": created,
                        "database": str(Path(database).resolve()), "error_policy": error_policy,
                        "selection": selection or {}, "specification": specification, "inputs": [], "runs": []}
            if output_root is not None:
                manifest.update(output_root=str(output_root), output_layout=output_layout)
            for index, record in enumerate(checked):
                summary = record.summary()
                summary.update({"input_id": str(index), "status": "ready" if record.ready else "excluded"})
                manifest["inputs"].append(summary)
                if not record.ready:
                    continue
                input_name = f"inputs/{index:04d}-{record.document.sha256[:12]}.yaml"
                write_new(staging / input_name, record.document.raw)
                for case in record.planned_runs or [{'effective': record.effective, 'provenance': record.provenance, 'template': record.template}]:
                    effective = dict(case['effective'])
                    provenance = dict(case['provenance'])
                    # Freeze automatic seeds only after all preflight checks.
                    for seed in SEEDS:
                        if effective.get(seed, "auto") == "auto":
                            effective[seed] = str(secrets.randbits(64))
                            provenance[seed] = provenance.get(seed, "automatic") + ":frozen"
                    worker_options=[]
                    for option in case.get('worker_options') or []:
                        option={**option,'effective':dict(option['effective']),'provenance':dict(option['provenance'])}
                        for seed in SEEDS:
                            if option['effective'].get(seed,'auto')=='auto':
                                option['effective'][seed]=effective[seed]
                                option['provenance'][seed]=option['provenance'].get(seed,'automatic')+':frozen'
                        worker_options.append(option)
                    run_id = uuid.uuid4().hex
                    request_name = f"requests/{run_id}.yaml"
                    working = record.document.with_generation_values(output_changes(record.document, effective))
                    write_new(staging / request_name, working)
                    manifest["runs"].append({"id": run_id, "input_id": str(index), "snapshot": input_name,
                        "source_sha256": record.document.sha256, "request_yaml": request_name, "request_yaml_sha256": sha(working),
                        "song_id": record.item.song_id, "variant_id": record.item.variant_id,
                        "title": record.document.values["title"], "effective": effective, "provenance": provenance,
                        'album': record.item.metadata.get('album_name') or '',
                        "execution": execution_options(specification),
                        "worker_options":worker_options,
                        "worker_default_fields":case.get('worker_default_fields',[]),
                        "model_revision": specification.get("model_revision"),
                        "template": case.get("template"), "experiment": case.get("experiment"), "parent_review_generation_id": parent, "request_id": request_id})
            rendered = json_text(manifest).encode("utf-8")
            write_new(staging / "manifest.json", rendered)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO generation_batches VALUES(?,?,?,?,?)", (
                batch_id, created, "prepared", json_text(selection or {}),
                json_text({"error_policy": error_policy, "manifest_sha256": sha(rendered)})))
            for record, summary in zip(checked, manifest["inputs"]):
                source_hash = None
                if record.ready:
                    source_hash = record.document.sha256
                    conn.execute("INSERT OR IGNORE INTO source_documents VALUES(?,?,?,?)", (
                        source_hash, record.document.raw, str(record.item.path), created))
                    if conn.execute("SELECT raw_bytes FROM source_documents WHERE sha256=?", (source_hash,)).fetchone()[0] != record.document.raw:
                        raise ValueError("Existing immutable source document does not match its hash")
                conn.execute("INSERT INTO generation_batch_inputs VALUES(?,?,?,?,?,?)", (
                    batch_id, summary["input_id"], source_hash, str(record.item.path), summary["status"], json_text(summary["issues"])))
            for run in manifest["runs"]:
                template = run["template"] or {}
                conn.execute("""INSERT INTO generation_runs
                    (id,batch_id,source_sha256,song_id,variant_id,title,cluster,style,template_id,template_sha256,status,
                     requested_json,effective_json,created_at,parent_review_generation_id,request_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    run["id"], batch_id, run["source_sha256"], run["song_id"], run["variant_id"], run["title"],
                    run["effective"].get("cluster"), run["effective"].get("style"), template.get("id"), template.get("sha256"),
                    "prepared", json_text(specification), json_text(run["effective"]), created, parent, request_id))
            if target.exists():
                raise FileExistsError(target)
            staging.rename(target)
            published = True
            conn.commit()
            return target / "manifest.json"
        except Exception:
            conn.rollback()
            shutil.rmtree(target if published else staging)
            raise


def verify_plan(path):
    path = Path(relocated_path(path)).resolve(strict=True)
    raw = path.read_bytes()
    manifest = manifest_paths(json.loads(raw))
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1 or not {"database", "batch_id", "runs"}.issubset(manifest):
        raise ValueError("Not a supported frozen batch manifest")
    with closing(open_database(manifest["database"])) as conn:
        row = conn.execute("SELECT policy_json FROM generation_batches WHERE id=?", (manifest["batch_id"],)).fetchone()
        if row is None or json.loads(row[0])["manifest_sha256"] != sha(raw):
            raise ValueError("Frozen manifest is unregistered or has changed")
    for run in manifest["runs"]:
        for key, digest in (("snapshot", "source_sha256"), ("request_yaml", "request_yaml_sha256")):
            target = (path.parent / run[key]).resolve(strict=True)
            if not target.is_relative_to(path.parent) or sha(target.read_bytes()) != run[digest]:
                raise ValueError("Frozen source/request YAML has changed")
    return manifest


def write_completed_yaml(source_raw, actual_values, destination, *, succeeded, completed_at=None, timezone_name="Europe/London"):
    if not succeeded:
        raise ValueError("Only successful generations may write a completed sidecar")
    instant = completed_at or datetime.now(timezone.utc)
    if instant.tzinfo is None:
        raise ValueError("Completion time must include a timezone")
    document = YamlDocument(source_raw)
    changes = output_changes(document, actual_values)
    changes["generation_datetime"] = instant.astimezone(ZoneInfo(timezone_name)).strftime("%y%m%d%H%M")
    result = document.with_generation_values(changes)
    publish_new_file(destination, result)
    return sha(result)

