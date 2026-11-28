"""Recorded replacements enter the library without changing saved audio."""
import pytest
import numpy as np
import soundfile as sf
from test_preparation import prepared
from _gbr_preparation.service import canonical,digest


@pytest.mark.parametrize('custom_name',[False,True])
def test_recorded_presets_replace_only_the_catalogue_and_keep_saved_copies(prepared,tmp_path,custom_name):
    _,prep,_,_,_,_=prepared
    source=tmp_path/'legacy.wav';sf.write(source,np.sin(np.arange(16000)*.03)*.1,16000,subtype='PCM_24')
    old=prep.sounds.register(source,'My custom horn' if custom_name else 'Air horn','Stabs',builtin=True)
    metadata=old['_metadata'];metadata.update(builtin_key='horn',builtin_version=1)
    prep.db.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?',(canonical(metadata),old['id']))
    original=prep.sounds.audio(old['id']).read_bytes()
    active=prep.sounds.list()['sounds'];assert len(active)==(7 if custom_name else 6)
    assert (old['id'] in {item['id'] for item in active})==custom_name
    assert prep.sounds.audio(old['id']).read_bytes()==original
    replacements={item['preset']:item for item in active}
    assert replacements['horn']['id']!=old['id']
    for key in ['horn','lift','drop']:
        item=replacements[key]
        assert item['license']=='CC0-1.0' and item['source_url'].startswith('https://freesound.org/')
        audio,rate=sf.read(prep.sounds.audio(item['id']))
        assert rate==44100 and audio.shape[1]==2
        assert np.isfinite(audio).all() and abs(audio).max()>.1
    assert replacements['lift']['sha256']!=replacements['drop']['sha256']
    assert prep.sounds.list()['sounds']==active
