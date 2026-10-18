import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from review.analysis import VERSION, measure
from review.app import create_app
from review.server_loop import create_loop


@pytest.mark.parametrize("sr,seconds,channels", [
    (22050, 3.1, 1), (44100, 3.1, 2), (48000, 3.1, 2),
    (96000, 3.1, 2), (44100, .07, 1), (48000, .61, 2),
])
def test_partial_final_block_finishes_analysis(tmp_path, sr, seconds, channels):
    t = np.arange(round(sr * seconds)) / sr
    signal = .13 * np.sin(2 * np.pi * 330 * t)
    if channels == 2:
        signal = np.stack([signal, .11 * np.sin(2 * np.pi * 660 * t)], axis=1)
    path = tmp_path / "tail.flac"
    sf.write(path, signal, sr, subtype="PCM_24")
    original = hashlib.sha256(path.read_bytes()).digest()
    metrics, events, preview = measure(path, tmp_path / "cache")
    assert hashlib.sha256(path.read_bytes()).digest() == original
    assert preview["features"][-1]["end"] == pytest.approx(len(t) / sr)
    assert all(math.isfinite(f["flux"]) for f in preview["features"])
    assert math.isfinite(metrics["spectral_flux"])
    assert len(preview["waveform"]) == channels
    assert (tmp_path / "cache" / "spectrogram.png").is_file()
    assert json.loads((tmp_path / "cache" / "preview.json").read_text())["duration"] == pytest.approx(len(t) / sr)


def test_retry_failed_analysis_produces_playable_sampling(service):
    app = create_app(service.config.path, start_worker=False)
    s = app.state.service
    sr = 44100
    t = np.arange(round(sr * 63.1)) / sr
    path = s.config.inputs[0] / "failed-tail.flac"
    sf.write(path, .15 * np.sin(2 * np.pi * 330 * t), sr, subtype="PCM_24")
    path.with_suffix(".yaml").write_text("title: Tail regression\n")
    aid = s.indexer.index(path)
    gid = s.db.one("SELECT id FROM generations WHERE audio_id=?", (aid,))["id"]
    s.db.execute("UPDATE audio SET analysis_state='failed' WHERE id=?", (aid,))
    with TestClient(app, headers={"X-GBR-Reviewer":app.state.service.default_reviewer_id}, base_url="http://localhost") as client:
        assert client.get(f"/api/tracks/{gid}/preview").json()["ready"] is False
        assert client.post("/api/reanalyse", json={}, headers={"X-GBR-Request": "1"}).status_code == 200
        assert s.db.one("SELECT analysis_state FROM audio WHERE id=?", (aid,))["analysis_state"] == "pending"
        s.analyzer.run(aid)
        assert s.db.one("SELECT analysis_state FROM audio WHERE id=?", (aid,))["analysis_state"] == "ready"
        analysis = s.db.one("SELECT * FROM analyses WHERE audio_id=?", (aid,))
        assert analysis["version"] == VERSION
        preview = client.get(f"/api/tracks/{gid}/preview").json()
        assert preview["ready"]
        for mode, seconds in (("quick", 20), ("standard", 50)):
            sections = [r for r in preview["segments"] if r["mode"] == mode]
            assert sum(r["end"] - r["start"] for r in sections) == pytest.approx(seconds)
            rendered = s.playback.render(gid, mode, "wav")
            assert sf.info(rendered).duration == pytest.approx(seconds)
            response = client.get(f"/api/playback/{gid}?mode={mode}&format=wav", headers={"Range": "bytes=0-63"})
            assert response.status_code == 206 and len(response.content) == 64


def test_socket_loop_preserves_exception_reporting():
    import asyncio
    loop = create_loop()
    errors = []
    try:
        assert isinstance(loop, asyncio.SelectorEventLoop)
        loop.set_exception_handler(lambda active, context: errors.append(context["exception"]))
        def fail():
            raise RuntimeError("Real errors must remain visible")
        loop.call_soon(fail)
        loop.run_until_complete(asyncio.sleep(0))
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError)
    finally:
        loop.close()


@pytest.mark.parametrize("package", ["review", "review"])
def test_launcher_and_server_loop_work_after_package_rename(tmp_path, package):
    root = Path(__file__).resolve().parents[1]
    source_package = root / ("review" if (root / "review").is_dir() else "review")
    shutil.copytree(source_package, tmp_path / package, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(root / "run_review.py", tmp_path / "run_review.py")
    result = subprocess.run([sys.executable, str(tmp_path / "run_review.py"), "--help"], cwd=tmp_path.parent,
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "serve" in result.stdout
    # Exercise the exact custom import string used by the renamed CLI.
    code = ("import asyncio, uvicorn; "
            f"c=uvicorn.Config(lambda *args: None, loop='{package}.server_loop:create_loop'); "
            "loop=c.get_loop_factory()(); "
            "assert isinstance(loop, asyncio.SelectorEventLoop); loop.close()")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
