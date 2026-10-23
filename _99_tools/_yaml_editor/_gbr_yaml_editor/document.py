"""Surgical UTF-8 YAML edits. No whole-document serialization."""
from dataclasses import dataclass
import difflib
import re
import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, ScalarNode
from _gbr_generation.yaml_document import FIELD_ORDER, YamlDocument, YamlError, same_value, scalar_text

KEY = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*\Z')


def key_name(value):
    if not isinstance(value, str) or not KEY.fullmatch(value):
        raise YamlError('Use a simple top-level field name (letters, numbers, underscores or hyphens)')
    return value


@dataclass
class Entry:
    name: str
    key: object
    value: object
    start: int
    end: int


class Source:
    def __init__(self, raw):
        self.raw = raw
        self.bom = b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
        try:
            self.text = raw[len(self.bom):].decode('utf-8')
            for event in yaml.parse(self.text, Loader=yaml.SafeLoader):
                if isinstance(event, AliasEvent) or getattr(event, 'anchor', None) is not None or getattr(event, 'tag', None) is not None:
                    raise YamlError('Anchors, aliases and explicit tags require text repair first')
            root = yaml.compose(self.text, Loader=yaml.SafeLoader)
            if not isinstance(root, MappingNode) or root.flow_style or not root.value:
                raise YamlError('Expected a nonempty block mapping; use text repair for this file')
            self.entries = []
            for key, value in root.value:
                if not isinstance(key, ScalarNode) or key.tag != 'tag:yaml.org,2002:str' or key.start_mark.line != key.end_mark.line:
                    raise YamlError('Top-level keys must be single-line strings')
                start = self.text.rfind('\n', 0, key.start_mark.index) + 1
                end = value.end_mark.index
                if value.end_mark.column != 0:
                    newline = self.text.find('\n', end)
                    end = len(self.text) if newline < 0 else newline + 1
                self.entries.append(Entry(key.value, key, value, start, end))
            self.indent = root.value[0][0].start_mark.column
        except (yaml.YAMLError, UnicodeError) as exc:
            raise YamlError(str(exc)) from exc

    def one(self, name):
        matches = [e for e in self.entries if e.name == name]
        if len(matches) > 1:
            raise YamlError(f'Duplicate field {name!r}: add a keep-first/keep-last operation before editing it')
        return matches[0] if matches else None

    def eol(self, at=0):
        match = re.search(r'\r\n|\n', self.text[at:]) or re.search(r'\r\n|\n', self.text)
        return match.group() if match else '\n'

    def patch(self, start, end, replacement):
        # Character marks from PyYAML are converted back to bytes, preserving the BOM.
        a = len(self.bom) + len(self.text[:start].encode('utf-8'))
        b = len(self.bom) + len(self.text[:end].encode('utf-8'))
        return self.raw[:a] + replacement.encode('utf-8') + self.raw[b:]

    def insert_template(self, name, block, order):
        """Insert a missing block beside its nearest template neighbour, without moving other bytes."""
        if self.one(name):
            return self.raw
        index = order.index(name)
        following = next((self.one(n) for n in order[index+1:] if self.one(n)), None)
        preceding = next((self.one(n) for n in reversed(order[:index]) if self.one(n)), None)
        at = following.start if following else preceding.end if preceding else self.entries[-1].end
        eol = self.eol(at)
        block = block.replace('\r\n', '\n').replace('\n', eol)
        prefix = eol if at and not self.text[:at].endswith(('\n', '\r')) else ''
        if not block.endswith(('\n', '\r')):
            block += eol
        return self.patch(at, at, prefix + block)

    def set(self, name, value, operation):
        entry = self.one(name)
        if entry and operation.get('missing_only'):
            return self.raw
        if entry:
            old = yaml.safe_load(self.text)[name]
            if same_value(old, value):
                return self.raw
            node = entry.value
            if not isinstance(node, ScalarNode):
                raise YamlError(f'{name}: structured values require text repair')
            if node.start_mark.line != entry.key.start_mark.line:
                raise YamlError(f'{name}: a value on a separate line requires text repair')
            eol = self.eol(entry.start)
            inline = node.style not in {'|', '>'} and node.start_mark.line == node.end_mark.line
            if inline and not (isinstance(value, str) and '\n' in value):
                rendered = scalar_text(value, node.style)
                if node.start_mark.index == node.end_mark.index:
                    rendered = ' ' + rendered
                return self.patch(node.start_mark.index, node.end_mark.index, rendered)
            line_end = self.text.find('\n', node.start_mark.index)
            line_end = len(self.text) if line_end < 0 else line_end + 1
            if inline:
                comment = self.text[node.end_mark.index:line_end].rstrip('\r\n')
                end = line_end
            else:
                header = self.text[node.start_mark.index:line_end].rstrip('\r\n')
                match = re.fullmatch(r'[|>](?:[1-9]?[+-]?|[+-][1-9]?)([ \t]*#[^\r\n]*)?', header)
                if not match:
                    raise YamlError(f'{name}: unsupported block header; use text repair')
                comment = match.group(1) or ''
                end = node.end_mark.index
            rendered = render_value(value, self.indent, eol, comment)
            return self.patch(node.start_mark.index, end, rendered)
        anchor = operation.get('anchor', '').strip()
        position = operation.get('position', 'template')
        if position in {'before', 'after'}:
            target = self.one(key_name(anchor))
            if not target:
                raise YamlError(f'Insertion anchor {anchor!r} is missing')
            at = target.start if position == 'before' else target.end
        elif position == 'start':
            at = self.entries[0].start
        elif position == 'end':
            at = self.entries[-1].end
        elif position == 'template':
            following = FIELD_ORDER[FIELD_ORDER.index(name)+1:] if name in FIELD_ORDER else ()
            target = next((self.one(n) for n in following if self.one(n)), None)
            if not target:
                raise YamlError(f'Choose before/after/start/end to insert {name!r}; no template anchor exists')
            at = target.start
        else:
            raise YamlError('Unknown insertion position')
        eol = self.eol(at)
        prefix = eol if at and not self.text[:at].endswith(('\n', '\r')) else ''
        rendered = ' ' * self.indent + name + ': ' + render_value(value, self.indent, eol)
        return self.patch(at, at, prefix + rendered)


