"""Reuse the old uploader's local connection, without copying or exposing its keys."""
import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from test_publishing import publication
from _gbr_preparation.rclone import connection
from _gbr_preparation.publishing import R2


@pytest.fixture
def saved_rclone(monkeypatch):
    state={'key':'saved-access','secret':'saved-secret%with-percent','endpoint':'https://saved.r2.cloudflarestorage.com','calls':[]}
    monkeypatch.setattr('_gbr_preparation.rclone.shutil.which',lambda name:'/local/rclone' if name=='rclone' else None)
    def run(args,**kwargs):
        state['calls'].append((args,kwargs))
        raw=f"[r2]\ntype = s3\nprovider = Cloudflare\naccess_key_id = {state['key']}\nsecret_access_key = {state['secret']}\nendpoint = {state['endpoint']}\n# Mōses · 日本語\n"
        return subprocess.CompletedProcess(args,0,raw.encode('utf-8'),b'')
    monkeypatch.setattr('_gbr_preparation.rclone.subprocess.run',run)
    return state


def test_reuses_one_remote_read_only_without_returning_or_copying_keys(publication,saved_rclone):
    _,_,publisher,store,_,_,_=publication
    result=publisher.use_rclone('r2',bucket='good-boy-records')
    assert result['configured'] and result['credentials_available']
    assert result['settings']['endpoint']==saved_rclone['endpoint'] and result['settings']['rclone_remote']=='r2'
    assert result['settings']['public_base']=='https://media.test'
    assert not publisher.secrets_path.exists()
    assert not store.objects
    public=json.dumps(result)+publisher.config_path.read_text(encoding='utf-8')
    assert saved_rclone['key'] not in public and saved_rclone['secret'] not in public
    args,kwargs=saved_rclone['calls'][0]
    assert args==['/local/rclone','config','show','r2','--ask-password=false']
    assert kwargs['stdin']==subprocess.DEVNULL and kwargs['timeout']==15


def test_saved_remote_environment_overrides_and_runtime_key_pair(saved_rclone,monkeypatch):
    monkeypatch.setenv('RCLONE_CONFIG_R2_ACCESS_KEY_ID','override-key')
    assert connection('r2')['_access_key']=='override-key'
    monkeypatch.setenv('RCLONE_CONFIG_R2_ACCESS_KEY_ID','')
    monkeypatch.setenv('RCLONE_CONFIG_R2_SECRET_ACCESS_KEY','')
    monkeypatch.setenv('RCLONE_CONFIG_R2_ENV_AUTH','true')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID','runtime-key');monkeypatch.setenv('AWS_SECRET_ACCESS_KEY','runtime-secret')
    monkeypatch.setenv('AWS_SESSION_TOKEN','runtime-token')
    value=connection('r2')
    assert value['_access_key']=='runtime-key' and value['_secret_key']=='runtime-secret' and value['_session_token']=='runtime-token'


@pytest.mark.parametrize('name',['','--help','r2:bucket','r2\nother','../r2','r2;other'])
def test_invalid_remote_never_reaches_the_cli(publication,saved_rclone,name):
    _,_,publisher,_,_,_,_=publication
    with pytest.raises(ValueError):publisher.use_rclone(name)
    assert saved_rclone['calls']==[]


def test_endpoint_alias_works_and_blank_key_saves_keep_the_connection(publication,saved_rclone):
    _,_,publisher,_,_,_,_=publication
    result=publisher.save_settings({'endpoint':'r2','bucket':'good-boy-records'})
    assert result['settings']['rclone_remote']=='r2'
    values={k:result['settings'][k] for k in ('endpoint','bucket','public_base','website_url','profile')}
    result=publisher.save_settings({**values,'access_key':'','secret_key':''})
    assert result['settings']['rclone_remote']=='r2' and not publisher.secrets_path.exists()


def test_next_job_reads_rotated_keys_from_rclone_and_fences_endpoint_changes(publication,saved_rclone):
    _,_,publisher,_,_,_,_=publication
    publisher.use_rclone('r2')
    captured=[];publisher.store_factory=lambda settings:captured.append(settings) or settings
    publisher.store();saved_rclone.update(key='rotated-access',secret='rotated-secret')
    publisher.store()
    assert captured[0]['_access_key']=='saved-access' and captured[1]['_access_key']=='rotated-access'
    assert not publisher.secrets_path.exists()
    saved_rclone['endpoint']='https://other.r2.cloudflarestorage.com'
    with pytest.raises(ValueError,match='endpoint changed'):publisher.store()
    assert len(captured)==2


