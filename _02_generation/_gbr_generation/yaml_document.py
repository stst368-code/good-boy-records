"""Parse for validation; patch source byte spans without serializing the document."""
from dataclasses import dataclass
import hashlib
import json
import math
import re

import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

FIELD_ORDER = (
    "song_id", "variant_id", "title", "cluster", "style", "model", "dit", "text_encoder",
    "encoder_cfg", "encoder_seed", "top_k", "sampler", "scheduler", "sampler_cfg",
    "sampler_seed", "sampler_steps", "duration", "generation_datetime", "caption", "lyrics",
)
GENERATION_FIELDS = frozenset(FIELD_ORDER[3:-1])


class YamlError(ValueError):
    pass


def same_value(left, right):
    if isinstance(left, float) and math.isnan(left):
        return isinstance(right, float) and math.isnan(right)
    if isinstance(left, dict):
        return isinstance(right, dict) and left.keys() == right.keys() and all(same_value(v, right[k]) for k, v in left.items())
    if isinstance(left, list):
        return isinstance(right, list) and len(left) == len(right) and all(same_value(a, b) for a, b in zip(left, right))
    return type(left) is type(right) and left == right


@dataclass(frozen=True)
class BytePatch:
    start: int
    end: int
    replacement: bytes
    fields: tuple[str, ...]


def scalar_text(value, previous_style=None):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise YamlError("Non-finite numbers cannot be written")
        result = repr(value)
        if "e" in result.lower() and "." not in result.split("e")[0]:
            result = result.replace("e", ".0e")
        return result
    if not isinstance(value, str) or "\n" in value or "\r" in value:
        raise YamlError("This patch operation requires a single-line scalar")
    if previous_style == "'":
        return "'" + value.replace("'", "''") + "'"
    if previous_style != '"' and re.fullmatch(r"[\w./+-]+", value) and yaml.safe_load(value) == value:
        return value
    return json.dumps(value, ensure_ascii=False)


