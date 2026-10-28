"""Account reads, pod identity, endpoint inspection and existing dispatcher integration."""
from copy import deepcopy
import io
import json
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from test_workers import app
from test_comfy_execution import server,inventory
import test_generation_queue as fixtures
from _gbr_generation.runpod_account import AccountAPI,AccountError,endpoints
from _gbr_generation.credentials import AccountCredential
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan


def pod(identifier='testpod',**changes):
    return {'id':identifier,'name':'Music '+identifier,'status':'RUNNING','ports':['8188/http','22/tcp'],
        'gpu':{'id':'A40','count':1,'memory':64,'vcpuCount':16},'cost':.35,
        'env':{'PASSWORD':'private-environment-fixture'},'runtime':{'uptime':20},**changes}


def account(g,monkeypatch,rows,details=None):
    monkeypatch.setattr(g.runpod.credential,'get',lambda:'account-secret-fixture')
    monkeypatch.setattr(AccountAPI,'pods',lambda self,stop=None:deepcopy(rows))
    monkeypatch.setattr(AccountAPI,'request',lambda self,path:deepcopy((details or {}).get(path.rsplit('/',1)[-1],next(p for p in rows if p['id']==path.rsplit('/',1)[-1]))))


def compatible(entry,**kwargs):
    return {'online':True,'compatible':True,'busy':False,'checked_at':time.time(),'models':{},'hardware':{'gpu_name':'A40'}}


def test_first_configuration_secure_key_and_auth_never_returned_or_written(app,monkeypatch):
    g=app.generation;vault={}
    class Vault:
        def set_password(self,service,name,value):vault[name]=value
        def get_password(self,service,name):return vault.get(name)
    monkeypatch.setattr(AccountCredential,'vault',lambda self:Vault())
    response=app.client.post('/api/generation/runpod/settings',headers=app.headers,json={'api_key':'account-secret-fixture','enabled':False})
    assert response.status_code==200,response.text
    assert response.json()['has_api_key'] and 'account-secret-fixture' not in response.text
    assert 'account-secret-fixture' not in g.review.config.path.read_text()
    fresh=AccountCredential(g);assert fresh.get()=='account-secret-fixture'
    assert not g.secrets.get('api_key') and len(vault)==1
    monkeypatch.setattr(AccountAPI,'pods',lambda self,stop=None:[pod()])
    monkeypatch.setattr(AccountAPI,'request',lambda self,path:pod())
    monkeypatch.setattr(g.workers,'query',compatible)
    assert g.runpod.cycle()
    rows=g.runpod.status()['workers'];assert len(rows)==1 and rows[0]['id']=='runpod|testpod'
    assert 'private-environment-fixture' not in json.dumps(g.runpod.status())
    assert app.client.post('/api/generation/runpod/settings',json={'api_key':'bad'}).status_code==403
    oversized='private-key-fixture-'*150
    invalid=app.client.post('/api/generation/runpod/settings',headers=app.headers,json={'api_key':oversized})
    assert invalid.status_code==400 and oversized not in invalid.text


def test_discovery_idempotent_endpoint_change_stop_restore_and_preferences(app,monkeypatch):
    g=app.generation;rows=[pod()];account(g,monkeypatch,rows)
    monkeypatch.setattr(g.workers,'query',compatible)
    g.runpod.cycle();assert g.runpod.status()['workers'],json.dumps(g.runpod.progress);entry=g.workers.get('runpod|testpod')
    g.workers.save({**entry,'defaults':{'top_k':42},'enabled':False},entry['id'])
    g.workers.set_discovered(entry['id'],False,False)
    rows[0]['ports']=['8190/http'];g.runpod.save({'candidate_ports':[8190],'enabled':False});g.runpod.cycle()
    changed=g.workers.get(entry['id']);assert any(e['address'].endswith('-8190.proxy.runpod.net') for e in changed['runpod']['endpoints'])
    assert changed['defaults']=={'top_k':42} and not changed['enabled'] and not changed['runpod']['auto_allocate']
    rows[0]['status']='EXITED';g.runpod.cycle();assert not g.workers.get(entry['id'])['runpod']['eligible']
    rows[0]['status']='RUNNING';g.runpod.cycle();assert g.workers.get(entry['id'])['runpod']['eligible']
    assert len([e for e in g.workers.entries() if e.get('runpod')])==1
    rows.clear();g.runpod.cycle();assert g.workers.get(entry['id'])['runpod']['state']=='NOT_RETURNED'


def test_multiple_independent_pods_unknown_and_403_are_visible_without_allocation(app,monkeypatch):
    g=app.generation;rows=[pod('good'),pod('denied'),pod('imageonly')];account(g,monkeypatch,rows)
    def probe(entry,**kwargs):
        if entry['id']=='runpod|denied':return {'online':False,'compatible':False,'error':'RunPod/Cloudflare denied access HTTP 403','checked_at':time.time()}
        if entry['id']=='runpod|imageonly':return {'online':True,'compatible':False,'missing_nodes':['MiniMaxMusic3TextEncode'],'checked_at':time.time()}
        return compatible(entry)
    monkeypatch.setattr(g.workers,'query',probe);g.runpod.cycle()
    status=g.runpod.status();assert status['progress']['connected']==1 and status['progress']['unavailable']==2
    denied=g.workers.get('runpod|denied');assert denied['runpod']['state']=='RUNNING' and '403' in denied['runpod']['last_error']
    assert denied['connection'] is None and not g.workers.automatic(denied)
    assert g.workers.get('runpod|imageonly')['runpod']['service_state']=='incompatible'
    assert g.workers.automatic(g.workers.get('runpod|good'))


