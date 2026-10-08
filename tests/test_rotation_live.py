"""Rotation sleeve in the live bot: validated with the core and the jobs, bought once a month, kept by
every flatten and out of the daily loss limit, and the jobs are sized into what is left."""
import json

import numpy as np
import pandas as pd
import pytest
import yaml

import live
from autobot import rotation
from autobot.liveconfig import validate_live
from tests.test_core_live import CORE, CoreTC
from tests.test_live import BASE, env  # noqa: F401

ROT = {"enabled": True, "allocation_pct": 40, "top_n": 2, "symbols": ["XLK", "XLE", "XLV"]}


def daily(seed, drift):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-02", "2026-10-08", tz="UTC") + pd.Timedelta(hours=4)
    r = rng.normal(drift, 0.004, len(idx))
    c = 100 * np.cumprod(1 + r)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1.0}, index=idx)


def cfg_with(env, rot=ROT, core=CORE):  # noqa: F811
    cfg = yaml.safe_load(env.read_text())
    cfg["live"]["core"] = dict(core)
    cfg["live"]["rotation"] = dict(rot)
    cfg["live"]["risk"]["max_position_usd"] = 1e9
    return cfg


def test_validation_with_core_and_jobs():
    live_cfg = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    live_cfg["core"] = dict(CORE)
    live_cfg["rotation"] = dict(ROT)
    assert validate_live(live_cfg) == []
    live_cfg["rotation"] = {**ROT, "symbols": ["SPY", "XLK"]}                   # SPY belongs to a job
    assert any("also in an enabled job" in e for e in validate_live(live_cfg))
    live_cfg["rotation"] = {**ROT, "allocation_pct": 80}                         # 30% core + 80% > 100%
    assert any("max_gross_pct" in e for e in validate_live(live_cfg))


def test_bot_buys_the_sleeve_once_a_month_and_keeps_it(env, monkeypatch):  # noqa: F811
    bars = live._download()
    d = {"XLK": daily(1, 0.0015), "XLE": daily(2, 0.0010), "XLV": daily(3, -0.0010)}
    monkeypatch.setattr(live, "_download", lambda sym, tf, *a, **k: d[sym] if tf == "1Day" and sym in d else bars)
    tc = CoreTC()
    bot = live.Bot(cfg_with(env), tc=tc)
    bot.step_once()
    st = json.loads((live.LOGS / "status.json").read_text())
    rot = st["rotation"]["target"]
    assert set(rot["weights"]) <= {"XLK", "XLE"} and rot["weights"]                 # XLV falls: never picked
    bought = {o.symbol for o in tc.orders} & {"XLK", "XLE", "XLV"}
    assert bought == set(rot["weights"])
    assert sum(st["sizing"]["usd"].values()) <= 100_000 * (1 - 0.30 - 0.40) + 1   # jobs get what core and sleeve leave
    n = len(tc.orders)
    bot.step_once()                                                                  # same month: no new sleeve orders
    assert [o for o in tc.orders[n:] if o.symbol in {"XLK", "XLE", "XLV"}] == []
    decs = [json.loads(l) for l in (live.LOGS / "decisions.jsonl").read_text().splitlines()]
    assert any(x.get("job") == "rotation" and x["reason"].startswith("Rotation:") for x in decs)


def test_flatten_and_loss_limit_leave_the_sleeve_alone(env):  # noqa: F811
    tc = CoreTC({"VOO": 60, "XLK": 40, "SPY": 5}, equity="97000", last_equity="100000", core_pl=-1000)
    bot = live.Bot(cfg_with(env), tc=tc)
    bot.flatten_all("test")
    assert sorted(tc.closed) == ["SPY"] and "XLK" in tc.pos and "VOO" in tc.pos
    # -3% today, -1,000 of it in VOO (the long-term holdings are left out): the trading jobs lost 2%
    assert bot.trading_pnl_pct() == pytest.approx(-2.0)


def test_switching_the_sleeve_on_is_logged(env):  # noqa: F811
    from tests.test_live import _rewrite
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=CoreTC(), cfg_path=env)
    assert not bot.rotation["enabled"]
    _rewrite(env, lambda l: l.__setitem__("rotation", dict(ROT)))
    bot.reload_config()
    assert bot.rotation["enabled"] and any("rotation.enabled: None -> True" in c["change"] for c in bot.changes)
