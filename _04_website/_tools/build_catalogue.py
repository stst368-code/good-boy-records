#!/usr/bin/env python3
"""Build the compact Good Boy Records Sonic catalogue directly from showcase/.

This is intentionally the only catalogue-building pass.

Source of truth:
    showcase/<song>/<variant>/
        *.yaml
        *.flac / *.mp3
        *.png / *.jpg / *.webp
        gbr.playback.json   (preferred timing/reaction file, if present)
        <variant>.lyrics.json (canonical lyrics-only fallback)
        gbr.lyrics.json       (cleanup-era fallback)
        *.lyrics.json         (last-resort compatibility fallback)

Outputs:
    _data/catalogue.json    (committed metadata; explicit refresh only)

No content-source track mirrors, data/yaml copies, data/live-lyrics copies,
masters folder, prepared sleeve tree, side-A/B system, or fixed genre slots.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    raise SystemExit("PyYAML is required: python -m pip install PyYAML")

ROOT = Path(__file__).resolve().parent.parent
SHOWCASE = ROOT / "_showcase"
TAXONOMY = ROOT / "_templates" / "music_genre_taxonomy.csv"
DEFAULT_OUTPUT = ROOT / "_data" / "catalogue.json"
EXISTING_IDS = {}
EXISTING_TRACKS = {}
STYLES = ROOT.parent / '_01_writing/_references/styles.csv'

AUDIO_EXTS = {".flac", ".mp3"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
ATR_AUDIO_EXTS = {".flac", ".mp3", ".wav", ".m4a", ".ogg", ".opus", ".webm"}
AUDIO_RANK = {".flac": 0, ".mp3": 1}
RESERVED_TOP = {"easter"}
RESERVED_VARIANT_SUFFIX = "-atr"


def recording_yaml(path):
    # The worker's immutable input snapshot accompanies its completed recipe.
    # It is provenance, not a second playable recording.
    return path.name.casefold() not in {'source.yaml', 'source.yml'}


def slugify(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "track"


def humanise(value: Any) -> str:
    return re.sub(r"[-_]+", " ", str(value or "")).strip().title()


def share_token(track_id, version):
    value = 1469598103934665603
    for character in f'm:{track_id}:{version}':
        value = ((value ^ ord(character)) * 1099511628211) & ((1 << 64) - 1)
    alphabet = '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    result = ''
    while value:
        value, digit = divmod(value, 62)
        result = alphabet[digit] + result
    return (result or '0').zfill(8)[-8:]


def normalise_language(value: Any, default: str = "en") -> str:
    """Return a compact language tag for catalogue filtering.

    Variant YAML ``language`` is the source of truth. Legacy tracks default to
    English, matching the lyric/timeline pipeline's existing behaviour.
    """
    text = str(value or default).strip().lower().replace("_", "-")
    aliases = {"english": "en", "spanish": "es", "romanian": "ro", "ukrainian": "uk",
               "polish": "pl", "japanese": "ja", "mongolian": "mn", "korean": "ko",
               "luganda": "lg", "arabic": "ar", "turkish": "tr", "chinese": "zh",
               "hindi": "hi", "german": "de", "french": "fr", "italian": "it",
               "portuguese": "pt"}
    return aliases.get(text, text or default)


def norm(value: Any) -> str:
    """Aggressive lookup normalisation used only for taxonomy matching."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def number(raw: dict[str, Any], *keys: str) -> int | float | None:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip():
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                pass
    return None


def seed_text(raw, *keys):
    # JS numbers cannot represent all unsigned 64-bit seeds exactly.
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip() or isinstance(value, int) and not isinstance(value, bool):
            return str(value).strip()
    return None


def repair_literal_block_indentation(text: str, keys: tuple[str, ...] = ("lyrics",)) -> str:
    """Repair a common generated-YAML literal-block indentation error in memory."""
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


