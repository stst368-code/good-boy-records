"""Pull lyric alignment from another GBR PC; no shared drive or local GBR DB."""
import argparse
from base64 import b64decode,b64encode
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener
import uuid
import zipfile

ROOT=Path(__file__).resolve().parent
MAX_BYTES=1024**3


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def credential(path,value=None):
    """Windows DPAPI binds a remembered pairing key to the signed-in user."""
    if sys.platform!='win32':return None
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]
    raw=value.encode() if value is not None else b64decode(path.read_text()) if path.is_file() else b''
    if not raw:return None
    buffer=ctypes.create_string_buffer(raw);incoming=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_byte)));out=Blob()
    function=ctypes.windll.crypt32.CryptProtectData if value is not None else ctypes.windll.crypt32.CryptUnprotectData
    if not function(ctypes.byref(incoming),None,None,None,None,1,ctypes.byref(out)):raise OSError('Windows could not unlock the saved pairing key')
    try:
        result=ctypes.string_at(out.data,out.size)
        if value is not None:path.write_text(b64encode(result).decode());return value
        return result.decode()
    finally:ctypes.windll.kernel32.LocalFree(out.data)


class Client:
    def __init__(self,server,token):
        parsed=urlsplit(server)
        if parsed.scheme not in {'http','https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.strip('/'):
            raise ValueError('Use the main GBR address, for example http://192.168.1.10:8765')
        self.server=server.rstrip('/');self.token=token;self.opener=build_opener(NoRedirect())

    def request(self,path,data=None,*,lease=None,stream=None,size=None):
        headers={'Authorization':'Bearer '+self.token,'X-GBR-Request':'1'}
        if lease:headers['X-GBR-Lease']=lease
        if stream is not None:body=stream;headers.update({'Content-Type':'application/zip','Content-Length':str(size)})
        elif data is not None:body=json.dumps(data,allow_nan=False).encode();headers['Content-Type']='application/json'
        else:body=None
        return self.opener.open(Request(self.server+'/api/processing-worker'+path,data=body,headers=headers),timeout=90)

    def json(self,path,data,lease=None):
        with self.request(path,data,lease=lease) as response:return json.load(response)


def available(comfy_url=None,device='auto'):
    if comfy_url:
        try:
            with build_opener(NoRedirect()).open(comfy_url.rstrip('/')+'/queue',timeout=2) as response:queue=json.load(response)
            if queue.get('queue_running'):return False
        except (OSError,ValueError):pass
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=utilization.gpu,memory.free','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3)
        if result.returncode==0:
            for row in result.stdout.splitlines():
                used,free=[float(value.strip()) for value in row.split(',')[:2]]
                if used>35 or (free<2200 and device=='cuda'):return False
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    try:
        import psutil
        if psutil.cpu_percent(interval=None)>90:return False
        if psutil.virtual_memory().available<6*1024**3:return False
    except ImportError:pass
    return True


