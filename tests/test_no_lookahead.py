"""
No-look-ahead checks for every registered strategy (base and meta).

If a signal at bar t depends only on bars <= t, then computing it on df[:t+1]
must give the same values as computing it on the full df, up to bar t.
Any use of future data (a negative shift, a centered window, a full-sample
statistic) breaks that equality.
"""
import numpy as np
import pandas as pd
import pytest

from autobot.data import synthetic_bars
from autobot.strategies import STRATEGIES, get_signal


@pytest.fixture(scope="module")
def intraday():
    return synthetic_bars(["AAA"], "15Min", 90, seed=3)["AAA"]


@pytest.fixture(scope="module")
def daily():
    return synthetic_bars(["BBB"], "1Day", 900, seed=5)["BBB"]


def _cuts(n):
    return sorted({n // 3, n // 2, 2 * n // 3, n - 7, n - 1})


def _assert_causal(name, df, allow_short):
    full = get_signal(name, df, allow_short=allow_short)
    for cut in _cuts(len(df)):
        part = get_signal(name, df.iloc[: cut + 1], allow_short=allow_short)
        pd.testing.assert_series_equal(part, full.iloc[: cut + 1], check_names=False,
                                       obj=f"{name} cut={cut} short={allow_short}")


@pytest.mark.parametrize("name", list(STRATEGIES))
@pytest.mark.parametrize("allow_short", [False, True])
def test_intraday_signal_is_causal(name, intraday, allow_short):
    _assert_causal(name, intraday, allow_short)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_daily_signal_is_causal(name, daily):
    _assert_causal(name, daily, False)


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_signal_shape(name, intraday):
    sig = get_signal(name, intraday)
    assert sig.index.equals(intraday.index)
    assert not sig.isna().any()
    assert np.all(np.abs(sig.values) <= 1 + 1e-9)


def test_intraday_strategies_flat_overnight(intraday):
    from autobot.indicators import last_bar_of_session
    eod = last_bar_of_session(intraday)
    for name in ["opening_range_breakout", "intraday_momentum", "noise_area"]:
        sig = get_signal(name, intraday, allow_short=True)
        assert (sig[eod] == 0).all(), name
        assert (sig != 0).any(), f"{name} never trades on the test data"


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_exit_layer_on_top_is_causal(name, intraday):
    """The optional trailing exit / re-entry layer (autobot.exits) on every strategy: truncated
    data must give the same earlier targets."""
    from autobot.exits import apply_exit_layer
    cfg = {"trail_pct": 0.25, "reentry": "both", "cooldown_bars": 3, "no_entry_last_min": 30}
    full = apply_exit_layer(get_signal(name, intraday), intraday, cfg, crypto=False)
    for cut in _cuts(len(intraday)):
        part_df = intraday.iloc[: cut + 1]
        part = apply_exit_layer(get_signal(name, part_df), part_df, cfg, crypto=False)
        pd.testing.assert_series_equal(part, full.iloc[: cut + 1], check_names=False, obj=f"{name}+exit cut={cut}")


@pytest.mark.parametrize("name", ["ema_cross", "rsi_reversion", "donchian_breakout", "regime_switch", "noise_area"])
def test_overnight_policies_are_causal(name):
    """The overnight study (autobot.overnight_study): every hold-or-sell decision, the gap
    accounting and the costs on truncated data match the full run up to the cut. The calendar,
    earnings sessions and story times are inputs known in advance (or up to the decision)."""
    from autobot import overnight_study as ov
    from autobot.backtest import CostModel
    df = synthetic_bars(["AAA"], "5Min", 60, seed=4)["AAA"]  # the live timeframe
    rng = np.random.default_rng(11)
    first = np.r_[True, np.diff(df.index.tz_convert("America/New_York").date.astype("datetime64[D]").astype(int)) != 0]
    df.loc[first, "open"] = df.close.shift(1)[first] * (1 + rng.normal(0, 0.01, first.sum()))  # opening gaps
    df.iloc[0, df.columns.get_loc("open")] = df.close.iloc[0]
    cal = ov.calendar(df)
    days = sorted(set(df.index.tz_convert("America/New_York").date))
    earnings = frozenset(days[5::9])
    news_t = np.sort(df.index[::150].as_unit("ns").asi8 + 7 * 60 * 10**9)
    pols = [p for p in ov.policies(5) if p.name in ("hold", "flatten_15", "if_all", "if_signal_profit_trend", "if_in_profit_x")]

    def run(part):
        inp = ov.build_inputs("AAA", part, name, {}, CostModel(0, 3), cal=cal, news=False)
        inp.earnings, inp.bad_news_t = earnings, news_t
        return {p.name: ov.simulate(inp, p) for p in pols}

    full = run(df)
    for cut in _cuts(len(df)):
        part = run(df.iloc[: cut + 1])
        for p in pols:
            a, b = part[p.name], full[p.name]
            pd.testing.assert_frame_equal(a["frame"], b["frame"].iloc[: cut + 1], obj=f"{name}/{p.name} cut={cut}")
            n = len(a["nights"])
            pd.testing.assert_frame_equal(a["nights"], b["nights"].iloc[:n], obj=f"{name}/{p.name} nights cut={cut}")