def test_account_and_detail_read_failure_retain_healthy_worker_and_task_state(app,monkeypatch):
    g=app.generation;rows=[pod()];account(g,monkeypatch,rows);monkeypatch.setattr(g.workers,'query',compatible)
    g.runpod.cycle();before=g.workers.get('runpod|testpod');report=deepcopy(g.workers.reports[before['id']])
    monkeypatch.setattr(AccountAPI,'pods',lambda *args:(_ for _ in ()).throw(AccountError('Account unavailable HTTP 503')))
    g.runpod.cycle();assert g.workers.get(before['id'])==before and g.workers.reports[before['id']]==report
    assert not g.runpod.progress['account_ok'] and g.runpod.progress['next_at']>time.time()
    monkeypatch.setattr(AccountAPI,'pods',lambda *args:rows)
    monkeypatch.setattr(AccountAPI,'request',lambda *args:(_ for _ in ()).throw(AccountError('Detail read unavailable HTTP 429')))
    g.runpod.cycle();assert g.workers.get(before['id'])==before and g.runpod.progress['errors']


def test_real_comfy_service_is_compiled_and_adopted_into_same_registry(app,monkeypatch):
    g=app.generation;account(g,monkeypatch,[pod()])
    with server() as (url,state):
        state['attempt']='inspection-placeholder'
        manual=g.workers.save({'connection':{'backend':'comfyui','address':url},'defaults':{'top_k':44}})
        monkeypatch.setattr('_gbr_generation.runpod_account.endpoints',lambda *args:[manual['connection']])
        g.runpod.cycle();assert g.runpod.status()['workers'],json.dumps(g.runpod.progress);entry=g.workers.get('runpod|testpod')
        assert entry['defaults']=={'top_k':44} and manual['id'] not in {e['id'] for e in g.workers.entries()}
        report=g.workers.reports[entry['id']];assert report['capabilities']['minimax_workflow_validated'] and report['history_available']
        assert report['hardware']['usable_vram_bytes']==123456789
        assert not state['submissions'] and ('GET','/queue') in state['requests']
        selected,_=g.workers.select(entry['id']);assert selected['id']==entry['id']
        assert all(method=='GET' for method,path in state['requests'])


def test_endpoints_follow_advertised_mapping_and_never_assume_local_port():
    row=pod(ports=['9000/http'],publicIp='8.8.8.8',portMappings={'22':22126,'9000':32123},ssh={'direct':{'host':'8.8.8.8','port':22126,'username':'root'}})
    candidates=endpoints(row,{'candidate_ports':[8188],'identity_file':'','native':True},[])
    assert {c['address'] for c in candidates}>={'https://testpod-9000.proxy.runpod.net','https://testpod-8188.proxy.runpod.net','http://8.8.8.8:32123','ssh://root@8.8.8.8:22126'}
    assert not any('8190' in c['address'] for c in candidates)


def test_api_uses_bearer_only_paginates_and_redacts_http_errors(monkeypatch):
    requests=[];responses=[{'pods':[pod('one')],'pagination':{'hasNextPage':True,'nextCursor':'next page'}},{'pods':[pod('two')],'pagination':{'hasNextPage':False}}]
    class Opener:
        def open(self,request,timeout):
            requests.append(request);assert timeout==6
            return io.BytesIO(json.dumps(responses.pop(0)).encode())
    monkeypatch.setattr('_gbr_generation.runpod_account.build_opener',lambda *args:Opener())
    assert len(AccountAPI('account-secret-fixture').pods())==2
    assert requests[0].get_header('Authorization')=='Bearer account-secret-fixture'
    assert 'cursor=next+page' in requests[1].full_url and 'account-secret-fixture' not in requests[0].full_url
    for code in (401,403,429,503):
        def fail(*args,**kwargs):raise HTTPError('secret-query-fixture',code,'account-secret-fixture',{'Retry-After':'45'},io.BytesIO(b'account-secret-fixture'))
        monkeypatch.setattr(Opener,'open',fail)
        with pytest.raises(AccountError) as error:AccountAPI('account-secret-fixture').pods()
        assert str(code) in str(error.value) and 'account-secret-fixture' not in str(error.value)
        if code==429:assert error.value.retry_after==45