def load_yaml(path: Path) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"skip {path}: {exc}")
        return None
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        try:
            raw = yaml.safe_load(repair_literal_block_indentation(text)) or {}
        except Exception as exc:
            print(f"skip {path.relative_to(SHOWCASE)}: invalid YAML ({exc})")
            return None
    except Exception as exc:
        print(f"skip {path.relative_to(SHOWCASE)}: invalid YAML ({exc})")
        return None
    if not isinstance(raw, dict):
        print(f"skip {path.relative_to(SHOWCASE)}: YAML root is not a mapping")
        return None
    return raw


def showcase_url(path: Path) -> str:
    return "showcase/" + path.relative_to(SHOWCASE).as_posix()


def choose_audio(directory: Path, yaml_path: Path, release_id: str) -> dict[str, str]:
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS]
    out: dict[str, str] = {}
    for ext, key in ((".flac", "flac"), (".mp3", "mp3")):
        candidates = [p for p in files if p.suffix.lower() == ext]
        if not candidates:
            continue
        preferred = []
        for stem in (release_id, yaml_path.stem):
            preferred.extend(p for p in candidates if slugify(p.stem) == slugify(stem))
        pick = preferred[0] if preferred else (candidates[0] if len(candidates) == 1 else None)
        if pick:
            out[key] = showcase_url(pick)
        else:
            print(f"warn {directory.relative_to(SHOWCASE)}: multiple {ext} files; none clearly owns this YAML")
    return out


def choose_artwork(directory: Path, yaml_path: Path, raw: dict[str, Any], release_id: str) -> str | None:
    explicit = raw.get("cover") or raw.get("artwork")
    if explicit:
        value = str(explicit)
        candidate = directory / value
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTS:
            return showcase_url(candidate)
        for ext in IMAGE_EXTS:
            candidate = directory / f"{value}{ext}"
            if candidate.is_file():
                return showcase_url(candidate)

    images = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    for stem in (release_id, yaml_path.stem, directory.name):
        hits = [p for p in images if slugify(p.stem) == slugify(stem)]
        if len(hits) == 1:
            return showcase_url(hits[0])
    if len(images) == 1:
        return showcase_url(images[0])
    return None


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def single_recording(directory):
    stems = {p.stem for p in directory.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS}
    return len([*directory.glob('*.yaml'), *directory.glob('*.yml')]) <= 1 or len(stems) <= 1


def choose_timing(directory, yaml_path):
    standalone = single_recording(directory)
    ordered = [yaml_path.with_suffix('.playback.json')]
    if standalone:
        ordered.append(directory / 'gbr.playback.json')
    ordered.append(yaml_path.with_suffix('.lyrics.json'))
    if standalone:
        ordered.extend([directory / (directory.name + '.lyrics.json'), directory / 'gbr.lyrics.json'])
        ordered.extend(sorted(directory.glob('*.lyrics.json')))
    for path in dict.fromkeys(ordered):
        data = read_json(path) if path.is_file() else None
        if data and isinstance(data.get('lines'), list):
            return showcase_url(path), data
    return None, None


