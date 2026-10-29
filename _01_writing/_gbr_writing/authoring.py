"""Manual bases and composable captions, journalled in the existing writing ledger."""
import json
import re
import unicodedata

from _gbr_generation.catalogue import Styles, identifier, title_key
from _gbr_generation.yaml_document import YamlDocument
from _gbr_worker.hardware import gpu_lock
from _review.reviewers import Conflict
from _review.util import now
from .service import packed, patch_fields, sha
from .lyrics import normalize_lyrics


SECTIONS = [
    ('Global Metadata', [('basic','Basic Attributes'),('emotion','Global Emotional Progression'),
        ('imagery','Application Scenarios & Imagery'),('sonics','Sonics & Production Profile')]),
    ('Vocal Details', [('timbre','Vocal Gender & Timbre'),('vocal_style','Vocal Style'),
        ('harmony','Harmony/Backing Vocals'),('vocal_fx','Vocal FX')]),
    ('Arrangement', [('primary','Primary'),('secondary','Secondary'),
        ('groove','Groove & Foundation Progression'),('textures','Embellishments, Textures & Spatial FX')]),
]
LABELS={key:label for _,fields in SECTIONS for key,label in fields}


def normal(value):
    return '\n'.join(line.strip().lower() for line in value.splitlines() if line.strip())


def split_caption(text):
    """Read labelled, multiline reference sections without changing the reference."""
    result={key:'' for key in LABELS};active=None
    for line in text.splitlines():
        clean=line.strip().lstrip('#').strip()
        matched=False
        for key,label in LABELS.items():
            match=re.match(r'^(?:\d+[.)]?\s+|[AB][.)]?\s+)?'+re.escape(label)+r'\s*:\s*(.*)$',clean,re.I)
            if match:
                active=key;result[key]=match[1];matched=True;break
        if matched:continue
        if clean.rstrip(':') in [s for s,_ in SECTIONS] or clean.startswith('Instrument Lifecycle Description'):
            active=None
        elif active and clean:result[active]+=('\n' if result[active] else '')+clean
    return result


def compose(fields):
    unknown=set(fields)-set(LABELS)
    if unknown:raise ValueError('Unknown caption fields: '+', '.join(sorted(unknown)))
    if not any(value.strip() for value in fields.values()):raise ValueError('Enter at least one caption section')
    lines=[]
    for heading,items in SECTIONS:
        lines.append(heading)
        if heading=='Arrangement':lines.append('Instrument Lifecycle Description (Primary/Secondary Layering):')
        for key,label in items:lines.append(label+': '+fields.get(key,'').strip())
    return '\n'.join(lines)+'\n'