def test_refresh_is_async_overlap_safe_and_shuts_down(app,monkeypatch):
    g=app.generation;gate=threading.Event();entered=threading.Event();calls=[]
    monkeypatch.setattr(g.runpod.credential,'get',lambda:'fixture')
    g.runpod.save({'enabled':False})
    def slow(self,stop=None):
        calls.append(1);entered.set()
        while not gate.wait(.01):
            if stop.is_set():return []
        return []
    monkeypatch.setattr(AccountAPI,'pods',slow)
    start=time.monotonic();response=app.client.post('/api/generation/runpod/refresh',headers=app.headers)
    assert response.status_code==200 and time.monotonic()-start<1
    assert entered.wait(2)
    for _ in range(3):g.runpod.refresh()
    assert len(calls)==1 and g.runpod.progress['running']
    gate.set();g.runpod.close();assert not g.runpod.thread.is_alive()


def test_discovered_worker_joins_active_batch_and_records_pod_in_existing_session(monkeypatch):
    with fixtures.app() as f,server() as (a,sa),server() as (b,sb):
        first=fixtures.configured(f,a);preview=f.preview(connection=None,worker_id='auto',caption_mode='yaml',experiments={'repetitions':3,'seed_policy':'randomize'})
        f.generation.start(preview['token']);path=f.launched[0]['manifest'];manifest=verify_plan(path)
        assert execute(path,worker_id=first['id'])['attempts'][0]['status']=='succeeded'
        account(f.generation,monkeypatch,[pod('newpod')]);sb['attempt']='inspection-placeholder'
        monkeypatch.setattr('_gbr_generation.runpod_account.endpoints',lambda *args:[{'backend':'comfyui','address':b}])
        f.generation.runpod.cycle();entry=f.generation.workers.get('runpod|newpod')
        # Use the existing admission path; the dispatcher discovers active batches
        # from their unchanged controller ownership records.
        monkeypatch.setattr(f.generation,'tasks',lambda include_logs=True:[{'active':True,'batch_id':manifest['batch_id'],'worker_id':first['id'],'connection':first['connection']}])
        f.generation.dispatcher.tick()
        assert len(f.launched)==2 and f.launched[-1]['worker_id']=='runpod|newpod'
        assert execute(path,worker_id=entry['id'])['attempts'][0]['status']=='succeeded'
        observed=f.generation.review.db.one('SELECT provider_instance_id,hardware_json FROM worker_sessions WHERE provider_instance_id=?',('newpod',))
        assert observed and json.loads(observed['hardware_json'])['gbr_worker_id']=='runpod|newpod'


def test_explicit_discovered_worker_preserves_stable_identity_in_single_worker_manifest(monkeypatch):
    with fixtures.app() as f,server() as (url,state):
        state['attempt']='inspection-placeholder';account(f.generation,monkeypatch,[pod()])
        monkeypatch.setattr('_gbr_generation.runpod_account.endpoints',lambda *args:[{'backend':'comfyui','address':url}])
        f.generation.runpod.cycle();assert f.generation.runpod.status()['workers'],json.dumps(f.generation.runpod.progress)
        preview=f.preview(connection=None,worker_id='runpod|testpod',caption_mode='yaml')
        assert preview['worker_id']=='runpod|testpod'
        f.generation.start(preview['token']);assert f.launched[0]['worker_id']=='runpod|testpod'
        assert execute(f.launched[0]['manifest'],worker_id='runpod|testpod')['attempts'][0]['status']=='succeeded'
        folder=f.generation.runtime/'app_jobs'/'test-controller';folder.mkdir(parents=True)
        (folder/'task.json').write_text(json.dumps(f.launched[0]));(folder/'state.json').write_text(json.dumps({'status':'succeeded'}))
        tasks=f.generation.tasks(False)
        assert len(tasks[0]['runs'])==1 and tasks[0]['runpod_pod_id']=='testpod'


def test_pod_stopping_during_detail_read_is_not_probed_or_allocated(app,monkeypatch):
    g=app.generation;account(g,monkeypatch,[pod()],details={'testpod':pod(status='EXITED')})
    def unexpected(*args,**kwargs):raise AssertionError('Stopped pod must not be probed')
    monkeypatch.setattr(g.workers,'query',unexpected)
    g.runpod.cycle();entry=g.workers.get('runpod|testpod')
    assert entry['runpod']['state']=='EXITED' and not g.workers.automatic(entry)


def test_native_ssh_uses_existing_doctor_and_preserves_hardware(app,monkeypatch):
    from _gbr_worker.runpod import RunpodClient
    g=app.generation;account(g,monkeypatch,[pod()])
    monkeypatch.setattr('_gbr_generation.runpod_account.endpoints',lambda *args:[{'backend':'runpod_dedicated','address':'ssh://root@8.8.8.8:22000','auto_setup':False}])
    calls=[]
    def doctor(self,operation,**kwargs):
        calls.append(operation);return {'protocol':1,'hardware':{'gpu_name':'A40','usable_vram_bytes':48*1024**3},'versions':{'torch':'fixture'}}
    monkeypatch.setattr(RunpodClient,'rpc',doctor)
    g.runpod.cycle();entry=g.workers.get('runpod|testpod')
    assert calls==['doctor'] and entry['connection']['backend']=='runpod_dedicated' and g.workers.automatic(entry)
    assert g.workers.reports[entry['id']]['hardware']['usable_vram_bytes']==48*1024**3
