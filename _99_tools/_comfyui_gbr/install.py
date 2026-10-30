"""Install a small loader; the checked-out GBR branch remains the source."""
import argparse
from pathlib import Path

MARKER='# GBR ComfyUI workspace loader\n'


def install(comfy_dir):
    comfy=Path(comfy_dir).expanduser().resolve(strict=True)
    if not (comfy/'main.py').is_file() or not (comfy/'custom_nodes').is_dir():raise ValueError('Choose the ComfyUI folder containing main.py and custom_nodes')
    target=comfy/'custom_nodes/gbr_workspace/__init__.py'
    if target.exists() and not target.read_text(encoding='utf-8').startswith(MARKER):raise ValueError('gbr_workspace already contains another installation; retained it')
    source=Path(__file__).resolve().parent
    content=MARKER+f'''import importlib.util
import sys
from pathlib import Path
source=Path({str(source)!r})
spec=importlib.util.spec_from_file_location('gbr_comfy_workspace',source/'__init__.py',submodule_search_locations=[str(source)])
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
NODE_CLASS_MAPPINGS=module.NODE_CLASS_MAPPINGS
NODE_DISPLAY_NAME_MAPPINGS=module.NODE_DISPLAY_NAME_MAPPINGS
WEB_DIRECTORY=str(source/'_web')
'''
    target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--comfy-dir',required=True,type=Path);args=parser.parse_args()
    try:print(str(install(args.comfy_dir))+'\nRestart ComfyUI, then open the GBR workspace panel.')
    except (OSError,ValueError) as error:parser.exit(1,str(error)+'\n')
