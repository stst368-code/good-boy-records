"""Read-only R2 v2 inventory plan: no bucket creation, uploads or removals."""
from pathlib import Path
import re
from .service import digest


def plan(db):
    objects={};missing=[];conflicts=[]
    rows=db.all("SELECT id,kind,sha256,uri,size_bytes FROM generation_artifacts WHERE kind<>'release_asset' ORDER BY id")
    # Legacy reviewed media and YAMLs predate generation_artifacts. Project their
    # existing records into the inventory instead of copying them into a new DB.
    rows+=db.all("SELECT 'media:'||id AS id,'audio' AS kind,sha256,path AS uri,size AS size_bytes FROM media_files WHERE storage_state IN ('input','archived')")
    for recipe in db.all('SELECT generation_id,yaml_sha256,yaml_archive_path FROM generation_metadata WHERE yaml_sha256 IS NOT NULL AND yaml_archive_path IS NOT NULL'):
        path=Path(recipe['yaml_archive_path'])
        rows.append({'id':'recipe:'+recipe['generation_id'],'kind':'completed_yaml','sha256':recipe['yaml_sha256'],
                     'uri':str(path),'size_bytes':path.stat().st_size if path.is_file() else None})
    for artifact in rows:
        sha=artifact['sha256'];path=Path(artifact['uri'])
        if not sha or not re.fullmatch('[0-9a-f]{64}',sha):
            conflicts.append({'artifact_id':artifact['id'],'reason':'missing or invalid SHA256'});continue
        if not path.is_file():
            missing.append(artifact['id']);continue
        if (artifact['size_bytes'] is not None and path.stat().st_size!=artifact['size_bytes']) or digest(path)!=sha:
            conflicts.append({'artifact_id':artifact['id'],'reason':'current bytes differ from registry'});continue
        # Deduplicate content independently of title/cluster/style, which may change.
        entry=objects.setdefault(sha,{'key':'gbr/v2/objects/sha256/'+sha[:2]+'/'+sha,'sha256':sha,
                    'size_bytes':path.stat().st_size,'artifact_ids':[],'kinds':[]})
        entry['artifact_ids'].append(artifact['id'])
        if artifact['kind'] not in entry['kinds']:entry['kinds'].append(artifact['kind'])
    return {'format':'gbr-r2-layout-plan-v2','dry_run':True,'private_objects':list(objects.values()),
            'unique_bytes':sum(o['size_bytes'] for o in objects.values()),'missing_artifacts':missing,
            'conflicts':conflicts,'public_release_state':'not_approved',
            'policy':{'catalogue':'song and variant identities come from the shared writing catalogue, never storage folders',
                'public':'Only approved release artifacts enter a separate public bucket; private DB/config/reference media never do',
                'cutover':'Upload and verify immutable objects, then change one release manifest pointer; keep legacy URLs during verification',
                'deletions':'No delete or sync operation is included in this plan'}}