class Taxonomy:
    def __init__(self, path: Path, styles: Path = STYLES):
        self.rows: list[dict[str, Any]] = []
        self.lookup: dict[str, dict[str, Any]] = {}
        self.parent_order: dict[str, int] = {}
        self.style_order, self.cluster_order = {}, {}
        if styles.is_file():
            with styles.open(encoding='utf-8-sig', newline='') as stream:
                for row in csv.DictReader(stream):
                    cluster, style = norm(row.get('cluster')), norm(row.get('style_primary'))
                    self.cluster_order.setdefault(cluster, len(self.cluster_order))
                    self.style_order.setdefault((cluster, style), len(self.style_order))
        if not path.is_file():
            print(f"warn taxonomy missing: {path}; unmatched versions will be Unclassified")
            return
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for idx, row in enumerate(reader, start=1):
                clean = {str(k or "").strip(): (str(v or "").strip()) for k, v in row.items()}
                order_raw = clean.get("order") or clean.get("index") or str(idx)
                try:
                    order = int(float(order_raw))
                except ValueError:
                    order = idx
                clean["_order"] = order
                self.rows.append(clean)
                parent = clean.get("parent_genre") or "Unclassified"
                self.parent_order[parent] = min(self.parent_order.get(parent, order), order)
                for source in (clean.get("normalised_child"), clean.get("child_genre")):
                    key = norm(source)
                    if key and key not in self.lookup:
                        self.lookup[key] = clean

    def match(self, version, cluster='', style=''):
        result = self._legacy_match(style or version)
        if cluster:
            label = str(cluster).strip()
            result.update(cluster=label, parent_genre=label, child_genre=str(style or version).strip())
            known = next((r for r in self.rows if norm(r.get('cluster')) == norm(label)), None)
            palette = ['#D0802F', '#DCB250', '#66CC99', '#5D9BAA', '#8068A0', '#A45B88']
            colour = (known.get('parent_colour') or '#D0802F') if known else palette[int(hashlib.sha1(label.casefold().encode()).hexdigest()[:8],16) % len(palette)]
            result['parent_colour'] = colour
            result['parent_order'] = self.cluster_order.get(norm(label), 1_000_000)
            result['taxonomy_order'] = self.style_order.get((norm(label), norm(style or version)), 1_000_000)
            if result['taxonomy_order'] == 1_000_000:
                result['child_colour'] = colour
        return result

    def _legacy_match(self, version: str) -> dict[str, Any]:
        row = self.lookup.get(norm(version))
        if row is None:
            return {
                "cluster": "unclassified",
                "parent_genre": "Unclassified",
                "child_genre": humanise(version),
                "parent_colour": "#808080",
                "child_colour": "#909090",
                "taxonomy_order": 1_000_000,
                "parent_order": 1_000_000,
            }
        parent = row.get("parent_genre") or "Unclassified"
        return {
            "cluster": row.get("cluster") or "",
            "parent_genre": parent,
            "child_genre": row.get("child_genre") or humanise(version),
            "parent_colour": row.get("parent_colour") or "#808080",
            "child_colour": row.get("child_colour") or row.get("parent_colour") or "#909090",
            "taxonomy_order": int(row["_order"]),
            "parent_order": int(self.parent_order.get(parent, row["_order"])),
        }


def composition_yaml(song_dir: Path) -> tuple[Path | None, dict[str, Any]]:
    exact = [song_dir / f"{song_dir.name}.yaml", song_dir / f"{song_dir.name}.yml"]
    for path in exact:
        if path.is_file():
            return path, load_yaml(path) or {}
    # Do not guess among several root YAMLs. A composition YAML is optional.
    roots = [p for p in song_dir.iterdir() if p.is_file() and p.suffix.lower() in {".yaml", ".yml"} and recording_yaml(p)]
    if len(roots) == 1:
        return roots[0], load_yaml(roots[0]) or {}
    return None, {}


def infer_version(raw: dict[str, Any], yaml_path: Path, composition_id: str) -> str:
    value = str(raw.get('style') or raw.get('style_primary') or raw.get('version') or '').strip()
    if value:
        return value
    folder = slugify(yaml_path.parent.name)
    prefix = composition_id + "-"
    return folder[len(prefix):] if folder.startswith(prefix) else folder


def stable_track_id(composition_id, version, yaml_path, seen):
    previous = EXISTING_IDS.get(showcase_url(yaml_path))
    if previous and previous not in seen:
        seen.add(previous)
        return previous
    base = slugify(f'{composition_id}-{version}')
    reserved = set(EXISTING_IDS.values())
    if base not in seen and base not in reserved:
        seen.add(base)
        return base
    suffix = hashlib.sha1(yaml_path.relative_to(SHOWCASE).as_posix().encode()).hexdigest()[:7]
    value, counter = f'{base}-{suffix}', 2
    while value in seen or value in reserved:
        value = f'{base}-{suffix}-{counter}'
        counter += 1
    seen.add(value)
    return value


