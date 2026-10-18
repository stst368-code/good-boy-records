import hashlib
import io
import json
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
import pytest
import soundfile as sf

from review.analysis import VERSION, measure, select_segments
from review.app import create_app
from review.mobile import access_code, enable_http
from review.config import password_hash, password_ok
from conftest import track, complete


def test_tuning_requested_during_analysis_is_not_lost(service, monkeypatch):
    _, aid, _ = track(service, seconds=6)
    monkeypatch.setattr(service.indexer, "scan", lambda: None)
    def running_analysis(audio_id):
        assert audio_id == aid
        service.analysis_generation += 1  # Simulate Settings > Retune during measurement.
        service.db.execute("UPDATE audio SET analysis_state='ready' WHERE id=?", (audio_id,))
        service.stop_event.set()
    monkeypatch.setattr(service.analyzer, "run", running_analysis)
    service.work()
    assert service.db.one("SELECT analysis_state FROM audio WHERE id=?", (aid,))["analysis_state"] == "retune_pending"


def test_subtle_arrangement_changes_without_large_level_or_flux_jump(tmp_path):
    sr = 16000
    t = np.arange(sr * 54) / sr
    baseline = .11 * np.sin(2 * np.pi * 220 * t)
    added = np.where(t < 18, 0, np.where(t < 36, .04 * np.sin(2 * np.pi * 880 * t),
                                        .04 * np.sin(2 * np.pi * 1100 * t)))
    path = tmp_path / "arrangement.flac"
    sf.write(path, baseline + added, sr, subtype="PCM_24")
    metrics, events, preview = measure(path, tmp_path / "cache")
    fs = preview["features"]
    assert max(f["flux"] for f in fs) < .35
    assert max(abs(a["rms_db"] - b["rms_db"]) for a, b in zip(fs, fs[1:])) < 16
    boundaries = [e["measurements"]["boundary_seconds"] for e in events if e["type"] == "structural_transition"]
    assert all(any(abs(p - expected) <= 1 for p in boundaries) for expected in (18, 36)), boundaries
    clips = select_segments(preview, events, 10, "fast")
    assert sum(s["end"] - s["start"] for s in clips) == pytest.approx(10)
    assert all(any(s["start"] < expected < s["end"] for s in clips) for expected in (18, 36))


def test_steady_pulsing_passage_does_not_create_section_boundaries(tmp_path):
    sr = 16000
    t = np.arange(sr * 40) / sr
    signal = (.12 + .02 * np.sin(2 * np.pi * 2 * t)) * np.sin(2 * np.pi * 330 * t)
    path = tmp_path / "steady.flac"
    sf.write(path, signal, sr, subtype="PCM_24")
    metrics, events, preview = measure(path, tmp_path / "cache")
    assert not [e for e in events if e["type"] == "structural_transition"]


def test_antiphase_stereo_retains_spectral_information(tmp_path):
    sr = 16000
    t = np.arange(sr * 4) / sr
    signal = .12 * np.sin(2 * np.pi * 880 * t)
    path = tmp_path / "antiphase.flac"
    sf.write(path, np.stack([signal, -signal], axis=1), sr, subtype="PCM_24")
    metrics, events, preview = measure(path, tmp_path / "cache")
    assert metrics["spectral_centroid_hz"] == pytest.approx(880, abs=10)
    assert metrics["stereo_correlation"] < -.99


def test_cached_retuning_preserves_measurements_reviews_and_media(service, monkeypatch):
    path, aid, gid = track(service, seconds=70)
    original_bytes = hashlib.sha256(path.read_bytes()).hexdigest()
    first = service.analyzer.run(aid)
    old = service.db.one("SELECT * FROM analyses WHERE id=?", (first,))
    # Exercise compatibility with the shipped 1.0.1 cached descriptors.
    cache = Path(old["preview_path"])
    data = json.loads(cache.read_text())
    for f in data["features"]:
        f.pop("spectrum_bands")
        f.pop("chroma")
    cache.write_text(json.dumps(data))
    service.db.execute("UPDATE analyses SET version='gbr-signal-1.0.1' WHERE id=?", (first,))
    complete(service, gid)
    review_before = service.db.all("SELECT * FROM reviews")
    def no_decode(*args, **kwargs):
        raise AssertionError("Cached retuning must not decode original media")
    monkeypatch.setattr(sf, "SoundFile", no_decode)
    second = service.analyzer.retune(aid)
    new = service.db.one("SELECT * FROM analyses WHERE id=?", (second,))
    metrics = json.loads(new["metrics_json"])
    assert metrics["integrated_lufs"] == json.loads(old["metrics_json"])["integrated_lufs"]
    assert metrics["feature_source_version"] == "gbr-signal-1.0.1"
    assert metrics["retuned_from_analysis_id"] == first
    assert new["preview_path"] == old["preview_path"]
    assert service.db.all("SELECT * FROM reviews") == review_before
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_bytes
    assert service.db.one("SELECT COUNT(*) AS n FROM analyses WHERE audio_id=?", (aid,))["n"] == 2
    assert len(service.db.all("SELECT * FROM segments WHERE analysis_id=? AND mode='fast'", (second,))) == 5


