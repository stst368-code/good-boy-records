"""A song edit list and its source-to-timeline mapping, independent of storage."""
from copy import deepcopy
import math
import re

MAX_SEGMENTS = 128
MAX_SOUNDS = 128
MODES = {'normal', 'mute', 'reverse', 'stutter', 'scratch', 'stop', 'start'}


def number(value, low, high, label):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid '+label)
    return float(value)


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise ValueError('Invalid timeline object ID')
    return value


def length(segment):
    return segment['duration'] if segment['kind'] == 'gap' else segment['end']-segment['start']


def spans(project):
    at = 0.
    result = []
    for segment in project['segments']:
        end = at+length(segment)
        result.append({**segment, 'at': at, 'until': end})
        at = end
    return result


def duration(project):
    return sum(length(segment) for segment in project['segments'])


def warp(segment):
    """Monotonic pieces of a bounded record gesture; time never leaves the clip."""
    size, source = length(segment), segment.get('start', 0)
    mode = segment.get('mode', 'normal')
    if mode in {'normal', 'mute'}:
        return [(0., size, source, source+size)]
    if mode == 'reverse':
        return [(0., size, source+size, source)]
    if mode == 'stutter':
        step = min(.25, size)
        return [(at, min(size, at+step), source, source+min(step, size-at))
                for at in (i*step for i in range(math.ceil(size/step)))]
    if mode == 'scratch':
        knots = [(0,0),(.18,.65),(.38,.12),(.57,.78),(.76,.28),(1,1)]
        return [(a*size,b*size,source+c*size,source+d*size)
                for (a,c),(b,d) in zip(knots,knots[1:])]
    knots = [(i/32, (i/32)**2/2 if mode=='start' else i/32-(i/32)**2/2) for i in range(33)]
    return [(a*size,b*size,source+c*size,source+d*size)
            for (a,c),(b,d) in zip(knots,knots[1:])]


def validate(project, source_duration, baseline):
    from .editorial import validate_lyrics
    from .mastering import validate_effects
    from .service import mix_recipe
    if not isinstance(project, dict) or set(project)-{'version','segments','sounds','words','approved','mix','artwork_id','language','lighting','stem_task_id'}:
        raise ValueError('Invalid song project')
    if project.get('version') != 1:
        raise ValueError('Unsupported song project version')
    segments = project.get('segments')
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise ValueError('Use 1 to 128 music sections')
    result = {'version': 1, 'segments': [], 'sounds': []}
    ids = set()
    for segment in segments:
        if not isinstance(segment, dict) or set(segment)-{'id','kind','start','end','duration','mode','gain_db','effects','effect_offset','label'}:
            raise ValueError('Invalid music section')
        sid = identity(segment.get('id'))
        if sid in ids: raise ValueError('Timeline object IDs must be unique')
        ids.add(sid)
        kind = segment.get('kind')
        row = {'id':sid,'kind':kind,'label':str(segment.get('label',''))[:160]}
        if kind == 'gap':
            row['duration'] = number(segment.get('duration'), .01, 120, 'break duration')
        elif kind == 'audio':
            start = number(segment.get('start'),0,source_duration,'music start')
            end = number(segment.get('end'),start,source_duration,'music end')
            if end-start < .01: raise ValueError('Music sections must be at least 0.01 seconds')
            mode = segment.get('mode','normal')
            if mode not in MODES: raise ValueError('Unknown music gesture')
            if mode in {'scratch','stop','start','stutter'} and end-start > 30:
                raise ValueError('Select up to 30 seconds for a record gesture')
            row.update(start=start,end=end,mode=mode,
                       gain_db=number(segment.get('gain_db',0),-60,12,'section gain'),
                       effect_offset=number(segment.get('effect_offset',0),0,7200,'effect time offset'),
                       effects=validate_effects(segment.get('effects',[])))
        else: raise ValueError('Unknown music section kind')
        result['segments'].append(row)
    total = duration(result)
    if total > 7200: raise ValueError('Keep a song project under two hours')
    sounds = project.get('sounds', [])
    if not isinstance(sounds,list) or len(sounds)>MAX_SOUNDS:
        raise ValueError('Use up to 128 sound clips per song')
    by_id = {s['id']:s for s in result['segments']}
    for clip in sounds:
        if not isinstance(clip,dict) or set(clip)-{'id','clip_id','segment_id','at','offset','trim','duration','gain_db','fade_in','fade_out','duck_db','pan','label'}:
            raise ValueError('Invalid sound clip')
        cid=identity(clip.get('id'))
        if cid in ids: raise ValueError('Timeline object IDs must be unique')
        ids.add(cid)
        anchor=clip.get('segment_id')
        if anchor is not None and anchor not in by_id: raise ValueError('Sound clip anchor is unavailable')
        size=number(clip.get('duration'),.01,120,'sound duration')
        row={'id':cid,'clip_id':identity(clip.get('clip_id')),'segment_id':anchor,
             'at':number(clip.get('at',0),0,total,'sound position'),
             'offset':number(clip.get('offset',0),0,length(by_id[anchor]) if anchor else total,'sound offset'),
             'trim':number(clip.get('trim',0),0,120,'sound trim'),'duration':size,
             'gain_db':number(clip.get('gain_db',-6),-60,12,'sound gain'),
             'duck_db':number(clip.get('duck_db',0),-36,0,'music ducking'),
             'pan':number(clip.get('pan',0),-1,1,'sound pan'),
             'label':str(clip.get('label',''))[:160]}
        for key in ('fade_in','fade_out'):
            row[key]=number(clip.get(key,min(.02,size/4)),0,size,'sound fade')
        if row['fade_in']+row['fade_out']>size: raise ValueError('Sound fades must fit inside the clip')
        result['sounds'].append(row)
    result['words']=validate_lyrics({'words':project.get('words')},baseline,source_duration)
    if type(project.get('approved',False)) is not bool: raise ValueError('Invalid lyric approval')
    mix=deepcopy(project.get('mix',{}))
    if not isinstance(mix,dict):raise ValueError('Invalid project mix')
    if 'version' in mix and mix.pop('version') not in (1,2):raise ValueError('Unknown project mix version')
    from _gbr_lyrics import language_value
    result.update(approved=project.get('approved',False),mix=mix_recipe(mix),
                  artwork_id=project.get('artwork_id'),language=language_value(project.get('language')))
    for effect in result['mix'].get('effects',[]):
        if effect.get('range',{}).get('end',0)>total or any(p['time']>total for p in effect.get('automation',[])):
            raise ValueError('Mastering effects must fit inside the song timeline')
    result['stem_task_id']=identity(project['stem_task_id']) if project.get('stem_task_id') else None
    if (result['mix']['vocal_db'] or result['mix']['accompaniment_db']) and not result['stem_task_id']:
        raise ValueError('Run lyric alignment and enable the separated stems to use vocal/accompaniment faders')
    light=project.get('lighting') or {}
    if not isinstance(light,dict) or set(light)-{'sensitivity','cues'}:raise ValueError('Invalid project lighting')
    result['lighting']={'sensitivity':number(light.get('sensitivity',1),.25,3,'lighting sensitivity'),'cues':[]}
    cues=light.get('cues',[])
    if not isinstance(cues,list) or len(cues)>200:raise ValueError('Use up to 200 lighting cues')
    for cue in cues:
        if not isinstance(cue,dict) or set(cue)-{'segment_id','at','offset','duration','boost','label'}:
            raise ValueError('Invalid light cue')
        anchor=cue.get('segment_id')
        if anchor is not None and anchor not in by_id:raise ValueError('Light cue anchor is unavailable')
        result['lighting']['cues'].append({'segment_id':anchor,'at':number(cue.get('at',0),0,total,'light cue'),
            'offset':number(cue.get('offset',0),0,total,'light offset'),'duration':number(cue.get('duration',2),.01,120,'light duration'),
            'boost':number(cue.get('boost',1),0,3,'light boost'),'label':str(cue.get('label',''))[:160]})
    return result


