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
