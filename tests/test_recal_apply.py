"""Applying recalibration proposals: validated, backed up, never mid-position, hand-made jobs
untouched, pending retried, and a removed symbol is closed by the bot's existing rule."""
import json
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

import live
from autobot import recal_apply as ra
from autobot import recalibrate as rc
from tests.test_live import BASE, FakeTC, env  # noqa: F401  (env: a temp config + fake bars for live.Bot)

NOW = datetime(2026, 9, 30, 21, 0, tzinfo=timezone.utc)
HAND = {"name": "trend", "strategy": "ema_cross", "params": {"fast": 9, "slow": 21}, "symbols": ["SPY"], "allocation_pct": 20, "enabled": True}
OLD = {"name": "auto_NVDA", "strategy": "ema_cross", "params": {"fast": 5, "slow": 34}, "symbols": ["NVDA"], "allocation_pct": 10, "enabled": True}
GONE = {"name": "auto_AMD", "strategy": "rsi_reversion", "params": {"period": 7}, "symbols": ["AMD"], "allocation_pct": 10, "enabled": True}
NEW_NVDA = {"name": "auto_NVDA", "strategy": "rsi_reversion", "params": {"period": 7, "oversold": 20}, "symbols": ["NVDA"], "allocation_pct": 10, "enabled": True}
NEW_BTC = {"name": "auto_BTCUSD", "strategy": "ema_cross", "params": {"fast": 9, "slow": 21}, "symbols": ["BTC/USD"], "allocation_pct": 10, "enabled": True}


