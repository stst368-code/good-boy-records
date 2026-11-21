import json
import numpy as np
import pytest
import soundfile as sf

from test_preparation import prepared, promote, wait
from _gbr_preparation.service import mix_recipe, digest
from _gbr_preparation.waveform import waveform


def test_section_effect_automation_is_sample_aligned_and_preview_matches_master(prepared):
    _,prep,_,_,gid,_=prepared
    source=promote(prepared);original_hash=digest(source)
    recipe={'effects':[{'preset':'parametric_eq','strength':100,'parameters':{'frequency':330,'q':1,'gain_db':10},
                       'range':{'start':1,'end':3,'fade_in':.2,'fade_out':.2},
                       'automation':[{'time':1,'value':0},{'time':2,'value':100},{'time':3,'value':0}]}]}
    master=prep.submit(gid,'mix_master',{'mix':recipe})
    wait(prep,master['id'])
    preview=prep.submit(gid,'mix_preview',{'mix':recipe,'start':.5,'seconds':2})
    wait(prep,preview['id'])
    processed,rate=sf.read(prep.artifact(master['id']+'-output'))
    excerpt,preview_rate=sf.read(prep.artifact(preview['id']+'-output'))
    original,_=sf.read(source)
    assert len(processed)==len(original) and rate==preview_rate
    np.testing.assert_allclose(processed[:rate],original[:rate],atol=2/2**23)
    np.testing.assert_allclose(processed[3*rate+1:],original[3*rate+1:],atol=2/2**23)
    assert np.max(np.abs(processed[rate:3*rate]-original[rate:3*rate]))>.05
    np.testing.assert_allclose(excerpt,processed[rate//2:rate//2+2*rate],atol=2/2**23)
    assert digest(source)==original_hash
    measured=json.loads(prep.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(master['id'],))['telemetry_json'])['measurements']
    assert 'integrated_lufs' in measured and 'true_peak_dbfs' in measured


@pytest.mark.parametrize('effect',[
    {'preset':'compressor','strength':100,'parameters':{'ratio':100}},
    {'preset':'tape_warmth','strength':100,'range':{'start':2,'end':1}},
    {'preset':'tape_warmth','strength':100,'range':{'start':0,'end':1,'fade_in':2}},
    {'preset':'tape_warmth','strength':100,'automation':[{'time':-1,'value':10}]},
    {'preset':'tape_warmth','strength':100,'automation':[{'time':1,'value':0},{'time':1,'value':100}]},
    {'preset':'tape_warmth','strength':100,'automation':[{'time':1,'value':float('nan')}]},
])
def test_bad_effect_automation_cannot_reach_filter_graph(effect):
    with pytest.raises(ValueError):mix_recipe({'effects':[effect]})


def test_ranges_must_fit_audio_and_waveform_is_bounded_and_cached(prepared,monkeypatch):
    _,prep,_,_,gid,_=prepared
    source=promote(prepared);checksum=digest(source)
    data=waveform(prep,gid)
    assert data['source_sha256']==checksum and data['duration']==4 and len(data['peaks'])<=1800
    assert data['channels']==1 and data['sample_peak_dbfs']<0
    monkeypatch.setattr(sf,'blocks',lambda *a,**k: (_ for _ in ()).throw(AssertionError('Cached waveform decoded audio again')))
    assert waveform(prep,gid)==data
    with pytest.raises(ValueError,match='fit inside'):
        prep.submit(gid,'mix_master',{'mix':{'effects':[{'preset':'tape_warmth','strength':60,'range':{'start':0,'end':5}}]}})
    assert digest(source)==checksum
