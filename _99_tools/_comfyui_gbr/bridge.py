"""Loopback-only, allowlisted bridge to the existing GBR application API."""
import ipaddress
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from aiohttp import ClientError,ClientSession,ClientTimeout,web

ROOT=Path(__file__).resolve().parent
GET=[r'bootstrap',r'writing/(authoring|base|caption-source|caption-examples|caption-auditions)',r'generation/(catalogue|templates|workers|jobs)',r'generation/jobs/[a-f0-9]{32}',r'generation/experiments/[a-f0-9]{32}/audio',r'tracks/[a-f0-9]{64}',r'queue/next',r'playback/[a-f0-9]{64}']
POST=[r'login',r'logout',r'reviewer/(select|heartbeat)',r'writing/(base|captions)',r'generation/(preview|start)',r'queue/open',r'tracks/[a-f0-9]{64}/review']


def endpoint(value=None):
    value=value or os.environ.get('GBR_APP_URL','http://127.0.0.1:8765')
    url=urlsplit(value)
    if url.scheme!='http' or url.hostname not in {'127.0.0.1','localhost','::1'} or url.username or url.password or url.path not in {'','/'} or url.query or url.fragment:
        raise ValueError('GBR_APP_URL must be a loopback HTTP origin, for example http://127.0.0.1:8765')
    return value.rstrip('/')


def allowed(method,path):
    return any(re.fullmatch(pattern,path) for pattern in GET if method=='GET') or any(re.fullmatch(pattern,path) for pattern in POST if method=='POST')


def guard(request,write=False):
    try:local=ipaddress.ip_address(request.remote or '').is_loopback
    except ValueError:local=False
    if not local:raise web.HTTPForbidden(text='The experimental GBR workspace is available on the ComfyUI host only')
    origin=request.headers.get('Origin')
    if origin and urlsplit(origin).netloc!=request.host:raise web.HTTPForbidden(text='Same-origin request required')
    if request.headers.get('Sec-Fetch-Site')=='cross-site' or write and request.headers.get('X-GBR-Request')!='1':raise web.HTTPForbidden(text='Same-origin request header required')


def register(routes,upstream=None):
    @routes.get('/gbr/workspace')
    async def workspace(request):
        guard(request)
        return web.FileResponse(ROOT/'_static/workspace.html',headers={'Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; media-src 'self'; frame-ancestors 'self'; object-src 'none'"})

    @routes.get('/gbr/assets/{name}')
    async def asset(request):
        guard(request);name=request.match_info['name']
        if name not in {'workspace.js','workspace.css'}:raise web.HTTPNotFound()
        return web.FileResponse(ROOT/'_static'/name,headers={'Cache-Control':'no-cache'})

    @routes.route('*','/gbr/api/{path:.*}')
    async def proxy(request):
        guard(request,request.method!='GET')
        path=request.match_info['path']
        if not allowed(request.method,path):raise web.HTTPForbidden(text='This operation is outside the GBR prototype')
        if request.content_length is not None and request.content_length>100000:raise web.HTTPRequestEntityTooLarge(max_size=100000,actual_size=request.content_length)
        body=bytearray()
        async for block in request.content.iter_chunked(16384):
            body.extend(block)
            if len(body)>100000:raise web.HTTPRequestEntityTooLarge(max_size=100000,actual_size=len(body))
        headers={'X-GBR-Request':'1','Accept-Encoding':'identity'}
        for name in ('Content-Type','Range','X-GBR-Reviewer'):
            if request.headers.get(name):headers[name]=request.headers[name]
        if request.cookies.get('gbr_comfy_session'):headers['Cookie']='gbr_session='+request.cookies['gbr_comfy_session']
        try:
            target=endpoint(upstream)+'/api/'+path
            async with ClientSession(timeout=ClientTimeout(total=90,sock_connect=3,sock_read=60),auto_decompress=False) as session:
                async with session.request(request.method,target,params=request.query,data=body or None,headers=headers,allow_redirects=False) as source:
                    if 300<=source.status<400:raise web.HTTPBadGateway(text='GBR returned an unexpected redirect')
                    response=web.StreamResponse(status=source.status,headers={k:v for k,v in source.headers.items() if k.lower() in {'content-type','content-range','accept-ranges','content-length'}})
                    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
                    if 'gbr_session' in source.cookies:
                        cookie=source.cookies['gbr_session']
                        response.set_cookie('gbr_comfy_session',cookie.value,path='/gbr',httponly=True,samesite='Strict',secure=request.secure,max_age=int(cookie['max-age']) if cookie['max-age'] else None)
                    await response.prepare(request)
                    async for block in source.content.iter_chunked(65536):await response.write(block)
                    await response.write_eof();return response
        except (ClientError,OSError,TimeoutError) as error:
            raise web.HTTPBadGateway(text='GBR is unavailable. Start _97_app/run_gbr.py, then reconnect in the workspace.') from error
