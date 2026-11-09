"""Fractional shares: the bot buys stocks and funds by amount (4 decimals) where Alpaca allows it, never below
Alpaca's $1 minimum, and falls back to whole shares where it can't (or isn't told it may)."""
import json
from types import SimpleNamespace

import yaml

import live
from autobot import core, rotation, sizing
from autobot.liveconfig import validate_live
from tests.test_core_live import CoreTC, cfg_with_core
from tests.test_live import BASE, env  # noqa: F401


def test_shares_and_minimum():
    assert sizing.shares(30, 700, False) == 0.0                         # $30 buys no whole VOO
    assert sizing.shares(30, 700, True) == 0.0428                        # but 0.0428 of one
    assert sizing.shares(1000, 333.33, True) == 3.0000 or sizing.shares(1000, 333.33, True) == 3.0001
    assert sizing.tradable(0.0013, 700, True) is False                   # $0.91: below Alpaca's $1 minimum
    assert sizing.tradable(0.0015, 700, True) is True
    assert sizing.tradable(0.5, 700, False) is False and sizing.tradable(1, 700, False) is True
    assert sizing.to_qty(30, 700, 1, crypto=False, fractional=True) == 0.0428
    assert sizing.to_qty(30, 700, -1, crypto=False, fractional=False) == 0.0


def test_core_on_a_100_dollar_account():
    cfg = {"enabled": True, "symbol": "VOO", "allocation_pct": 30, "rebalance_band_pct": 5}
    assert core.rebalance(0, 700, 100, cfg, True) is None                # whole shares: nothing affordable
    o = core.rebalance(0, 700, 100, cfg, True, fractional=True)
    assert o["qty"] == 0.0428 and o["target"] == 0.0428                  # about $30 of VOO


def test_rotation_by_amount_where_allowed():
    tgt = {"weights": {"XLK": 8.0, "XLE": 8.0}, "reasons": {"XLK": "in the top 5", "XLE": "in the top 5"}}
    cfg = {"enabled": True, "allocation_pct": 40, "top_n": 5, "symbols": ["XLK", "XLE"], "rebalance_band_pct": 1}
    prices = {"XLK": 200.0, "XLE": 65.0}
    whole = rotation.orders({}, prices, 1000, tgt, cfg)
    assert {o["symbol"]: o["qty"] for o in whole} == {"XLE": 1.0}        # $80 buys one XLE, no XLK
    frac = rotation.orders({}, prices, 1000, tgt, cfg, fractional=lambda s: s == "XLK")
    q = {o["symbol"]: o["qty"] for o in frac}
    assert q["XLK"] == 0.4 and q["XLE"] == 1.0                           # XLK by amount, XLE whole (not allowed)
    sell = rotation.orders({"XLK": 0.4}, prices, 1000, {"weights": {}, "reasons": {}}, cfg, fractional=True)
    assert sell[0]["qty"] == -0.4                                         # a fund leaving the top: sold entirely


class FracTC(CoreTC):
    def get_asset(self, sym):
        return SimpleNamespace(fractionable=sym != "SPCX")


def test_bot_buys_the_core_by_amount_on_a_small_account(env):  # noqa: F811
    cfg = cfg_with_core(env)
    tc = FracTC(equity="100", last_equity="100")
    bot = live.Bot(cfg, tc=tc)
    bot.step_once()
    voo = [o for o in tc.orders if o.symbol == "VOO"]
    price = json.loads((live.LOGS / "status.json").read_text())["core"]["price"]
    assert len(voo) == 1 and 0 < float(voo[0].qty) < 1                    # a fraction of one share
    assert abs(float(voo[0].qty) * price - 30) < 1                        # about 30% of $100


def test_without_permission_the_bot_keeps_whole_shares(env):  # noqa: F811
    bot = live.Bot(cfg_with_core(env), tc=CoreTC(equity="100", last_equity="100"))   # no asset info: whole shares
    bot.step_once()
    assert [o for o in bot.tc.orders if o.symbol == "VOO"] == []
    off = cfg_with_core(env)
    off["live"]["fractional"] = False
    bot = live.Bot(off, tc=FracTC(equity="100", last_equity="100"))
    assert bot.frac_ok("VOO") is False


def test_setting_is_validated_and_logged():
    lv = yaml.safe_load(yaml.safe_dump(BASE))["live"]
    assert validate_live({**lv, "fractional": False}) == []
    assert any("fractional" in e for e in validate_live({**lv, "fractional": "yes"}))
    assert any("fractional" in e for e in live.config_diff(lv, {**lv, "fractional": False}))
