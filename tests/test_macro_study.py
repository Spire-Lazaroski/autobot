"""Long-history study: annual figures are used only once published, the monthly replay holds
last month's decision, and Shiller's dates parse (1871.1 is October)."""
import numpy as np
import pandas as pd
import pytest

from autobot import macro_study as ms


def test_annual_figures_wait_for_publication(monkeypatch):
    g = pd.DataFrame({"rGDP": [100.0, 90.0, 95.0, 99.0], "infl": [1.0, 9.0, 2.0, 2.0], "unemp": [4.0, 4.0, 6.0, 5.0],
                      "strate": [3.0] * 4, "ltrate": [4.0] * 4, "BankingCrisis": [0, 0, 1, 0]}, index=[2000, 2001, 2002, 2003])
    monkeypatch.setattr(ms, "gmd", lambda: g)
    idx = pd.period_range("2002-01", "2004-12", freq="M")
    r = ms.gmd_rules(idx)
    gdp = r["GMD: real GDP fell"]
    assert np.isnan(gdp[pd.Period("2002-06", "M")])       # June 2002: newest is 2000, which has no growth figure
    assert gdp[pd.Period("2002-07", "M")] == 0.0          # 2001's fall acts from July 2002 ...
    assert gdp[pd.Period("2003-06", "M")] == 0.0          # ... for 12 months
    assert gdp[pd.Period("2003-07", "M")] == 1.0
    assert r["GMD: banking crisis"][pd.Period("2003-07", "M")] == 0.0


def test_replay_holds_last_months_decision_and_pays_switches():
    idx = pd.period_range("2000-01", periods=4, freq="M")
    df = pd.DataFrame({"ret": [0.0, 0.10, -0.05, 0.02], "cash": [0.001] * 4}, index=idx)
    f = ms.replay(df, pd.Series([1.0, 0.0, 0.0, 1.0], index=idx))
    assert f.ret.iloc[0] == pytest.approx(0.10)                       # Feb: January's decision (in)
    assert f.ret.iloc[1] == pytest.approx(0.001 - ms.SWITCH_COST)     # Mar: out, cash, one switch
    assert f.ret.iloc[2] == pytest.approx(0.001)                      # Apr: still out


def test_shiller_dates_parse_october_correctly():
    x = ms.shiller()
    assert x.index[0] == pd.Period("1871-01", "M") and pd.Period("1871-10", "M") in x.index
    assert x.index.is_monotonic_increasing and not x.index.duplicated().any()
