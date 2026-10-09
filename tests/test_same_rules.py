"""The Lab and recalibration use the live bot's signal rules (live.trade_rules), so backtests describe the
bot as it actually trades."""
import pandas as pd

from autobot import backtest as bt
from autobot import trade_rules
from autobot.data import synthetic_bars

RULES = {"no_quick_reentry": True}


def test_ruled_signal_matches_the_live_rule_and_bare_without_rules():
    df = synthetic_bars(["X"], "5Min", 30, seed=3)["X"]
    raw = bt.get_signal("ema_cross", df, {"fast": 5, "slow": 20}, False)
    assert bt.ruled_signal("ema_cross", df, {"fast": 5, "slow": 20}, False, None).equals(raw)
    ruled = bt.ruled_signal("ema_cross", df, {"fast": 5, "slow": 20}, False, RULES)
    pd.testing.assert_series_equal(ruled, trade_rules.apply(raw, df, RULES))


def test_compare_and_search_use_the_rules():
    data = synthetic_bars(["X", "Y"], "5Min", 30, seed=4)
    a = bt.compare_strategies(data, {"ema_cross": {"fast": 5, "slow": 20}})[2]
    b = bt.compare_strategies(data, {"ema_cross": {"fast": 5, "slow": 20}}, rules=RULES)[2]
    assert b.trades.sum() <= a.trades.sum()


def test_recalibration_scores_with_the_live_rules():
    from autobot import recalibrate as rc
    cfg = {"live": {"timeframe": "5Min", "risk": {}, "trade_rules": RULES}}
    assert rc.settings(cfg)["trade_rules"] == RULES


def test_api_reads_the_rules_from_config(tmp_path, monkeypatch):
    import yaml
    from api import main
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump({"live": {"trade_rules": RULES}}))
    monkeypatch.setattr(main, "CONFIG_PATH", p)
    assert main.live_rules() == RULES and main.trade_rules_in_use() == {"trade_rules": RULES}
