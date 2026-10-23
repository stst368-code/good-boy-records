"""Read-only inventory check. Does not queue a ComfyUI prompt or install nodes."""
import argparse
import json
from pathlib import Path
from workflow import discover, compile_workflow


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address',required=True)
    parser.add_argument('--workflow',type=Path)
    parser.add_argument('--bindings',type=Path)
    parser.add_argument('--values',type=Path)
    args=parser.parse_args()
    result=discover(args.address)
    if any([args.workflow,args.bindings,args.values]):
        if not all([args.workflow,args.bindings,args.values]):parser.error('Supply workflow, bindings and values together')
        result['compiled']=compile_workflow(*(json.loads(p.read_text(encoding='utf-8-sig')) for p in [args.workflow,args.bindings,args.values]),result['nodes'])
    print(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2))


if __name__=='__main__':main()