def test_prefetch_does_not_advance_rate_or_skip_and_keeps_reserved_order(service):
    app = create_app(service.config.path, False)
    s = app.state.service
    for i, name in enumerate(("a", "b", "c")):
        _, aid, _ = track(s, name, seconds=12, frequency=220 + i * 100)
        s.analyzer.run(aid)
    s.queue_settings("alphabetical", "asc", {})
    with TestClient(app, headers={"X-GBR-Reviewer":app.state.service.default_reviewer_id}, base_url="http://localhost") as c:
        current = c.get("/api/queue/next?include_preview=true").json()
        gid = current["track"]["id"]
        assert current["preview"]["ready"]
        before = s.db.one("SELECT * FROM sessions WHERE id='main'")
        result = c.post("/api/queue/prefetch", json={"current_id": gid, "mode": "fast"}, headers={"X-GBR-Request": "1"}).json()
        assert result["prepared"]
        assert s.db.one("SELECT * FROM sessions WHERE id='main'") == before
        assert s.db.one("SELECT COUNT(*) AS n FROM reviews")["n"] == 0
        assert s.db.one("SELECT COUNT(*) AS n FROM queue_skips")["n"] == 0
        assert list((s.config.data / "cache" / "playback").glob("*.flac"))
        complete(s, gid)
        assert s.next()["track"]["id"] == result["track_id"]


def test_prefetch_invalidated_by_new_filters_and_stale_requests(service):
    app = create_app(service.config.path, False)
    s = app.state.service
    ids = [track(s, name, frequency=220 + i * 100)[2] for i, name in enumerate(("a", "b", "c"))]
    s.queue_settings("alphabetical", "asc", {})
    s.next()
    assert s.next(peek=True)["track"]["id"] == ids[1]
    s.queue_settings("alphabetical", "asc", {"song": "c"})
    assert s.prefetched_id is None
    assert s.next()["track"]["id"] == ids[2]
    with TestClient(app, headers={"X-GBR-Reviewer":app.state.service.default_reviewer_id}, base_url="http://localhost") as c:
        result = c.post("/api/queue/prefetch", json={"current_id": ids[0]}, headers={"X-GBR-Request": "1"}).json()
        assert result == {"prepared": False}
    assert s.next()["track"]["id"] == ids[2]


def test_playback_pins_analysis_after_retuning(service):
    _, aid, gid = track(service, seconds=70)
    old = service.analyzer.run(aid)
    service.config.values["quick_seconds"] = 12
    new = service.analyzer.retune(aid)
    old_audio = service.playback.render(gid, "quick", "wav", analysis_id=old)
    new_audio = service.playback.render(gid, "quick", "wav", analysis_id=new)
    assert sf.info(old_audio).duration == pytest.approx(20)
    assert sf.info(new_audio).duration == pytest.approx(12)
    assert old_audio != new_audio
    _, other, other_gid = track(service, "other", frequency=880)
    with pytest.raises(ValueError, match="does not belong"):
        service.playback.render(other_gid, "quick", "wav", analysis_id=old)


def test_fast_review_mode_and_retune_settings_api(service):
    app = create_app(service.config.path, False)
    s = app.state.service
    _, aid, gid = track(s, seconds=70)
    s.analyzer.run(aid)
    with TestClient(app, headers={"X-GBR-Reviewer":app.state.service.default_reviewer_id}, base_url="http://localhost") as c:
        bootstrap = c.get("/api/bootstrap").json()
        assert bootstrap["durations"]["fast"] == 10
        settings = c.get("/api/settings").json()
        settings = {k: v for k, v in settings.items() if k not in ("data_directory", "archive_resolved", "archive_locked")}
        settings.update(fast_seconds=8, transition_sensitivity="sensitive")
        assert c.post("/api/settings", json=settings, headers={"X-GBR-Request": "1"}).status_code == 200
        assert s.db.one("SELECT analysis_state FROM audio WHERE id=?", (aid,))["analysis_state"] == "retune_pending"
        s.analyzer.retune(aid)
        response = c.get(f"/api/playback/{gid}?mode=fast&format=wav")
        assert sf.info(io.BytesIO(response.content)).duration == pytest.approx(8)
        assert c.get('/api/queue/next').json()['track']['id'] == gid
        assert c.post(f"/api/tracks/{gid}/review", json={"audio_quality":4,"composition":4,"consistency":4,"realism":4,"mode":"fast"}, headers={"X-GBR-Request":"1"}).status_code == 200
        assert s.db.one("SELECT mode FROM reviews")["mode"] == "fast"


def test_mobile_http_reuses_code_preserves_password_and_configuration(service):
    cfg = service.config
    before_inputs = cfg.values["input_directories"][:]
    code = enable_http(cfg)
    assert code and len(code) == 6 and code.isdigit()
    assert cfg.values["host"] == "0.0.0.0"
    assert cfg.values["ssl_certfile"] == cfg.values["ssl_keyfile"] == ""
    assert cfg.values["input_directories"] == before_inputs
    assert enable_http(cfg) == code
    assert password_ok(code, cfg.values["password_hash"])
    cfg.values["password_hash"] = password_hash("chosen-password")
    cfg.save()
    assert enable_http(cfg) is None
    assert password_ok("chosen-password", cfg.values["password_hash"])
    assert access_code(cfg) is None


def test_mobile_auth_http_cookie_and_host_only_code(service):
    code = enable_http(service.config)
    app = create_app(service.config.path, False)
    with TestClient(app, headers={"X-GBR-Reviewer":app.state.service.default_reviewer_id}, base_url="http://localhost", client=("192.168.1.52", 2345)) as c:
        assert c.get("/api/bootstrap").json()["authenticated"] is False
        assert "access_code" not in c.get("/api/bootstrap").json()
        assert c.get("/api/queue/next").status_code == 401
        response = c.post("/api/login", json={"password":code}, headers={"X-GBR-Request":"1"})
        assert response.status_code == 200
        assert "Secure" not in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert c.get("/api/queue/next").status_code == 200
        assert c.get("/api/mobile").status_code == 403
        assert c.post("/api/retune", json={}, headers={"X-GBR-Request":"1"}).status_code == 403
