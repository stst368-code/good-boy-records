"""Source-bound light cues and lyric annotations; source YAML is never rewritten."""
import math
import re


def number(value, low, high, name):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError('Invalid '+name)
    return float(value)


def lyric_words(lyrics, language='en'):
    from _gbr_lyrics import MARKER,language_codes
    words=[];section='';line=0;current=language_codes(language,lyrics)[0]
    for text in str(lyrics or '').splitlines():
        if re.fullmatch(r'\s*\[[^\]]+\]\s*',text):section=text.strip()[1:-1];continue
        position=0
        for marker in [*MARKER.finditer(text),None]:
            chunk=text[position:marker.start() if marker else len(text)]
            for token in chunk.split():
                words.append({'id':f'w{len(words):05d}','text':token,'source_text':token,'section':section,'line':line,
                              'language':current,'start':None,'end':None,'confidence':None,'confirmed':False,'omitted':False})
            if marker:current=marker[1].lower();position=marker.end()
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
    if not isinstance(incoming,list) or len(incoming)>20000:raise ValueError('Use up to 20,000 lyric words')
    originals={word['id']:word for word in baseline};supplied=[];seen=set();result=[]
    for word in incoming:
        if not isinstance(word,dict):raise ValueError('Invalid lyric token')
        identifier=word.get('id')
        if not isinstance(identifier,str) or identifier in seen:raise ValueError('Lyric token IDs must be unique')
        seen.add(identifier)
        if identifier in originals:original=originals[identifier];supplied.append(identifier)
        else:
            if not re.fullmatch(r'manual_[a-f0-9]{32}',identifier):raise ValueError('Lyric token IDs or order changed')
            if type(word.get('line')) not in (int,str) or len(str(word['line']))>100:raise ValueError('Invalid lyric line')
            original={'id':identifier,'text':word.get('text'),'source_text':word.get('text'),'section':str(word.get('section',''))[:200],
                      'line':word['line'],'start':None,'end':None,'manual':True,'confirmed':False,'omitted':False}
        text=word.get('text')
        if not isinstance(text,str) or not text.strip() or len(text)>200 or '\n' in text or '\r' in text:raise ValueError('Enter a short lyric token')
        start,end=word.get('start'),word.get('end')
        if (start is None)!=(end is None):raise ValueError('Set both lyric start and end, or leave both untimed')
        if start is not None:
            start=number(start,0,duration,'word start');end=number(end,start,duration,'word end')
            if end<=start:raise ValueError('Word end must follow its start')
        if any(type(word.get(key,original.get(key,False))) is not bool for key in ('confirmed','omitted','estimated')):raise ValueError('Invalid lyric confirmation')
        edited=original.get('edited',False) or text!=original['text'] or start!=original['start'] or end!=original['end']
        from _gbr_lyrics import language_codes
        language=language_codes(word.get('language') or original.get('language') or 'en')
        if len(language)!=1:raise ValueError('Choose one language per word')
        current={**original,'edited':edited,'text':text,'start':start,'end':end,'language':language[0],
                 'confirmed':word.get('confirmed',False),'omitted':word.get('omitted',False),
                 'estimated':word.get('estimated',original.get('estimated',False))}
        parts=word.get('parts',original.get('parts'))
        if parts is not None:
            if not isinstance(parts,list) or not 1<=len(parts)<=100:raise ValueError('Use 1 to 100 highlight parts')
            clean=[];previous=0.
            for part in parts:
                if not isinstance(part,dict) or set(part)-{'text','start','end'}:raise ValueError('Invalid highlight part')
                label=part.get('text')
                if not isinstance(label,str) or not label:raise ValueError('Highlight parts need text')
                left=number(part.get('start'),0,1,'highlight start');right=number(part.get('end'),left,1,'highlight end')
                if abs(left-previous)>.00001 or right<=left:raise ValueError('Highlight parts must run end to end')
                clean.append({'text':label,'start':left,'end':right});previous=right
            if abs(previous-1)>.00001 or ''.join(p['text'] for p in clean)!=text:raise ValueError('Highlight parts must spell the word and cover its full duration')
            current['parts']=clean
        elif 'parts' in current:current.pop('parts')
        result.append(current)
    if supplied!=[word['id'] for word in baseline]:
        raise ValueError('Keep every supplied lyric token in order; mark a missing word omitted instead of deleting it')
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
