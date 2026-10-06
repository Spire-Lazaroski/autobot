"""ML study phase 2: targets stay inside the session and end at the close-out, the simple swing
rule uses earlier sessions only, and the entry / exit rules behave as described."""
import numpy as np
import pandas as pd

from autobot import ml_study as ms
from tests.test_ml_features import bars


def test_targets_stop_at_the_closeout_and_never_cross_days():
    df = bars(days=3)
    t = ms.targets(df)
    ny = df.index.tz_convert(ms.NY)
    m = ny.hour * 60 + ny.minute
    assert t.fwd_ret[m >= ms.CLOSEOUT_MIN].isna().all()           # the close-out bar and the 15:55 bar: no target
    i = int(np.flatnonzero(m == 15 * 60 + 40)[0])                 # 15:40: only 2 bars left to the 15:50 close-out bar
    assert np.isclose(t.fwd_ret.iloc[i], (df.close.iloc[i + 2] / df.close.iloc[i] - 1) * 100)
    assert np.isclose(t.fwd_ret.iloc[0], (df.close.iloc[12] / df.close.iloc[0] - 1) * 100)
    sw = (df.high.iloc[1:13].max() - df.low.iloc[1:13].min()) / df.close.iloc[0] * 100
    assert np.isclose(t.fwd_swing.iloc[0], sw)


def test_simple_swing_rule_uses_earlier_sessions_only():
    from autobot import ml_features as mf
    df = bars(days=12)
    f = mf.build(df, None, None, with_turbulence=False)
    y = ms.targets(df).fwd_swing
    a = ms.naive_swing(f, y)
    y2 = y.copy()
    y2.iloc[-78:] *= 5                                            # the last day's own outcome must not move its forecast
    np.testing.assert_allclose(ms.naive_swing(f, y2).iloc[-78:].to_numpy(), a.iloc[-78:].to_numpy())


def test_entry_and_exit_rules():
    idx = pd.date_range("2026-06-01 13:30", periods=6, freq="5min", tz="UTC")
    pred = pd.Series([0.01, 0.06, 0.02, -0.01, 0.06, np.nan], index=idx)     # in %: 1, 6, 2, -1, 6 bps
    assert ms.entry_signal(pred, np.ones(6, bool)).tolist() == [0, 1, 1, 0, 1, 0]
    base = pd.Series([1, 1, 1, 0, 1, 1], index=idx, dtype=float)
    pred = pd.Series([0.0, -0.06, 0.05, 0.0, 0.0, 0.0], index=idx)
    assert ms.exit_overlay(base, pred).tolist() == [1, 0, 0, 0, 1, 1]       # out until the strategy goes flat itself


def _panel(days=50, seed=0):
    from autobot import ml_features as mf
    parts = []
    for k, s in enumerate(["AAA", "BBB"]):
        df = bars(days=days, seed=seed + k)
        f = mf.build(df, None, None, with_turbulence=False)
        p = f.join(ms.targets(df))
        p["naive_swing"] = ms.naive_swing(f, p.fwd_swing)
        p["symbol"] = s
        parts.append(p)
    return pd.concat(parts).rename_axis("t").reset_index()


def test_linear_and_lstm_predict_only_unseen_windows(monkeypatch):
    import pytest
    pytest.importorskip("torch")
    from autobot import ml_features as mf, ml_lstm as ml
    monkeypatch.setattr(ms, "START", 30)
    monkeypatch.setattr(ms, "STEP", 10)
    monkeypatch.setattr(ml, "MAX_EPOCHS", 1)
    P = _panel()
    first = sorted(P.day.unique())[30]
    lin = ml.linear_predict(P, "fwd_swing", mf.COLUMNS)
    L, info = ml.lstm_predict(P, mf.COLUMNS, progress=lambda *a: None)
    early = (P.day < first).to_numpy()
    assert lin[early].isna().all() and L[early].isna().all().all()
    assert lin[~early].notna().mean() > 0.8 and L.lstm_swing[~early].notna().mean() > 0.8
    # a future shock in the targets must not change the first window's predictions (trained on earlier days only)
    P2 = P.copy()
    late = (P2.day >= sorted(P.day.unique())[40]).to_numpy()
    P2.loc[late, "fwd_swing"] *= 10
    w1 = ((P.day >= first) & (P.day < sorted(P.day.unique())[40])).to_numpy()
    np.testing.assert_allclose(ml.linear_predict(P2, "fwd_swing", mf.COLUMNS)[w1], lin[w1])
