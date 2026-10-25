"""Source-bound light cues and lyric annotations; source YAML is never rewritten."""
import math
import re


def number(value, low, high, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError('Invalid '+name)
    return float(value)


def lyric_words(lyrics):
    words=[];section='';line=0
    for text in str(lyrics or '').splitlines():
        if re.fullmatch(r'\s*\[[^\]]+\]\s*',text):section=text.strip()[1:-1];continue
        for token in text.split():
            words.append({'id':f'w{len(words):05d}','text':token,'source_text':token,'section':section,'line':line,
                          'start':None,'end':None,'confidence':None,'confirmed':False,'omitted':False})
        line+=1
    return words


def position_lyrics(words,duration):
    """Place missing timings between their neighbours, without changing anchors.

    These are editable estimates, not recognition results or confirmations.
    Reading an old sidecar never rewrites it; estimates persist on explicit save.
    """
    result=[{**word,'estimated':word.get('estimated',False)} for word in words]
    active=[word for word in result if not word.get('omitted')]
    if duration<=0:return result
    index=0
    while index<len(active):
        if active[index].get('start') is not None and active[index].get('end') is not None:
            index+=1;continue
        first=index
        while index<len(active) and (active[index].get('start') is None or active[index].get('end') is None):index+=1
        previous=active[first-1] if first else None
        following=active[index] if index<len(active) else None
        left=previous['end'] if previous else 0
        right=following['start'] if following else duration
        if right<=left:
            # Recognition anchors can overlap. Keep them intact and put the
            # missing words nearby so they remain visible and draggable.
            centre=max(0,min(duration,(left+right)/2))
            span=min(duration,max(.1,(index-first)*.2))
            left=max(0,min(duration-span,centre-span/2));right=left+span
        step=(right-left)/(index-first)
        for offset,word in enumerate(active[first:index]):
            start=left+offset*step
            word.update(start=start,end=min(duration,start+step*.9),estimated=True,confirmed=False)
    return result


def validate_lyrics(data, baseline, duration):
    incoming=data.get('words')
    if not isinstance(incoming,list) or len(incoming)!=len(baseline):raise ValueError('Keep every supplied lyric token; mark a missing word omitted instead of deleting it')
    result=[]
    for word,original in zip(incoming,baseline):
        if not isinstance(word,dict):raise ValueError('Invalid lyric token')
        if word.get('id')!=original['id']:raise ValueError('Lyric token IDs or order changed')
        text=word.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>200 or '\n' in text or '\r' in text:raise ValueError('Enter a short lyric token')
        start,end=word.get('start'),word.get('end')
        if (start is None)!=(end is None):raise ValueError('Set both lyric start and end, or leave both untimed')
        if start is not None:
            start=number(start,0,duration,'word start');end=number(end,start,duration,'word end')
            if end<=start:raise ValueError('Word end must follow its start')
        if any(type(word.get(key,original.get(key,False))) is not bool for key in ('confirmed','omitted','estimated')):raise ValueError('Invalid lyric confirmation')
        edited=original.get('edited',False) or text!=original['text'] or start!=original['start'] or end!=original['end']
        result.append({**original,'edited':edited,'text':text,'start':start,'end':end,'confirmed':word.get('confirmed',False),'omitted':word.get('omitted',False),'estimated':word.get('estimated',original.get('estimated',False))})
    return result


def lighting(data, features):
    sensitivity=number(data.get('sensitivity',1),.25,3,'light sensitivity')
    duration=features['duration'];sections=[]
    if not isinstance(data.get('sections',[]),list):raise ValueError('Sections must be a list')
    for section in data.get('sections',[]):
        if not isinstance(section,dict):raise ValueError('Invalid light section')
        start=number(section.get('start'),0,duration,'section start');end=number(section.get('end'),start,duration,'section end')
        if end<=start:raise ValueError('Section end must follow its start')
        label=section.get('label','')
        if not isinstance(label,str) or len(label)>200:raise ValueError('Invalid section label')
        sections.append({'start':start,'end':end,'boost':number(section.get('boost',1),0,3,'section boost'),'label':label})
    if len(sections)>200:raise ValueError('At most 200 light sections per recording')
    events=[];previous=0.;last=-10.
    for frame in features['frames']:
        time=frame['t'];energy=frame['energy'];boost=max([s['boost'] for s in sections if s['start']<=time<s['end']]+[0])
        if time-last>=.15 and (energy*sensitivity>.65 and (energy-previous)*sensitivity>.1 or boost>0 and energy>.1 and time-last>=.45):
            events.append({'time':time,'strength':min(1,energy*sensitivity*(1+boost)),'duration':.25+min(boost,2)*.15});last=time
        previous=energy
    return {'format':'gbr-playback-v1','source_sha256':features['source_sha256'],'duration':duration,
            'sensitivity':sensitivity,'sections':sections,'dance':{'events':events,'algorithm':'gbr-sensitive-rise-v2'}}
