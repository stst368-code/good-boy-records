"""Append-only worker admissions alongside an unchanged frozen batch manifest."""
from contextlib import closing
import json
from pathlib import Path
from .planner import open_database,sha


def policy(manifest):
    with closing(open_database(manifest['database'])) as db:
        return json.loads(db.execute('SELECT policy_json FROM generation_batches WHERE id=?',(manifest['batch_id'],)).fetchone()[0])


def admission(path,manifest,worker_id):
    saved=policy(manifest).get('worker_assignments',{}).get(worker_id)
    if not saved:return None
    target=(Path(path).parent/saved['file']).resolve(strict=True)
    if not target.is_relative_to((Path(path).parent/'worker_assignments').resolve()):raise ValueError('Worker assignment path changed')
    raw=target.read_bytes()
    if sha(raw)!=saved['sha256']:raise ValueError('Frozen worker assignment changed')
    value=json.loads(raw)
    if value['batch_id']!=manifest['batch_id'] or value['worker_id']!=worker_id or value['manifest_sha256']!=policy(manifest)['manifest_sha256']:
        raise ValueError('Worker assignment identity changed')
    if set(value['options'])-{r['id'] for r in manifest['runs']}:raise ValueError('Worker assignment contains an unknown run')
    base={r['id']:r for r in manifest['runs']};result={}
    for run_id,option in value['options'].items():
        run=base[run_id]
        flexible=set(run.get('worker_default_fields',[k for k,v in run['provenance'].items() if v=='defaults']))
        changes=option['changes']
        if set(changes)-flexible or set(changes)&{'caption','cluster','style','encoder_seed','sampler_seed','model'}:
            raise ValueError('Worker admission tried to change a fixed recipe field')
        result[run_id]={'effective':{**run['effective'],**changes},'provenance':{**run['provenance'],**option['provenance']},
            'execution':option['execution'],'model_revision':option['model_revision']}
    return result


def target_id(run):
    target=run['execution']['diffusion']
    return target['backend']+'|'+(target.get('address') or '')


def runs_for(path,manifest,worker_id):
    registered=policy(manifest).get('worker_assignments',{})
    shared=any(r.get('worker_options') for r in manifest['runs'])
    if shared and not worker_id:raise ValueError('This shared batch requires a worker identity')
    if not shared and not registered:return [r for r in manifest['runs'] if not worker_id or target_id(r)==worker_id],worker_id
    worker_id=worker_id or target_id(manifest['runs'][0])
    additions=admission(path,manifest,worker_id) or {}
    result=[]
    for run in manifest['runs']:
        option=additions.get(run['id']) or next((o for o in run.get('worker_options',[]) if o['worker_id']==worker_id),None)
        if option:result.append({**run,**{k:option[k] for k in ('effective','provenance','execution','model_revision')}})
        elif not run.get('worker_options') and target_id(run)==worker_id:result.append(run)
    return result,worker_id
