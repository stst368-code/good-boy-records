from copy import deepcopy
import io
import json
import numpy as np
import pytest
import soundfile as sf
from test_preparation import prepared,promote,wait,ROOT
from test_publishing import publication,prepare
from _gbr_preparation.project_model import projected_words,validate,warp
from _gbr_preparation.service import digest


def save(prep,gid,loaded):
    return prep.song_projects.save(gid,{'parent_revision_id':loaded['revision_id'],
        'source_sha256':loaded['source_sha256'],'input_artifact_id':loaded['source_artifact_id'],
        'project':loaded['project']})


def test_gap_repeat_sample_mix_and_lyrics_use_one_clock(prepared):
    _,prep,_,_,gid,_=prepared;source=promote(prepared);original_hash=digest(source)
    loaded=prep.song_projects.get(gid);document=loaded['project']
    document['mix']['limiter_db']=None
    document['segments']=[{'id':'a','kind':'audio','start':0,'end':1,'mode':'normal'},
        {'id':'break','kind':'gap','duration':1},
        {'id':'b','kind':'audio','start':1,'end':4,'mode':'normal'},
        {'id':'repeat','kind':'audio','start':0,'end':1,'mode':'normal'}]
    for word,start,end in zip(document['words'],[.1,.6,1.5,2.5],[.4,.9,2,3]):
        word.update(start=start,end=end)
    document['approved']=True
    # A quiet synthetic user clip placed inside the music break.
    raw=io.BytesIO();sf.write(raw,np.ones((4000,2),dtype='float32')*.08,16000,format='WAV',subtype='PCM_24')
    clip=prep.sounds.import_audio(raw.getvalue(),'My test stab')
    document['sounds']=[{'id':'sfx','clip_id':clip['id'],'segment_id':'break','offset':.2,
        'duration':.2,'gain_db':0,'fade_in':0,'fade_out':0}]
    revision=save(prep,gid,loaded)
    assert revision['duration']==6
    master=prep.submit(gid,'mix_master',{'song_project_id':revision['revision_id']})
    wait(prep,master['id'])
    data,rate=sf.read(prep.artifact(master['id']+'-output'));original,_=sf.read(source)
    assert len(data)==6*rate
    np.testing.assert_allclose(data[:rate],original[:rate],atol=2/2**23)
    assert np.max(abs(data[rate:int(rate*1.19)]))<2/2**23
    assert np.mean(data[int(rate*1.22):int(rate*1.38)])==pytest.approx(.08,abs=.001)
    np.testing.assert_allclose(data[2*rate:5*rate],original[rate:],atol=2/2**23)
    np.testing.assert_allclose(data[5*rate:],original[:rate],atol=2/2**23)
    preview=prep.submit(gid,'mix_preview',{'song_project_id':revision['revision_id'],'start':.5,'seconds':3})
    wait(prep,preview['id']);excerpt,_=sf.read(prep.artifact(preview['id']+'-output'))
    np.testing.assert_allclose(excerpt,data[rate//2:rate//2+3*rate],atol=2/2**23)
    editorial=prep.editorial(gid,master['id']+'-output')
    assert editorial['duration']==6 and editorial['approved']
    assert editorial['song_project_id']==revision['revision_id']
    assert [w['start'] for w in editorial['words']]==pytest.approx([.1,.6,2.5,3.5,5.1,5.6])
    assert editorial['raw_lyrics']=='first line\nsecond line\nfirst line'
    assert editorial['features']['duration']==6
    assert editorial['features']['source_sha256']==digest(prep.artifact(master['id']+'-output'))
    prep.song_projects.assert_release(gid,master['id']+'-output',None)
    assert digest(source)==original_hash
    assert 'project' not in next(t['recipe'] for t in prep.tasks(gid) if t['kind']=='song_project')
    assert not list(prep.root.rglob('.gbr-processing-*'))


def test_versions_conflicts_durable_samples_and_stale_master_guard(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    loaded=prep.song_projects.get(gid);saved=save(prep,gid,loaded)
    assert save(prep,gid,saved)['revision_id']==saved['revision_id']
    with pytest.raises(ValueError,match='saved elsewhere'):save(prep,gid,loaded)
    job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    saved['project']['segments'].append({'id':'g','kind':'gap','duration':.5})
    newer=save(prep,gid,saved)
    with pytest.raises(ValueError,match='latest saved'):prep.song_projects.assert_release(gid,job['id']+'-output',None)
    assert len(newer['versions'])==2
    assert len(prep.song_projects.get(gid,saved['revision_id'])['project']['segments'])==1


def test_sound_import_bounds_rename_and_owned_copy(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    library=prep.sounds.list()
    assert len(library['sounds'])==6
    assert len(prep.sounds.list()['sounds'])==6
    clip=library['sounds'][0]
    prep.sounds.rename(clip['id'],'New name','Custom')
    assert prep.sounds.item(clip['id'])['name']=='New name'
    frozen=prep.sounds.freeze(gid,[clip['id']])
    assert frozen[clip['id']]['path']!=str(prep.sounds.audio(clip['id']))
    with pytest.raises(ValueError,match='Cannot decode'):prep.sounds.import_audio(b'not audio','bad')
    with pytest.raises(ValueError,match='32 MiB'):prep.sounds.import_audio(b'x'*(32*1024**2+1),'huge')
    loaded=prep.song_projects.get(gid)
    loaded['project']['sounds']=[{'id':'bad','clip_id':clip['id'],'duration':120,'trim':1}]
    with pytest.raises(ValueError,match='exceeds'):save(prep,gid,loaded)


@pytest.mark.parametrize('mode',['reverse','scratch','stutter','stop','start','mute'])
def test_record_gestures_change_actual_music_and_keep_duration(prepared,mode):
    _,prep,_,_,gid,_=prepared;source=promote(prepared)
    loaded=prep.song_projects.get(gid);loaded['project']['segments'][0]['mode']=mode
    loaded['project']['mix']['limiter_db']=None
    saved=save(prep,gid,loaded)
    job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    actual,rate=sf.read(prep.artifact(job['id']+'-output'));original,_=sf.read(source)
    assert len(actual)==len(original)
    if mode=='mute':assert np.max(abs(actual))==0
    else:assert np.max(abs(actual-original))>.05
    if mode=='reverse':
        np.testing.assert_allclose(actual[rate:2*rate],original[::-1][rate:2*rate],atol=2/2**23)


def test_stop_curve_does_not_duplicate_a_word_at_each_interpolation_piece(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    loaded=prep.song_projects.get(gid);doc=loaded['project']
    doc['segments'][0]['mode']='stop'
    for word in doc['words']:word.update(start=None,end=None)
    doc['words'][0].update(start=.1,end=1.5)
    assert len(projected_words(doc))==1
    invalid=deepcopy(doc);invalid['segments'][0]['start']=-1
    with pytest.raises(ValueError):validate(invalid,4,doc['words'])
    invalid=deepcopy(doc);invalid['lighting']['cues']=[4]
    with pytest.raises(ValueError):validate(invalid,4,doc['words'])


def test_record_start_and_stop_actually_ramp_the_music_pitch_in_opposite_directions(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    loaded=prep.song_projects.get(gid);loaded['project']['mix']['limiter_db']=None
    def dominant(audio,rate,left,right):
        excerpt=audio[int(left*rate):int(right*rate)].mean(axis=1)
        power=abs(np.fft.rfft(excerpt*np.hanning(len(excerpt))))
        return np.fft.rfftfreq(len(excerpt),1/rate)[np.argmax(power[1:])+1]
    for mode in ['start','stop']:
        loaded['project']['segments'][0]['mode']=mode
        loaded=save(prep,gid,loaded)
        job=prep.submit(gid,'mix_master',{'song_project_id':loaded['revision_id']});wait(prep,job['id'])
        audio,rate=sf.read(prep.artifact(job['id']+'-output'),always_2d=True)
        early=dominant(audio,rate,.2,.6);late=dominant(audio,rate,3.4,3.8)
        if mode=='start':assert late>early*3
        else:assert early>late*3


def test_unchanged_audio_keeps_source_bound_lyrics_and_saved_samples_can_recover(prepared):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    loaded=prep.song_projects.get(gid);loaded['project']['approved']=True
    loaded['project']['mix']['limiter_db']=None
    saved=save(prep,gid,loaded)
    job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    editorial=prep.editorial(gid,job['id']+'-output')
    assert editorial['approved'] and editorial['song_project_id']==saved['revision_id']
    clip=prep.sounds.list()['sounds'][0]
    frozen=prep.sounds.freeze(gid,[clip['id']])
    prep.sounds.audio(clip['id']).unlink()
    assert str(prep.sounds.audio(clip['id']))==frozen[clip['id']]['path']
    assert prep.sounds.freeze(gid,[clip['id']])[clip['id']]['sha256']==clip['sha256']


def test_published_package_uses_final_song_clock_and_edited_lyrics(publication):
    _,prep,publisher,store,gid,source,_=publication
    prep.worker=ROOT/'_99_tools/_workers_processing/worker.py'
    loaded=prep.song_projects.get(gid);doc=loaded['project'];doc['approved']=True
    doc['words'][0]['text']='Restored'
    doc['segments']=[{'id':'first','kind':'audio','start':0,'end':.4,'mode':'normal'},
                     {'id':'gap','kind':'gap','duration':.5},
                     {'id':'last','kind':'audio','start':.4,'end':1,'mode':'normal'}]
    saved=save(prep,gid,loaded)
    with pytest.raises(ValueError,match='latest saved'):publisher.start_prepare(gid,{'include_flac':True})
    job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    master=job['id']+'-output'
    _,release=prepare(publication,master_id=master,timed_lyrics=True)
    assert release['track']['lyrics']['raw'].startswith('Restored line')
    assert release['track']['duration']==pytest.approx(1.5)
    from pathlib import Path
    lyric_file=next(Path(item['path']) for item in release['files'] if Path(item['path']).name=='lyrics.json')
    public=json.loads(lyric_file.read_text(encoding='utf-8'))
    public_words=[word for line in public['lines'] for word in line['words']]
    assert public_words[-1]['start']>.5
    assert all(0<=word['start']<word['end']<=1.5 for word in public_words)


def test_separated_stem_mix_is_arranged_with_the_music_not_over_the_break(prepared):
    _,prep,_,_,gid,_=prepared;source=promote(prepared);original,rate=sf.read(source)
    from _gbr_preparation.service import canonical
    task=prep._insert(gid,'lyric_alignment',{'version':1},digest(source),'done')
    for name,ratio in [('vocals',.25),('accompaniment',.75)]:
        path=prep.bundle(gid)/'_derived'/(name+'.wav');path.parent.mkdir(exist_ok=True)
        sf.write(path,original*ratio,rate,subtype='FLOAT')
        prep.db.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
            (task+'-'+name,'stem_'+name,digest(path),str(path),path.stat().st_size,
             canonical({'processing_task_id':task,'source_generation_id':gid})))
    loaded=prep.song_projects.get(gid);doc=loaded['project'];doc['stem_task_id']=task
    doc['mix'].update(vocal_db=-6,accompaniment_db=-12,limiter_db=None)
    doc['segments']=[{'id':'a','kind':'audio','start':0,'end':2,'mode':'normal'},
                     {'id':'gap','kind':'gap','duration':1},
                     {'id':'b','kind':'audio','start':2,'end':4,'mode':'normal'}]
    saved=save(prep,gid,loaded);job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    actual,_=sf.read(prep.artifact(job['id']+'-output'))
    expected=original*(.25*10**(-6/20)+.75*10**(-12/20))
    np.testing.assert_allclose(actual[:2*rate],expected[:2*rate],atol=2/2**23)
    assert np.max(abs(actual[2*rate:3*rate]))==0
    np.testing.assert_allclose(actual[3*rate:],expected[2*rate:],atol=2/2**23)


def test_clip_effect_clock_follows_a_split_across_an_inserted_break(prepared):
    _,prep,_,_,gid,_=prepared;source=promote(prepared);original,rate=sf.read(source)
    loaded=prep.song_projects.get(gid);doc=loaded['project'];doc['mix']['limiter_db']=None
    effect={'preset':'parametric_eq','strength':100,'parameters':{'frequency':330,'q':1,'gain_db':6},
            'range':{'start':1,'end':3,'fade_in':0,'fade_out':0}}
    doc['segments']=[{'id':'a','kind':'audio','start':0,'end':2,'mode':'normal','effects':[effect]},
                     {'id':'g','kind':'gap','duration':.5},
                     {'id':'b','kind':'audio','start':2,'end':4,'mode':'normal','effects':[effect],'effect_offset':2}]
    saved=save(prep,gid,loaded);job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    actual,_=sf.read(prep.artifact(job['id']+'-output'))
    np.testing.assert_allclose(actual[:rate],original[:rate],atol=2/2**23)
    np.testing.assert_allclose(actual[int(3.5*rate)+1:],original[3*rate+1:],atol=2/2**23)
    assert np.sqrt(np.mean(actual[int(2.7*rate):int(3.3*rate)]**2))>.25


def test_annotation_only_versions_reuse_audio_and_refresh_bound_sidecars(prepared,monkeypatch):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    saved=save(prep,gid,prep.song_projects.get(gid))
    job=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,job['id'])
    original=prep.artifact(job['id']+'-output');sha=digest(original)
    # A word/approval/light change must not enqueue an audio-render worker.
    monkeypatch.setattr(prep.pool,'submit',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('Rendered unchanged audio')))
    saved['project']['words'][0]['text']='Recovered';saved['project']['approved']=True
    saved['project']['lighting']['sensitivity']=2
    newer=save(prep,gid,saved)
    assert newer['reused_master_id'] and prep.artifact(newer['reused_master_id'])==original
    editorial=prep.editorial(gid,newer['reused_master_id'])
    assert editorial['source_sha256']==sha and editorial['approved']
    assert editorial['words'][0]['text']=='Recovered' and editorial['sensitivity']==2
    assert editorial['song_project_id']==newer['revision_id']
    prep.song_projects.assert_release(gid,newer['reused_master_id'],None)
    newer['project']['mix']['gain_db']=-3
    changed=save(prep,gid,newer)
    assert changed['reused_master_id'] is None
    with pytest.raises(ValueError,match='latest saved'):prep.song_projects.assert_release(gid,newer['reused_master_id'],None)


def test_range_preview_assembles_only_the_required_prefix_and_matches_master(prepared,tmp_path):
    _,prep,_,_,gid,_=prepared;source=promote(prepared)
    loaded=prep.song_projects.get(gid);doc=loaded['project']
    doc['mix']['effects']=[{'preset':'concert_hall','strength':35},{'preset':'compressor','strength':60}]
    saved=save(prep,gid,loaded);recipe=prep.song_projects.render_recipe(gid,saved['revision_id'])
    from _gbr_preparation.project_audio import assemble
    request={'kind':'mix_preview','source':str(source),'output':str(tmp_path/'preview'),
             'recipe':{'song_project':recipe['project'],'clips':recipe['clips'],'start':.25,'seconds':1}}
    prefix=assemble(request)
    assert sf.info(prefix).duration==2.25
    master=prep.submit(gid,'mix_master',{'song_project_id':saved['revision_id']});wait(prep,master['id'])
    preview=prep.submit(gid,'mix_preview',{'song_project_id':saved['revision_id'],'start':.25,'seconds':1});wait(prep,preview['id'])
    full,rate=sf.read(prep.artifact(master['id']+'-output'))
    actual,_=sf.read(prep.artifact(preview['id']+'-output'))
    np.testing.assert_allclose(actual,full[rate//4:rate//4+rate],atol=2/2**23)
