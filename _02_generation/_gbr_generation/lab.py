"""Private, immutable short-test recipes on the existing experiment ledger."""
from pathlib import Path

from .catalogue import Catalogue, Input, identifier
from .parameters import PARAMETERS
from .planner import publish_new_file, sha
from .yaml_document import YamlDocument


class Lab:
    def __init__(self, generation):
        self.generation = generation

    def source(self, root, path):
        catalogue = Catalogue(self.generation.catalogue_root(root))
        selected = catalogue.select(files=[path])
        if len(selected) != 1:
            raise ValueError('Choose one writing recipe for the lab')
        item = selected[0]
        values = YamlDocument(item.path.read_bytes()).values
        return item, values

    def input(self, request, root):
        fields = {key: request.get(key, '') for key in ('title', 'caption', 'lyrics')}
        if any(not isinstance(v, str) or not v.strip() for v in fields.values()):
            raise ValueError('Give the trial a name, caption and a short lyric excerpt')
        if len(fields['title']) > 160 or len(fields['caption']) > 20000 or len(fields['lyrics']) > 40000:
            raise ValueError('Keep lab captions and lyrics to a short test excerpt')
        original, values = None, {}
        if request.get('source_path'):
            original, values = self.source(root, request['source_path'])
        template = self.generation.root/'_01_writing/_built/track-template.yaml'
        if not template.is_file():
            template = Path(__file__).resolve().parents[2]/'_01_writing/_built/track-template.yaml'
        raw_template = template.read_bytes()
        fallback = str(int(sha(fields['title'].encode())[:12], 16)+1)
        def identity(value, default):
            try: return identifier(value)
            except ValueError: return default
        sid = identity((original.song_id if original else None) or values.get('song_id'), fallback)
        vid = identity((original.variant_id if original else None) or values.get('variant_id'), '1')
        values.update(fields, song_id=sid, variant_id=vid)
        values['cluster'] = values.get('cluster') or 'Quick lab'
        values['style'] = values.get('style') or 'Custom trial'
        from _gbr_writing.service import patch_fields
        raw = patch_fields(original.path.read_bytes() if original else raw_template, values, raw_template)
        path = self.generation.runtime/'lab'/sha(raw)/'source.yaml'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            publish_new_file(path, raw)
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError('The saved lab recipe changed') from None
        return Input(path, sid, vid, {'_master_titles': [fields['title']]}), values

    def read(self, root, path):
        _, values = self.source(root, path)
        return {'fields': {key: str(values.get(key) or '') for key in ('title', 'caption', 'lyrics')},
                'parameters': {key: str(values[key]) if key.endswith('_seed') else values[key]
                               for key in PARAMETERS if values.get(key) is not None}}
