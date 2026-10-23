#!/usr/bin/env python3
"""Build the GBR knowledge-document manifest from content-source/folders/*.md.

No document filenames are hard-coded in the player. Optional YAML front matter:

---
title: Prompting
tab: Prompting
order: 10
connect:
  - workflow
  - 03-Technology.md#models
knowledge: true
---

`connect` creates related/dashed cross-cluster edges. Ordinary Markdown links
between documents/headings also become graph edges automatically.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "_content" / "_folders"


def slugify(value: str) -> str:
    value = str(value or "").strip().lower()
    value = re.sub(r"^\d+[-_. ]*", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-") or "document"


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :]
    if yaml is None:
        meta: dict[str, Any] = {}
        for line in raw.splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
        return meta, body
    loaded = yaml.safe_load(raw) or {}
    return (loaded if isinstance(loaded, dict) else {}), body


def first_h1(body: str) -> str:
    for line in body.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            return re.sub(r"[`*_~]", "", m.group(1)).strip()
    return ""


def as_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def numeric_order(meta: dict[str, Any], path: Path) -> float:
    try:
        return float(meta.get("order"))
    except (TypeError, ValueError):
        m = re.match(r"^(\d+)", path.stem)
        return float(m.group(1)) if m else 9999.0


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "_site" / "knowledge-manifest.json"
    if not output.is_absolute():
        output = (ROOT / output).resolve()

    documents: list[dict[str, Any]] = []
    if SOURCE.exists():
        for path in sorted(SOURCE.rglob("*.md"), key=lambda p: p.as_posix().lower()):
            raw = path.read_text(encoding="utf-8-sig")
            meta, body = parse_front_matter(raw)
            if meta.get("knowledge") is False or str(meta.get("hidden", "")).lower() in {"true", "1", "yes"}:
                continue

            rel = 'content-source/folders/' + path.relative_to(SOURCE).as_posix()
            title = str(meta.get("title") or meta.get("tab") or first_h1(body) or path.stem).strip()
            doc_id = slugify(str(meta.get("id") or path.stem))
            documents.append(
                {
                    "id": doc_id,
                    "title": title,
                    "label": str(meta.get("tab") or title).strip(),
                    "order": numeric_order(meta, path),
                    "path": rel,
                    "filename": path.name,
                    "connect": as_list(meta.get("connect") or meta.get("connections") or meta.get("related")),
                }
            )

    documents.sort(key=lambda d: (float(d["order"]), d["title"].casefold(), d["path"].casefold()))
    payload = {"version": 2, "documents": documents}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Knowledge manifest: {len(documents)} document(s) -> {output}")
    for doc in documents:
        print(f"  {doc['order']:g}  {doc['id']}: {doc['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
