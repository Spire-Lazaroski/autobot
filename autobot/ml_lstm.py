"""
Machine-learning study, phase 3: an LSTM (a network that reads the last 2 hours of bars in order)
on the same three questions, days and costs as phase 2 (autobot.ml_study). Diagnosis only.

    .venv-ml/Scripts/python -m autobot.ml_lstm           # data/ml/study_lstm.json (uses the graphics card if present)

Input: the 35 per-bar inputs of the last SEQ bars (24 = 2 hours, may reach into the previous
session, which had ended). Output: two numbers per bar, the next hour's return and swing (one
network learns both). Each walk-forward window (same as phase 2) trains a fresh network on all
earlier sessions: inputs are scaled with the training rows' averages only; the last 20 training
sessions are held back to pick the number of passes (at most MAX_EPOCHS); then it predicts the
next 20 sessions. Settings are fixed before the run.

Also added for the swing: a straight-line forecast (linear regression on the same 35 inputs, same
windows), so the trees' gain over the simple time-of-day rule is not overstated.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import ml_features as mf
from . import ml_study as ms

OUT = mf.OUT_DIR / "study_lstm.json"
SEQ, HIDDEN, DROPOUT, BATCH, LR, MAX_EPOCHS, VAL_SESSIONS = 24, 32, 0.2, 1024, 1e-3, 6, 20
TARGETS = ["fwd_ret", "fwd_swing"]


def seq_valid(P: pd.DataFrame, seq: int = SEQ) -> np.ndarray:
    """Rows with a full history of `seq` bars of the same symbol (P is ordered by symbol, then time)."""
    pos = P.groupby("symbol", sort=False).cumcount().to_numpy()
    return pos >= seq - 1


def scaler(X: np.ndarray, rows: np.ndarray):
    mu = np.nanmean(X[rows], axis=0)
    sd = np.nanstd(X[rows], axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return mu, sd


def scaled(X: np.ndarray, mu, sd) -> np.ndarray:
    Z = np.clip((X - mu) / sd, -5, 5)
    return np.nan_to_num(Z, nan=0.0).astype(np.float32)


def linear_predict(P: pd.DataFrame, target: str, cols: List[str], progress=print) -> pd.Series:
    """Straight-line forecast, walk-forward on the same windows as the trees."""
    X = P[cols].to_numpy(np.float64)
    y = P[target].to_numpy(float)
    day = P.day.to_numpy()
    out = np.full(len(P), np.nan)
    for a, b in ms.windows(sorted(P.day.unique())):
        tr = (day < a) & np.isfinite(y)
        te = (day >= a) & (day <= b)
        mu, sd = scaler(X, tr)
        A = np.c_[np.ones(tr.sum()), scaled(X[tr], mu, sd)]
        coef = np.linalg.lstsq(A, y[tr], rcond=None)[0]
        out[te] = np.c_[np.ones(te.sum()), scaled(X[te], mu, sd)] @ coef
    return pd.Series(out, index=P.index)


def lstm_predict(P: pd.DataFrame, cols: List[str], progress=print) -> pd.DataFrame:
    import torch
    from torch import nn

    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    progress(f"LSTM on {dev} ({torch.cuda.get_device_name(0) if dev.type == 'cuda' else 'processor'})")

    class Net(nn.Module):
        def __init__(self, n_in):
            super().__init__()
            self.lstm = nn.LSTM(n_in, HIDDEN, batch_first=True)
            self.drop = nn.Dropout(DROPOUT)
            self.head = nn.Linear(HIDDEN, len(TARGETS))

        def forward(self, x):
            h, _ = self.lstm(x)
            return self.head(self.drop(h[:, -1]))

    X = P[cols].to_numpy(np.float64)
    Y = P[TARGETS].to_numpy(np.float64)
    day = P.day.to_numpy()
    ok = seq_valid(P)
    days = sorted(P.day.unique())
    offs = torch.arange(-SEQ + 1, 1, device=dev)
    out = np.full((len(P), len(TARGETS)), np.nan)
    info = []
    for k, (a, b) in enumerate(ms.windows(days)):
        torch.manual_seed(k)
        np.random.seed(k)
        known = (day < a) & ok & np.isfinite(Y).all(axis=1)
        v0 = days[days.index(a) - VAL_SESSIONS]
        tr, va = known & (day < v0), known & (day >= v0)
        te = (day >= a) & (day <= b) & ok
        mu, sd = scaler(X, tr)
        Xg = torch.from_numpy(scaled(X, mu, sd)).to(dev)
        ymu, ysd = Y[tr].mean(axis=0), Y[tr].std(axis=0)
        Yg = torch.from_numpy(((np.nan_to_num(Y) - ymu) / ysd).astype(np.float32)).to(dev)
        net = Net(len(cols)).to(dev)
        opt = torch.optim.Adam(net.parameters(), lr=LR, weight_decay=1e-5)
        loss_fn = nn.SmoothL1Loss()
        tr_idx = torch.from_numpy(np.flatnonzero(tr)).to(dev)
        va_idx = torch.from_numpy(np.flatnonzero(va)).to(dev)

        def batches(idx, shuffle):
            if shuffle:
                idx = idx[torch.randperm(len(idx), device=dev)]
            for s in range(0, len(idx), BATCH):
                i = idx[s:s + BATCH]
                yield Xg[i[:, None] + offs], Yg[i]

        best, best_state, best_ep = math.inf, None, 0
        for ep in range(MAX_EPOCHS):
            net.train()
            for xb, yb in batches(tr_idx, True):
                opt.zero_grad()
                loss = loss_fn(net(xb), yb)
                loss.backward()
                nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                opt.step()
            net.eval()
            with torch.no_grad():
                vl = sum(loss_fn(net(xb), yb).item() * len(yb) for xb, yb in batches(va_idx, False)) / max(1, len(va_idx))
            if vl < best:
                best, best_ep = vl, ep + 1
                best_state = {n: t.detach().clone() for n, t in net.state_dict().items()}
            else:
                break                                          # stop at the first pass that does worse on held-back days
        net.load_state_dict(best_state)
        net.eval()
        te_idx = torch.from_numpy(np.flatnonzero(te)).to(dev)
        with torch.no_grad():
            pr = torch.cat([net(xb) for xb, _ in batches(te_idx, False)]).cpu().numpy() if len(te_idx) else np.zeros((0, 2))
        out[np.flatnonzero(te)] = pr * ysd + ymu
        info.append({"test_from": str(a), "train_rows": int(tr.sum()), "passes": best_ep, "val_loss": round(best, 5)})
        progress(f"  window {k + 1}: test from {a}, {tr.sum():,} training rows, {best_ep} passes, held-back loss {best:.4f}")
    return pd.DataFrame(out, columns=["lstm_ret", "lstm_swing"], index=P.index), info


def paired_error(y: pd.Series, p1: pd.Series, p2: pd.Series, day: pd.Series) -> Dict:
    """Daily average absolute error of forecast 1 minus forecast 2 (negative = 1 is better), 95% range."""
    d = pd.DataFrame({"e": ((y - p1).abs() - (y - p2).abs()).to_numpy(), "d": day.to_numpy()}).groupby("d").e.mean()
    se = d.std(ddof=1) / math.sqrt(len(d))
    return {"days": len(d), "mean": round(float(d.mean()), 5), "ci95": [round(float(d.mean() - 1.96 * se), 5), round(float(d.mean() + 1.96 * se), 5)],
            "days_1_better_%": round(float((d < 0).mean() * 100), 1)}


def run(cfg: Dict, symbols: Optional[List[str]] = None, progress=print) -> Dict:
    syms, data, P = ms.panel(symbols)
    cols = mf.COLUMNS
    progress(f"{len(P):,} bars, {len(syms)} symbols")
    tp = mf.OUT_DIR / "pred_trees.pkl.gz"
    if tp.exists():
        T0 = pd.read_pickle(tp)
        P = P.merge(T0, on=["symbol", "t"], how="left")
    else:
        P["pred_ret"] = ms.walk_predict(P, "fwd_ret", cols, progress)
        P["pred_swing"] = ms.walk_predict(P, "fwd_swing", cols, progress)
    P["lin_swing"] = linear_predict(P, "fwd_swing", cols, progress)
    L, info = lstm_predict(P, cols, progress)
    P = P.join(L)
    days = sorted(P.day.unique())
    test_days = {d for a, b in ms.windows(days) for d in days if a <= d <= b}
    T = P[P.day.isin(test_days)]
    sw = T.dropna(subset=["fwd_swing", "naive_swing", "pred_swing", "lin_swing", "lstm_swing"])
    swing = {k: ms.forecast_scores(sw.fwd_swing, sw[c], sw.day) for k, c in
             (("lstm", "lstm_swing"), ("trees", "pred_swing"), ("linear", "lin_swing"), ("simple_rule", "naive_swing"))}
    swing["lstm_vs_trees"] = paired_error(sw.fwd_swing, sw.lstm_swing, sw.pred_swing, sw.day)
    swing["trees_vs_linear"] = paired_error(sw.fwd_swing, sw.pred_swing, sw.lin_swing, sw.day)
    swing["lstm_vs_linear"] = paired_error(sw.fwd_swing, sw.lstm_swing, sw.lin_swing, sw.day)
    rt = T.dropna(subset=["fwd_ret", "lstm_ret", "pred_ret"])
    ret = {"lstm": ms.forecast_scores(rt.fwd_ret, rt.lstm_ret, rt.day), "trees": ms.forecast_scores(rt.fwd_ret, rt.pred_ret, rt.day)}
    progress("swing error: " + ", ".join(f"{k} {v['mae']}" for k, v in swing.items() if "mae" in v))
    entry, exit_ = ms.trade_tests(P, data, cfg, syms, test_days, "lstm_ret", progress)
    res = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": str(data.as_of), "symbols": syms,
           "model": {"seq_bars": SEQ, "hidden": HIDDEN, "dropout": DROPOUT, "batch": BATCH, "lr": LR, "max_passes": MAX_EPOCHS,
                     "held_back_sessions": VAL_SESSIONS}, "windows": info,
           "test_sessions": len(test_days), "first_test_day": str(min(test_days)),
           "return_forecast": ret, "swing": swing, "entry": entry, "exit": exit_}
    res["lines"] = lines(res)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


def lines(res: Dict) -> List[str]:
    rf, sw, en, ex = res["return_forecast"], res["swing"], res["entry"], res["exit"]
    ci = lambda p: f"{p['mean_bps']:+.2f} bps/day ({p['ci95_bps'][0]:+.2f} to {p['ci95_bps'][1]:+.2f}, {'clear' if p['significant'] else 'not clear'})"  # noqa: E731
    pe = lambda p: f"{p['mean']:+.4f} ({p['ci95'][0]:+.4f} to {p['ci95'][1]:+.4f}), first better on {p['days_1_better_%']}% of days"  # noqa: E731
    return [
        f"LSTM ({res['model']['seq_bars']} bars in, {res['model']['hidden']} memory cells), 11 symbols pooled, walk-forward from {res['first_test_day']} "
        f"({res['test_sessions']} unseen sessions), after costs.",
        f"Next-hour return: LSTM daily rank correlation {rf['lstm']['daily_rank_corr']:+.4f} ({rf['lstm']['daily_rank_corr_ci95'][0]:+.4f} to "
        f"{rf['lstm']['daily_rank_corr_ci95'][1]:+.4f}), sign right {rf['lstm']['sign_right_%']}% (trees {rf['trees']['daily_rank_corr']:+.4f}, {rf['trees']['sign_right_%']}%)",
        f"1. exit: strategies alone {ex['strategies_test_return_%']:+.2f}% vs with LSTM exit {ex['with_ml_exit_%']:+.2f}%; {ci(ex['ml_exit_vs_none'])}; "
        f"pairs better {ex['pairs_clearly_better']} / worse {ex['pairs_clearly_worse']} of {ex['pairs']} (luck ~{ex['expected_by_luck']})",
        "2. swing error (% of price): " + ", ".join(f"{k} {sw[k]['mae']} (corr {sw[k]['corr']})" for k in ("lstm", "trees", "linear", "simple_rule")),
        f"   LSTM minus trees: {pe(sw['lstm_vs_trees'])}",
        f"   trees minus linear: {pe(sw['trees_vs_linear'])}",
        f"3. entry: LSTM strategy {en['ml_test_return_%']:+.2f}% vs cash 0 / holding open-to-close {en['hold_open_to_close_%']:+.2f}%; vs cash {ci(en['ml_vs_cash'])}; "
        f"{en['trades']} trades",
    ]


def main():
    import yaml
    cfg = yaml.safe_load((mf.ROOT / "config.yaml").read_text(encoding="utf-8"))
    res = run(cfg)
    print("\n".join(res["lines"]))
    print("saved", OUT)


if __name__ == "__main__":
    main()
