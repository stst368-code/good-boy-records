"""Graph purity, bridge restrictions, streaming and installer contracts."""
import asyncio
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer

PACK=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('gbr_test_pack',PACK/'__init__.py',submodule_search_locations=[str(PACK)])
pack=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pack;spec.loader.exec_module(pack)
from gbr_test_pack import nodes
from gbr_test_pack.bridge import allowed,endpoint,guard,register
from gbr_test_pack.install import install


def test_graph_is_pure_repeatable_and_preserves_inputs():
    song={'file':'_yaml/song.yaml','source_sha256':'a'*64,'title':'song','lyrics':'[verse]\nline one\nline two'}
    caption=nodes.Caption().build('Rock','Punk',**{key:'test '+key for key in nodes.FIELDS})[0]
    before=deepcopy((song,caption))
    first=nodes.GenerationPlan().build(song,caption,'auto','experiment',30,1.7,1.7,360)
    assert first==nodes.GenerationPlan().build(song,caption,'auto','experiment',30,1.7,1.7,360)
    assert (song,caption)==before
    assert first['result'][0]['purpose']=='experiment'
    assert first['result'][0]['song']['lyrics']==song['lyrics']
    first['result'][0]['song']['title']='changed';assert song['title']=='song'
    assert nodes.GenerationPlan.INPUT_TYPES()['required']['purpose'][0][0]=='experiment'


@pytest.mark.parametrize('url',['https://evil.test','http://192.168.1.2:8765','http://127.0.0.1@evil.test','http://127.0.0.1:8765/path','http://127.0.0.1?key=x'])
def test_bridge_rejects_nonlocal_or_credential_endpoints(url):
    with pytest.raises(ValueError):endpoint(url)


@pytest.mark.parametrize('path',['../settings','generation/../preferences','releases/missing/publish','settings','generation/workers/remove','generation/preferences','writing/settings','playback/../../settings'])
def test_bridge_does_not_expose_arbitrary_api_routes(path):assert not allowed('POST',path) and not allowed('GET',path)


def test_host_and_origin_protection():
    for request in [SimpleNamespace(remote='203.0.113.1',headers={},host='127.0.0.1'),SimpleNamespace(remote='127.0.0.1',headers={'Origin':'https://evil.test'},host='127.0.0.1'),SimpleNamespace(remote='127.0.0.1',headers={'Sec-Fetch-Site':'cross-site'},host='127.0.0.1')]:
        with pytest.raises(web.HTTPForbidden):guard(request)
    with pytest.raises(web.HTTPForbidden):guard(SimpleNamespace(remote='127.0.0.1',headers={},host='127.0.0.1'),True)


def test_proxy_streams_audio_forwards_review_identity_and_isolates_session_cookie():
    async def scenario():
        seen=[]
        async def handler(request):
            seen.append((request.path,dict(request.headers),await request.read()))
            if request.path=='/api/login':
                response=web.json_response({'authenticated':True});response.set_cookie('gbr_session','private-session',httponly=True,max_age=3600);return response
            return web.Response(body=b'audio-bytes',headers={'Content-Type':'audio/flac','Content-Range':'bytes 0-10/11'},status=206)
        remote=web.Application();remote.router.add_route('*','/{path:.*}',handler)
        async with TestServer(remote) as upstream:
            app=web.Application();routes=web.RouteTableDef();register(routes,str(upstream.make_url('')).rstrip('/'));app.add_routes(routes)
            async with TestClient(TestServer(app)) as client:
                assert (await client.post('/gbr/api/login',json={'password':'test'})).status==403
                response=await client.post('/gbr/api/login',json={'password':'test'},headers={'X-GBR-Request':'1'})
                assert response.status==200
                assert response.cookies['gbr_comfy_session']['httponly']
                assert response.cookies['gbr_comfy_session']['path']=='/gbr'
                response=await client.get('/gbr/api/playback/'+'a'*64,headers={'Cookie':'gbr_comfy_session=private-session','X-GBR-Reviewer':'reviewer','Range':'bytes=0-10'})
                assert response.status==206 and await response.read()==b'audio-bytes'
                assert response.headers['Content-Range']=='bytes 0-10/11'
                assert seen[-1][1]['X-GBR-Reviewer']=='reviewer'
                assert seen[-1][1]['Cookie']=='gbr_session=private-session'
                assert (await client.get('/gbr/api/settings')).status==403
                assert (await client.post('/gbr/api/login',data=b'x'*100001,headers={'X-GBR-Request':'1'})).status==413
                assert (await client.get('/gbr/workspace')).status==200
    asyncio.run(scenario())


def test_installer_is_idempotent_and_never_overwrites_another_extension(tmp_path):
    (tmp_path/'custom_nodes').mkdir();(tmp_path/'main.py').write_text('')
    path=install(tmp_path);before=path.read_bytes();assert install(tmp_path).read_bytes()==before
    assert 'gbr_comfy_workspace' in before.decode()
    path.write_text('another extension')
    with pytest.raises(ValueError):install(tmp_path)
    assert path.read_text()=='another extension'
