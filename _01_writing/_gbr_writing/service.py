"""File-preserving writing drafts and journalled catalogue publication in the shared DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import csv
import difflib
import hashlib
import io
import json
import os
import re
from pathlib import Path
import threading
import time
import uuid

from _gbr_generation.catalogue import Catalogue, Styles, identifier as catalogue_id, title_key, validate_template_format
from _gbr_generation.yaml_document import YamlDocument, same_value
from _gbr_yaml_editor.document import Source, edit
from _gbr_worker.hardware import gpu_lock
from _gbr_worker.llama import LlamaClient, address
from _review.reviewers import Conflict
from _review.util import now
from .references import retrieve, prompt

FIELDS = {'title','lyrics','caption','story','language','cluster','style','explicit'}
KINDS = {'new':{'title','lyrics','caption','story'}, 'lyrics':{'lyrics'}, 'caption':{'caption'},
         'both':{'lyrics','caption'}, 'translate':{'lyrics'}}
DEFAULTS = {'endpoint':'http://127.0.0.1:8080', 'model':'', 'temperature':0.8, 'top_p':0.95, 'max_tokens':4096}


def sha(raw):return hashlib.sha256(raw).hexdigest()
def packed(value):return json.dumps(value,ensure_ascii=False,allow_nan=False)


def patch_fields(raw, changes, template):
    order = [e.name for e in Source(template).entries]
    for key, value in changes.items():
        current = Source(raw)
        if current.one(key) and same_value(YamlDocument(raw).values.get(key), value):continue
        operation = {'kind':'set','key':key,'value':value}
        if not current.one(key):
            following = next((n for n in order[order.index(key)+1:] if current.one(n)), None) if key in order else None
            following = following or next((n for n in ('caption','lyrics') if n != key and current.one(n)), None)
            operation.update(position='before' if following else 'after',anchor=following or current.entries[-1].name)
            if key == 'style' and current.one('cluster'):operation.update(position='after',anchor='cluster')
        raw = edit(raw,[operation])
    return raw


def csv_bytes(original, row, replace_filename=None):
    bom = b'\xef\xbb\xbf' if original.startswith(b'\xef\xbb\xbf') else b''
    text = original.decode('utf-8-sig'); eol = '\r\n' if '\r\n' in text else '\n'
    reader = csv.DictReader(io.StringIO(text,newline='')); names = reader.fieldnames
    if not names or 'filename' not in names:raise ValueError('Catalogue requires a filename column')
    rows = list(reader)
    if any(None in r for r in rows):raise ValueError('Malformed catalogue row; fix it before publishing')
    handle = io.StringIO(newline=''); writer = csv.DictWriter(handle,names,lineterminator=eol)
    if replace_filename is None:
        writer.writerow({k:row.get(k,'') for k in names})
        return original + (eol.encode() if original and not original.endswith((b'\n',b'\r')) else b'') + handle.getvalue().encode('utf8')
    matches = [r for r in rows if Path(r['filename'].replace('\\','/')).name == replace_filename]
    if len(matches) != 1:raise ValueError('The source must have exactly one matching catalogue row')
    matches[0].update({k:v for k,v in row.items() if k in names})
    writer.writeheader();writer.writerows(rows)
    return bom + handle.getvalue().encode('utf8')


class WritingService:
    def __init__(self, root, review):
        self.root, self.review = Path(root), review
        self.lock = threading.RLock();self.futures = {};self.pool = ThreadPoolExecutor(max_workers=1,thread_name_prefix='gbr-writing')
        self.ready = bool(review.db.one("SELECT 1 FROM sqlite_master WHERE name='worker_sessions'"))
        if self.ready:
            with review.db.transaction() as connection:
                connection.executescript((Path(__file__).resolve().parents[2]/'_98_database/_gbr_database/writing.sql').read_text(encoding="utf-8"))

    def close(self):self.pool.shutdown(wait=False,cancel_futures=True)
    @property
    def built(self):return self.root/'_01_writing/_built'
    @property
    def template(self):return (self.built/'track-template.yaml').read_bytes()

    def settings(self):
        saved = self.review.config.values.get('writing',{})
        return {**DEFAULTS, **saved, 'binary_directory':str(self.root/'_99_tools/_llama_cpp'), 'database_ready':self.ready}

    def save_settings(self, values):
        values = {**DEFAULTS,**values};values['endpoint'] = address(values['endpoint'])
        if set(values) != set(DEFAULTS):raise ValueError('Unsupported writing worker setting')
        if not isinstance(values['model'],str) or len(values['model']) > 1000:raise ValueError('Invalid model ID')
        if not 0 <= values['temperature'] <= 2 or not 0 < values['top_p'] <= 1 or not 256 <= values['max_tokens'] <= 32768:raise ValueError('Invalid writing sampling settings')
        packed(values)
        with self.review.config_lock:
            self.review.config.values['writing'] = values;self.review.config.save()
        return self.settings()

    def query(self):return LlamaClient(self.settings()['endpoint']).query()

    def catalogue(self):
        catalogue = Catalogue(self.built)
        return {'inputs':[{'file':str(i.path.relative_to(self.built)),'song_id':i.song_id,'variant_id':i.variant_id,
                         'issues':[issue.message for issue in i.issues]} for i in catalogue.inputs], 'root':str(self.built)}

    def source(self, filename):
        path = (self.built/filename).resolve()
        if not path.is_relative_to(self.built.resolve()) or path.suffix.casefold() not in {'.yaml','.yml'} or path.name == 'track-template.yaml':
            raise ValueError('Choose a track YAML within _built')
        if not path.is_file():raise ValueError('Writing source was not found: '+filename)
        return path, path.read_bytes()

    def submit(self, request, reviewer=None):
        if not self.ready:raise ValueError('Writing drafts use the migrated shared database; launch GBR through _97_app')
        kind = request['kind']
        if kind not in KINDS:raise ValueError('Unknown writing operation')
        if not request.get('brief','').strip():raise ValueError('Enter a writing brief first')
        if kind == 'translate' and not request.get('language'):raise ValueError('Choose a target language for translation')
        files = request.get('files',[])
        if kind == 'new' and files or kind != 'new' and not files:raise ValueError('Select existing YAMLs for rewrites, or use New song with no source selected')
        if len(set(files)) != len(files):raise ValueError('A writing source was selected twice')
        prepared = []
        for filename in files or [None]:
            path,raw = self.source(filename) if filename else (None,self.template)
            source = YamlDocument(raw).values
            references,warnings = retrieve(self.root,request,source)
            messages = prompt(request,source,references)
            identifier = uuid.uuid4().hex
            frozen = {**request,'settings':{k:self.settings()[k] for k in DEFAULTS},'warnings':warnings,
                      'references':[{k:v for k,v in r.items() if k != 'text'} for r in references]}
            prepared.append((identifier,path,raw,frozen,messages))
        # Validate the complete selection before queueing any model request.
        with self.review.db.transaction() as connection:
            for identifier,path,raw,frozen,messages in prepared:
                connection.execute('INSERT INTO writing_tasks(id,kind,status,reviewer_id,source_path,source_sha256,source_yaml,request_json,prompt_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (identifier,kind,'queued',reviewer,str(path) if path else None,sha(raw),raw,packed(frozen),packed(messages),now()))
        for identifier,*_ in prepared:self.futures[identifier] = self.pool.submit(self.run,identifier)
        return {'tasks':[i[0] for i in prepared]}

    def run(self, identifier):
        try:
            with self.lock, self.review.db.transaction() as connection:
                changed = connection.execute("UPDATE writing_tasks SET status='running' WHERE id=? AND status='queued'",(identifier,)).rowcount
            if not changed:return
            task = self.task(identifier);request = json.loads(task['request_json']);settings = request['settings']
            started = time.perf_counter()
            with gpu_lock('gbr-writing:'+settings['endpoint']):
                values,telemetry = LlamaClient(settings['endpoint']).complete(json.loads(task['prompt_json']),settings)
            if set(values) != KINDS[task['kind']] or any(not isinstance(v,str) or not v.strip() for v in values.values()):
                raise ValueError('Writing model returned missing or unrequested fields; the source was not changed')
            if 'caption' in values:validate_template_format(values['caption'],'llm')
            if request.get('language') and task['kind'] in {'new','translate'}:values['language'] = request['language']
            for field in ('cluster','style'):
                if request.get(field):values[field] = request[field]
            result = patch_fields(task['source_yaml'],values,self.template)
            telemetry['elapsed_seconds'] = time.perf_counter()-started
            environment = {'model':telemetry['model'],'reported_models':telemetry['worker'].get('model_details',[])}
            session = sha(packed({'address':settings['endpoint'],**environment}).encode())
            with self.review.db.transaction() as connection:
                connection.execute('INSERT OR IGNORE INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (session,settings['endpoint'],'llama_cpp',None,now(),None,None,None,packed({'measurement_coverage':'unavailable'}),packed(environment)))
                connection.execute("UPDATE writing_tasks SET status='ready',result_yaml=?,result_json=?,worker_session_id=?,completed_at=? WHERE id=?",
                    (result,packed({'telemetry':telemetry,'edits':[]}),session,now(),identifier))
        except Exception as error:
            with self.review.db.transaction() as connection:
                connection.execute("UPDATE writing_tasks SET status='failed',error=?,completed_at=? WHERE id=?",(str(error),now(),identifier))

    def task(self, identifier):
        task = self.review.db.one('SELECT * FROM writing_tasks WHERE id=?',(identifier,))
        if not task:raise ValueError('Writing draft was not found')
        return task

    def public(self, identifier):
        task = self.task(identifier);raw = task['result_yaml'] or task['source_yaml']
        return {'id':identifier,'kind':task['kind'],'status':task['status'],'source':task['source_path'], 'error':task['error'],
                'revision':task['revision'],'created_at':task['created_at'],'active':identifier in self.futures and not self.futures[identifier].done(),
                'values':YamlDocument(raw).values,'yaml':raw.decode('utf-8-sig'), 'warnings':json.loads(task['request_json']).get('warnings',[]),
                'diff':''.join(difflib.unified_diff(task['source_yaml'].decode('utf-8-sig').splitlines(True),raw.decode('utf-8-sig').splitlines(True),fromfile='source',tofile='draft')),
                'details':json.loads(task['result_json']),'publication':json.loads(task['publication_json'])}

    def tasks(self):
        if not self.ready:return []
        rows = self.review.db.all('SELECT id,kind,status,source_path,source_yaml,result_yaml,created_at,error FROM writing_tasks ORDER BY rowid DESC LIMIT 100')
        return [{'id':r['id'],'kind':r['kind'],'status':r['status'],'source':r['source_path'],'created_at':r['created_at'],
                 'title':YamlDocument(r['result_yaml'] or r['source_yaml']).values.get('title') or 'Untitled draft', 'error':r['error'],
                 'active':r['id'] in self.futures and not self.futures[r['id']].done()} for r in rows]

    def update(self, identifier, changes, revision, reviewer=None):
        if not changes or set(changes)-FIELDS or any(not isinstance(v,str) for v in changes.values()):raise ValueError('Edit only the displayed writing fields')
        if 'explicit' in changes and changes['explicit'] not in {'yes','no'}:raise ValueError('Explicit must be yes or no')
        with self.lock,self.review.db.transaction() as connection:
            task = dict(connection.execute('SELECT * FROM writing_tasks WHERE id=?',(identifier,)).fetchone() or {})
            if not task:raise ValueError('Draft was not found')
            if task['status'] != 'ready' or task['revision'] != revision:raise Conflict('This draft changed; reload it before saving')
            source = YamlDocument(task['result_yaml']).values
            result = patch_fields(task['result_yaml'],changes,self.template)
            details = json.loads(task['result_json']);details.setdefault('edits',[]).append({'at':now(),'reviewer_id':reviewer,'before':{k:source.get(k) for k in changes},'after':changes})
            connection.execute('UPDATE writing_tasks SET result_yaml=?,result_json=?,revision=revision+1 WHERE id=?',(result,packed(details),identifier))
        return self.public(identifier)

    def cancel(self, identifier):
        with self.review.db.transaction() as connection:
            if not connection.execute("UPDATE writing_tasks SET status='cancelled' WHERE id=? AND status='queued'",(identifier,)).rowcount:
                raise ValueError('Only a queued writing request can be cancelled')
        return self.public(identifier)

    def retry(self, identifier, reviewer=None):
        task = self.task(identifier)
        if identifier in self.futures and not self.futures[identifier].done():raise ValueError('The writing request is still running')
        request = json.loads(task['request_json'])
        request = {k:v for k,v in request.items() if k not in {'settings','warnings','references'}}
        request['files'] = [str(Path(task['source_path']).relative_to(self.built))] if task['source_path'] else []
        request['parent_task_id'] = identifier
        return self.submit(request,reviewer)

    def publish(self, identifier, mode, revision):
        with self.lock,gpu_lock('gbr-writing-catalogue:'+str(self.built.resolve())):
            task = self.task(identifier)
            if task['status'] == 'published':return self.public(identifier)
            if task['status'] == 'publishing':return self.finish_publication(identifier)
            if task['status'] != 'ready' or task['revision'] != revision:raise Conflict('Reload the current draft before publishing')
            raw = task['result_yaml'];values = YamlDocument(raw).values
            if not all(isinstance(values.get(k),str) and values[k].strip() for k in ('title','lyrics')):raise ValueError('Title and lyrics are required before publishing')
            if values.get('caption') and values['caption'] != YamlDocument(task['source_yaml']).values.get('caption'):
                validate_template_format(values['caption'],'llm')
            master = self.built/'track_list.csv';variant = self.built/'variant_list.csv'
            masters = list(csv.DictReader(io.StringIO(master.read_bytes().decode('utf-8-sig'))))
            variants = list(csv.DictReader(io.StringIO(variant.read_bytes().decode('utf-8-sig'))))
            source = Path(task['source_path']) if task['source_path'] else None
            if source and (not source.is_file() or sha(source.read_bytes()) != task['source_sha256']):raise Conflict('Source YAML changed; review a fresh draft before publishing')
            if source:
                base = YamlDocument(task['source_yaml']).values
                try:song = int(catalogue_id(base.get('song_id')));old_variant = int(catalogue_id(base.get('variant_id')))
                except ValueError:raise ValueError('Fix source song/variant IDs before publishing a rewrite') from None
                matches = [r for r in masters if str(r['song_id']) == str(song)]
                if len(matches) != 1:raise ValueError('Source requires one master track record')
                if mode == 'archive_replace':
                    number = old_variant;catalogue = master if number == 1 else variant;target = source
                elif mode == 'keep':
                    numbers = [int(r['variant_id']) for r in variants if r['song_id'] == str(song) and str(r['variant_id']).isdigit()]
                    number = max([1,*numbers])+1;catalogue = variant
                    target = source.with_name((title_key(values['title']) or 'song')+'__v'+str(number)+'.yaml')
                else:raise ValueError('Choose keep-original or archive-and-replace')
                records = masters if old_variant == 1 else variants
                source_rows = [r for r in records if r['song_id'] == str(song) and Path(r['filename'].replace('\\','/')).name == source.name and (old_variant == 1 or r.get('variant_id') == str(old_variant))]
                if len(source_rows) != 1:raise ValueError('Source IDs and filename must match one catalogue record')
                row = dict(source_rows[0])
            else:
                song = max([0,*[int(r['song_id']) for r in masters if str(r['song_id']).isdigit()]])+1
                number = 1;catalogue = master;target = self.built/'_yaml'/((title_key(values['title']) or 'song')+'.yaml');row = {}
            if target.exists() and target != source:raise ValueError('Destination YAML already exists; choose another title or fix the catalogue')
            raw = patch_fields(raw,{'song_id':song,'variant_id':number},self.template)
            row.setdefault('author','gbr')
            row.update(song_id=str(song),variant_id=str(number),filename=str(target.relative_to(self.built)).replace('\\','/'),
                       song_name=values['title'])
            original_values = YamlDocument(task['source_yaml']).values
            if not source or any(values.get(k) != original_values.get(k) for k in ('cluster','style')):
                cluster,style = values.get('cluster') or '',values.get('style') or ''
                if bool(cluster) != bool(style):raise ValueError('Set both cluster and style, or leave both blank')
                matches = Styles(self.root/'_01_writing/_references/styles.csv').candidates(cluster,style) if cluster else []
                if cluster and not matches:raise ValueError('Cluster/style must match styles.csv; save the caption as an LLM template first for a new style')
                selected = json.loads(task['request_json']).get('template_ids',[])
                preferred = next((r for r in matches if r['ID'] in selected), matches[0] if matches else None)
                row.update(original_cluster=cluster,original_style=preferred['ID'] if preferred else '')
            before = catalogue.read_bytes();after = csv_bytes(before,row,source.name if source and mode == 'archive_replace' else None)
            plan = {'target':str(target),'catalogue':str(catalogue),'before_sha':sha(before),'after_csv':after.decode('utf8'),
                    'archive':str(self.root/'_01_writing/_archive'/identifier/source.name) if source and mode == 'archive_replace' else None,
                    'source':str(source) if source else None,'mode':mode,'song_id':song,'variant_id':number}
            with self.review.db.transaction() as connection:
                connection.execute("UPDATE writing_tasks SET status='publishing',publication_json=?,result_yaml=? WHERE id=?",(packed(plan),raw,identifier))
            return self.finish_publication(identifier)

    def finish_publication(self, identifier):
        task = self.task(identifier);plan = json.loads(task['publication_json']);target = Path(plan['target']);catalogue = Path(plan['catalogue'])
        raw = task['result_yaml'];after = plan['after_csv'].encode('utf8')
        if sha(catalogue.read_bytes()) not in {plan['before_sha'],sha(after)}:raise Conflict('Catalogue changed during publication; files were retained for inspection')
        if plan.get('archive'):
            archive = Path(plan['archive']);source = Path(plan['source'])
            if archive.exists():
                if sha(archive.read_bytes()) != task['source_sha256']:raise Conflict('Archived source changed; publication stopped')
            else:
                if not source.is_file() or sha(source.read_bytes()) != task['source_sha256']:raise Conflict('Source changed before archival')
                archive.parent.mkdir(parents=True,exist_ok=True);source.rename(archive)
        if target.exists():
            if target.read_bytes() != raw:raise Conflict('Destination changed; publication did not overwrite it')
        else:
            target.parent.mkdir(parents=True,exist_ok=True);temporary = target.with_name('.'+target.name+'.'+identifier+'.tmp')
            try:
                with temporary.open('wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
                os.link(temporary,target)  # Exclusive install; never clobber an existing recipe.
            finally:temporary.unlink(missing_ok=True)
        if target.read_bytes() != raw:raise ValueError('Published YAML failed verification')
        if catalogue.read_bytes() != after:
            if sha(catalogue.read_bytes()) != plan['before_sha']:raise Conflict('Catalogue changed before commit')
            temporary = catalogue.with_name('.'+catalogue.name+'.'+identifier+'.tmp')
            try:
                with temporary.open('wb') as handle:handle.write(after);handle.flush();os.fsync(handle.fileno())
                temporary.replace(catalogue)
            finally:temporary.unlink(missing_ok=True)
        with self.review.db.transaction() as connection:
            plan.pop('after_csv',None)
            plan['published_at'] = now()
            connection.execute("UPDATE writing_tasks SET status='published',publication_json=? WHERE id=?",(packed(plan),identifier))
        return self.public(identifier)

    def save_caption_template(self, identifier, template_id):
        if not re.fullmatch('[a-z0-9][a-z0-9_-]{1,119}',template_id):raise ValueError('Use a unique lowercase template ID with letters, digits, underscores or hyphens')
        folder = self.root/'_01_writing/_references';catalogue = folder/'styles.csv'
        with self.lock,gpu_lock('gbr-caption-catalogue:'+str(folder.resolve())):
            task = self.task(identifier)
            if task['status'] not in {'ready','published'}:raise ValueError('Save a completed draft first')
            values = YamlDocument(task['result_yaml']).values;caption = values.get('caption') or ''
            validate_template_format(caption,'llm')
            if not values.get('cluster') or not values.get('style'):raise ValueError('Set cluster and style on this draft before saving a template')
            details = json.loads(task['result_json']);plans = details.setdefault('caption_templates',{})
            plan = plans.get(template_id)
            if plan and plan.get('complete'):
                if plan['caption_sha256'] != sha(caption.encode()):raise ValueError('This template ID already contains an earlier caption; use another ID')
                return plan
            target = folder/'_llm_templates'/(template_id+'.txt')
            if not plan:
                before = catalogue.read_bytes();rows = list(csv.DictReader(io.StringIO(before.decode('utf-8-sig'))))
                if any(r['ID'] == template_id for r in rows) or target.exists():raise ValueError('Template ID or file already exists')
                row = {'ID':template_id,'cluster':values['cluster'],'style_primary':values['style'],
                       'template':'_llm_templates/'+template_id+'.txt','template_source':'llm'}
                # Styles uses ID rather than filename; retain every existing row byte.
                names = next(csv.reader(io.StringIO(before.decode('utf-8-sig'))));out = io.StringIO(newline='')
                eol = '\r\n' if b'\r\n' in before else '\n'
                if 'template_source' not in names:raise ValueError('styles.csv requires template_source before adding LLM templates')
                csv.DictWriter(out,names,lineterminator=eol).writerow({k:row.get(k,'') for k in names})
                after = before + (eol.encode() if not before.endswith((b'\r',b'\n')) else b'') + out.getvalue().encode()
                plan = {'id':template_id,'path':str(target),'caption':caption,'caption_sha256':sha(caption.encode()),'before_sha':sha(before),'after_csv':after.decode('utf8'),'complete':False}
                plans[template_id] = plan
                with self.review.db.transaction() as connection:connection.execute('UPDATE writing_tasks SET result_json=? WHERE id=?',(packed(details),identifier))
            raw = plan['caption'].encode();after = plan['after_csv'].encode()
            if sha(catalogue.read_bytes()) not in {plan['before_sha'],sha(after)}:raise Conflict('Style catalogue changed during publication; retained for inspection')
            if target.exists() and target.read_bytes() != raw:raise Conflict('Caption template file changed; it was not overwritten')
            temporary = target.with_name('.'+target.name+'.'+identifier+'.tmp')
            try:
                if not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with temporary.open('wb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
                    os.link(temporary,target)
            finally:temporary.unlink(missing_ok=True)
            temporary = catalogue.with_name('.styles.'+identifier+'.tmp')
            try:
                if catalogue.read_bytes() != after:
                    if sha(catalogue.read_bytes()) != plan['before_sha']:raise Conflict('Style catalogue changed before commit')
                    with temporary.open('wb') as handle:handle.write(after);handle.flush();os.fsync(handle.fileno())
                    temporary.replace(catalogue)
            finally:temporary.unlink(missing_ok=True)
            plan.pop('caption',None);plan.pop('after_csv',None);plan['complete'] = True;plan['saved_at'] = now()
            with self.review.db.transaction() as connection:connection.execute('UPDATE writing_tasks SET result_json=? WHERE id=?',(packed(details),identifier))
            return plan
