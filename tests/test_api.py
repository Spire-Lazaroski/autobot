"""API smoke tests on demo data (no network, no keys)."""
import time

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)
DATA = {"source": "demo", "symbols": ["SPY", "QQQ"], "timeframe": "15Min", "days": 60}


def test_strategies_listed():
    r = client.get("/api/strategies")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert {"buy_and_hold", "ema_cross", "noise_area"} <= names


def test_compare():
    r = client.post("/api/compare", json={"data": DATA, "strategies": {"buy_and_hold": {}, "ema_cross": {"fast": 5}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {row["strategy"] for row in body["summary"]} == {"buy_and_hold", "ema_cross"}
    assert set(body["equity"]["series"]) == {"buy_and_hold", "ema_cross"}
    assert len(body["equity"]["t"]) == len(body["equity"]["series"]["ema_cross"])


def test_detail():
    r = client.post("/api/detail", json={"data": DATA, "symbol": "SPY", "strategy": "bollinger_reversion"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["candles"]["t"]) == len(b["equity"]) == len(b["position"])
    assert [o["name"] for o in b["overlays"]] == ["upper", "mid", "lower"]
    assert b["fills"] and b["fills"][0]["side"] in ("buy", "sell")


def test_search_job():
    r = client.post("/api/search", json={"data": DATA, "names": ["sma_cross"]})
    job = r.json()
    for _ in range(100):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert job["status"] == "done", job
    assert job["result"]["best"][0]["strategy"] == "sma_cross"


def test_config_rejects_duplicate_symbols():
    cfg = client.get("/api/config").json()
    jobs = cfg["live"]["jobs"]
    bad = {**cfg, "live": {**cfg["live"], "jobs": jobs + [{**jobs[0], "name": "dupe", "enabled": True}]}}
    r = client.put("/api/config", json=bad)
    assert r.status_code == 422 and "One job per symbol" in r.text


def test_bad_request():
    r = client.post("/api/compare", json={"data": {**DATA, "timeframe": "7Min"}, "strategies": {"ema_cross": {}}})
    assert r.status_code == 422


def _wait(job):
    for _ in range(300):
        job = client.get(f"/api/jobs/{job['id']}").json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    return job


def test_walkforward_job():
    r = client.post("/api/walkforward", json={"data": DATA, "strategy": "ema_cross", "train_bars": 400, "test_bars": 100})
    job = _wait(r.json())
    assert job["status"] == "done", job
    res = job["result"]
    assert set(res["equity"]["series"]) == {"walk_forward", "fixed", "buy_and_hold"}
    assert res["windows"][0]["params"]["fast"] in (5, 9, 12)


def test_shift_regime():
    r = client.post("/api/shift", json={"data": DATA, "strategy": "regime_switch", "params": {"vol_lookback": 200}})
    assert r.status_code == 200, r.text
    b = r.json()
    assert set(b["equity"]["series"]) == {"regime_switch", "ema_cross", "rsi_reversion", "buy_and_hold"}
    assert b["timelines"]["SPY"]["regimes"] and b["timelines"]["SPY"]["active"]
    assert b["switches"]["SPY"]["switches"] >= b["switches"]["SPY"]["switches_that_traded"]
    assert abs(sum(b["time_in"].values()) - 100) < 0.1


def test_shift_rejects_nested_meta():
    r = client.post("/api/shift", json={"data": DATA, "strategy": "perf_rotation", "params": {"candidates": ["ensemble_vote"]}})
    assert r.status_code == 422


def test_detail_meta_has_active():
    r = client.post("/api/detail", json={"data": DATA, "symbol": "SPY", "strategy": "perf_rotation"})
    assert r.status_code == 200, r.text
    assert len(r.json()["active"]["names"]) == len(r.json()["candles"]["t"])


def test_config_rejects_bad_params():
    cfg = client.get("/api/config").json()
    jobs = cfg["live"]["jobs"]
    bad = {**cfg, "live": {**cfg["live"], "jobs": [{**jobs[0], "params": {"fastt": 3}}] + jobs[1:]}}
    r = client.put("/api/config", json=bad)
    assert r.status_code == 422 and "unknown params" in r.text


def test_stop_needs_confirmation():
    r = client.post("/api/live/stop", json={})
    assert r.status_code == 400


def test_start_refused_while_running(monkeypatch):
    import api.live as L
    monkeypatch.setattr(L, "bot_state", lambda: {"running": True, "pid": 4242})
    called = []
    monkeypatch.setattr(L.subprocess, "Popen", lambda *a, **k: called.append(a))
    r = client.post("/api/live/start")
    assert r.status_code == 409 and not called


def test_overnight_study_job_on_demo_data():
    r = client.post("/api/overnight-study", json={"days": 60, "long": False, "source": "demo"})
    assert r.status_code == 200, r.text
    job = _wait(r.json())
    if job["status"] == "error" and "No enabled stock jobs" in job["error"]:
        return  # config.yaml without stock jobs: nothing to study
    assert job["status"] == "done", job
    res = job["result"]
    assert res["source"] == "demo" and "90d" in res["periods"] and res["summary"]
    assert client.post("/api/overnight-study", json={"days": 5}).status_code == 400
    assert client.get("/api/overnight-study").status_code == 200


def test_recal_fix_study_endpoint():
    assert client.get("/api/recal-fix-study").status_code == 200


def test_stop_tells_the_bot_whether_to_sell_the_core(monkeypatch, tmp_path):
    import api.live as L
    import live
    stop = tmp_path / "STOP"
    monkeypatch.setattr(L, "STOP_FILE", stop)
    monkeypatch.setattr(live, "STOP_FILE", stop)
    monkeypatch.setattr(L, "bot_state", lambda: {"running": True, "pid": 4242})
    assert client.post("/api/live/stop", json={"confirm": True}).status_code == 200
    assert live.stop_includes_core() is False
    client.post("/api/live/stop", json={"confirm": True, "include_core": True})
    assert live.stop_includes_core() is True
