import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest
import soundfile as sf
from review.config import Config
from review.service import Service


@pytest.fixture
def service(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    (tmp_path / "config.json").write_text(json.dumps({"input_directories": [str(inp)], "quick_seconds": 20, "standard_seconds": 50}))
    return Service(Config(tmp_path / "config.json"))


def track(service, name="song", seconds=4, frequency=330, metadata=None, stereo=False):
    path = service.config.inputs[0] / (name + ".flac")
    sr = 16000
    t = np.arange(round(sr * seconds)) / sr
    signal = .18 * np.sin(2 * np.pi * frequency * t)
    if stereo:
        signal = np.stack([signal, .18 * np.sin(2 * np.pi * frequency * t + .2)], axis=1)
    sf.write(path, signal, sr, subtype="PCM_24")
    raw = metadata if metadata is not None else f"title: {name}\ngenre: Chiptune\nsampler: euler\nscheduler: simple\nsampler_steps: 35\n"
    path.with_suffix(".yaml").write_text(raw, encoding="utf-8")
    aid = service.indexer.index(path)
    gid = service.db.one("SELECT id FROM generations WHERE audio_id=? ORDER BY rowid DESC", (aid,))["id"]
    return path, aid, gid


def complete(service, gid, values=(5, 5, 5, 5), previous=None):
    # Archive/meter tests arrange a valid first-pass lease for the specific
    # fixture. Queue/concurrency tests exercise allocation through the API.
    if previous is None and not service.db.one("SELECT 1 FROM reviews r JOIN generations g ON g.audio_id=r.audio_id WHERE g.id=?", (gid,)):
        aid = service.db.one("SELECT audio_id FROM generations WHERE id=?", (gid,))["audio_id"]
        rid = service.default_reviewer_id
        with service.db.transaction() as c:
            session = service.queues.session(c, rid)
            c.execute("INSERT INTO queue_reservations VALUES(?,?,?,?,?,?) ON CONFLICT(audio_id) DO UPDATE SET generation_id=excluded.generation_id,expires_at=excluded.expires_at WHERE queue_reservations.reviewer_id=excluded.reviewer_id", (aid, rid, gid, 0, service.queues.clock(), service.queues.clock()+180))
            c.execute("UPDATE sessions SET current_generation_id=? WHERE id=?", (gid, session["id"]))
    return service.review(gid, dict(zip(("audio_quality", "composition", "consistency", "realism"), values)), "quick", previous)


def archive_all(service):
    for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending' ORDER BY created_at,rowid"):
        service.archiver.process(op)
