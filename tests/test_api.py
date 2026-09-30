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
