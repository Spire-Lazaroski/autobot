"""Core combo: the filters use only published data and the cut halves the target with a floor."""
import pandas as pd

from autobot import core_combo_study as cc


def test_cpi_counts_only_two_months_later_and_rates_the_next_day():
    idx = pd.bdate_range("2022-01-03", "2022-12-30")
    inf = cc.inflation_rising(idx)
    cpi = cc.fred("CPIAUCSL")
    yoy = cpi / cpi.shift(12) - 1
    may = bool(yoy.loc["2022-03-01"] > yoy.loc["2021-12-01"])          # March 2022's figure ...
    assert bool(inf.loc["2022-05-02"]) == may                           # ... first used on 1 May 2022
    rat = cc.rates_rising(idx)
    assert rat.index.equals(idx)


def test_cut_halves_with_a_floor():
    t = pd.Series([0.40, 0.16, 0.30])
    f = pd.Series([True, True, False])
    assert cc.cut(t, f).tolist() == [0.20, 0.10, 0.30]