def sound_positions(project):
    offsets={s['id']:s['at'] for s in spans(project)}
    return [{**clip,'position':offsets[clip['segment_id']]+clip['offset'] if clip.get('segment_id') else clip['at']}
            for clip in project['sounds']]


def projected_words(project):
    """Project original words through cuts, gaps, repeats and record gestures."""
    result=[];groups={};group=0;previous_end=None
    for segment in spans(project):
        if segment['kind']=='gap':continue
        if segment.get('mode')!='normal' or previous_end is None or abs(segment['start']-previous_end)>.00001:
            group+=1
        previous_end=segment['end'] if segment.get('mode')=='normal' else None
        if segment.get('mode')=='mute':continue
        pieces=warp(segment)
        occurrence=0;last_right=None;last_direction=None
        for piece_index,(at,end,left,right) in enumerate(pieces):
            direction=right>left
            if last_right is None or abs(left-last_right)>.00001 or direction!=last_direction:occurrence+=1
            last_right,last_direction=right,direction
            for word in project['words']:
                if word.get('omitted') or word.get('start') is None:continue
                low,high=max(min(left,right),word['start']),min(max(left,right),word['end'])
                if high<=low:continue
                a=at+(low-left)/(right-left)*(end-at)
                b=at+(high-left)/(right-left)*(end-at)
                a,b=segment['at']+min(a,b),segment['at']+max(a,b)
                normal=segment.get('mode')=='normal'
                # Continuous normal sections share a word across an inserted gap.
                key=(group,word['id']) if normal else (segment['id'],occurrence,word['id'])
                if key in groups:
                    current=groups[key];current['start']=min(current['start'],a);current['end']=max(current['end'],b)
                else:
                    current={**deepcopy(word),'id':str(word['id'])+'-'+str(len(result)),
                             'source_id':word['id'],'segment_id':segment['id'],
                             'start':a,'end':b,'line':f'{group}:{word["line"]}' if normal else f'{segment["id"]}:{occurrence}:{word["line"]}'}
                    groups[key]=current;result.append(current)
    return sorted(result,key=lambda word:(word['start'],word['end']))


def raw_lyrics(words):
    lines={}
    for word in words:
        if not word.get('omitted'):lines.setdefault(word['line'],[]).append(word['text'])
    return '\n'.join(' '.join(line) for line in lines.values())
