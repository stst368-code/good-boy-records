import importlib.util
from pathlib import Path
import shutil
import subprocess
import numpy as np
import pytest
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('processing_worker', ROOT/'_99_tools/_workers_processing/worker.py')
worker = importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


@pytest.mark.parametrize('rate', [16000, 44100, 48000])
def test_old_limiter_matches_native_sample_timing_and_full_tail(tmp_path, monkeypatch, rate):
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('FFmpeg unavailable')
    real_run = subprocess.run
    signal = np.zeros((rate, 2));signal[rate//4] = .1;signal[-2] = .1
    source = tmp_path/'source.wav';sf.write(source, signal, rate, subtype='FLOAT')
    native, mode = worker.limiter_filters(ffmpeg, -1, rate)
    if mode != 'native':pytest.skip('Native reference requires latency-capable FFmpeg')
    def old_help(command, **kwargs):
        if command[-1] == 'filter=alimiter':
            return subprocess.CompletedProcess(command, 0, 'attack <double>\nlevel <boolean>\n', '')
        return real_run(command, **kwargs)
    monkeypatch.setattr(worker.subprocess, 'run', old_help)
    fallback, mode = worker.limiter_filters(ffmpeg, -1, rate)
    assert mode == 'explicit_samples' and 'latency=' not in ','.join(fallback)
    outputs = []
    for name, filters in [('native', native), ('old', fallback)]:
        output = tmp_path/(name+'.wav')
        real_run([ffmpeg, '-v', 'error', '-i', str(source), '-af', ','.join(filters), '-c:a', 'pcm_f32le', str(output)], check=True)
        outputs.append(sf.read(output, always_2d=True)[0])
    assert outputs[0].shape == outputs[1].shape == signal.shape
    np.testing.assert_allclose(outputs[0], outputs[1], atol=1e-7)
    assert np.argmax(outputs[1][:rate//2,0]) == rate//4
    assert outputs[1][-2,0] == pytest.approx(.1, abs=1e-6)


@pytest.mark.parametrize('kind', ['mix_preview', 'mix_master'])
def test_old_build_real_render_duration_fades_and_limiter_peak(tmp_path, monkeypatch, kind):
    if not shutil.which('ffmpeg'):pytest.skip('FFmpeg unavailable')
    real_run = subprocess.run
    def old_help(command, **kwargs):
        if command[-1] == 'filter=alimiter':
            return subprocess.CompletedProcess(command, 0, 'attack <double>\nlevel <boolean>\n', '')
        return real_run(command, **kwargs)
    monkeypatch.setattr(worker.subprocess, 'run', old_help)
    source = tmp_path/'source.flac';rate=44100
    signal = np.zeros((2*rate,2));signal[rate//2:rate+rate//2] = .95
    sf.write(source, signal, rate, subtype='PCM_24')
    mix = dict(gain_db=0,bass_db=0,mid_db=0,treble_db=0,reverb=0,limiter_db=-1,fade_in=.1,fade_out=.1)
    result = worker.render({'kind':kind,'source':str(source),'output':str(tmp_path/'output'), 'duration':2,
                            'recipe':{'mix':mix,'start':.5,'seconds':1}})
    data,sr = sf.read(result['output'],always_2d=True)
    assert len(data) == rate*(1 if kind=='mix_preview' else 2)
    assert np.max(np.abs(data)) <= 10**(-1/20)+1e-6
    assert result['software']['limiter_compensation'] == 'explicit_samples'
    if kind == 'mix_preview':assert data[0,0] > .1 # no lookahead shift into audition