def package_result(folder,result):
    outputs=[{'output':result['output'],'artifact_kind':result['artifact_kind']},*result.get('additional_outputs',[])]
    manifest={key:result.get(key,{}) for key in ['software','hardware','measurements']};manifest['outputs']=[]
    bundle=folder/'upload.zip';total=0
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
        for index,item in enumerate(outputs):
            path=Path(item['output']).resolve()
            if not path.is_relative_to(folder.resolve()) or path.is_symlink():raise ValueError('Worker returned an invalid output path')
            total+=path.stat().st_size
            if total>MAX_BYTES:raise ValueError('Worker result exceeds 1 GiB')
            name='output-'+str(index)+path.suffix
            manifest['outputs'].append({'file':name,'kind':item['artifact_kind'],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
            archive.write(path,name)
        archive.writestr('result.json',json.dumps(manifest,allow_nan=False))
    return bundle


def execute(client,task,device='auto',python=None):
    identifier=task['id'];lease=task['lease'];stop=threading.Event();process=None
    with tempfile.TemporaryDirectory(prefix='gbr-preparation-') as temporary:
        folder=Path(temporary);progress=folder/'progress.json';log=folder/'worker.log'
        started=time.monotonic();phase={'phase':'transferring','message':'Downloading source audio'}
        def report():
            while not stop.is_set():
                values={**phase,'wall_seconds':time.monotonic()-started}
                try:
                    if progress.is_file():values.update(json.loads(progress.read_text(encoding='utf-8')))
                    if log.is_file():
                        with log.open('rb') as stream:stream.seek(max(0,log.stat().st_size-6000));values['log_tail']=stream.read().decode('utf-8',errors='replace')
                    client.json('/tasks/'+identifier+'/progress',values,lease)
                except (OSError,ValueError):pass
                stop.wait(3)
        reporter=threading.Thread(target=report,name='gbr-worker-progress',daemon=True);reporter.start()
        try:
            source=folder/'source.flac';checksum=hashlib.sha256();received=0
            with client.request('/tasks/'+identifier+'/source',lease=lease) as response,source.open('wb') as output:
                while chunk:=response.read(1024**2):
                    received+=len(chunk)
                    if received>min(MAX_BYTES,task['source_bytes']):raise ValueError('Source download exceeds its expected size')
                    output.write(chunk);checksum.update(chunk)
            if received!=task['source_bytes'] or checksum.hexdigest()!=task['source_sha256']:raise ValueError('Source audio checksum mismatch')
            request={'kind':'lyric_alignment','source':str(source),'source_sha256':task['source_sha256'],
                'duration':task['duration'],'recipe':task['recipe'],'output':str(folder/'result'),'progress_file':str(progress)}
            environment=os.environ.copy();environment.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8',GBR_PROCESSING_DEVICE=device)
            environment.setdefault('HF_HOME',str(ROOT/'_runtime/huggingface'));environment.setdefault('TORCH_HOME',str(ROOT/'_runtime/torch'))
            command=[str(python or sys.executable),str(ROOT/'alignment.py')]
            phase.update(phase='starting',message='Starting lyric alignment')
            fallback=False
            for execution_device in ([device,'cpu'] if device=='auto' else [device]):
                environment['GBR_PROCESSING_DEVICE']=execution_device;progress.unlink(missing_ok=True)
                with (folder/'stdout.json').open('w+b') as stdout,log.open('w+b') as stderr:
                    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,env=environment)
                    process.stdin.write(json.dumps(request,allow_nan=False).encode());process.stdin.close()
                    try:process.wait(timeout=7200)
                    except subprocess.TimeoutExpired:raise RuntimeError('Alignment exceeded its two-hour limit') from None
                    if process.returncode:
                        with log.open('rb') as stream:stream.seek(max(0,log.stat().st_size-6000));error=stream.read().decode('utf-8',errors='replace')
                        gpu_error=any(message in error.lower() for message in ['cuda out of memory','cuda error','cuda failed','cudnn','cublas','no kernel image','float16 compute type'])
                        if execution_device=='auto' and gpu_error:
                            fallback=True;phase.update(phase='cpu_fallback',message='GPU could not run this model · retrying on CPU')
                            continue
                        raise RuntimeError(error or 'Alignment worker exited without a result')
                    stdout.seek(0);result=json.load(stdout);break
            if fallback:result.setdefault('measurements',{})['fallback']='CPU after GPU runtime error'
            phase.update(phase='uploading',message='Returning timings and separated stems')
            progress.unlink(missing_ok=True)
            bundle=package_result(folder,result)
            with bundle.open('rb') as stream:
                with client.request('/tasks/'+identifier+'/result',lease=lease,stream=stream,size=bundle.stat().st_size) as response:
                    if not json.load(response).get('accepted'):raise RuntimeError('GBR did not accept the result')
        except BaseException as error:
            try:client.json('/tasks/'+identifier+'/fail',{'error':str(error)[:6000] or 'Preparation worker stopped'},lease)
            except (OSError,ValueError):pass
            raise
        finally:
            stop.set();reporter.join(timeout=5)
            if process and process.poll() is None:process.kill();process.wait()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',required=True,help='Main GBR PC LAN address, including port 8765.')
    parser.add_argument('--name',default=socket.gethostname())
    parser.add_argument('--device',choices=['auto','cpu','cuda'],default='auto')
    parser.add_argument('--comfy-url',help='Optional local ComfyUI address to avoid using that PC while it generates.')
    parser.add_argument('--token',help='Pairing key; omitted keys are read from Windows credentials, environment or a hidden prompt.')
    parser.add_argument('--once',action='store_true',help='Process at most one available track, then exit.')
    args=parser.parse_args(argv)
    runtime=ROOT.parents[1]/'_97_app/_runtime/processing-worker';runtime.mkdir(parents=True,exist_ok=True)
    saved=runtime/(hashlib.sha256(args.server.encode()).hexdigest()+'.key')
    token=args.token or os.environ.get('GBR_PROCESSING_TOKEN') or credential(saved)
    if not token:token=getpass.getpass('GBR pairing key: ').strip();credential(saved,token)
    client=Client(args.server,token)
    identity=runtime/'identity';machine=identity.read_text().strip() if identity.is_file() else uuid.uuid4().hex
    identity.write_text(machine)
    doctor=subprocess.run([sys.executable,str(ROOT/'worker.py'),'--doctor'],capture_output=True,text=True)
    if doctor.returncode or 'lyric_alignment' not in json.loads(doctor.stdout).get('ready_adapters',[]):
        raise SystemExit('Run setup-processing on this PC first. For a GTX 1060 use setup-processing --cuda cu126. FFmpeg must be on PATH.')
    print('Preparation worker connected to '+args.server+'. Waiting for tracks; Ctrl+C stops this worker.',flush=True)
    while True:
        try:
            worker={'id':machine,'name':args.name,'device':args.device,'available':available(args.comfy_url,args.device)}
            task=client.json('/claim',worker).get('task')
            if task:
                print('Aligning '+task['id']+' on '+args.device,flush=True)
                try:execute(client,task,args.device);print('Alignment returned to GBR.',flush=True)
                except (OSError,ValueError,RuntimeError) as error:print('Alignment stopped: '+str(error)[-700:],file=sys.stderr,flush=True)
            if args.once:return 0
        except HTTPError as error:
            if error.code in {401,403}:raise SystemExit('Pairing key was rejected. Create a new key in Preparation → Processing and enter it again.') from None
            print('Main GBR PC is unavailable; retrying shortly.',file=sys.stderr,flush=True)
        except (URLError,OSError):print('Main GBR PC is unavailable; retrying shortly.',file=sys.stderr,flush=True)
        time.sleep(5)


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:print('\nPreparation worker stopped.')