class Authoring:
    def __init__(self,service):self.w=service

    def options(self):
        rows=self.w.catalogue()['inputs']
        styles=Styles(self.w.root/'_01_writing/_references/styles.csv')
        return {'tracks':rows,'albums':sorted({r['metadata'].get('album_name','') for r in rows}-{''}),
                'clusters':sorted({r['cluster'] for r in styles.rows if r['cluster']}),
                'sections':[{'name':name,'fields':[{'key':k,'label':v} for k,v in fields]} for name,fields in SECTIONS]}

    def source(self,file):
        path,raw=self.w.source(file)
        row=next((r for r in self.w.catalogue()['inputs'] if r['file']==file),None)
        if not row or not row['song_id']:raise ValueError('Choose a registered track or variant')
        values=YamlDocument(raw).values
        return {'values':values,'metadata':row['metadata'],'file':file,'sha256':sha(raw)}

    def existing(self,request):
        key=request['request_id']
        if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid save request ID')
        row=self.w.review.db.one('SELECT id,request_json FROM writing_tasks WHERE id=?',(key,))
        if row and json.loads(row['request_json']).get('manual_request')!=request:
            raise Conflict('This save request already has different content; reload before saving again')
        return row

    def draft(self,request,raw,changes,reviewer,source=None,metadata=None,extra=None):
        if not self.w.ready:raise ValueError('Launch GBR with the shared database before saving writing')
        frozen={'manual_request':request,'warnings':[],'catalogue_metadata':metadata or {},**(extra or {})}
        result=patch_fields(raw,changes,self.w.template)
        with self.w.review.db.transaction() as db:
            db.execute('INSERT INTO writing_tasks(id,kind,status,reviewer_id,source_path,source_sha256,source_yaml,request_json,prompt_json,result_yaml,result_json,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (request['request_id'],'manual_base' if metadata is not None else 'manual_caption','ready',reviewer,
                 str(source) if source else None,sha(raw),raw,packed(frozen),'[]',result,'{}',now(),now()))

    def save_base(self,request,reviewer):
        with self.w.lock,gpu_lock('gbr-writing-catalogue:'+str(self.w.built.resolve())):
            existing=self.existing(request)
            if not existing:
                title=re.sub(r'\s+','-',normal(request['title']))
                lyrics=normalize_lyrics(request['lyrics'])
                if not title or not title_key(title) or not lyrics:raise ValueError('Song name and lyrics are required')
                if any(char in title for char in '/\\\x00'):raise ValueError('Song name cannot contain directory separators')
                # Only the expressly edited writing fields change in a variant.
                source,raw=self.w.source(request['file']) if request.get('file') else (None,self.w.template)
                if source:
                    if not request.get('source_sha256') or sha(raw)!=request['source_sha256']:
                        raise Conflict('Source YAML changed; reload the selected song before saving a variant')
                    self.source(request['file'])
                album=normal(request.get('album_name',''));position=request.get('album_position')
                if position is not None and not album:raise ValueError('Choose an album before setting its position')
                metadata={k:normal(request.get(k,'')) for k in ('author','inspiration_author','inspiration_track')}
                metadata.update(album_bool='t' if album else 'f',album_name=album,album_position=str(position) if position else '')
                self.draft(request,raw,{'title':title,'lyrics':lyrics},reviewer,source,metadata)
        # publish owns the same cross-process lock. Do not acquire it recursively.
        return self.w.publish(request['request_id'],'keep',0)

    def examples(self,field,query='',cluster=''):
        if field not in LABELS:raise ValueError('Choose a caption subsection')
        folder=self.w.root/'_01_writing/_references';styles=Styles(folder/'styles.csv');result=[];issues=[]
        terms=query.casefold().split()
        for row in styles.rows:
            if cluster and row['cluster']!=cluster:continue
            path=(folder/row['template'].replace('\\','/')).resolve()
            if not path.is_relative_to(folder.resolve()):continue
            try:
                if path.stat().st_size>200000:raise ValueError('Reference exceeds 200 KB')
                value=split_caption(path.read_text(encoding='utf-8-sig'))[field]
            except (OSError,UnicodeError,ValueError) as error:
                issues.append({'id':row['ID'],'error':str(error)});continue
            if not value:continue
            searchable=(row['ID']+' '+row['cluster']+' '+row['style_primary']+' '+value).casefold()
            if all(term in searchable for term in terms):result.append({'id':row['ID'],'cluster':row['cluster'],
                'version':row.get('version') or row['style_primary'],'text':value})
        return {'examples':result[:100],'total':len(result),'issues':issues}

    def auditions(self,field,template_id):
        if field not in LABELS:raise ValueError('Choose a caption subsection')
        folder=self.w.root/'_01_writing/_references';row=Styles(folder/'styles.csv').by_id.get(template_id)
        if not row:raise ValueError('Caption reference was not found')
        path=(folder/row['template'].replace('\\','/')).resolve()
        if not path.is_relative_to(folder.resolve()):raise ValueError('Caption reference is outside _references')
        raw=path.read_bytes();wanted=split_caption(raw.decode('utf-8-sig'))[field]
        key=lambda text:' '.join(text.casefold().split())
        if not wanted:return {'recordings':[],'rating':'Latest overall review bucket 4 or 5'}
        # Use the existing review, generation-provenance and playback records.
        # Exact subsection matches also find mix-and-match custom captions.
        records=self.w.review.db.all('''SELECT g.id,g.audio_id,s.title,r.average,r.bucket,
            m.parsed_json,gr.effective_json,gr.template_id,gr.template_sha256
            FROM generations g JOIN songs s ON s.id=g.song_id
            JOIN latest_reviews r ON r.audio_id=g.audio_id
            JOIN generation_metadata m ON m.generation_id=g.id
            LEFT JOIN generation_output_links link ON link.review_generation_id=g.id
            LEFT JOIN generation_runs gr ON gr.id=link.run_id
            WHERE r.bucket IN (4,5) ORDER BY r.average DESC,r.created_at DESC,g.id LIMIT 2000''')
        result=[];seen=set()
        for record in records:
            if record['audio_id'] in seen:continue
            metadata=json.loads(record['parsed_json']);effective=json.loads(record['effective_json'] or '{}')
            caption=effective.get('caption') or metadata.get('caption')
            matched=isinstance(caption,str) and key(split_caption(caption)[field])==key(wanted)
            if not caption:matched=record['template_id']==template_id and record['template_sha256']==sha(raw)
            if not matched or not self.w.review.archiver.available(record['audio_id']):continue
            seen.add(record['audio_id']);result.append({'id':record['id'],'title':record['title'],'score':record['average'],
                'bucket':record['bucket'],'match':'Exact caption subsection','playback_url':'/api/playback/'+record['id']+'?mode=full'})
            if len(result)>=8:break
        return {'recordings':result,'rating':'Latest overall review bucket 4 or 5'}

    def save_caption(self,request,reviewer):
        folder=self.w.root/'_01_writing/_references'
        with self.w.lock:
            existing=self.existing(request)
            if existing:
                task=self.w.task(request['request_id']);saved=json.loads(task['request_json'])['caption_save']
                return self.w.save_caption_template(task['id'],saved['id'],'custom',saved['extra'])
            caption=compose(request['fields']);cluster=request['cluster'].strip();version=request.get('version','').strip()
            if not cluster or not title_key(cluster):raise ValueError('A cluster is required')
            song=str(identifier(request['song_id'])) if request.get('song_id') else ''
            if song:
                masters=[r for r in self.w.catalogue()['inputs'] if r['variant_id']=='1' and r['song_id']==song]
                if len(masters)!=1:raise ValueError('Choose one registered song for the caption link')
            with gpu_lock('gbr-caption-catalogue:'+str(folder.resolve())):
                rows=Styles(folder/'styles.csv').rows
                # Reserve unfinished saves too, so a failed file write never steals a name.
                for row in self.w.review.db.all("SELECT request_json FROM writing_tasks WHERE kind='manual_caption'"):
                    saved=json.loads(row['request_json']).get('caption_save')
                    if saved:rows.append(saved['row'])
                pairs={(r['cluster'].casefold(),(r.get('version') or ('' if r.get('template_source')=='custom' and r['style_primary']==r['cluster'] else r['style_primary'])).casefold()) for r in rows}
                chosen=version;suffix=2
                while (cluster.casefold(),chosen.casefold()) in pairs:
                    chosen=(version+'-'+str(suffix)) if version else '-'+str(suffix);suffix+=1
                stem=title_key(cluster)+(('-'+title_key(chosen)) if chosen else '')
                # IDs are portable ASCII; display names and caption text retain Unicode.
                template_id=title_key(unicodedata.normalize('NFKD',stem).encode('ascii','ignore').decode())[:110] or 'caption'
                if len(template_id)<2:template_id+='-caption'
                candidate=template_id;suffix=2
                used={r['ID'] for r in rows}
                while candidate in used or (folder/'_custom_templates'/(candidate+'.txt')).exists():
                    candidate=template_id+'-'+str(suffix);suffix+=1
                extra={'version':chosen,'song_id':song}
                saved={'id':candidate,'extra':extra,'row':{'ID':candidate,'cluster':cluster,'style_primary':chosen or cluster,'template_source':'custom',**extra}}
                self.draft(request,self.w.template,{'caption':caption,'cluster':cluster,'style':chosen or cluster},reviewer,extra={'caption_save':saved})
            return self.w.save_caption_template(request['request_id'],candidate,'custom',extra)
