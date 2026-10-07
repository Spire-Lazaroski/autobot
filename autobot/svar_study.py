"""
Structural VAR impulse responses: how do prices and volatility react, bar by bar, to a news shock and
to a market shock? Explanation, not a trading rule. Diagnosis only.

    .venv-ml/Scripts/python -m autobot.svar_study          # data/ml/study_svar.json

Part 1, 5-minute bars, per stock (the 7 symbols with enough scored news), regular hours, the overnight
move left out. Variables, in this order (recursive / Cholesky identification: a variable can react in
the same bar to the ones before it, not to the ones after it):
  news   stories about the stock received in the bar (main or secondary relevance, keyword-scored),
         signed by their tone (sentiment sum)
  spy    SPY's return in the bar, bps (the market shock; left out for SPY and QQQ themselves)
  ret    the stock's return in the bar, bps
  vol    the bar's absolute return divided by its normal size at that time of day (20 earlier sessions)
Lags: 12 bars (1 hour). Responses to a one-standard-deviation shock over 24 bars (2 hours), cumulative
for the return, with 95% ranges (asymptotic). Caveat: news services also write stories ABOUT moves
("stock X falls 5%"), so news is not purely the cause; a robustness run orders news after the return.
Trading reading: what matters is how much of a response comes AFTER the bar of the shock (that part
could be traded); if it is all in the first bar, the price has already adjusted.

Part 2, daily (FRED, 10 years): the economy and the market. Variables in order: change in the yield
curve (T10Y3M), S&P 500 return, change in the VIX. Lags 5, responses over 20 days.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import ml_features as mf

NY = mf.NY
OUT = mf.OUT_DIR / "study_svar.json"
LAGS, H = 12, 24
NEWS_SYMBOLS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "META", "AMD"]
BAR = pd.Timedelta(minutes=5)


def news_per_bar(index: pd.DatetimeIndex, news: pd.DataFrame) -> pd.DataFrame:
    """Stories received during each bar (after its start, by its end), count and signed tone."""
    out = pd.DataFrame({"count": 0.0, "tone": 0.0}, index=index)
    if news is None or news.empty:
        return out
    n = news[news.relevance != "mention"]
    t = n.t_info.to_numpy(dtype="datetime64[ns]").astype("int64")
    starts = index.to_numpy(dtype="datetime64[ns]").astype("int64")
    pos = np.searchsorted(starts, t, side="left") - 1             # the bar whose (start, end] holds t
    ok = (pos >= 0) & (t <= starts[np.clip(pos, 0, len(starts) - 1)] + BAR.value)
    g = pd.DataFrame({"pos": pos[ok], "tone": n.sentiment.fillna(0).to_numpy(float)[ok]}).groupby("pos")
    out.iloc[g.size().index, 0] = g.size().to_numpy()
    out.iloc[g.tone.sum().index, 1] = g.tone.sum().to_numpy()
    return out


def frame(df: pd.DataFrame, spy: Optional[pd.DataFrame], news: Optional[pd.DataFrame], news_first: bool = True) -> pd.DataFrame:
    ny = df.index.tz_convert(NY)
    day = np.asarray(ny.date)
    first = np.r_[True, day[1:] != day[:-1]]
    r = (df.close.pct_change() * 1e4).where(~first, 0.0).fillna(0.0)
    slot = ny.hour * 60 + ny.minute
    a = pd.DataFrame({"a": r.abs().to_numpy(), "slot": slot, "day": day})
    norm = a.pivot_table(index="day", columns="slot", values="a").rolling(20, min_periods=5).mean().shift(1)
    nv = norm.stack().reindex(pd.MultiIndex.from_arrays([day, slot])).to_numpy()
    out = pd.DataFrame(index=df.index)
    out["news"] = news_per_bar(df.index, news).tone.to_numpy()
    if spy is not None:
        s = spy.close.reindex(df.index)
        out["spy"] = (s.pct_change() * 1e4).where(~first, 0.0).fillna(0.0).to_numpy()
    out["ret"] = r.to_numpy()
    out["vol"] = r.abs().to_numpy() / np.where(nv > 0, nv, np.nan)
    out = out.dropna()
    if not news_first:
        out = out[[c for c in out.columns if c != "news"] + ["news"]]
    return out


def irf_summary(X: pd.DataFrame, lags: int = LAGS, h: int = H) -> Dict:
    from statsmodels.tsa.api import VAR
    res = VAR(X.reset_index(drop=True)).fit(lags)
    irf = res.irf(h)
    names = list(X.columns)
    cum, cse = irf.orth_cum_effects, irf.cum_effect_stderr(orth=True)
    resp, rse = irf.orth_irfs, irf.stderr(orth=True)
    i_ret, i_vol = names.index("ret"), names.index("vol")
    out = {"rows": int(len(X)), "lags": lags, "shocks": {}}
    for shock in [c for c in names if c in ("news", "spy", "ret")]:
        j = names.index(shock)

        def path(m, se, i):
            return {"same_bar": [round(float(m[0, i, j]), 3), round(float(1.96 * se[0, i, j]), 3)],
                    "after_1h": [round(float(m[min(12, h), i, j]), 3), round(float(1.96 * se[min(12, h), i, j]), 3)],
                    "after_2h": [round(float(m[h, i, j]), 3), round(float(1.96 * se[h, i, j]), 3)]}
        out["shocks"][shock] = {
            "shock_size": round(float(np.sqrt(np.asarray(res.sigma_u)[j, j])), 4),
            "price_cum_bps": path(cum, cse, i_ret),
            "price_after_first_bar_bps": round(float(cum[h, i_ret, j] - cum[0, i_ret, j]), 3),
            "vol_x_normal": path(resp, rse, i_vol)}
    try:
        g = res.test_causality("ret", ["news"], kind="f") if "news" in names else None
        out["news_predicts_return_p"] = round(float(g.pvalue), 4) if g else None
        g2 = res.test_causality("vol", ["news"], kind="f") if "news" in names else None
        out["news_predicts_vol_p"] = round(float(g2.pvalue), 4) if g2 else None
    except Exception:  # noqa: BLE001
        pass
    return out


def daily_macro() -> Dict:
    from statsmodels.tsa.api import VAR
    from .macro_study import DIR
    def fred(n):
        f = pd.read_csv(DIR / f"{n}.csv")
        s = pd.to_numeric(f.iloc[:, 1], errors="coerce")
        s.index = pd.to_datetime(f.iloc[:, 0])
        return s.dropna()
    sp, vix, curve = fred("SP500"), fred("VIXCLS"), fred("T10Y3M")
    X = pd.DataFrame({"curve": curve.diff() * 100, "sp500": sp.pct_change() * 1e4, "vix": vix.diff()}).dropna()
    res = VAR(X.reset_index(drop=True)).fit(5)
    irf = res.irf(20)
    cum, cse = irf.orth_cum_effects, irf.cum_effect_stderr(orth=True)
    names = list(X.columns)

    def eff(shock, resp, d):
        i, j = names.index(resp), names.index(shock)
        return [round(float(cum[d, i, j]), 3), round(float(1.96 * cse[d, i, j]), 3)]
    return {"rows": int(len(X)), "from": str(X.index[0].date()), "to": str(X.index[-1].date()),
            "shock_sizes": {n: round(float(np.sqrt(np.asarray(res.sigma_u)[k, k])), 3) for k, n in enumerate(names)},
            "sp500_bps_after_curve_shock": {"day0": eff("curve", "sp500", 0), "day5": eff("curve", "sp500", 5), "day20": eff("curve", "sp500", 20)},
            "vix_after_sp500_shock": {"day0": eff("sp500", "vix", 0), "day5": eff("sp500", "vix", 5), "day20": eff("sp500", "vix", 20)},
            "sp500_bps_after_vix_shock": {"day0": eff("vix", "sp500", 0), "day5": eff("vix", "sp500", 5), "day20": eff("vix", "sp500", 20)}}


def run(progress=print) -> Dict:
    from .event_study import news_events
    syms = NEWS_SYMBOLS
    data = cs.Data(syms)
    ev = news_events(with_first=False)
    spy = data.df5["SPY"]
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of),
           "lags": LAGS, "horizon_bars": H, "per_symbol": {}, "news_last": {}}
    for s in syms:
        nw = ev[ev.symbol == s]
        m = None if s in ("SPY", "QQQ") else spy
        res["per_symbol"][s] = irf_summary(frame(data.df5[s], m, nw))
        res["news_last"][s] = irf_summary(frame(data.df5[s], m, nw, news_first=False))["shocks"].get("news")
        n = res["per_symbol"][s]["shocks"]["news"]
        progress(f"{s}: news shock -> price {n['price_cum_bps']['same_bar'][0]:+.2f} bps same bar, "
                 f"{n['price_cum_bps']['after_2h'][0]:+.2f} after 2h; vol x{n['vol_x_normal']['same_bar'][0]:+.3f}")
    res["daily_macro"] = daily_macro()
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    out = [f"Structural VAR, 5-minute bars, {res['lags']} lags, responses to a 1-sd shock (cumulative price in bps, ± 95%)."]
    for s, r in res["per_symbol"].items():
        n = r["shocks"]["news"]
        p = n["price_cum_bps"]
        line = (f"  {s:<5} news (tone, 1 sd = {n['shock_size']}): price same bar {p['same_bar'][0]:+.2f}±{p['same_bar'][1]:.2f}, "
                f"after 1h {p['after_1h'][0]:+.2f}±{p['after_1h'][1]:.2f}, after 2h {p['after_2h'][0]:+.2f}±{p['after_2h'][1]:.2f} "
                f"(after the first bar {n['price_after_first_bar_bps']:+.2f}); vol same bar x{n['vol_x_normal']['same_bar'][0]:+.3f}; "
                f"news predicts return p={r.get('news_predicts_return_p')}, vol p={r.get('news_predicts_vol_p')}")
        out.append(line)
        if "spy" in r["shocks"]:
            m = r["shocks"]["spy"]["price_cum_bps"]
            out.append(f"        market (SPY 1 sd = {r['shocks']['spy']['shock_size']} bps): price same bar {m['same_bar'][0]:+.2f}, after 2h {m['after_2h'][0]:+.2f}±{m['after_2h'][1]:.2f} "
                       f"(after the first bar {r['shocks']['spy']['price_after_first_bar_bps']:+.2f})")
        nl = res["news_last"].get(s)
        if nl:
            out.append(f"        news ordered last (robustness): price after 2h {nl['price_cum_bps']['after_2h'][0]:+.2f}±{nl['price_cum_bps']['after_2h'][1]:.2f}")
    d = res["daily_macro"]
    out.append(f"Daily {d['from']} to {d['to']} (1 sd: curve {d['shock_sizes']['curve']} bp, S&P {d['shock_sizes']['sp500']} bps, VIX {d['shock_sizes']['vix']} pts):")
    out.append(f"  yield-curve shock -> S&P bps: day 0 {d['sp500_bps_after_curve_shock']['day0']}, day 5 {d['sp500_bps_after_curve_shock']['day5']}, day 20 {d['sp500_bps_after_curve_shock']['day20']}")
    out.append(f"  S&P shock -> VIX points: day 0 {d['vix_after_sp500_shock']['day0']}, day 5 {d['vix_after_sp500_shock']['day5']}, day 20 {d['vix_after_sp500_shock']['day20']}")
    out.append(f"  VIX shock -> S&P bps: day 0 {d['sp500_bps_after_vix_shock']['day0']}, day 5 {d['sp500_bps_after_vix_shock']['day5']}, day 20 {d['sp500_bps_after_vix_shock']['day20']}")
    return out


def main():
    res = run()
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