def render_value(value, indent, eol, comment=''):
    if isinstance(value, str) and '\n' in value:
        if '\r' in value:
            raise YamlError('Use LF within the entered value; file line endings are preserved separately')
        trailing = len(value) - len(value.rstrip('\n'))
        first = next((s for s in value.split('\n') if s), '')
        indicator = '|2' if first[:1].isspace() else '|'
        indicator += '-' if trailing == 0 else '' if trailing == 1 else '+'
        lines = value.split('\n')[:-1 if trailing else None]
        return indicator + comment + eol + ''.join(' ' * (indent+2) + s + eol for s in lines)
    return scalar_text(value) + comment + eol


def edit(raw, operations, catalogue_ids=None):
    for operation in operations:
        source = Source(raw)
        kind = operation.get('kind')
        if kind == 'catalogue_ids':
            if catalogue_ids is None:
                raise YamlError('No unique, valid catalogue row matches this file')
            for name in ('song_id', 'variant_id'):
                raw = Source(raw).set(name, int(catalogue_ids[name]), {'missing_only': operation.get('missing_only', True), 'position': 'template'})
            continue
        if kind == 'add_template':
            before = yaml.safe_load(source.text)
            for field in operation['template_fields']:
                current = Source(raw)
                if current.one(field['key']):
                    continue
                block = field['block']
                if current.indent != operation['template_indent']:
                    delta = current.indent - operation['template_indent']
                    block = ''.join((' ' * delta + line if delta >= 0 else line[-delta:])
                                    if line.strip() else line for line in block.splitlines(keepends=True))
                raw = current.insert_template(field['key'], block, operation['keys'])
                if field['key'] in operation.get('defaults', {}):
                    raw = Source(raw).set(field['key'], operation['defaults'][field['key']], {})
            if operation.get('reorder', True):
                raw = edit(raw, [{'kind': 'reorder', 'keys': operation['keys']}])
            after = yaml.safe_load(raw.decode('utf-8-sig'))
            if any(not same_value(value, after.get(key)) for key, value in before.items()):
                raise YamlError('Adding template fields would change an existing value')
            continue
        if kind == 'reorder':
            names = operation.get('keys', [])
            if not isinstance(names, list) or len(set(names)) != len(names) or not names:
                raise YamlError('Enter a nonempty list of unique field names')
            for name in names:
                key_name(name)
            entries = [source.one(n) for n in names if source.one(n)]
            slots = sorted(entries, key=lambda e: e.start)
            text = source.text
            # Replace only the requested field slots; unlisted fields and gaps stay in place.
            for slot, incoming in reversed(list(zip(slots, entries))):
                block = source.text[incoming.start:incoming.end]
                if slot.end < len(source.text) and not block.endswith(('\n', '\r')):
                    block += source.eol(slot.start)
                text = text[:slot.start] + block + text[slot.end:]
            raw = source.bom + text.encode('utf-8')
            if not same_value(yaml.safe_load(source.text), yaml.safe_load(text)):
                raise YamlError('Reordering would change a value because of block whitespace; use text repair')
            continue
        name = key_name(operation.get('key'))
        if kind == 'deduplicate':
            matches = [e for e in source.entries if e.name == name]
            keep = operation.get('keep', 'first')
            if keep not in {'first', 'last'}:
                raise YamlError('Keep must be first or last')
            drop = matches[1:] if keep == 'first' else matches[:-1]
            for entry in reversed(drop):
                raw = Source(raw).patch(entry.start, entry.end, '')
        elif kind == 'set':
            raw = source.set(name, operation.get('value'), operation)
            if not (source.one(name) and operation.get('missing_only')):
                if not same_value(yaml.safe_load(raw.decode('utf-8-sig'))[name], operation.get('value')):
                    raise YamlError(f'{name}: replacement did not preserve the entered value')
        elif kind == 'remove':
            entry = source.one(name)
            if entry:
                raw = source.patch(entry.start, entry.end, '')
        elif kind == 'rename':
            new = key_name(operation.get('new_key'))
            entry = source.one(name)
            if new == name or not entry:
                continue
            if source.one(new):
                raise YamlError(f'Cannot rename {name!r}: {new!r} already exists')
            raw = source.patch(entry.key.start_mark.index, entry.key.end_mark.index, new)
        else:
            raise YamlError('Unknown edit operation')
    YamlDocument(raw)  # Fail closed on duplicates, malformed output and unsupported structures.
    return raw


def repair_text(raw, entered):
    source_bom = b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
    original = raw[len(source_bom):].decode('utf-8')
    old_lines = original.splitlines(keepends=True)
    normalized = original.replace('\r\n', '\n')
    entered = entered.replace('\r\n', '\n')
    before, after = normalized.splitlines(keepends=True), entered.splitlines(keepends=True)
    eol = '\r\n' if '\r\n' in original else '\n'
    result = []
    for tag, i, j, a, b in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
        if tag == 'equal':
            result.extend(old_lines[i:j])
        elif tag in {'insert', 'replace'}:
            result.extend(line[:-1] + eol if line.endswith('\n') else line for line in after[a:b])
    updated = source_bom + ''.join(result).encode('utf-8')
    YamlDocument(updated)
    return updated
