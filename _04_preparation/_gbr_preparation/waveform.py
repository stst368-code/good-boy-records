"""Bounded, cached waveform peaks; no full audio buffers in the browser."""
import json
import math

from .service import digest, canonical


def waveform(preparation, gid, artifact=None):
    import numpy as np
    import soundfile as sf
    from _review.util import atomic_text

    preparation.require()
    _, source = preparation.source(gid, artifact_id=artifact)
    checksum = digest(source)
    directory = preparation.bundle(gid)/'_derived'
    if directory.is_symlink() or not directory.resolve().is_relative_to(preparation.bundle(gid).resolve()):
        raise ValueError('Waveform cache must stay inside the recording bundle')
    cache = directory/(checksum+'-waveform-v1.json')
    if cache.is_symlink():
        raise ValueError('Waveform cache cannot be a symbolic link')
    if cache.is_file():
        return json.loads(cache.read_text(encoding='utf-8'))
    info = sf.info(source)
    count = max(1, math.ceil(info.frames/1800))
    peaks, maximum, squares, samples = [], 0., 0., 0
    for block in sf.blocks(source, blocksize=count, dtype='float32', always_2d=True):
        if not np.isfinite(block).all():
            raise ValueError('Working audio contains invalid samples')
        peaks.append([[round(float(block[:, ch].min()), 5), round(float(block[:, ch].max()), 5)]
                      for ch in range(info.channels)])
        maximum = max(maximum, float(np.max(np.abs(block))))
        squares += float(np.sum(block.astype('float64')**2))
        samples += block.size
    result = {'format': 'gbr-waveform-v1', 'source_sha256': checksum, 'duration': info.duration,
              'sample_rate': info.samplerate, 'channels': info.channels, 'peaks': peaks,
              'sample_peak_dbfs': 20*math.log10(maximum) if maximum else None,
              'rms_dbfs': 10*math.log10(squares/samples) if squares and samples else None}
    directory.mkdir(parents=True, exist_ok=True)
    atomic_text(cache, canonical(result))
    return result