class YamlDocument:
    def __init__(self, raw):
        self.raw = bytes(raw)
        self.bom = b"\xef\xbb\xbf" if self.raw.startswith(b"\xef\xbb\xbf") else b""
        try:
            self.text = self.raw[len(self.bom):].decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise YamlError("YAML must be UTF-8 (an existing UTF-8 BOM is preserved)") from exc
        try:
            for event in yaml.parse(self.text, Loader=yaml.SafeLoader):
                if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
                    raise YamlError("Aliases and anchors require an explicit editor migration before generation")
                if getattr(event, "tag", None) is not None:
                    raise YamlError("Explicit YAML tags are not supported for byte-preserving generation")
            root = yaml.compose(self.text, Loader=yaml.SafeLoader)
            if not isinstance(root, MappingNode) or root.flow_style:
                raise YamlError("Expected one block mapping at the document root")
            self._check_keys(root)
            self.values = yaml.safe_load(self.text)
        except yaml.YAMLError as exc:
            raise YamlError(str(exc)) from exc
        self.fields = {key.value: (key, value) for key, value in root.value}
        self.indent = root.value[0][0].start_mark.column if root.value else 0
        self.sha256 = hashlib.sha256(self.raw).hexdigest()

    def _check_keys(self, node):
        if isinstance(node, MappingNode):
            seen = set()
            for key, value in node.value:
                if not isinstance(key, ScalarNode) or key.tag != "tag:yaml.org,2002:str" or key.start_mark.line != key.end_mark.line:
                    raise YamlError("Mapping keys must be single-line strings")
                if key.value in seen:
                    raise YamlError(f"Duplicate YAML key {key.value!r} at line {key.start_mark.line + 1}")
                seen.add(key.value)
                self._check_keys(value)
        elif isinstance(node, SequenceNode):
            for child in node.value:
                self._check_keys(child)

    def _byte_index(self, character_index):
        return len(self.bom) + len(self.text[:character_index].encode("utf-8"))

    def _insertion_anchor(self, field):
        following = FIELD_ORDER[FIELD_ORDER.index(field) + 1:]
        for name in following:
            if name in self.fields:
                key = self.fields[name][0]
                line_start = self.text.rfind("\n", 0, key.start_mark.index) + 1
                line_end = self.text.find("\n", key.start_mark.index)
                eol = "\r\n" if line_end > 0 and self.text[line_end - 1] == "\r" else "\n"
                return self._byte_index(line_start), eol
        raise YamlError(f"Cannot insert {field!r} without a following template anchor; no field was appended")

    def _caption_patch(self, value):
        if not isinstance(value, str) or "\r" in value or not value:
            raise YamlError("A generated caption must be nonempty text with LF newlines")
        trailing = len(value) - len(value.rstrip("\n"))
        first_content = next((line for line in value.split("\n") if line), "")
        indicator = "|2" if first_content[:1].isspace() else "|"
        indicator += "-" if trailing == 0 else "" if trailing == 1 else "+"
        if "caption" not in self.fields:
            position, eol = self._insertion_anchor("caption")
            header, start, end = " " * self.indent + "caption: " + indicator, position, position
        else:
            key, node = self.fields["caption"]
            if not isinstance(node, ScalarNode) or node.start_mark.line != key.start_mark.line:
                raise YamlError("A caption on a separate scalar line requires explicit migration first")
            if node.style == ">" or node.style in {'"', "'"} and node.start_mark.line != node.end_mark.line:
                raise YamlError("Folded or multiline quoted captions require explicit migration first")
            line_end = self.text.find("\n", node.start_mark.index)
            line_end = len(self.text) if line_end < 0 else line_end + 1
            line = self.text[node.start_mark.index:line_end]
            eol = "\r\n" if line.endswith("\r\n") else "\n"
            if node.style == "|":
                match = re.fullmatch(r"\|(?:[1-9]?[+-]?|[+-][1-9]?)([ \t]*#[^\r\n]*)?\r?\n?", line)
                if not match:
                    raise YamlError("Caption indentation indicators require explicit migration first")
                comment = match.group(1) or ""
                end = self._byte_index(node.end_mark.index)
            else:
                comment = self.text[node.end_mark.index:line_end].rstrip("\r\n")
                end = self._byte_index(line_end)
            header = (" " if node.start_mark.index == node.end_mark.index else "") + indicator + comment
            start = self._byte_index(node.start_mark.index)
        body = "".join(" " * (self.indent + 2) + line + eol for line in value.split("\n")[:-1 if trailing else None])
        return BytePatch(start, end, (header + eol + body).encode("utf-8"), ("caption",))

    def generation_patches(self, changes):
        unknown = set(changes) - GENERATION_FIELDS
        if unknown:
            raise YamlError("Generation cannot change these fields: " + ", ".join(sorted(unknown)))
        patches, insertions = [], {}
        for field in FIELD_ORDER:
            if field not in changes:
                continue
            value = changes[field]
            if field == "caption":
                if self.values.get(field) != value:
                    patches.append(self._caption_patch(value))
                continue
            if field in self.fields:
                if self.values[field] == value and type(self.values[field]) is type(value):
                    continue
                node = self.fields[field][1]
                if not isinstance(node, ScalarNode) or node.style in {"|", ">"} or node.start_mark.line != node.end_mark.line:
                    raise YamlError(f"Generation field {field!r} must be an inline scalar before it can be changed")
                rendered = scalar_text(value, node.style)
                if node.start_mark.index == node.end_mark.index:
                    rendered = " " + rendered
                patches.append(BytePatch(self._byte_index(node.start_mark.index), self._byte_index(node.end_mark.index),
                                         rendered.encode("utf-8"), (field,)))
            else:
                position, eol = self._insertion_anchor(field)
                text = " " * self.indent + field + ": " + scalar_text(value) + eol
                group = insertions.setdefault(position, [[], []])
                group[0].append(text)
                group[1].append(field)
        patches.extend(BytePatch(position, position, "".join(parts).encode("utf-8"), tuple(fields))
                       for position, (parts, fields) in insertions.items())
        return sorted(patches, key=lambda p: (p.start, p.end, min(FIELD_ORDER.index(f) for f in p.fields)))

    def with_generation_values(self, changes):
        patches = self.generation_patches(changes)
        result, cursor = [], 0
        for patch in patches:
            if patch.start < cursor:
                raise YamlError("Overlapping byte patches were refused")
            result.extend((self.raw[cursor:patch.start], patch.replacement))
            cursor = patch.end
        result.append(self.raw[cursor:])
        raw = b"".join(result)
        output = YamlDocument(raw)
        for field, value in self.values.items():
            if field not in changes and not same_value(output.values.get(field), value):
                raise YamlError(f"Untouched field changed: {field}")
        for field, value in changes.items():
            if output.values.get(field) != value:
                raise YamlError(f"Output value did not round-trip: {field}")
        return raw