@pytest.fixture
def setup(tmp_path):
    cfg = {"live": {"timeframe": "5Min", "risk": {"max_daily_loss_pct": 2.0, "max_position_usd": 10000},
                    "jobs": [HAND, OLD, GONE]}, "news": {"symbols": ["SPY"]}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    out = tmp_path / "recal"
    prop = {"date": "2026-09-30", "changes": ["NVDA", "AMD", "BTC/USD"], "proposed_jobs": [NEW_NVDA, NEW_BTC]}
    out.mkdir()
    (out / "2026-09-30.json").write_text(json.dumps(prop))
    return path, out, prop


def jobs_of(path):
    return {j["name"]: j for j in yaml.safe_load(path.read_text())["live"]["jobs"]}


def test_apply_after_the_close_changes_only_the_proposal(setup):
    path, out, prop = setup
    r = ra.apply(prop, market_open=False, positions={}, now=NOW, cfg_path=path, out_dir=out)
    assert r["applied"] == ["AMD", "BTC/USD", "NVDA"] and not r["pending"] and not r["error"]
    j = jobs_of(path)
    assert j["trend"] == HAND                               # hand-made job untouched
    assert j["auto_NVDA"] == NEW_NVDA and j["auto_BTCUSD"] == NEW_BTC and "auto_AMD" not in j
    assert yaml.safe_load(path.read_text())["news"] == {"symbols": ["SPY"]}   # other sections kept
    backups = list((out / "config_backups").glob("*.yaml"))
    assert len(backups) == 1 and jobs_of(backups[0])["auto_NVDA"] == OLD     # the previous config, byte for byte
    state = json.loads((out / "state.json").read_text())
    assert state["NVDA"] == {"since": "2026-09-30", "changes": ["2026-09-30"]}
    assert ra.status(out)["decisions"]["2026-09-30"]["status"] == "applied"


def test_never_writes_a_config_that_fails_validation(setup):
    path, out, prop = setup
    before = path.read_bytes()
    bad = {**prop, "proposed_jobs": [{**NEW_NVDA, "params": {"no_such_param": 1}}, NEW_BTC]}
    r = ra.apply(bad, market_open=False, positions={}, now=NOW, cfg_path=path, out_dir=out)
    assert r["error"] and "validation" in r["error"] and r["applied"] == []
    assert path.read_bytes() == before                         # untouched
    assert not (out / "config_backups").exists() or not list((out / "config_backups").glob("*"))
    assert ra.status(out)["decisions"]["2026-09-30"]["status"] == "error"


def test_never_mid_position_then_applied_when_ready(setup):
    path, out, prop = setup
    r = ra.apply(prop, market_open=True, positions={"BTCUSD": 0.05}, now=NOW, cfg_path=path, out_dir=out)
    assert r["applied"] == [] and {x["symbol"] for x in r["pending"]} == {"NVDA", "AMD", "BTC/USD"}
    assert jobs_of(path)["auto_NVDA"] == OLD
    # market closes, but the bot still holds BTC: stocks go, the coin waits
    r = ra.tick(market_open=False, positions={"BTCUSD": 0.05}, now=NOW, cfg_path=path, out_dir=out)
    assert r["applied"] == ["AMD", "NVDA"] and [x["symbol"] for x in r["pending"]] == ["BTC/USD"]
    assert "auto_BTCUSD" not in jobs_of(path)
    r = ra.tick(market_open=True, positions={}, now=NOW, cfg_path=path, out_dir=out)   # BTC flat: it goes
    assert r["applied"] == ["BTC/USD"] and ra.status(out)["pending"] is None
    assert jobs_of(path)["auto_BTCUSD"] == NEW_BTC


def test_a_newer_proposal_replaces_old_pending_items(setup):
    path, out, prop = setup
    ra.apply(prop, market_open=True, positions={}, now=NOW, cfg_path=path, out_dir=out)
    (out / "2026-10-01.json").write_text(json.dumps({"date": "2026-10-01", "changes": [], "proposed_jobs": []}))
    assert ra.tick(market_open=False, positions={}, now=NOW, cfg_path=path, out_dir=out) is None
    assert ra.status(out)["pending"] is None and jobs_of(path)["auto_NVDA"] == OLD


def test_symbol_now_owned_by_a_hand_made_job_is_skipped(setup):
    path, out, prop = setup
    cfg = yaml.safe_load(path.read_text())
    cfg["live"]["jobs"] = [HAND, {**OLD, "name": "mine", "symbols": ["NVDA"]}, GONE]   # the user took NVDA over by hand
    path.write_text(yaml.safe_dump(cfg, sort_keys=False))
    r = ra.apply(prop, market_open=False, positions={}, now=NOW, cfg_path=path, out_dir=out)
    assert [x["symbol"] for x in r["skipped"]] == ["NVDA"] and "auto_NVDA" not in jobs_of(path)
    assert jobs_of(path)["mine"]["symbols"] == ["NVDA"]


def test_reject_records_and_clears_pending(setup):
    path, out, prop = setup
    ra.apply(prop, market_open=True, positions={}, now=NOW, cfg_path=path, out_dir=out)
    ra.reject("2026-09-30", NOW, out)
    st = ra.status(out)
    assert st["decisions"]["2026-09-30"]["status"] == "rejected" and st["pending"] is None


def test_removed_symbol_is_closed_by_the_bot(env, tmp_path):  # noqa: F811
    """The existing rule: a symbol that leaves all enabled jobs has its position closed."""
    cfg = yaml.safe_load(env.read_text())
    cfg["live"]["jobs"].append({**GONE, "symbols": ["AMD"]})
    env.write_text(yaml.safe_dump(cfg, sort_keys=False))
    tc = FakeTC(positions={"AMD": 7})
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=tc, cfg_path=env)
    out = tmp_path / "recal"
    out.mkdir()
    ra.apply({"date": "2026-09-30", "changes": ["AMD"], "proposed_jobs": []}, market_open=False, positions={},
             now=NOW, cfg_path=env, out_dir=out)
    import os
    import time
    t = time.time() + 5
    os.utime(env, (t, t))
    bot.reload_config()
    assert tc.closed == ["AMD"]


def test_api_apply_needs_confirmation():
    from api.main import app
    client = TestClient(app)
    r = client.post("/api/recalibration/apply", json={})
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
