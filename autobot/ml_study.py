"""
Machine-learning study, phase 2: gradient-boosted trees (LightGBM) on the three questions.
Diagnosis only: nothing here changes the bot.

    .venv-ml/Scripts/python -m autobot.ml_study          # data/ml/study_trees.json

Inputs: autobot.ml_features (built first: python -m autobot.ml_features). One model is trained on
all symbols together (more examples; every input is a ratio, so symbols are comparable).

Targets, both inside the same session (nothing crosses the close):
  fwd_ret    return from this bar's close to 12 bars later (1 hour), or to the 15:55 close-out if sooner
  fwd_swing  highest high minus lowest low over the next 12 bars (or to the close-out), % of price

Walk-forward: sessions 1-60 only warm up the Markov input; from session 120 on, every 20 sessions
the models are refit on ALL earlier sessions and predict the next 20 (never seen in training).
Model settings are fixed before the run (no tuning on results).

The three questions:
  1. exit   for each strategy the bot can run (defaults + live jobs, long only, no quick re-entry):
            sell early when the return model predicts the next hour below -EXIT_BPS; back in only
            after the strategy itself goes flat and signals again. Paired per day vs the strategy alone.
  2. swing  forecast accuracy of fwd_swing vs a simple rule (the average swing at this time of day
            over the previous 20 sessions x today's volatility vs normal); error and correlation.
  3. entry  a standalone strategy: buy when the predicted next hour beats ENTRY_BPS (above costs),
            sell when the prediction turns negative, flat before the close; vs cash and vs holding
            from the open, on the same days, after the same costs.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import cost_study as cs
from . import ml_features as mf
from . import recalibrate as rc
from . import trade_rules as tr
from .short_tp_study import _account, _paired
from .strategies import STRATEGIES, get_signal

NY = mf.NY
OUT = mf.OUT_DIR / "study_trees.json"
H = 12                      # horizon in bars (1 hour)
CLOSEOUT_MIN = 950          # the bar starting 15:50 ends at the 15:55 close-out
START, STEP = 120, 20       # first test session, refit every 20 sessions
ENTRY_BPS, EXIT_BPS = 5.0, 5.0
RULES = {"no_quick_reentry": True}
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=500, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, n_jobs=2)


# ----------------------------------------------------------------- targets (future: only for training and scoring)
def targets(df: pd.DataFrame, h: int = H) -> pd.DataFrame:
    ny = df.index.tz_convert(NY)
    day = np.asarray(ny.date)
    m = np.asarray(ny.hour * 60 + ny.minute)
    c, hi, lo = df.close.to_numpy(float), df.high.to_numpy(float), df.low.to_numpy(float)
    n = len(df)
    ret, swing = np.full(n, np.nan), np.full(n, np.nan)
    starts = np.flatnonzero(np.r_[True, day[1:] != day[:-1]])
    ends = np.r_[starts[1:], n]
    for a, b in zip(starts, ends):
        last = a + int(np.searchsorted(m[a:b], CLOSEOUT_MIN, side="right")) - 1   # the close-out bar
        for i in range(a, last):
            j = min(i + h, last)
            ret[i] = (c[j] / c[i] - 1) * 100
            swing[i] = (hi[i + 1:j + 1].max() - lo[i + 1:j + 1].min()) / c[i] * 100
    return pd.DataFrame({"fwd_ret": ret, "fwd_swing": swing}, index=df.index)


def naive_swing(f: pd.DataFrame, y: pd.Series, sessions: int = 20) -> pd.Series:
    """The simple rule: average swing at this time of day over the previous `sessions` sessions,
    scaled by today's average bar range vs its own 20-session normal. Uses earlier sessions only
    (their whole hour had finished)."""
    slot = f.minute.to_numpy()
    tab = pd.DataFrame({"y": y.to_numpy(), "slot": slot, "day": f.day.to_numpy()}).pivot_table(index="day", columns="slot", values="y")
    base = tab.rolling(sessions, min_periods=5).mean().shift(1)
    key = pd.MultiIndex.from_arrays([f.day.to_numpy(), slot])
    b = base.stack().reindex(key).to_numpy()
    atr = f.atr_pct
    norm = atr.groupby(f.day.to_numpy()).mean().rolling(sessions, min_periods=5).mean().shift(1)   # earlier sessions
    return pd.Series(b * (atr.to_numpy() / norm.reindex(f.day.to_numpy()).to_numpy()), index=f.index)


# ----------------------------------------------------------------- data
def panel(symbols: Optional[List[str]] = None):
    syms, data, _ = mf.load(symbols)
    parts = []
    for s in syms:
        f = pd.read_pickle(mf.OUT_DIR / f"features_{s}.pkl.gz")
        t = targets(data.df5[s]).reindex(f.index)
        p = f.join(t)
        p["naive_swing"] = naive_swing(f, t.fwd_swing)
        p["symbol"] = s
        parts.append(p)
    P = pd.concat(parts).rename_axis("t").reset_index()     # one row per symbol x bar, plain row numbers
    return syms, data, P


def windows(days: List) -> List[tuple]:
    return [(days[k], days[min(k + STEP, len(days)) - 1]) for k in range(START, len(days), STEP)]


def walk_predict(P: pd.DataFrame, target: str, cols: List[str], progress=print) -> pd.Series:
    import lightgbm as lgb
    days = sorted(P.day.unique())
    pred = np.full(len(P), np.nan)
    day = P.day.to_numpy()
    y = P[target].to_numpy()
    X = P[cols].to_numpy(dtype=np.float32)
    for k, (a, b) in enumerate(windows(days)):
        tr_ = (day < a) & np.isfinite(y)
        te = (day >= a) & (day <= b)
        mdl = lgb.LGBMRegressor(**PARAMS, random_state=k).fit(X[tr_], y[tr_])
        pred[te] = mdl.predict(X[te])
        if progress and k % 5 == 0:
            progress(f"  {target}: window {k + 1}, test from {a}, {tr_.sum():,} training rows")
    return pd.Series(pred, index=P.index)


def importance(P: pd.DataFrame, target: str, cols: List[str]) -> Dict[str, float]:
    """Which inputs the model leans on most (gain share), from one fit on every session before the
    last window (for the write-up only; scoring always uses the walk-forward predictions)."""
    import lightgbm as lgb
    a = windows(sorted(P.day.unique()))[-1][0]
    m = ((P.day < a) & P[target].notna()).to_numpy()
    mdl = lgb.LGBMRegressor(**PARAMS, importance_type="gain").fit(P[cols].to_numpy(np.float32)[m], P[target].to_numpy()[m])
    g = pd.Series(mdl.feature_importances_, index=cols)
    return (g / g.sum() * 100).round(1).sort_values(ascending=False).head(10).to_dict()


# ----------------------------------------------------------------- scoring
def forecast_scores(y: pd.Series, p: pd.Series, day: pd.Series) -> Dict:
    m = (y.notna() & p.notna()).to_numpy()
    y, p, day = y[m], p[m], day[m]
    ic = pd.DataFrame({"y": y.to_numpy(), "p": p.to_numpy(), "d": day.to_numpy()}).groupby("d").apply(
        lambda g: g.y.corr(g.p, method="spearman") if len(g) > 5 else np.nan, include_groups=False).dropna()
    se = ic.std(ddof=1) / math.sqrt(len(ic))
    return {"rows": int(m.sum()), "mae": round(float((y - p).abs().mean()), 4),
            "corr": round(float(np.corrcoef(y, p)[0, 1]), 4),
            "daily_rank_corr": round(float(ic.mean()), 4), "daily_rank_corr_ci95": [round(float(ic.mean() - 1.96 * se), 4), round(float(ic.mean() + 1.96 * se), 4)],
            "sign_right_%": round(float((np.sign(y) == np.sign(p)).mean() * 100), 2)}


def entry_signal(pred: pd.Series, test: np.ndarray) -> pd.Series:
    """Long when the predicted next hour > ENTRY_BPS, out when it turns negative (test days only)."""
    p = pred.to_numpy() * 100            # % -> bps
    out = np.zeros(len(p))
    pos = 0.0
    for i in range(len(p)):
        if not test[i] or not np.isfinite(p[i]):
            pos = 0.0
        elif pos == 0 and p[i] > ENTRY_BPS:
            pos = 1.0
        elif pos > 0 and p[i] < 0:
            pos = 0.0
        out[i] = pos
    return pd.Series(out, index=pred.index)


def exit_overlay(base: pd.Series, pred: pd.Series) -> pd.Series:
    """Sell a long early when the predicted next hour < -EXIT_BPS; stay out until the strategy
    itself goes flat (then follow it again)."""
    b, p = base.to_numpy(), pred.to_numpy() * 100
    out = b.copy().astype(float)
    blocked = False
    for i in range(len(b)):
        if b[i] <= 0:
            blocked = False
        elif blocked or (np.isfinite(p[i]) and p[i] < -EXIT_BPS):
            blocked = True
            out[i] = 0.0
    return pd.Series(out, index=base.index)


def _daily_on(frame: pd.DataFrame, days: set) -> pd.Series:
    d = cs._daily(frame.ret)
    return d[d.index.isin(days)]


def trade_tests(P: pd.DataFrame, data, cfg: Dict, syms: List[str], test_days: set, col: str = "pred_ret", progress=print):
    """1. exit and 3. entry, replayed like the bot with costs, from the return predictions in P[col]."""
    r = rc.settings(cfg)
    fm = int(r["flatten_min"]) or 5
    entry_rows, exit_rows = [], []
    for s in syms:
        df = data.bars(s, "5Min")
        pred = P[P.symbol == s].set_index("t")[col].reindex(df.index)
        test = np.isin(np.asarray(df.index.tz_convert(NY).date), list(test_days))
        costs = rc.costs_for(s, r)
        sim = lambda sig: cs.sim(df, data.cal[s], "ml", {}, costs, "5Min", fm, signal=sig)["frame"]  # noqa: E731
        f_ml = sim(entry_signal(pred, test))
        f_hold = sim(pd.Series(test.astype(float), index=df.index))
        ml_d, hold_d = _daily_on(f_ml, test_days), _daily_on(f_hold, test_days)
        entry_rows.append({"symbol": s, "test_days": [str(d) for d in ml_d.index], "ml": ml_d.tolist(), "hold": hold_d.reindex(ml_d.index).tolist(),
                           "cash": [0.0] * len(ml_d), "trades": int(len(cs.round_trips(f_ml[test])))})
        pairs = [(n, STRATEGIES[n].defaults, "default") for n in cs.strategies_for("5Min", r)]
        pairs += [(j["strategy"], j.get("params") or {}, f"live job {j['name']}") for j in cfg["live"].get("jobs") or []
                  if j.get("enabled", True) and j["symbols"][0] == s]
        for name, params, kind in pairs:
            try:
                base = tr.apply(get_signal(name, df, params, False), df, RULES)
            except Exception:  # noqa: BLE001
                continue
            f_b, f_o = sim(base), sim(exit_overlay(base, pred))
            b_d, o_d = _daily_on(f_b, test_days), _daily_on(f_o, test_days)
            exit_rows.append({"symbol": s, "strategy": name, "family": STRATEGIES[name].family, "kind": kind,
                              "test_days": [str(d) for d in b_d.index], "none": b_d.tolist(), "wf": o_d.reindex(b_d.index).fillna(0).tolist(),
                              "trades_base": int(len(cs.round_trips(f_b[test]))), "trades_ml": int(len(cs.round_trips(f_o[test])))})
        progress(f"{s}: entry {((1 + ml_d).prod() - 1) * 100:+.2f}% vs hold {((1 + hold_d).prod() - 1) * 100:+.2f}%, {len(pairs)} strategies for the exit test")

    def tot(x):
        return round(float(((1 + x).prod() - 1) * 100), 2)

    ml_a, hold_a = _account(entry_rows, "ml"), _account(entry_rows, "hold")
    entry_pair = [_paired(pd.Series(e["ml"]), pd.Series(e["cash"])) for e in entry_rows]
    entry = {"test_days": len(ml_a), "ml_test_return_%": tot(ml_a), "hold_open_to_close_%": tot(hold_a),
             "ml_vs_cash": _paired(ml_a, ml_a * 0), "ml_vs_hold": _paired(ml_a, hold_a),
             "symbols_clearly_above_cash": sum(p["significant"] and p["mean_bps"] > 0 for p in entry_pair),
             "symbols_clearly_below_cash": sum(p["significant"] and p["mean_bps"] < 0 for p in entry_pair),
             "trades": sum(e["trades"] for e in entry_rows),
             "per_symbol": {e["symbol"]: tot(pd.Series(e["ml"])) for e in entry_rows}}
    o_a, b_a = _account(exit_rows, "wf"), _account(exit_rows, "none")
    ex_pair = [_paired(pd.Series(e["wf"]), pd.Series(e["none"])) for e in exit_rows]
    exit_ = {"pairs": len(exit_rows), "test_days": len(b_a), "strategies_test_return_%": tot(b_a), "with_ml_exit_%": tot(o_a),
             "ml_exit_vs_none": _paired(o_a, b_a),
             "pairs_clearly_better": sum(p["significant"] and p["mean_bps"] > 0 for p in ex_pair),
             "pairs_clearly_worse": sum(p["significant"] and p["mean_bps"] < 0 for p in ex_pair),
             "expected_by_luck": round(0.025 * len(exit_rows), 1),
             "trades_base": sum(e["trades_base"] for e in exit_rows), "trades_ml": sum(e["trades_ml"] for e in exit_rows)}
    return entry, exit_


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms, data, P = panel(symbols)
    cols = mf.COLUMNS
    progress(f"{len(P):,} bars, {len(syms)} symbols, {P.day.nunique()} sessions")
    P["pred_ret"] = walk_predict(P, "fwd_ret", cols, progress)
    P["pred_swing"] = walk_predict(P, "fwd_swing", cols, progress)
    days = sorted(P.day.unique())
    test_days = {d for a, b in windows(days) for d in days if a <= d <= b}
    T = P[P.day.isin(test_days)]

    # 2. swing: trees vs the simple rule, same rows
    sw = T[T.fwd_swing.notna() & T.naive_swing.notna()]
    swing = {"trees": forecast_scores(sw.fwd_swing, sw.pred_swing, sw.day),
             "simple_rule": forecast_scores(sw.fwd_swing, sw.naive_swing, sw.day),
             "trees_error_lower_%": round(float((1 - (sw.fwd_swing - sw.pred_swing).abs().mean() / (sw.fwd_swing - sw.naive_swing).abs().mean()) * 100), 1),
             "importance": importance(P, "fwd_swing", cols)}
    progress(f"swing: trees MAE {swing['trees']['mae']} vs simple rule {swing['simple_rule']['mae']}")
    ret_scores = forecast_scores(T.fwd_ret, T.pred_ret, T.day)
    ret_scores["importance"] = importance(P, "fwd_ret", cols)

    # 1. exit and 3. entry: replayed like the bot, with costs
    P[["symbol", "t", "pred_ret", "pred_swing"]].to_pickle(mf.OUT_DIR / "pred_trees.pkl.gz")
    entry, exit_ = trade_tests(P, data, cfg, syms, test_days, "pred_ret", progress)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "model": "LightGBM " + json.dumps(PARAMS), "horizon_bars": H, "entry_bps": ENTRY_BPS, "exit_bps": EXIT_BPS,
           "test_sessions": len(test_days), "first_test_day": str(min(test_days)),
           "return_forecast": ret_scores, "swing": swing, "entry": entry, "exit": exit_}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    rf, sw, en, ex = res["return_forecast"], res["swing"], res["entry"], res["exit"]
    ci = lambda p: f"{p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f}, {'clear' if p['significant'] else 'not clear'})"  # noqa: E731
    return [
        f"Gradient-boosted trees, 11 symbols pooled, walk-forward from {res['first_test_day']} ({res['test_sessions']} unseen sessions), after costs.",
        f"Next-hour return forecast: daily rank correlation {rf['daily_rank_corr']:+.4f} ({rf['daily_rank_corr_ci95'][0]:+.4f} to {rf['daily_rank_corr_ci95'][1]:+.4f}), sign right {rf['sign_right_%']}%",
        f"1. exit: strategies alone {ex['strategies_test_return_%']:+.2f}% vs with ML exit {ex['with_ml_exit_%']:+.2f}%; {ci(ex['ml_exit_vs_none'])}; "
        f"pairs better {ex['pairs_clearly_better']} / worse {ex['pairs_clearly_worse']} of {ex['pairs']} (luck ~{ex['expected_by_luck']}); trades {ex['trades_base']} -> {ex['trades_ml']}",
        f"2. swing: trees error {sw['trees']['mae']} vs simple rule {sw['simple_rule']['mae']} ({sw['trees_error_lower_%']:+.1f}% lower); "
        f"correlation {sw['trees']['corr']} vs {sw['simple_rule']['corr']}",
        f"3. entry: ML strategy {en['ml_test_return_%']:+.2f}% vs cash 0 / holding open-to-close {en['hold_open_to_close_%']:+.2f}%; vs cash {ci(en['ml_vs_cash'])}; "
        f"symbols above / below cash {en['symbols_clearly_above_cash']} / {en['symbols_clearly_below_cash']}; {en['trades']} trades",
        "Inputs used most (return): " + ", ".join(f"{k} {v}%" for k, v in list(rf["importance"].items())[:5]),
        "Inputs used most (swing): " + ", ".join(f"{k} {v}%" for k, v in list(sw["importance"].items())[:5]),
    ]


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
