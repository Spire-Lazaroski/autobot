"""ML study phase 4: economy inputs are used only from the day they were public."""
from datetime import date

import pandas as pd

from autobot import ml_macro as mm


def fake_fred(name):
    if name in ("T10Y3M", "SP500"):
        idx = pd.bdate_range("2025-01-01", "2026-03-31")
        return pd.Series(range(len(idx)), index=idx, dtype=float)
    idx = pd.date_range("2024-01-01", "2026-03-01", freq="MS")
    return pd.Series(range(len(idx)), index=idx, dtype=float)


GMD = pd.DataFrame({"infl": [3.0, 4.0, 5.0], "rGDP": [100.0, 102.0, 101.0], "unemp": [4.0, 4.5, 5.0]}, index=[2023, 2024, 2025])


def test_daily_monthly_and_annual_values_only_once_public():
    days = [date(2026, 2, 2), date(2026, 2, 9), date(2026, 3, 9), date(2026, 6, 30), date(2026, 7, 1)]
    e = mm.economy(days, fred=fake_fred, gmd=GMD)
    d = fake_fred("T10Y3M")
    assert e.curve_10y3m.iloc[0] == d[d.index < "2026-02-02"].iloc[-1]          # yesterday's value, not today's
    m = fake_fred("UNRATE")
    jan, dec = m["2026-01-01"], m["2025-12-01"]
    assert e.sahm.iloc[0] == dec and e.sahm.iloc[1] == jan                     # January's figure only from Feb 8
    assert e.gmd_infl.iloc[3] == 4.0 and e.gmd_infl.iloc[4] == 5.0             # 2025's annual figure only from July 2026
    assert round(e.gmd_rgdp_growth.iloc[4], 6) == round((101 / 102 - 1) * 100, 6)