def variant_yamls(song_dir: Path) -> list[Path]:
    out = []
    for path in [*song_dir.rglob("*.yaml"), *song_dir.rglob("*.yml")]:
        if not recording_yaml(path):
            continue
        if path.parent == song_dir:
            continue
        if any(part.casefold().endswith(RESERVED_VARIANT_SUFFIX) for part in path.relative_to(song_dir).parts[:-1]):
            continue
        out.append(path)
    return sorted(set(out), key=lambda p: p.as_posix().casefold())


def build_track(
    yaml_path: Path,
    raw: dict[str, Any],
    composition_id: str,
    common: dict[str, Any],
    common_yaml_path: Path | None,
    taxonomy: Taxonomy,
    seen_ids: set[str],
) -> dict[str, Any]:
    title_id = slugify(raw.get("title") or composition_id)
    version = infer_version(raw, yaml_path, title_id)
    track_id = stable_track_id(title_id, version, yaml_path, seen_ids)
    previous = EXISTING_TRACKS.get(showcase_url(yaml_path), {})
    token = previous.get('shareToken') or share_token(track_id, str(previous.get('variant') or previous.get('version') or version))

    audio = choose_audio(yaml_path.parent, yaml_path, track_id)
    artwork_url = choose_artwork(yaml_path.parent, yaml_path, raw, track_id)
    timing_url, timing = choose_timing(yaml_path.parent, yaml_path)

    stats = timing.get("stats") if isinstance(timing, dict) and isinstance(timing.get("stats"), dict) else {}
    quality = timing.get("quality") if isinstance(timing, dict) and isinstance(timing.get("quality"), dict) else {}
    coverage = stats.get("coverage")
    review_required = bool(quality.get("review_required", False))
    approved = bool(quality.get("approved", False))
    usable = bool(
        quality.get(
            "usable",
            quality.get("usable_for_live_lyrics", approved or not review_required),
        )
    )

    song_story = str(common.get("story") or "").strip()
    song_inspiration = str(common.get("inspiration") or "").strip()
    song_inspiration_url = str(common.get("inspirationyt") or common.get("inspiration_url") or "").strip()
    version_story = str(raw.get("story") or "").strip()
    version_inspiration = str(raw.get("inspiration") or "").strip()
    version_inspiration_url = str(raw.get("inspirationyt") or raw.get("inspiration_url") or "").strip()

    # Preserve both creative layers.  The old flattened story/style fields remain
    # as compatibility fallbacks for existing player code and older catalogues.
    inspiration = version_inspiration or song_inspiration
    inspiration_url = version_inspiration_url or song_inspiration_url
    timing_language = timing.get("language") if isinstance(timing, dict) else None
    source_language = timing.get("source", {}).get("language") if isinstance(timing, dict) and isinstance(timing.get("source"), dict) else None
    language = normalise_language(raw.get("language") or timing_language or source_language or common.get("language") or "en")

    track = {
        "id": track_id,
        "shareToken": token,
        "composition": title_id,
        "title": title_id,
        "displayTitle": str(raw.get("display_title") or raw.get("title") or common.get("title") or humanise(title_id)),
        "variant": version,
        "version": version,
        "language": language,
        "genre": taxonomy.match(version, str(raw.get('cluster') or '').strip(), str(raw.get('style') or raw.get('style_primary') or '').strip()),
        "song_id": raw.get('song_id'), "variant_id": raw.get('variant_id'),
        "explicit": raw.get('explicit'), "generation_datetime": raw.get('generation_datetime'),
        "audio": {
            "sources": {
                "flac": audio.get("flac"),
                "mp3": audio.get("mp3"),
            }
        },
        "artwork_url": artwork_url,
        "lyrics": {
            "raw": str(raw.get("lyrics") or "").rstrip(),
            "wordTiming": (
                {
                    "src": timing_url,
                    "format": timing.get("format") if isinstance(timing, dict) else None,
                    "language": language,
                    "coverage": coverage,
                    "rating": quality.get("rating"),
                    "reviewRequired": review_required,
                    "approved": approved,
                    "usable": usable,
                }
                if timing_url else None
            ),
        },
        "lyrics_url": timing_url,
        "yamlUrl": showcase_url(yaml_path),
        "story": version_story or song_story,
        "style": {
            "inspiration": inspiration,
            "inspirationUrl": inspiration_url,
        },
        "provenance": {
            "song": {
                "story": song_story,
                "inspiration": song_inspiration,
                "inspirationUrl": song_inspiration_url,
                "yamlUrl": showcase_url(common_yaml_path) if common_yaml_path else "",
            },
            "version": {
                "story": version_story,
                "inspiration": version_inspiration,
                "inspirationUrl": version_inspiration_url,
                "yamlUrl": showcase_url(yaml_path),
            },
        },
        "model": {
            "name": str(raw.get("model") or "").strip(),
            "dit": str(raw.get("dit") or "").strip(),
            "textEncoder": str(raw.get("text_encoder") or raw.get("textenc") or raw.get("text_encoder_model") or "").strip(),
        },
        "generation": {
            "encoderCfg": number(raw, "encoder_cfg"),
            "encoderSeed": seed_text(raw, "encoder_seed"),
            "topK": number(raw, "top_k", "topk"),
            "sampler": str(raw.get("sampler") or "").strip(),
            "scheduler": str(raw.get("scheduler") or "").strip(),
            "samplerCfg": number(raw, "sampler_cfg", "cfg"),
            "samplerSeed": seed_text(raw, "sampler_seed", "seed"),
            "steps": number(raw, "sampler_steps", "steps"),
            "temperature": number(raw, "temperature"),
            "topP": number(raw, "top_p", "topp"),
            "shift": number(raw, "shift"),
            "duration": number(raw, "duration", "duration_seconds", "seconds"),
            "batchSize": number(raw, "batch_size", "batch"),
            "denoise": number(raw, "denoise"),
            "weight": number(raw, "weight", "model_weight"),
            "ditWeight": number(raw, "dit_weight"),
            "textEncoderWeight": number(raw, "text_encoder_weight", "encoder_weight"),
        },
    }
    # Future timeline hooks are only present when the files actually exist.
    playback = yaml_path.with_suffix('.playback.json')
    analysis = yaml_path.with_suffix('.audio.json')
    if single_recording(yaml_path.parent):
        if not playback.is_file(): playback = yaml_path.parent / 'gbr.playback.json'
        if not analysis.is_file(): analysis = yaml_path.parent / 'gbr.audio.json' 
    if playback.is_file():
        track["playback_url"] = showcase_url(playback)
    if analysis.is_file():
        track["audio_analysis_url"] = showcase_url(analysis)
    return track


