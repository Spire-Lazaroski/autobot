"""Core holding in the live bot: validation, once-a-day buy / rebalance, trading jobs sized into
what is left, kept by every flatten unless asked, and left out of the daily loss limit."""
import json
from types import SimpleNamespace

import pytest
import yaml

import live
from autobot import core
from autobot.liveconfig import validate_live
from tests.test_live import BASE, FakeTC, env  # noqa: F401

CORE = {"enabled": True, "symbol": "VOO", "allocation_pct": 30, "rebalance_band_pct": 5}


class CoreTC(FakeTC):
    def __init__(self, positions=None, equity="100000", last_equity="100000", core_pl=0.0):
        super().__init__(positions)
        self.equity, self.last, self.core_pl, self.cancelled = equity, last_equity, core_pl, 0

    def get_account(self):
        return SimpleNamespace(equity=self.equity, last_equity=self.last, account_number="PA-TEST")

    def get_all_positions(self):
        return [SimpleNamespace(symbol=s, qty=str(q), unrealized_intraday_pl=str(self.core_pl if s == "VOO" else 0))
                for s, q in self.pos.items()]

    def cancel_orders(self):
        self.cancelled += 1


def cfg_with_core(env, **core_cfg):  # noqa: F811
    cfg = yaml.safe_load(env.read_text())
    cfg["live"]["core"] = {**CORE, **core_cfg}
    return cfg


def test_validation():
    live_cfg = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    live_cfg["core"] = dict(CORE)
    assert validate_live(live_cfg) == []
    live_cfg["core"] = {**CORE, "symbol": "SPY"}                       # SPY belongs to job 'trend'
    assert any("also in an enabled job" in e for e in validate_live(live_cfg))
    live_cfg["core"] = {**CORE, "allocation_pct": 120, "rebalance_band_pct": 0, "x": 1}
    errs = " | ".join(validate_live(live_cfg))
    assert "allocation_pct" in errs and "rebalance_band_pct" in errs and "unknown settings x" in errs
    live_cfg["core"] = {**CORE, "symbol": "BTC/USD"}
    assert any("no crypto" in e for e in validate_live(live_cfg))


def test_rebalance_rule():
    assert core.rebalance(0, 500, 100_000, CORE, True)["qty"] == 60          # 30% of 100k at 500
    assert core.rebalance(0, 500, 100_000, CORE, False) is None              # only on the day's first tick
    assert core.rebalance(64, 500, 100_000, CORE, True) is None              # 32%: inside the 5-point band
    big = core.rebalance(72, 500, 100_000, CORE, True)                       # 36%: back to 60 shares
    assert big["qty"] == -12 and "above" in big["reason"]
    assert core.rebalance(0, 500, 100_000, {**CORE, "enabled": False}, True) is None


