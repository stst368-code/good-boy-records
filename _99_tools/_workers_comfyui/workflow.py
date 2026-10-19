"""Preflight and compile API-format Comfy workflows without running a graph.

Transport is independent of the engine: a localhost URL may be a local server
or an SSH tunnel to a dedicated RunPod. No models are configured per worker.
"""
from copy import deepcopy
import hashlib
import json
from urllib.parse import urlsplit
from urllib.request import urlopen


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def discover(address,fetch=None):
    url=urlsplit(address)
    if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('Use a ComfyUI base URL without credentials, paths or query parameters')
    def get(path):
        if fetch:return fetch(path)
        with urlopen(address.rstrip('/')+path,timeout=15) as response:return json.load(response)
    return {'system':get('/system_stats'),'nodes':get('/object_info')}


def compile_workflow(graph,bindings,values,inventory):
    if not isinstance(graph,dict) or not graph or any(not isinstance(v,dict) or 'class_type' not in v or not isinstance(v.get('inputs'),dict) for v in graph.values()):
        raise ValueError('Export the API-format workflow, not the editor canvas JSON')
    missing=sorted({node['class_type'] for node in graph.values()}-set(inventory))
    if missing:raise ValueError('Worker is missing node classes: '+', '.join(missing))
    if set(values)-set(bindings):raise ValueError('Unmapped requested controls: '+', '.join(sorted(set(values)-set(bindings))))
    compiled=deepcopy(graph)
    used=set()
    for field,value in values.items():
        targets=bindings[field]
        if not isinstance(targets,list) or not targets:raise ValueError('Each control needs an explicit node/input binding')
        for target in targets:
            node_id=str(target['node']);input_name=target['input'];key=(node_id,input_name)
            if key in used:raise ValueError('Two requested controls target the same workflow input')
            used.add(key)
            if node_id not in compiled:raise ValueError('Bound node is missing: '+node_id)
            node=compiled[node_id];definition=inventory[node['class_type']]
            inputs=definition.get('input',{})
            declaration=inputs.get('required',{}).get(input_name) or inputs.get('optional',{}).get(input_name)
            if not declaration:raise ValueError('Bound input is not advertised by worker: '+input_name)
            kind=declaration[0];options=declaration[1] if len(declaration)>1 and isinstance(declaration[1],dict) else {}
            actual=value
            if kind=='INT' and isinstance(actual,str) and actual.isdigit():actual=int(actual)
            if isinstance(kind,list) and actual not in kind:raise ValueError(field+' is not available on this worker')
            if isinstance(kind,str) and kind in ('INT','FLOAT'):
                if isinstance(actual,bool) or not isinstance(actual,(int,float)) or kind=='INT' and not isinstance(actual,int):raise ValueError(field+' has an invalid numeric type')
                if actual<options.get('min',-float('inf')) or actual>options.get('max',float('inf')):raise ValueError(field+' is outside the worker range')
            if kind=='STRING' and not isinstance(actual,str):raise ValueError(field+' requires text')
            if input_name in node['inputs'] and isinstance(node['inputs'][input_name],list):raise ValueError('Binding would replace a graph connection: '+input_name)
            node['inputs'][input_name]=actual
    # Validate fixed graph values too, so renamed inputs/choices fail before dispatch.
    for node_id,node in compiled.items():
        declaration=inventory[node['class_type']].get('input',{})
        required=declaration.get('required',{});optional=declaration.get('optional',{})
        absent=set(required)-set(node['inputs'])
        if absent:raise ValueError('Workflow is missing required inputs: '+', '.join(sorted(absent)))
        for name,value in node['inputs'].items():
            advertised=required.get(name) or optional.get(name)
            if not advertised:raise ValueError('Workflow input is not advertised: '+name)
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and type(value[1]) is int:
                if value[0] not in compiled or value[1]<0:raise ValueError('Workflow contains an invalid connection')
                continue
            kind=advertised[0];limits=advertised[1] if len(advertised)>1 and isinstance(advertised[1],dict) else {}
            if isinstance(kind,list) and value not in kind:raise ValueError('Workflow fixed choice is unavailable: '+name)
            if isinstance(kind,str) and kind in ('INT','FLOAT'):
                if isinstance(value,bool) or not isinstance(value,(int,float)) or kind=='INT' and type(value) is not int:raise ValueError('Workflow fixed numeric type is invalid: '+name)
                if value<limits.get('min',-float('inf')) or value>limits.get('max',float('inf')):raise ValueError('Workflow fixed value is outside range: '+name)
            if kind=='STRING' and not isinstance(value,str):raise ValueError('Workflow fixed text input is invalid: '+name)
    return {'prompt':compiled,'workflow_sha256':hashlib.sha256(canonical(compiled).encode()).hexdigest(),
            'source_workflow_sha256':hashlib.sha256(canonical(graph).encode()).hexdigest(),
            'effective_controls':values,'node_classes':sorted({n['class_type'] for n in compiled.values()}),
            'status':'compiled_only'}
