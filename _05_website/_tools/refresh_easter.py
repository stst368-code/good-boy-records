#!/usr/bin/env python3
"""Refresh the Easter catalogue from the actual R2 objects via rclone.

Audio is sufficient. A same-stem YAML and same-stem artwork are optional.
The YAML uses the normal GBR schema; its existing `story` field is surfaced.
"""
from __future__ import annotations

import json, os, re, subprocess, sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML is required: python -m pip install PyYAML")

AUDIO_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".ogg", ".opus", ".webm"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "track"


def humanise(value: Any) -> str:
    return re.sub(r"[-_]+", " ", str(value or "")).strip().title()


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    # rclone emits UTF-8. Decode it ourselves instead of allowing Windows'
    # locale (commonly cp1252) to decode the pipe in subprocess reader threads.
    return data.decode("utf-8-sig", errors="replace")


def run(*args: str) -> str:
    p = subprocess.run(
        ["rclone", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    stdout = _decode(p.stdout)
    stderr = _decode(p.stderr)
    if p.returncode:
        raise RuntimeError(stderr.strip() or stdout.strip() or f"rclone exited {p.returncode}")
    return stdout


def remote_root() -> str:
    remote = os.environ.get("R2_REMOTE", "r2").rstrip(":")
    bucket = os.environ.get("R2_BUCKET", "good-boy-records").strip("/")
    return f"{remote}:{bucket}/showcase/easter"


def public_path(name: str) -> str:
    return "showcase/easter/" + name.replace("\\", "/")


def repair_literal_block_indentation(text: str, keys: tuple[str, ...] = ("lyrics",)) -> str:
    """Mirror the normal catalogue YAML repair for generated literal blocks."""
    lines = text.splitlines()
    repaired: list[str] = []
    block_key: str | None = None
    block_indent = 0
    key_re = re.compile(
        r"^(?P<indent>\s*)(?P<key>" + "|".join(re.escape(k) for k in keys) + r")\s*:\s*[|>]\s*[-+]?\s*$",
        re.IGNORECASE,
    )
    yaml_key_re = re.compile(r"^[A-Za-z0-9_.-]+\s*:\s*(?:\S.*)?$")
    for line in lines:
        match = key_re.match(line)
        if match:
            block_key = match.group("key").lower()
            block_indent = len(match.group("indent"))
            repaired.append(line)
            continue
        if block_key is not None and line.strip():
            indent = len(line) - len(line.lstrip(" "))
            if indent <= block_indent:
                stripped = line.strip()
                if indent == 0 and yaml_key_re.match(stripped) and not stripped.startswith("["):
                    block_key = None
                    repaired.append(line)
                    continue
                repaired.append(" " * (block_indent + 2) + line.lstrip())
                continue
        repaired.append(line)
    return "\n".join(repaired) + ("\n" if text.endswith("\n") else "")


def parse_yaml_text(text: str, name: str) -> tuple[dict[str, Any], bool]:
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        try:
            raw = yaml.safe_load(repair_literal_block_indentation(text)) or {}
        except Exception as exc:
            print(f"warn Easter YAML {name}: invalid YAML ({exc})")
            return {}, False
    except Exception as exc:
        print(f"warn Easter YAML {name}: invalid YAML ({exc})")
        return {}, False
    if not isinstance(raw, dict):
        print(f"warn Easter YAML {name}: YAML root is not a mapping")
        return {}, False
    return raw, True


def load_remote_yaml(root: str, name: str) -> tuple[dict[str, Any], bool]:
    try:
        return parse_yaml_text(run("cat", f"{root}/{name}"), name)
    except Exception as exc:
        print(f"warn Easter YAML {name}: {exc}")
        return {}, False


def first_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _existing_by_stem(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for track in ((data.get("easter") or {}).get("tracks") or []):
        if not isinstance(track, dict):
            continue
        sources = ((track.get("audio") or {}).get("sources") or {})
        if not isinstance(sources, dict):
            continue
        for value in sources.values():
            if isinstance(value, str) and value.strip():
                out.setdefault(Path(value.replace("\\", "/")).stem.casefold(), track)
                break
    return out


def build_tracks(root: str, existing: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    existing = existing or {}
    names = [x.strip().replace("\\", "/") for x in run("lsf", root, "--files-only", "-R").splitlines() if x.strip()]
    # Easter is intentionally flat for track ownership. Ignore nested objects rather than pairing ambiguously.
    names = [n for n in names if "/" not in n]
    by_stem: dict[str, list[str]] = {}
    for name in names:
        by_stem.setdefault(Path(name).stem.casefold(), []).append(name)

    audio_stems = sorted(
        (stem for stem, members in by_stem.items() if any(Path(n).suffix.lower() in AUDIO_EXTS for n in members)),
        key=str.casefold,
    )
    tracks: list[dict[str, Any]] = []
    for index, stem_key in enumerate(audio_stems, 1):
        members = by_stem[stem_key]
        audio = [n for n in members if Path(n).suffix.lower() in AUDIO_EXTS]
        label = Path(sorted(audio, key=str.casefold)[0]).stem
        yaml_names = [n for n in members if Path(n).suffix.lower() in {".yaml", ".yml"}]
        image_names = [n for n in members if Path(n).suffix.lower() in IMAGE_EXTS]
        yaml_ok = True
        if yaml_names:
            raw, yaml_ok = load_remote_yaml(root, sorted(yaml_names, key=str.casefold)[0])
        else:
            raw = {}

        sources: dict[str, str] = {}
        for name in sorted(audio, key=str.casefold):
            fmt = Path(name).suffix.lower().lstrip(".")
            sources.setdefault(fmt, public_path(name))

        title = first_text(raw, "title", "song_title", "track_title") or label
        version = first_text(raw, "version", "genre", "song_genre") or "Hidden Track"
        story = first_text(raw, "story")
        language = (first_text(raw, "language") or "en").strip().lower().replace("_", "-")
        # If an R2 YAML object exists but could not be read/parsed, keep the
        # locally-built story for the same audio stem instead of erasing it.
        # A successfully-read blank story remains intentionally blank.
        if yaml_names and not yaml_ok and not story:
            previous = existing.get(stem_key) or {}
            previous_story = previous.get("story")
            if isinstance(previous_story, str) and previous_story.strip():
                story = previous_story.strip()
            previous_language = previous.get("language")
            if isinstance(previous_language, str) and previous_language.strip():
                language = previous_language.strip().lower().replace("_", "-")
        track: dict[str, Any] = {
            "id": f"easter-{index:02d}-{slugify(label)}",
            "title": title,
            "displayTitle": title if title != label else humanise(label),
            "variant": version,
            "language": language,
            "genre": {
                "cluster": "easter",
                "parent_genre": "Easter Universe",
                "child_genre": version,
                "parent_colour": "#D0802F",
                "child_colour": "#D9A65E",
                "taxonomy_order": index,
                "parent_order": 0,
            },
            "audio": {"sources": sources},
        }
        if yaml_names:
            track["yamlUrl"] = public_path(sorted(yaml_names, key=str.casefold)[0])
        if story:
            track["story"] = story
        if image_names:
            track["artwork_url"] = public_path(sorted(image_names, key=str.casefold)[0])
        tracks.append(track)
    return tracks


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: refresh_easter.py <catalogue.json>")
    catalogue_path = Path(sys.argv[1])
    data = json.loads(catalogue_path.read_text(encoding="utf-8"))
    root = remote_root()
    try:
        tracks = build_tracks(root, _existing_by_stem(data))
    except (FileNotFoundError, RuntimeError) as exc:
        # Do not destroy a valid locally-built Easter catalogue merely because rclone/R2 is unavailable.
        print(f"warn Easter R2 refresh skipped: {exc}")
        return 0
    data["easter"] = {"enabled": bool(tracks), "count": len(tracks), "tracks": tracks}
    # Support either catalogue shape consumed by the player.
    data.pop("easter_tracks", None)
    catalogue_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    yaml_count = sum(1 for t in tracks if t.get("yamlUrl"))
    story_count = sum(1 for t in tracks if isinstance(t.get("story"), str) and t.get("story", "").strip())
    artwork_count = sum(1 for t in tracks if t.get("artwork_url"))
    print(
        f"Easter R2 refresh: {len(tracks)} track(s) from {root}; "
        f"YAML: {yaml_count}; stories: {story_count}; artwork: {artwork_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