def test_bot_buys_the_core_once_a_day_and_shrinks_the_jobs(env):  # noqa: F811
    cfg = cfg_with_core(env)
    for j in cfg["live"]["jobs"]:
        j["allocation_pct"] = 50                                      # jobs ask 100%, only 70% is left
    cfg["live"]["risk"]["max_position_usd"] = 1e9
    tc = CoreTC()
    bot = live.Bot(cfg, tc=tc)
    bot.step_once()
    voo = [o for o in tc.orders if o.symbol == "VOO"]
    st = json.loads((live.LOGS / "status.json").read_text())
    price = st["core"]["price"]
    assert len(voo) == 1 and float(voo[0].qty) == int(30_000 // price)
    assert sum(st["sizing"]["usd"].values()) == pytest.approx(70_000)
    n = len(tc.orders)
    bot.step_once()                                                   # same day: no second core order
    assert [o for o in tc.orders[n:] if o.symbol == "VOO"] == []
    decs = [json.loads(l) for l in (live.LOGS / "decisions.jsonl").read_text().splitlines()]
    assert any(d.get("job") == "core" and "Core: buying VOO" in d["reason"] for d in decs)


def test_every_flatten_keeps_the_core_unless_asked(env, monkeypatch, tmp_path):  # noqa: F811
    tc = CoreTC({"VOO": 60, "SPY": 5, "AAPL": 3})
    bot = live.Bot(cfg_with_core(env), tc=tc)
    bot.flatten_all("test")
    assert sorted(tc.closed) == ["AAPL", "SPY"] and tc.flattened == 0 and "VOO" in tc.pos
    bot.flatten_all("test", include_core=True)
    assert tc.flattened == 1
    stop = tmp_path / "STOP"
    monkeypatch.setattr(live, "STOP_FILE", stop)
    stop.write_text("")
    assert live.stop_includes_core() is False
    stop.write_text(json.dumps({"include_core": True}))
    assert live.stop_includes_core() is True


def test_daily_loss_limit_ignores_the_core(env):  # noqa: F811
    # account -3% today, but -2.5% of it is the core: the trading jobs lost 0.5%, no halt
    tc = CoreTC({"VOO": 60}, equity="97000", last_equity="100000", core_pl=-2500)
    bot = live.Bot(cfg_with_core(env), tc=tc)
    assert bot.trading_pnl_pct() == pytest.approx(-0.5)
    assert bot.check_risk() is True and not bot.halted
    plain = live.Bot(yaml.safe_load(env.read_text()), tc=CoreTC({"VOO": 60}, equity="97000", last_equity="100000", core_pl=-2500))
    assert plain.check_risk() is False and plain.halted            # no core configured: today's rule


def test_switching_the_core_on_takes_effect_at_the_next_bar(env):  # noqa: F811
    from tests.test_live import _rewrite
    bot = live.Bot(yaml.safe_load(env.read_text()), tc=CoreTC(), cfg_path=env)
    assert not bot.core["enabled"]
    _rewrite(env, lambda l: l.__setitem__("core", dict(CORE)))
    bot.reload_config()
    assert bot.core["enabled"] and any("core.enabled: None -> True" in c["change"] for c in bot.changes)


# ----------------------------------------------------------------- volatility targeting (option B, 2026-10-06)
VOL = {"enabled": True, "min_pct": 15, "max_pct": 45, "lambda": 0.94, "normal_years": 5}


def daily_closes(n=1400, calm_sd=0.01, last_sd=None, last_n=40, seed=1):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(seed)
    r = rng.normal(0, calm_sd, n)
    if last_sd is not None:
        r[-last_n:] = rng.normal(0, last_sd, last_n)
    idx = pd.bdate_range("2021-01-04", periods=n, tz="America/New_York")
    return pd.Series(100 * np.cumprod(1 + r), index=idx)


def test_vol_target_rule():
    cfg = {**CORE, "vol_target": VOL}
    normal = core.vol_target(daily_closes(), cfg)
    assert 20 <= normal["target_pct"] <= 40 and 0.67 < normal["ratio"] < 1.5      # ordinary days: near the normal 30%
    rough = core.vol_target(daily_closes(last_sd=0.03), cfg)            # 3x as jumpy lately: smaller core, at the floor
    assert rough["target_pct"] == 15 and "jumpier than normal" in rough["reason"]
    calm = core.vol_target(daily_closes(last_sd=0.004), cfg)            # much calmer: bigger core, at the ceiling
    assert calm["target_pct"] == 45 and "calmer than normal" in calm["reason"]
    assert core.vol_target(daily_closes(n=100), cfg) is None            # too little history: plain 30% applies
    c = daily_closes()
    assert core.vol_target(c.iloc[:-1], cfg) == core.vol_target(c.iloc[:-1].copy(), cfg)   # uses only what it is given


def test_vol_target_validation():
    live_cfg = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    live_cfg["core"] = {**CORE, "vol_target": VOL}
    assert validate_live(live_cfg) == []
    live_cfg["core"] = {**CORE, "vol_target": {**VOL, "min_pct": 40, "max_pct": 20, "lambda": 1.0, "normal_years": 0, "x": 1}}
    errs = " | ".join(validate_live(live_cfg))
    assert "min_pct and max_pct" in errs and "lambda" in errs and "normal_years" in errs and "unknown settings x" in errs
    live_cfg["core"] = {**CORE, "allocation_pct": 50, "vol_target": VOL}
    assert any("must lie between" in e for e in validate_live(live_cfg))


def test_rebalance_uses_the_days_target():
    o = core.rebalance(0, 500, 100_000, CORE, True, target_pct=20, why_target="markets are jumpier than normal")
    assert o["qty"] == 40 and "up to 20% of the account" in o["reason"] and "jumpier" in o["reason"]
    assert core.rebalance(60, 500, 100_000, CORE, True, target_pct=27) is None          # 30% held vs 27%: inside the band
    o = core.rebalance(60, 500, 100_000, CORE, True, target_pct=15)                     # 30% held vs 15%: sell to 15%
    assert o["qty"] == -30 and "above its 15% target" in o["reason"]


def test_bot_sizes_the_core_by_the_days_volatility(env, monkeypatch):  # noqa: F811
    import pandas as pd
    calm_bars = live._download()
    closes = daily_closes(last_sd=0.03)                                 # markets jumpy: target at the 15% floor
    daily = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1.0})
    daily.index = daily.index.tz_convert("UTC")
    monkeypatch.setattr(live, "_download", lambda sym, tf, *a, **k: daily if tf == "1Day" else calm_bars)
    cfg = cfg_with_core(env, vol_target=VOL)
    cfg["live"]["risk"]["max_position_usd"] = 1e9
    tc = CoreTC()
    bot = live.Bot(cfg, tc=tc)
    bot.step_once()
    st = json.loads((live.LOGS / "status.json").read_text())
    voo = [o for o in tc.orders if o.symbol == "VOO"]
    assert st["core"]["vol_target"]["target_pct"] == 15
    assert len(voo) == 1 and float(voo[0].qty) == int(15_000 // st["core"]["price"])
    decs = [json.loads(l) for l in (live.LOGS / "decisions.jsonl").read_text().splitlines()]
    assert any(d.get("job") == "core" and "jumpier than normal" in d["reason"] for d in decs)


def test_no_daily_history_falls_back_to_the_plain_share(env, monkeypatch):  # noqa: F811
    bars = live._download()
    def dl(sym, tf, *a, **k):
        if tf == "1Day":
            raise RuntimeError("no data")
        return bars
    monkeypatch.setattr(live, "_download", dl)
    cfg = cfg_with_core(env, vol_target=VOL)
    cfg["live"]["risk"]["max_position_usd"] = 1e9
    tc = CoreTC()
    live.Bot(cfg, tc=tc).step_once()
    st = json.loads((live.LOGS / "status.json").read_text())
    voo = [o for o in tc.orders if o.symbol == "VOO"]
    assert "vol_target" not in st["core"] and float(voo[0].qty) == int(30_000 // st["core"]["price"])
