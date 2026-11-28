"""Bounded, language-specific forced alignment around human timing anchors."""
from copy import deepcopy
import difflib
import gc
import math


def normalized(text):
    return ''.join(c for c in text.casefold() if c.isalnum())


def aligned_tokens(expected,found,language):
    if language not in {'ja','zh'}:
        recognized=[normalized(w.get('word','')) for w in found]
        for block in difflib.SequenceMatcher(None,expected,recognized,autojunk=False).get_matching_blocks():
            for offset in range(block.size):yield block.a+offset,found[block.b+offset]
        return
    # WhisperX emits individual characters for Japanese/Chinese. GBR retains
    # the supplied token, so aggregate those character times back into it.
    transcript='';owners=[]
    for index,word in enumerate(found):
        text=normalized(word.get('word',''));transcript+=text;owners.extend([index]*len(text))
    wanted=''.join(expected);matched={}
    for block in difflib.SequenceMatcher(None,wanted,transcript,autojunk=False).get_matching_blocks():
        matched.update({block.a+i:block.b+i for i in range(block.size)})
    at=0
    for index,text in enumerate(expected):
        positions=[matched.get(i) for i in range(at,at+len(text))];at+=len(text)
        if not positions or any(i is None for i in positions):continue
        if positions!=list(range(positions[0],positions[0]+len(positions))):continue
        items=[found[i] for i in dict.fromkeys(owners[p] for p in positions)]
        timed=[w for w in items if w.get('start') is not None and w.get('end') is not None]
        if len(timed)!=len(items):continue
        scores=[float(w['score']) for w in timed if w.get('score') is not None]
        yield index,{'start':min(w['start'] for w in timed),'end':max(w['end'] for w in timed),
                     'score':sum(scores)/len(scores) if scores else None}


def estimated_positions(words,duration,reset_between_anchors=False):
    result=deepcopy(words);active=[w for w in result if not w.get('omitted')];index=0
    if reset_between_anchors:
        anchors=[i for i,w in enumerate(active) if w.get('confirmed') and w.get('start') is not None]
        for left,right in zip(anchors,anchors[1:]):
            if active[right]['start']<=active[left]['end']:continue
            for word in active[left+1:right]:
                # The old automatic positions can be far outside a corrected
                # section. Re-estimate this interval from the human anchors,
                # rather than keeping the old clock as a hidden constraint.
                word.update(start=None,end=None)
    while index<len(active):
        if active[index].get('start') is not None:index+=1;continue
        first=index
        while index<len(active) and active[index].get('start') is None:index+=1
        left=active[first-1]['end'] if first else 0
        right=active[index]['start'] if index<len(active) else duration
        if right<=left:left=max(0,min(left,duration-.02));right=min(duration,left+max(.02,(index-first)*.2))
        step=(right-left)/(index-first)
        for offset,word in enumerate(active[first:index]):
            word.update(start=left+offset*step,end=min(duration,left+(offset+.9)*step),estimated=True)
    return result


def refinement_windows(words,duration,default='en',only_missing=False):
    placed=estimated_positions(words,duration,reset_between_anchors=not only_missing);groups=[];current=[]
    def anchor(word):
        return word.get('confirmed') or (only_missing and not word.get('estimated') and word.get('start') is not None
            and (word.get('language') or default).split('-')[0]==default and (word.get('confidence') or 0)>=.5)
    for index,word in enumerate(placed):
        if word.get('omitted') or anchor(word):
            if current:groups.append(current);current=[]
            continue
        language=(word.get('language') or default).split('-')[0]
        if current:
            first=placed[current[0]]
            if language!=(first.get('language') or default).split('-')[0] or word.get('line')!=first.get('line') or len(current)>=24 or word['end']-first['start']>24:
                groups.append(current);current=[]
        current.append(index)
    if current:groups.append(current)
    result=[]
    for indexes in groups:
        first,last=indexes[0],indexes[-1]
        previous=next((w for w in reversed(placed[:first]) if anchor(w) and not w.get('omitted') and w.get('end') is not None),None)
        following=next((w for w in placed[last+1:] if anchor(w) and not w.get('omitted') and w.get('start') is not None),None)
        left=max(previous['end'] if previous else 0,placed[first]['start']-.35)
        right=min(following['start'] if following else duration,placed[last]['end']+.35)
        if right-left<.02:continue
        result.append({'indexes':indexes,'language':(placed[first].get('language') or default).split('-')[0],
                       'start':left,'end':right,'text':' '.join(placed[i]['text'] for i in indexes)})
    return result


def refine(words,speech,duration,whisperx,torch,device,notify,default='en',only_missing=False):
    result=deepcopy(words);windows=refinement_windows(words,duration,default,only_missing);warnings=[];improved=0
    languages=list(dict.fromkeys(window['language'] for window in windows))
    for language in languages:
        notify('refinement_model','Loading '+language+' alignment model',language=language)
        try:model,metadata=whisperx.load_align_model(language_code=language,device=device)
        except ValueError as error:
            warnings.append(language+': '+str(error));continue
        try:
            targets=[window for window in windows if window['language']==language]
            for index,window in enumerate(targets):
                notify('refinement','Aligning supplied '+language+' lyrics · '+str(index+1)+' / '+str(len(targets)),
                       language=language,completed_windows=index,total_windows=len(targets))
                aligned=whisperx.align([{'text':window['text'],'start':window['start'],'end':window['end']}],
                    model,metadata,speech,device,return_char_alignments=False)
                found=aligned.get('word_segments',[])
                expected=[normalized(words[i]['text']) for i in window['indexes']]
                for position,value in aligned_tokens(expected,found,language):
                    identifier=window['indexes'][position];start,end=value.get('start'),value.get('end');score=value.get('score')
                    if score is not None and (not math.isfinite(float(score)) or float(score)<.15):continue
                    if start is None or end is None:continue
                    if not all(math.isfinite(float(t)) for t in (start,end)):continue
                    start=max(window['start'],float(start));end=min(window['end'],float(end))
                    if end<=start:continue
                    result[identifier].update(start=start,end=end,confidence=min(1,float(score)) if score is not None else None,estimated=False)
                    improved+=1
        finally:
            del model;gc.collect()
            if device=='cuda':torch.cuda.empty_cache()
    return result,{'refined_words':improved,'fixed_anchors':sum(bool(w.get('confirmed')) for w in words),
                  'languages':languages,'warnings':warnings,'windows':len(windows)}
