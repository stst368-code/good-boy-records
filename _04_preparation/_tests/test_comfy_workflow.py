import copy
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'_99_tools/_workers_comfyui'))
from workflow import compile_workflow,discover


def fixtures():
    graph={'1':{'class_type':'Encoder','inputs':{'caption':'old','cfg':1.5,'seed':0}},'2':{'class_type':'Sampler','inputs':{'conditioning':['1',0],'sampler':'custom_solver'}}}
    inventory={'Encoder':{'input':{'required':{'caption':['STRING',{}],'cfg':['FLOAT',{'min':0,'max':20}],'seed':['INT',{'min':0,'max':2**64-1}]}}},'Sampler':{'input':{'required':{'conditioning':['CONDITIONING',{}],'sampler':[['custom_solver','other_solver']]}}}}
    bindings={'caption':[{'node':'1','input':'caption'}],'encoder_cfg':[{'node':'1','input':'cfg'}],'seed':[{'node':'1','input':'seed'}],'sampler':[{'node':'2','input':'sampler'}]}
    return graph,bindings,inventory


def test_compile_retains_workflow_and_wide_controls_and_64_bit_seed():
    graph,bindings,inventory=fixtures();before=copy.deepcopy(graph)
    result=compile_workflow(graph,bindings,{'caption':'first\nsecond','encoder_cfg':7.2,'seed':'18446744073709551615','sampler':'other_solver'},inventory)
    assert graph==before
    assert result['status']=='compiled_only'
    assert result['prompt']['1']['inputs']['seed']==2**64-1
    assert result['prompt']['1']['inputs']['caption']=='first\nsecond'
    assert result['prompt']['1']['inputs']['cfg']==7.2
    assert result['prompt']['2']['inputs']['conditioning']==['1',0]


def test_unmapped_or_unsupported_controls_fail_instead_of_being_ignored():
    graph,bindings,inventory=fixtures()
    with pytest.raises(ValueError,match='Unmapped'):compile_workflow(graph,bindings,{'top_k':100},inventory)
    with pytest.raises(ValueError,match='not available'):compile_workflow(graph,bindings,{'sampler':'imaginary'},inventory)
    with pytest.raises(ValueError,match='outside'):compile_workflow(graph,bindings,{'encoder_cfg':21},inventory)
    with pytest.raises(ValueError,match='missing node'):compile_workflow(graph,bindings,{}, {})


def test_binding_cannot_break_a_connection():
    graph,bindings,inventory=fixtures();bindings={'conditioning':[{'node':'2','input':'conditioning'}]}
    with pytest.raises(ValueError,match='graph connection'):compile_workflow(graph,bindings,{'conditioning':0},inventory)


def test_inventory_is_queried_and_never_submits_or_embeds_credentials():
    seen=[]
    result=discover('http://127.0.0.1:8188',lambda path:seen.append(path) or {'path':path})
    assert seen==['/system_stats','/object_info']
    assert result['nodes']['path']=='/object_info'
    for address in ['http://user:pass@localhost:8188','http://localhost?api_key=secret','file:///tmp/comfy']:
        with pytest.raises(ValueError):discover(address,lambda path:{})
