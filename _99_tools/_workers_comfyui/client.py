"""ComfyUI HTTP transport. Frozen prompt IDs survive controller disconnects."""
import hashlib
import json
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from workflow import discover,compile_workflow,canonical

ROOT=Path(__file__).parent


def base_url(address):
    # Reuse the preflight's credential/path validation without making a request.
    discover(address,fetch=lambda _: {})
    return address.rstrip('/')


def resolve(options,value,label):
    if value in options:return value
    aliases={'bf16':['bf16','bfloat16'],'fp32':['fp32','float32'],'fp16':['fp16','float16'],'int8':['int8','q8'],'fp8':['fp8']}
    candidates=[name for name in options if 'minimax' in name.casefold() and any(alias in name.casefold() for alias in aliases.get(value,[value]))]
    if label=='VAE' and not candidates:candidates=[name for name in options if 'minimax' in name.casefold() and any(part in name.casefold() for part in ('dav','vae'))]
    if len(candidates)!=1:raise ValueError(f'{label}: choose an exact checkpoint filename; matches={candidates}')
    return candidates[0]

class Client:
    def __init__(self,address):self.address=base_url(address)
    def request(self,path,data=None,timeout=45):
        body=None if data is None else canonical(data).encode()
        with urlopen(Request(self.address+path,data=body,headers={'Content-Type':'application/json'}),timeout=timeout) as response:return json.load(response)
    def capabilities(self,timeout=5):
        # Stock ComfyUI may route absent extension URLs to a static handler.
        try:
            result=self.request('/gbr/capabilities',timeout=timeout)
            return result if isinstance(result,dict) else {}
        except HTTPError as error:
            if error.code not in (404,405,501):raise
        except (json.JSONDecodeError,UnicodeDecodeError):pass
        return {}
    def inventory(self,timeout=45):
        return {'system':self.request('/system_stats',timeout=timeout),'nodes':self.request('/object_info',timeout=timeout),
                'capabilities':self.capabilities(timeout=min(timeout,5))}
    def prepare(self,job,inventory=None):
        inventory=inventory or self.inventory();nodes=inventory['nodes'];graph=json.loads((ROOT/'_workflows/music-full.json').read_text());bindings=json.loads((ROOT/'_workflows/music-bindings.json').read_text())
        def choices(node,key):
            try:return nodes[node]['input']['required'][key][0]
            except KeyError:raise ValueError('Missing Comfy node/input: '+node+'/'+key) from None
        p=job['parameters'];values={k:p[k] for k in ['caption','encoder_seed','duration','encoder_cfg','top_k','sampler_seed','sampler_steps','sampler_cfg','sampler','scheduler']}
        values.update(lyrics=job['lyrics'],text_encoder=resolve(choices('CLIPLoader','clip_name'),p['text_encoder'],'text encoder'),dit=resolve(choices('UNETLoader','unet_name'),p['dit'],'DiT'),
            vae=resolve(choices('VAELoader','vae_name'),graph['7']['inputs']['vae_name'],'VAE'),output_prefix='gbr/'+job['attempt_id'])
        compiled=compile_workflow(graph,bindings,values,nodes)
        identity={k:values[k] for k in ['text_encoder','dit','vae']};identity.update(identity_kind='workflow-and-checkpoint-names',checkpoint_sha256=None,node_classes=compiled['node_classes'])
        identity['source_workflow_sha256']=compiled['source_workflow_sha256']
        if inventory.get('capabilities',{}).get('checkpoint_sha256'):
            names={k:values[k] for k in ['text_encoder','dit','vae']}
            cache=inventory.setdefault('_checkpoint_hashes',{})
            try:
                key=canonical(names)
                if key not in cache:cache[key]=self.request('/gbr/model-info',names,timeout=300)
                identity['checkpoint_sha256']=cache[key];identity['identity_kind']='workflow-and-checkpoint-sha256'
            except HTTPError as error:
                if error.code not in (404,405,501):raise
        identity['revision']=hashlib.sha1(canonical(identity).encode()).hexdigest()
        if job.get('model_revision') and job['model_revision']!=identity['revision']:raise ValueError('Comfy model inventory changed from the reserved run')
        devices=inventory['system'].get('devices',[]);device=devices[0] if devices else {};system=inventory['system'].get('system',{})
        hardware={'address':self.address,'provider':'comfyui_url','gpu_name':device.get('name'),'usable_vram_bytes':device.get('vram_total'),'free_vram_bytes_at_start':device.get('vram_free'),'gpu_uuid':None,'gpu_peak_vram_bytes':None,'cuda_device':device.get('index'),'memory_profile':'server_managed'}
        return {'compiled':compiled,'identity':identity,'hardware':hardware,'system':system}
    def acknowledge(self,job,directory):
        ref=json.loads((Path(directory)/'comfy.json').read_text())
        if not self.capabilities().get('verified_output_cleanup'):
            raise RuntimeError('ComfyUI output cleanup is pending: install _99_tools/_workers_comfyui/gbr_bridge.py in ComfyUI/custom_nodes, restart ComfyUI, then retry Clean temporary files. The verified local result is already saved.')
        self.request('/gbr/ack',{'attempt_id':job['attempt_id'],'file':ref['file'],'sha256':ref['download_sha256']})
    def run(self,job,directory,resume=False):
        from _gbr_worker.runpod import RemotePending
        directory=Path(directory);journal=directory/'comfy.json'
        def save(data):
            temporary=journal.with_suffix('.tmp');temporary.write_text(canonical(data),encoding='utf-8');temporary.replace(journal)
        def event(**data):
            from datetime import datetime,timezone
            with (directory/'events.ndjson').open('a',encoding='utf-8') as f:f.write(canonical({'at':datetime.now(timezone.utc).isoformat(),**data})+'\n')
        try:
            if journal.is_file():ref=json.loads(journal.read_text())
            else:
                prepared=self.prepare(job);compiled=prepared['compiled'];identity=prepared['identity'];hardware=prepared['hardware'];system=prepared['system']
                ref={'state':'submitting','attempt_id':job['attempt_id'],'prompt':compiled['prompt'],'workflow_sha256':compiled['workflow_sha256'],'models':identity,'hardware':hardware,'versions':system,'client_id':uuid.uuid4().hex}
                save(ref)
                event(event='worker',hardware=hardware,versions=system);event(event='model_resolved',models=identity)
                submitted=self.request('/prompt',{'prompt':ref['prompt'],'client_id':ref['client_id'],'extra_data':{'gbr_attempt_id':job['attempt_id']}})
                if submitted.get('node_errors') or not submitted.get('prompt_id'):raise ValueError('Comfy rejected workflow: '+canonical(submitted))
                ref.update(state='queued',prompt_id=submitted['prompt_id']);save(ref)
            if not (directory/'events.ndjson').is_file():
                event(event='worker',hardware=ref['hardware'],versions=ref['versions']);event(event='model_resolved',models=ref['models'])
            if not ref.get('prompt_id'):
                # A timed-out POST may have been accepted. Never resubmit an uncertain attempt.
                candidates=[]
                queue=self.request('/queue')
                for row in [*queue.get('queue_running',[]),*queue.get('queue_pending',[])]:
                    if len(row)>3 and row[3].get('gbr_attempt_id')==job['attempt_id']:candidates.append(row[1])
                for identifier,history in self.request('/history?max_items=100').items():
                    prompt=history.get('prompt',[])
                    if len(prompt)>3 and prompt[3].get('gbr_attempt_id')==job['attempt_id']:candidates.append(identifier)
                if len(set(candidates))!=1:raise RemotePending('Submission acknowledgement missing; inspect Comfy queue/history. This attempt was not submitted again.')
                ref['prompt_id']=candidates[0];save(ref)
            deadline=time.monotonic()+7200
            while time.monotonic()<deadline:
                history=self.request('/history/'+ref['prompt_id']).get(ref['prompt_id'])
                if history:
                    if history.get('status',{}).get('status_str')=='error':raise RuntimeError('Comfy execution failed: '+canonical(history.get('status')))
                    if history.get('status',{}).get('completed'):
                        files=[f for output in history.get('outputs',{}).values() for f in output.get('audio',[]) if f.get('filename','').endswith('.flac')]
                        if len(files)!=1:raise ValueError('Expected one generated FLAC in Comfy history')
                        ref['file']=files[0];break
                time.sleep(1)
            else:raise RemotePending('Comfy prompt is still pending; reconnect to the saved prompt ID')
            file=ref['file']
            if file.get('type','output')!='output' or Path(file['filename']).name!=file['filename'] or '..' in Path(file.get('subfolder','')).parts or '\\' in file.get('subfolder','') or not file.get('subfolder','').startswith('gbr') or not file['filename'].startswith(job['attempt_id']):raise ValueError('Comfy returned a file outside this attempt namespace')
            output=directory/'audio.flac';temporary=directory/'download.part'
            with urlopen(self.address+'/view?'+urlencode(file),timeout=120) as response,temporary.open('wb') as stream:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>2*1024**3:raise ValueError('Comfy audio exceeds transfer limit')
                    stream.write(chunk)
            import soundfile as sf
            info=sf.info(temporary)
            if info.format!='FLAC' or not info.frames:raise ValueError('Comfy output is not valid FLAC')
            temporary.replace(output);ref['download_sha256']=hashlib.sha256(output.read_bytes()).hexdigest();ref['state']='downloaded';save(ref)
            from datetime import datetime,timezone
            messages=history.get('status',{}).get('messages',[])
            stamps={kind:payload.get('timestamp') for kind,payload in messages if kind in ('execution_start','execution_success') and isinstance(payload,dict)}
            cached=[node for kind,payload in messages if kind=='execution_cached' and isinstance(payload,dict) for node in payload.get('nodes',[])]
            if all(isinstance(stamps.get(kind),(int,float)) for kind in ('execution_start','execution_success')) and stamps['execution_success']>=stamps['execution_start']:
                event(event='stage_end',id=job['attempt_id']+'-comfy',stage='comfy_execution',started_at=datetime.fromtimestamp(stamps['execution_start']/1000,timezone.utc).isoformat(),completed_at=datetime.fromtimestamp(stamps['execution_success']/1000,timezone.utc).isoformat(),duration_seconds=(stamps['execution_success']-stamps['execution_start'])/1000,resource={'device_id':ref['hardware']['gpu_uuid'] or self.address,'sampling_interval_ms':None,'coverage':'unavailable','offload':{},'details':{'timing_source':'comfy_server_timestamps','queue_time_excluded':True}})
            result={'protocol':1,'run_id':job['run_id'],'attempt_id':job['attempt_id'],'status':'succeeded','completed_at':datetime.now(timezone.utc).isoformat(),
                'actual_parameters':job['parameters'],'models':ref['models'],'hardware':ref['hardware'],'workflow_sha256':ref['workflow_sha256'],
                'remote':{'prompt_id':ref['prompt_id'],'file':ref['file'],'download_sha256':ref['download_sha256']},'encoding':{'mode':'comfy_graph','cache_hit':('2' in cached) if any(kind=='execution_cached' for kind,_ in messages) else None,'key':None},'telemetry_limitations':['Per-prompt peak VRAM is unavailable from stock ComfyUI; checkpoint hashes require the GBR bridge.']}
            (directory/'result.json').write_text(canonical(result),encoding='utf-8')
            return result
        except HTTPError as error:
            if error.code in (400,422):raise ValueError('Comfy rejected request: '+error.read().decode('utf-8',errors='replace')[-4000:]) from error
            raise RemotePending('Comfy HTTP connection failed; reconnect to the saved attempt: '+str(error)) from error
        except (OSError,URLError,TimeoutError) as error:raise RemotePending('Comfy connection lost; reconnect to this attempt without resubmitting: '+str(error)) from error
        finally:
            (directory/'download.part').unlink(missing_ok=True)
