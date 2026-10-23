"""Validate the editable process guide and stage only its public dependencies."""
import json
from hashlib import sha256
import math
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.parse import urlsplit
import yaml

ID = re.compile(r'^[a-z][a-z0-9-]{0,63}$')
MEDIA = {'.png','.jpg','.jpeg','.gif','.webp','.mp3','.flac','.wav','.ogg','.mp4','.webm'}


def require(condition, message):
    if not condition:
        raise ValueError('How it’s made: ' + message)


def identifier(value):
    return isinstance(value, str) and bool(ID.fullmatch(value))


def text(value):
    return isinstance(value, str) and bool(value.strip())


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(payload, source):
    require(isinstance(payload, dict) and payload.get('format') == 'gbr-making-v1', 'unsupported content format')
    source = Path(source)
    media = set()

    def url(value):
        require(text(value), 'missing URL')
        parsed = urlsplit(value)
        require(not parsed.username and not parsed.password, 'URLs must not contain credentials')
        if parsed.scheme == 'https':
            require(bool(parsed.netloc) and '\\' not in value, 'invalid HTTPS URL')
            return
        if value.startswith('showcase:'):
            path = value[len('showcase:'):]
            require(bool(path) and not path.startswith('/') and all(p not in {'.','..',''} for p in path.split('/')) and not any(c in path for c in '\\?#%'), 'invalid showcase path')
            return
        require(value.startswith('media/') and not parsed.scheme and not parsed.netloc and not parsed.query and not parsed.fragment, 'URL must be HTTPS, showcase: or media/')
        require(not any(c in value for c in '\\%') and all(p not in {'..','.',''} for p in value.split('/')), 'invalid local media path')
        require(PurePosixPath(value).suffix.lower() in MEDIA, 'unsupported local media type')
        relative = PurePosixPath(value).relative_to('media')
        base = source / '_media'
        path = base.joinpath(*relative.parts)
        require(path.resolve().is_relative_to(base.resolve()), 'media escapes the media directory')
        require(path.is_file() and not any(p.is_symlink() for p in [path, *path.parents]), 'missing or symlinked media: ' + value)
        media.add(relative.as_posix())

    stages = payload.get('stages')
    topics = payload.get('topics', [])
    require(isinstance(stages, list) and 3 <= len(stages) <= 9, 'provide 3–9 wheel stages')
    require(isinstance(topics, list), 'topics must be a list')
    stage_ids, topic_ids = {}, set()
    for stage in stages:
        require(isinstance(stage, dict) and identifier(stage.get('id')) and stage['id'] not in stage_ids, 'invalid or duplicate stage ID')
        require(text(stage.get('title')) and text(stage.get('summary')), 'stages need a title and summary')
        require(bool(re.fullmatch(r'#[0-9a-fA-F]{6}', str(stage.get('colour','')))), 'stage colour must be #RRGGBB')
        require(isinstance(stage.get('delivers', []), list) and all(text(x) for x in stage.get('delivers', [])), 'invalid stage outputs')
        nodes = stage.get('nodes')
        require(isinstance(nodes, list) and 1 <= len(nodes) <= 80, 'provide 1–80 nodes per stage')
        ids = set()
        for node in nodes:
            require(isinstance(node, dict) and identifier(node.get('id')) and node['id'] not in ids, 'invalid or duplicate node ID')
            ids.add(node['id'])
            require(text(node.get('title')) and text(node.get('summary')), 'nodes need a title and summary')
            require(node.get('kind','step') in {'step','decision','output'}, 'unknown node kind')
        require(stage.get('entry') in ids, 'stage entry is missing')
        stage_ids[stage['id']] = ids
        edges = {}
        for node in nodes:
            links = node.get('next', [])
            require(isinstance(links, list) and len(links) <= 8, 'node next must be a list of at most eight edges')
            require(all(isinstance(e, dict) and e.get('to') in ids and (not e.get('label') or text(e['label'])) for e in links), 'edge target is missing or invalid')
            require(len({e['to'] for e in links}) == len(links), 'duplicate edge')
            edges[node['id']] = [e['to'] for e in links]
        visited, visiting = set(), set()
        def walk(node):
            require(node not in visiting, 'workflow cycle: use a stage-link for returns')
            if node in visited:
                return
            visiting.add(node)
            for child in edges[node]:
                walk(child)
            visiting.remove(node)
            visited.add(node)
        walk(stage['entry'])
        require(visited == ids, 'all nodes must be reachable from the stage entry')
    for topic in topics:
        require(isinstance(topic, dict) and identifier(topic.get('id')) and topic['id'] not in topic_ids and text(topic.get('title')), 'invalid or duplicate topic')
        topic_ids.add(topic['id'])
    experiments = set()
    for owner in [n for s in stages for n in s['nodes']] + topics:
        blocks = owner.get('body', [])
        require(isinstance(blocks, list), 'body must be a list of blocks')
        for block in blocks:
            require(isinstance(block, dict), 'invalid body block')
            kind = block.get('type')
            if kind in {'paragraph','heading','note','code'}:
                require(text(block.get('text')), 'text block is empty')
            elif kind == 'media':
                require(block.get('kind') in {'image','audio','video'}, 'unknown media kind')
                url(block.get('src'))
                if block['kind'] == 'image':
                    require(text(block.get('alt')), 'images need alt text')
                if block.get('poster'):
                    url(block['poster'])
            elif kind == 'links':
                require(isinstance(block.get('items'), list) and block['items'], 'links need items')
                for item in block['items']:
                    require(isinstance(item, dict) and text(item.get('label')), 'link needs a label')
                    url(item.get('url'))
            elif kind == 'stage-link':
                require(block.get('stage') in stage_ids and (not block.get('node') or block['node'] in stage_ids[block['stage']]) and text(block.get('label')), 'invalid stage-link')
            elif kind == 'topic-link':
                require(block.get('topic') in topic_ids and text(block.get('label')), 'invalid topic-link')
            elif kind == 'analytics-link':
                require(text(block.get('label')), 'analytics-link needs a label')
            elif kind == 'experiment':
                require(identifier(block.get('id')) and block['id'] not in experiments, 'invalid or duplicate experiment ID')
                experiments.add(block['id'])
                require(all(text(block.get(k)) for k in ['title','parameter','conditions']), 'experiment needs a title, parameter and fixed conditions')
                lo, hi, step = [block.get(k) for k in ['min','max','step']]
                require(all(number(x) for x in [lo,hi,step]) and step > 0 and hi > lo and (hi-lo)/step <= 5000, 'invalid experiment range')
                require(math.isclose((hi-lo)/step, round((hi-lo)/step), abs_tol=1e-7), 'range endpoint is not on the step grid')
                require(isinstance(block.get('samples'), list), 'experiment samples must be a list')
                values = set()
                for sample in block['samples']:
                    require(isinstance(sample, dict) and number(sample.get('value')), 'invalid sample value')
                    value = sample['value']
                    require(lo <= value <= hi and math.isclose((value-lo)/step, round((value-lo)/step), abs_tol=1e-7), 'sample is outside its parameter grid')
                    key = round(value, 8)
                    require(key not in values, 'duplicate sample value')
                    values.add(key)
                    url(sample.get('audio'))
            else:
                require(False, 'unknown block type: ' + str(kind))
    return media


def stage(root, target):
    root, target = Path(root), Path(target)
    source = root / '_content/_how_its_made'
    try:
        payload = yaml.safe_load((source / 'tour.yaml').read_text(encoding='utf-8-sig'))
    except yaml.YAMLError as error:
        raise ValueError('How it’s made: invalid tour.yaml: ' + str(error)) from error
    media = validate(payload, source)
    destination = target / 'how-its-made'
    destination.mkdir()
    for name in ('index.html','making.css','making.js'):
        shutil.copyfile(root / '_how_its_made' / name, destination / name)
    page = destination / 'index.html'
    html = page.read_text(encoding='utf-8')
    for asset in ('making.css', 'making.js'):
        version = sha256((destination / asset).read_bytes()).hexdigest()[:12]
        html = html.replace(asset + '?v=1', asset + '?v=' + version)
    page.write_text(html, encoding='utf-8')
    for relative in sorted(media):
        output = destination / 'media' / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / '_media' / relative, output)
    (destination / 'tour.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return {'stages':len(payload['stages']), 'topics':len(payload.get('topics',[])), 'media_files':len(media)}
