"""Use the shared surgical YAML editor for imported and revised Library copies."""
from pathlib import Path
import yaml


def update(raw,changes,root):
    from _gbr_yaml_editor.document import Source,edit
    template=Path(root)/'_01_writing/_built/track-template.yaml'
    order=list(yaml.safe_load(template.read_text(encoding='utf-8-sig')) or {}) if template.is_file() else [
        'song_id','variant_id','title','language','explicit','cluster','style','album_name','album_position','display_order',
        'model','dit','text_encoder','encoder_cfg','encoder_seed','top_k','sampler','scheduler','sampler_cfg','sampler_seed','sampler_steps','generation_datetime','caption','lyrics']
    if 'style' not in order and 'cluster' in order:order.insert(order.index('cluster')+1,'style')
    for key,value in changes.items():
        source=Source(raw);operation={'kind':'set','key':key,'value':value,'position':'end'}
        if not source.one(key) and key in order:
            index=order.index(key)
            following=next((name for name in order[index+1:] if source.one(name)),None)
            preceding=next((name for name in reversed(order[:index]) if source.one(name)),None)
            if following:operation.update(position='before',anchor=following)
            elif preceding:operation.update(position='after',anchor=preceding)
        raw=edit(raw,[operation])
    return raw
