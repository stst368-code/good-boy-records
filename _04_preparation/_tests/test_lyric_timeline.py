"""Approximate placement preserves supplied lyrics and existing alignment anchors."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'_04_preparation'))
from _gbr_preparation.editorial import lyric_words,position_lyrics,validate_lyrics


def test_missing_words_fit_before_between_and_after_known_neighbours():
    words=lyric_words('[verse]\none two three four five six seven')
    words[1].update(start=3,end=4,confidence=.92,confirmed=True)
    words[4].update(start=7,end=8,confidence=.82)
    original=deepcopy(words);placed=position_lyrics(words,12)
    assert words==original
    for index in [1,4]:assert {key:placed[index][key] for key in words[index]}==words[index]
    assert 0<=placed[0]['start']<placed[0]['end']<=3
    assert 4<=placed[2]['start']<placed[2]['end']<=placed[3]['start']<placed[3]['end']<=7
    assert 8<=placed[5]['start']<placed[5]['end']<=placed[6]['start']<placed[6]['end']<=12
    for index in [0,2,3,5,6]:assert placed[index]['estimated'] and not placed[index]['confirmed'] and placed[index]['confidence'] is None
    assert position_lyrics(placed,12)==placed


@pytest.mark.parametrize('duration',[.01,4,360])
def test_all_missing_words_are_drag_ready_in_source_order(duration):
    words=lyric_words('[intro]\nhello песня\n[chorus]\nsing the song')
    placed=position_lyrics(words,duration)
    assert [w['id'] for w in placed]==[w['id'] for w in words]
    assert [w['text'] for w in placed]==[w['text'] for w in words]
    assert [w['line'] for w in placed]==[w['line'] for w in words]
    assert all(0<=w['start']<w['end']<=duration for w in placed)
    assert all(a['end']<=b['start'] for a,b in zip(placed,placed[1:]))
    assert all(w['estimated'] and not w['confirmed'] for w in placed)


def test_omitted_words_and_overlapping_anchors_are_preserved():
    words=lyric_words('before missing omitted after')
    words[0].update(start=1,end=2)
    words[2]['omitted']=True
    words[3].update(start=1.5,end=2.5)
    placed=position_lyrics(words,3)
    assert placed[0]['start']==1 and placed[0]['end']==2
    assert placed[3]['start']==1.5 and placed[3]['end']==2.5
    assert placed[2]['omitted'] and placed[2]['start'] is None
    assert 0<=placed[1]['start']<placed[1]['end']<=3 and placed[1]['estimated']


def test_estimation_does_not_relax_timestamp_or_token_validation():
    baseline=position_lyrics(lyric_words('one two'),4)
    for key,value in [('start',float('nan')),('end',5),('id','different'),('estimated','yes')]:
        incoming=deepcopy(baseline);incoming[0][key]=value
        with pytest.raises(ValueError):validate_lyrics({'words':incoming},baseline,4)
    assert all(w['estimated'] for w in validate_lyrics({'words':baseline},baseline,4))


@pytest.mark.parametrize('mode,delta,expected',[
    ('move',-20,{'start':0,'end':1}),
    ('move',20,{'start':3,'end':4}),
    ('move',.25,{'start':1.25,'end':2.25}),
    ('start',-20,{'start':0,'end':2}),
    ('start',20,{'start':1.98,'end':2}),
    ('end',-20,{'start':1,'end':1.02}),
    ('end',20,{'start':1,'end':4}),
])
def test_drag_and_keyboard_timing_respect_audio_boundaries(mode,delta,expected):
    script="import fs from 'node:fs';const src=fs.readFileSync(process.argv[1],'utf8');const m=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));const d=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(m.shiftWordTiming(d.word,d.mode,d.delta,d.duration)));"
    payload={'word':{'start':1,'end':2},'mode':mode,'delta':delta,'duration':4}
    result=json.loads(subprocess.check_output(['node','--input-type=module','-e',script,str(ROOT/'_04_preparation/_static/timeline.js')],input=json.dumps(payload).encode()))
    assert result==pytest.approx(expected)
