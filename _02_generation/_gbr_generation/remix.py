"""Editable, immutable recipes derived from reviewed recordings and saved remixes."""
import json
from pathlib import Path
import re

from .catalogue import Input, identifier
from .parameters import PARAMETERS, SEEDS
from .planner import publish_new_file, sha
from .yaml_document import YamlDocument


TEXT_FIELDS = {'title', 'lyrics', 'caption', 'cluster', 'style', 'language', 'explicit', 'story'}


class Remixes:
    def __init__(self, generation):
        self.generation = generation
        self.db = generation.review.db

    def source(self, generation_id, revision=None, *, include_yaml=False):
        if not re.fullmatch(r'[0-9a-f]{64}', generation_id):
            raise ValueError('Choose a reviewed recording to remix')
        row = self.db.one('''SELECT m.*,g.original_directory,g.original_filename,g.audio_id,s.title
            FROM generation_metadata m JOIN generations g ON g.id=m.generation_id
            JOIN songs s ON s.id=g.song_id WHERE m.generation_id=?''', (generation_id,))
        if not row:
            raise ValueError('The source recording was not found')
        from _review.metadata_identity import display_metadata
        metadata = display_metadata(self.db, row)
        linked = self.db.one('''SELECT r.*,d.raw_bytes FROM generation_runs r
            JOIN source_documents d ON d.sha256=r.source_sha256
            JOIN generation_output_links l ON l.run_id=r.id
            WHERE l.review_generation_id=? ORDER BY r.rowid DESC LIMIT 1''', (generation_id,))
        if not linked and isinstance(metadata.get('gbr_run_id'), str):
            linked = self.db.one('''SELECT r.*,d.raw_bytes FROM generation_runs r
                JOIN source_documents d ON d.sha256=r.source_sha256 WHERE r.id=?''', (metadata['gbr_run_id'],))
        if revision:
            linked = self.db.one('''SELECT r.*,d.raw_bytes FROM generation_runs r
                JOIN source_documents d ON d.sha256=r.source_sha256
                WHERE r.parent_review_generation_id=? AND r.batch_id=? ORDER BY r.rowid LIMIT 1''',
                (generation_id, revision))
            if not linked:
                raise ValueError('This saved remix belongs to another recording or is unavailable')
        raw = linked['raw_bytes'] if linked else row['raw_yaml_bytes'] or (row['raw_yaml'] or '').encode('utf-8')
        try:
            values = YamlDocument(raw).values if raw else {}
        except ValueError:
            raw = None
            values = {}
        values = {**values, **{key:value for key,value in metadata.items()
                             if key in PARAMETERS|TEXT_FIELDS|{'song_id','variant_id'} and value is not None and value != ''}}
        if linked:
            values.update(json.loads(linked['effective_json']))
            values.update({key:value for key,value in YamlDocument(linked['raw_bytes']).values.items()
                           if key in TEXT_FIELDS-{'caption','cluster','style'}})
            values.update(song_id=linked['song_id'],variant_id=linked['variant_id'],title=linked['title'])
        else:
            for field in PARAMETERS:
                if field in row and row[field] is not None and row[field] != '':
                    values[field] = row[field]
        values.setdefault('title', str(metadata.get('title') or row['title']))
        for field in ('song_id', 'variant_id'):
            try:
                values[field] = identifier(values.get(field))
            except ValueError:
                values[field] = str(int(generation_id[:15],16)+1) if field=='song_id' else '1'
        for field in SEEDS:
            if values.get(field) is not None:
                values[field] = str(values[field])
        fields = {key:str(values.get(key) or '') for key in TEXT_FIELDS}
        if isinstance(values.get('explicit'), bool):
            fields['explicit'] = 'yes' if values['explicit'] else 'no'
        from .web_service import DEFAULT_PARAMETERS
        parameters = {key:values[key] for key in PARAMETERS-TEXT_FIELDS if values.get(key) is not None and values[key]!=''}
        missing = sorted(key for key in DEFAULT_PARAMETERS
                         if key not in parameters or key in SEEDS and parameters[key]=='auto')
        versions = self.db.all('''SELECT batch_id,MIN(created_at) AS created_at,COUNT(*) AS generations
            FROM generation_runs WHERE parent_review_generation_id=? AND batch_id IS NOT NULL
            GROUP BY batch_id ORDER BY created_at DESC LIMIT 50''', (generation_id,))
        model_revision = metadata.get('model_revision')
        if linked:
            model_revision = json.loads(linked['requested_json']).get('model_revision') or model_revision
        if not isinstance(model_revision,str) or not re.fullmatch(r'[0-9a-f]{40}',model_revision):
            model_revision = None
        result = {'generation_id':generation_id,'revision':revision,'song_id':values['song_id'],
                'variant_id':values['variant_id'],'fields':fields,'parameters':parameters,
                'model_revision':model_revision,'missing_parameters':missing,'versions':versions}
        if include_yaml:
            result['source_yaml'] = raw if raw and values else None
        return result

    def input(self, request):
        source = self.source(request['generation_id'],request.get('revision'),include_yaml=True)
        fields = request.get('fields') or {}
        if set(fields)-TEXT_FIELDS or any(not isinstance(value,str) for value in fields.values()):
            raise ValueError('Edit the displayed remix text fields')
        fields = {**source['fields'], **fields}
        if not fields['title'].strip() or not fields['lyrics'].strip() or not fields['caption'].strip():
            raise ValueError('Remixes need a title, lyrics and caption')
        if fields['explicit'] not in {'','yes','no'}:
            raise ValueError('Explicit must be yes or no')
        from _gbr_writing.service import patch_fields
        template_path = self.generation.root/'_01_writing/_built/track-template.yaml'
        if not template_path.is_file():
            template_path = Path(__file__).resolve().parents[2]/'_01_writing/_built/track-template.yaml'
        template = template_path.read_bytes()
        values = {**source['parameters'],**fields,'song_id':source['song_id'],'variant_id':source['variant_id']}
        raw = patch_fields(source.pop('source_yaml') or template,values,template)
        directory = self.generation.runtime/'remixes'/sha(raw)
        directory.mkdir(parents=True,exist_ok=True)
        path = directory/'source.yaml'
        try:
            publish_new_file(path,raw)
        except FileExistsError:
            if path.read_bytes()!=raw:
                raise ValueError('The saved remix source changed') from None
        return Input(path,source['song_id'],source['variant_id'],{'_master_titles':[fields['title']]}),source
