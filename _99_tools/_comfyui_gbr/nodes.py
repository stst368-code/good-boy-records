"""Pure graph values. Database writes and dispatch require a workspace action."""
from copy import deepcopy
import hashlib
import json

SECTIONS=[('Global Metadata',[('basic','Basic Attributes'),('emotion','Global Emotional Progression'),('imagery','Application Scenarios & Imagery'),('sonics','Sonics & Production Profile')]),('Vocal Details',[('timbre','Vocal Gender & Timbre'),('vocal_style','Vocal Style'),('harmony','Harmony/Backing Vocals'),('vocal_fx','Vocal FX')]),('Arrangement',[('primary','Primary'),('secondary','Secondary'),('groove','Groove & Foundation Progression'),('textures','Embellishments, Textures & Spatial FX')])]
FIELDS=[key for _,items in SECTIONS for key,_ in items]


def compose(fields):
    lines=[]
    for heading,items in SECTIONS:
        lines.append(heading)
        if heading=='Arrangement':lines.append('Instrument Lifecycle Description (Primary/Secondary Layering):')
        for key,label in items:lines.append(label+': '+fields.get(key,'').strip())
    return '\n'.join(lines)+'\n'


class Song:
    CATEGORY='GBR';FUNCTION='build';RETURN_TYPES=('GBR_SONG','STRING');RETURN_NAMES=('song','lyrics')
    DESCRIPTION='Snapshot of a saved GBR song. Use the GBR workspace to load or save a base/variant.'
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{name:('STRING',{'default':'','multiline':name=='lyrics'}) for name in ('file','source_sha256','title','lyrics')}}
    def build(self,**values):return (deepcopy(values),values['lyrics'])


class Caption:
    CATEGORY='GBR';FUNCTION='build';RETURN_TYPES=('GBR_CAPTION','STRING');RETURN_NAMES=('caption','text')
    DESCRIPTION='Mix editable caption sections. Reference loading and rated auditions are in the GBR workspace.'
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{name:('STRING',{'default':'','multiline':name in FIELDS}) for name in ['cluster','style',*FIELDS]}}
    def build(self,cluster,style,**fields):return ({'cluster':cluster,'style':style,'fields':deepcopy(fields),'text':compose(fields)},compose(fields))


class GenerationPlan:
    CATEGORY='GBR';FUNCTION='build';RETURN_TYPES=('GBR_PLAN',);RETURN_NAMES=('plan',);OUTPUT_NODE=True
    DESCRIPTION='Build a generation request without launching it. Open GBR → Preview → Launch. Experiment mode is the default.'
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'song':('GBR_SONG',),'caption':('GBR_CAPTION',),'worker_id':('STRING',{'default':'auto'}),
            'purpose':(['experiment','production'],),'steps':('INT',{'default':30,'min':1,'max':10000}),
            'encoder_cfg':('FLOAT',{'default':1.7,'min':0,'max':100}),
            'diffusion_cfg':('FLOAT',{'default':1.7,'min':0,'max':100}),
            'duration':('FLOAT',{'default':360,'min':1,'max':3600})}}
    def build(self,song,caption,worker_id,purpose,steps,encoder_cfg,diffusion_cfg,duration):
        if not song.get('file') or len(song.get('source_sha256',''))!=64:raise ValueError('Load a saved song from the GBR workspace first')
        if not caption.get('cluster','').strip() or not any(v.strip() for v in caption.get('fields',{}).values()):raise ValueError('Choose a cluster and enter a caption section')
        plan={'song':deepcopy(song),'caption':deepcopy(caption),'worker_id':worker_id,'purpose':purpose,
              'parameters':{'sampler_steps':steps,'encoder_cfg':encoder_cfg,'sampler_cfg':diffusion_cfg,'duration':duration}}
        fingerprint=hashlib.sha256(json.dumps(plan,sort_keys=True,allow_nan=False).encode()).hexdigest()
        plan['fingerprint']=fingerprint
        return {'ui':{'gbr_plan':[plan]},'result':(plan,)}


class ReviewTarget:
    CATEGORY='GBR';FUNCTION='build';RETURN_TYPES=('STRING',);OUTPUT_NODE=True
    DESCRIPTION='Open a saved recording in the GBR workspace. Queuing this node never submits ratings.'
    @classmethod
    def INPUT_TYPES(cls):return {'required':{'generation_id':('STRING',{'default':''})}}
    def build(self,generation_id):return {'ui':{'gbr_recording':[generation_id]},'result':(generation_id,)}

NODE_CLASS_MAPPINGS={'GBRSong':Song,'GBRCaption':Caption,'GBRGenerationPlan':GenerationPlan,'GBRReviewTarget':ReviewTarget}
NODE_DISPLAY_NAME_MAPPINGS={'GBRSong':'GBR · Song','GBRCaption':'GBR · Caption','GBRGenerationPlan':'GBR · Generation plan','GBRReviewTarget':'GBR · Review recording'}