def test_explicit_manual_keys_switch_away_from_rclone(publication,saved_rclone):
    _,_,publisher,_,_,_,_=publication
    publisher.use_rclone('r2')
    result=publisher.save_settings({'access_key':'manual-access','secret_key':'manual-secret'})
    assert not result['settings']['rclone_remote']
    assert json.loads(publisher.secrets_path.read_text(encoding='utf-8'))['secret_key']=='manual-secret'


@pytest.mark.parametrize('failure',['missing','timeout','nonzero','invalid_utf8','invalid_ini','wrong_backend','wrong_endpoint','missing_keys'])
def test_failed_import_keeps_settings_and_never_exposes_cli_secrets(publication,saved_rclone,monkeypatch,failure):
    _,_,publisher,_,_,_,_=publication
    before=publisher.config_path.read_bytes()
    if failure=='missing':monkeypatch.setattr('_gbr_preparation.rclone.shutil.which',lambda name:None)
    else:
        def bad(args,**kwargs):
            if failure=='timeout':raise subprocess.TimeoutExpired(args,15,output=b'TOP_SECRET')
            raw={'nonzero':b'TOP_SECRET','invalid_utf8':b'\x8d TOP_SECRET',
                 'invalid_ini':b'[r2]\nTOP_SECRET\n','wrong_backend':b'[r2]\ntype=sftp\npassword=TOP_SECRET',
                 'wrong_endpoint':b'[r2]\ntype=s3\nendpoint=https://other.example/\nsecret_access_key=TOP_SECRET',
                 'missing_keys':b'[r2]\ntype=s3\nendpoint=https://saved.r2.cloudflarestorage.com\nsecret_access_key=TOP_SECRET'}[failure]
            return subprocess.CompletedProcess(args,1 if failure=='nonzero' else 0,raw,b'TOP_SECRET')
        monkeypatch.setattr('_gbr_preparation.rclone.subprocess.run',bad)
    with pytest.raises(ValueError) as error:publisher.use_rclone('r2')
    assert 'TOP_SECRET' not in str(error.value)
    assert publisher.config_path.read_bytes()==before and not publisher.secrets_path.exists()


def test_rclone_credentials_win_over_unrelated_environment_keys(monkeypatch):
    import boto3
    session=Mock();monkeypatch.setattr(boto3,'Session',lambda **kwargs:session)
    monkeypatch.setenv('GBR_R2_ACCESS_KEY_ID','unrelated');monkeypatch.setenv('GBR_R2_SECRET_ACCESS_KEY','unrelated')
    R2({'profile':'','endpoint':'https://saved.r2.cloudflarestorage.com','bucket':'music','rclone_remote':'r2',
        'access_key_env':'GBR_R2_ACCESS_KEY_ID','secret_key_env':'GBR_R2_SECRET_ACCESS_KEY',
        '_access_key':'saved-key','_secret_key':'saved-secret','_session_token':'session-token'})
    kwargs=session.client.call_args.kwargs
    assert kwargs['aws_access_key_id']=='saved-key' and kwargs['aws_secret_access_key']=='saved-secret'
    assert kwargs['aws_session_token']=='session-token'


def test_import_endpoint_uses_existing_host_and_csrf_guards(publication,saved_rclone):
    from fastapi.testclient import TestClient
    from _review.app import create_app
    review,_,publisher,_,_,_,_=publication
    app=create_app(review.config.path,start_worker=False);service=app.state.publisher
    service.config_path=publisher.config_path;service.secrets_path=publisher.secrets_path
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/releases/settings/rclone',json={'remote':'r2'}).status_code==403
        response=client.post('/api/releases/settings/rclone',json={'remote':'r2'},headers={'x-gbr-request':'1'})
        assert response.status_code==200,response.text
        assert saved_rclone['key'] not in response.text and saved_rclone['secret'] not in response.text
        assert client.get('/api/releases/settings').json()['settings']['rclone_remote']=='r2'
    with TestClient(app,base_url='http://127.0.0.1',client=('203.0.113.9',1)) as client:
        assert client.post('/api/releases/settings/rclone',json={'remote':'r2'},headers={'x-gbr-request':'1'}).status_code in {401,403}