def build_easter() -> list[dict[str, Any]]:
    directory = SHOWCASE / "easter"
    if not directory.is_dir():
        return []
    grouped: dict[str, dict[str, Path]] = {}
    labels: dict[str, str] = {}
    for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in ATR_AUDIO_EXTS:
            continue
        key = path.stem.casefold()
        labels.setdefault(key, path.stem)
        grouped.setdefault(key, {})[path.suffix.lower().lstrip(".")] = path

    tracks = []
    for index, key in enumerate(sorted(grouped, key=lambda x: labels[x].casefold()), start=1):
        label = labels[key]
        sources = {fmt: showcase_url(path) for fmt, path in grouped[key].items()}
        yaml_candidates = [p for p in directory.iterdir() if p.is_file() and p.stem.casefold() == key and p.suffix.lower() in {".yaml", ".yml"}]
        image_candidates = [p for p in directory.iterdir() if p.is_file() and p.stem.casefold() == key and p.suffix.lower() in IMAGE_EXTS]
        yaml_path = sorted(yaml_candidates, key=lambda p: p.name.casefold())[0] if yaml_candidates else None
        raw = load_yaml(yaml_path) if yaml_path else {}
        raw = raw or {}
        title = str(raw.get("title") or raw.get("song_title") or raw.get("track_title") or label).strip()
        version = str(raw.get("version") or raw.get("genre") or raw.get("song_genre") or "Hidden Track").strip()
        track = {
            "id": f"easter-{index:02d}-{slugify(label)}",
            "title": title,
            "displayTitle": title if title != label else humanise(label),
            "variant": version,
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
        if yaml_path:
            track["yamlUrl"] = showcase_url(yaml_path)
            story = str(raw.get("story") or "").strip()
            if story:
                track["story"] = story
        if image_candidates:
            track["artwork_url"] = showcase_url(sorted(image_candidates, key=lambda p: p.name.casefold())[0])
        tracks.append(track)
    return tracks


def publishing_gate(tracks, manifest_path):
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or not isinstance(manifest.get('tracks'), list):
        raise ValueError('Expected the review app publishing manifest (schema_version 1)')
    selected = {}
    for row in manifest['tracks']:
        if row.get('publishing_status') not in {'selected', 'published'}:
            continue
        digest, identifier = row.get('yaml_sha256'), row.get('generation_id')
        if not digest or not identifier or digest in selected:
            raise ValueError('Publishing selections need unique completed-YAML hashes and generation IDs')
        selected[digest] = row
    matched, used = [], set()
    for track in tracks:
        relative = track['yamlUrl'].removeprefix('showcase/')
        digest = hashlib.sha256((SHOWCASE / relative).read_bytes()).hexdigest()
        if digest not in selected:
            continue
        if digest in used or not any(track['audio']['sources'].values()):
            raise ValueError('A publishing selection must match one YAML and playable audio in the curated collection')
        used.add(digest)
        row = selected[digest]
        track['generation_id'] = row['generation_id']
        track['publishing_status'] = row['publishing_status']
        matched.append(track)
    if used != set(selected):
        raise ValueError('Some publishing selections are missing from the curated collection; catalogue retained')
    if not matched:
        raise ValueError('No selected/published recordings matched; catalogue retained')
    return matched


def build(output: Path, publishing_manifest=None) -> int:
    if not SHOWCASE.is_dir():
        raise SystemExit(f"Showcase directory is missing: {SHOWCASE}")

    global EXISTING_IDS, EXISTING_TRACKS
    existing = (read_json(output) if output.is_file() else {}) or {}
    EXISTING_TRACKS = {t['yamlUrl']: t for t in existing.get('tracks', []) if t.get('yamlUrl') and t.get('id')}
    EXISTING_IDS = {p: t['id'] for p, t in EXISTING_TRACKS.items()}
    taxonomy = Taxonomy(TAXONOMY, STYLES)
    tracks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for yaml_path in sorted([*SHOWCASE.glob('*.yaml'), *SHOWCASE.glob('*.yml')]):
        if not recording_yaml(yaml_path):
            continue
        raw = load_yaml(yaml_path)
        if raw and raw.get('lyrics'):
            tracks.append(build_track(yaml_path, raw, slugify(raw.get('title') or yaml_path.stem), {}, None, taxonomy, seen_ids))

    song_dirs = [
        p for p in SHOWCASE.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name.casefold() not in RESERVED_TOP | {"one-off", "oneoff"}
    ]
    for song_dir in sorted(song_dirs, key=lambda p: p.name.casefold()):
        common_yaml_path, common = composition_yaml(song_dir)
        composition_id = slugify(common.get("title") or song_dir.name)
        if common_yaml_path and common.get('lyrics') and choose_audio(song_dir, common_yaml_path, common_yaml_path.stem):
            tracks.append(build_track(common_yaml_path, common, composition_id, {}, None, taxonomy, seen_ids))
        yamls = variant_yamls(song_dir)
        if not yamls:
            print(f"warn {song_dir.relative_to(SHOWCASE)}: no variant YAMLs")
        for yaml_path in yamls:
            raw = load_yaml(yaml_path)
            if not raw or not raw.get("lyrics"):
                continue
            tracks.append(build_track(yaml_path, raw, composition_id, common, common_yaml_path, taxonomy, seen_ids))

    # Standalone/one-off bank remains supported without giving it a second
    # catalogue architecture.
    for bank_name in ("one-off", "oneoff"):
        bank = SHOWCASE / bank_name
        if not bank.is_dir():
            continue
        for yaml_path in sorted([*bank.rglob("*.yaml"), *bank.rglob("*.yml")], key=lambda p: p.as_posix().casefold()):
            if not recording_yaml(yaml_path):
                continue
            raw = load_yaml(yaml_path)
            if not raw or not raw.get("lyrics"):
                continue
            composition_id = slugify(raw.get("title") or yaml_path.parent.name)
            if not raw.get("version"):
                raw = dict(raw)
                raw["version"] = raw.get("one_off_label") or "one-off"
            tracks.append(build_track(yaml_path, raw, composition_id, {}, None, taxonomy, seen_ids))

    if publishing_manifest:
        tracks = publishing_gate(tracks, publishing_manifest)
    slot = {t["id"] for t in tracks}
    if len(slot) != len(tracks):
        raise SystemExit("Internal error: duplicate catalogue IDs survived disambiguation")

    tracks.sort(
        key=lambda t: (
            int((t.get("genre") or {}).get("parent_order", 1_000_000)),
            int((t.get("genre") or {}).get("taxonomy_order", 1_000_000)),
            str(t.get("displayTitle") or "").casefold(),
            str(t.get("variant") or "").casefold(),
        )
    )
    easter = build_easter()
    if not (SHOWCASE / 'easter').is_dir() and existing.get('easter'):
        easter = existing['easter'].get('tracks', [])
    if not tracks and existing.get('tracks'):
        raise SystemExit('No tracks found; existing catalogue retained')

    payload = {
        "format": "gbr-sonic-catalogue-v1",
        "trackCount": len(tracks),
        "tracks": tracks,
        "easter": {
            "enabled": bool(easter),
            "count": len(easter),
            "tracks": easter,
        },
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(output)

    missing_audio = sum(1 for t in tracks if not any((t.get("audio") or {}).get("sources", {}).values()))
    missing_art = sum(1 for t in tracks if not t.get("artwork_url"))
    missing_timing = sum(1 for t in tracks if not t.get("lyrics_url"))
    unclassified = sum(1 for t in tracks if (t.get("genre") or {}).get("parent_genre") == "Unclassified")

    print(f"Built {len(tracks)} track(s) -> {output}")
    print(f"Easter: {len(easter)} hidden track(s)")
    print(f"Missing audio: {missing_audio}; artwork: {missing_art}; lyric timing: {missing_timing}; taxonomy: {unclassified}")
    return 0


def main() -> int:
    global SHOWCASE, STYLES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="catalogue JSON destination")
    parser.add_argument("--showcase", type=Path, default=SHOWCASE, help="Complete curated media collection; read only")
    parser.add_argument('--styles', type=Path, default=STYLES, help='Shared writing styles.csv; old taxonomy remains a historical fallback')
    parser.add_argument('--publishing-manifest', type=Path, help='Only include selections matched by completed-YAML SHA256')
    args = parser.parse_args()
    SHOWCASE = args.showcase.resolve()
    STYLES = args.styles.resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = (ROOT / output).resolve()
    try:
        return build(output, args.publishing_manifest)
    except (OSError, ValueError) as error:
        parser.exit(1, 'Cannot refresh catalogue: ' + str(error) + '\n')


if __name__ == "__main__":
    raise SystemExit(main())
