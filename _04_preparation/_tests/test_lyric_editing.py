"""Lyric editing, language boundaries and correction-led alignment contracts."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import pytest
from test_preparation import prepared,promote,ROOT
from test_song_projects import save
from _gbr_preparation.editorial import lyric_words,position_lyrics,validate_lyrics
from _gbr_lyrics import language_value,model_lyrics


def javascript(script,value=None):
    source=ROOT/'_04_preparation/_static/lyric-tools.js'
    loader="import fs from 'node:fs';const m=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(process.argv[1],'utf8')).toString('base64'));const input=JSON.parse(fs.readFileSync(0,'utf8'));"
    return json.loads(subprocess.check_output(['node','--input-type=module','-e',loader+script,str(source)],input=json.dumps(value).encode()))


def test_group_nudge_resize_and_pack_keep_unselected_words_and_source_order():
    words=[{'id':str(i),'text':text,'start':start,'end':end} for i,(text,start,end) in enumerate([('built',1,1.5),('this',2.5,3.2),('house',4,4.5)])]
    result=javascript("const p={words:input};m.nudgeWords(p,new Set(['0','1']),-10,8);const shifted=structuredClone(p.words);m.resizeWords(p,new Set(['0','1']),.7,8);m.packWords(p,new Set(['0','1']),8);console.log(JSON.stringify({shifted,packed:p.words}));",words)
    assert [w['start'] for w in result['shifted']]==[0,1.5,4]
    assert result['packed'][0]['end']==pytest.approx(.7)
    assert result['packed'][1]['start']==pytest.approx(.7) and result['packed'][1]['end']==pytest.approx(1.4)
    assert result['packed'][2]==words[2]
    assert all(w['confirmed'] for w in result['packed'][:2])


@pytest.mark.parametrize('time,selected',[(.3,'a'),(1.8,'a'),(2.1,'a'),(2.21,'b'),(3.1,'b'),(4.5,'b')])
def test_space_uses_the_confirmed_twenty_percent_reaction_rule(time,selected):
    result=javascript("console.log(JSON.stringify(m.pauseWord(input.words,input.time).id));",{'time':time,'words':[{'id':'a','start':.1,'end':1},{'id':'b','start':2,'end':3}]})
    assert result==selected


def test_follow_keeps_a_zoomed_playhead_in_view_and_respects_the_song_end():
    result=javascript("console.log(JSON.stringify([m.followStart(5,0,1,8),m.followStart(8,0,1,8),m.followStart(.5,0,1,8),m.followStart(5,0,8,8)]));")
    assert result==pytest.approx([4.82,7,0,0])


def test_parts_hold_the_middle_of_a_word_without_changing_its_spelling():
    result=javascript("console.log(JSON.stringify(m.wordParts('built','b|uil|t',[1,8,1])));")
    assert ''.join(p['text'] for p in result)=='built'
    assert [p['start'] for p in result]==pytest.approx([0,.1,.9])
    assert [p['end'] for p in result]==pytest.approx([.1,.9,1])


def test_language_markers_are_metadata_never_generation_tokens():
    text='[verse]\n{en} built this\n{ro} casa mea\n{en} again'
    assert language_value('en, ro',text)=='en, ro'
    assert model_lyrics(text)=='[verse]\nbuilt this\ncasa mea\nagain'
    words=lyric_words(text,'en, ro')
    assert [w['text'] for w in words]==['built','this','casa','mea','again']
    assert [w['language'] for w in words]==['en','en','ro','ro','en']
    assert all('{' not in w['text'] for w in words)
    untouched='[verse]\r\n  keep exactly this spacing  \r\n\r\nnext line\r\n'
    assert model_lyrics(untouched)==untouched


def test_manual_words_and_emphasis_survive_saved_versions_and_reprojection(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    loaded=prep.song_projects.get(gid);project=loaded['project'];originals=[w['id'] for w in project['words']]
    project['words'][0].update(text='built',start=.1,end=.8,confirmed=True,parts=[
        {'text':'b','start':0,'end':.1},{'text':'uil','start':.1,'end':.9},{'text':'t','start':.9,'end':1}])
    project['words'].insert(1,{'id':'manual_'+'a'*32,'text':'this','start':.85,'end':1.1,'line':0,'language':'en','confirmed':True})
    saved=save(prep,gid,loaded);again=prep.song_projects.get(gid)
    assert again['project']==saved['project']
    assert [w['id'] for w in again['project']['words'] if not w.get('manual')]==originals
    from _gbr_preparation.project_model import projected_words
    assert projected_words(saved['project'])[0]['parts'][1]['text']=='uil'
    assert next(w for w in projected_words(saved['project']) if w['text']=='this')['manual']


@pytest.mark.parametrize('parts',[
    [{'text':'built','start':.1,'end':1}],
    [{'text':'b','start':0,'end':.8},{'text':'uilt','start':.6,'end':1}],
    [{'text':'different','start':0,'end':1}],
    [{'text':'built','start':0,'end':float('nan')}],
])
def test_invalid_emphasis_cannot_be_saved(parts):
    baseline=position_lyrics(lyric_words('built'),4);incoming=deepcopy(baseline);incoming[0]['parts']=parts
    with pytest.raises(ValueError):validate_lyrics({'words':incoming},baseline,4)


def test_refinement_request_freezes_the_saved_manual_lyrics_and_anchors(prepared,monkeypatch):
    _,prep,_,_,gid,_=prepared;promote(prepared);prep.remote.pair()
    monkeypatch.setattr(prep,'local_generation_busy',lambda:True)
    loaded=prep.song_projects.get(gid);loaded['project']['language']='en, ro'
    loaded['project']['words'][0].update(start=.2,end=.6,confirmed=True,language='ro',text='bună')
    revision=save(prep,gid,loaded)
    task=prep.submit(gid,'lyric_alignment',{'song_project_id':revision['revision_id'],'refine':True,'force':True})
    recipe=json.loads(prep.db.one('SELECT recipe_json FROM processing_tasks WHERE id=?',(task['id'],))['recipe_json'])
    assert recipe['refine'] and recipe['language']=='en, ro'
    assert recipe['words']==revision['project']['words'] and recipe['words'][0]['confirmed']
    assert recipe['project_revision_id']==revision['revision_id']


def refinement_module():
    spec=importlib.util.spec_from_file_location('refinement_test',ROOT/'_99_tools/_workers_processing/alignment_refinement.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_language_passes_use_exact_text_and_preserve_checked_anchor_times():
    module=refinement_module();calls=[];languages=[]
    class Models:
        @staticmethod
        def load_align_model(language_code,device):languages.append(language_code);return object(),{'language':language_code}
        @staticmethod
        def align(segments,model,metadata,speech,device,return_char_alignments):
            segment=segments[0];calls.append(segment);tokens=segment['text'].split();step=(segment['end']-segment['start'])/len(tokens)
            return {'word_segments':[{'word':t,'start':segment['start']+i*step,'end':segment['start']+(i+1)*step,'score':.9} for i,t in enumerate(tokens)]}
    words=lyric_words('{en} fixed\n{ro} bună ziua\n{en} end','en, ro')
    words[0].update(start=.5,end=.9,confirmed=True);words[-1].update(start=3,end=3.4,confirmed=True)
    words[1].update(start=8,end=8.4);words[2].update(start=9,end=9.4)
    result,measurements=module.refine(words,None,12,Models,SimpleNamespace(), 'cpu',lambda *a,**k:None)
    assert languages==['ro'] and calls[0]['text']=='bună ziua'
    assert calls[0]['start']>=.9 and calls[0]['end']<=3
    assert result[0]==words[0] and result[-1]==words[-1]
    assert measurements['refined_words']==2 and measurements['fixed_anchors']==2


def test_more_than_ten_languages_are_not_collapsed_to_one_model():
    module=refinement_module();codes='en ro de fr es it nl pl tr hu sv'.split()
    words=lyric_words('\n'.join('{'+code+'} word' for code in codes),', '.join(codes))
    windows=module.refinement_windows(words,55)
    assert [w['language'] for w in windows]==codes
    assert all(0<=w['start']<w['end']<=55 for w in windows)


def test_non_space_languages_keep_the_supplied_token_and_character_timings():
    module=refinement_module()
    found=[{'word':char,'start':i*.2,'end':i*.2+.1,'score':.9} for i,char in enumerate('你好世界')]
    result=list(module.aligned_tokens(['你好','世界'],found,'zh'))
    assert [i for i,_ in result]==[0,1]
    assert result[0][1]['start']==0 and result[0][1]['end']==pytest.approx(.3)
    assert result[1][1]['start']==pytest.approx(.4) and result[1][1]['end']==pytest.approx(.7)


def test_known_text_refinement_reports_unsupported_languages_without_losing_anchors():
    module=refinement_module()
    class Missing:
        @staticmethod
        def load_align_model(language_code,device):raise ValueError('No default align-model for language: '+language_code)
    words=lyric_words('fixed\n{xx} uncertain')
    words[0].update(start=.2,end=.5,confirmed=True)
    result,measurements=module.refine(words,None,3,Missing,SimpleNamespace(),'cpu',lambda *a,**k:None)
    assert result==words and measurements['refined_words']==0
    assert measurements['warnings'] and 'xx' in measurements['warnings'][0]
