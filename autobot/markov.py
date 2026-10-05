"""
Two-state Markov switching model (a Gaussian hidden Markov model) for returns, written to be
causal: the bot and the backtests only ever see FILTERED probabilities (what is known after
bar t's close), never the smoothed ones that use later bars.

    state 0 = calm, state 1 = turbulent (always the state with the larger standard deviation,
    so the labels can't swap between fits)

fit(x)                Baum-Welch (EM) on a training sample of returns; returns the parameters
filtered(x, params)   P(turbulent | returns up to t) for each t, with the given parameters
turbulence(df, ...)   walk-forward: refit every `refit_every` sessions on the previous
                      `train_sessions` sessions only, and filter forward with the latest fit;
                      intraday returns are first divided by their usual size at that time of
                      day (previous 20 sessions), so the open's normal jumpiness isn't "turbulence"
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

NY = "America/New_York"


def _norm(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """Densities of each x under each state: shape (T, 2)."""
    z = (x[:, None] - mu[None, :]) / sd[None, :]
    return np.exp(-0.5 * z * z) / (sd[None, :] * np.sqrt(2 * np.pi)) + 1e-300


def fit(x: np.ndarray, iters: int = 40, tol: float = 1e-6) -> Optional[Dict]:
    """EM for a 2-state Gaussian HMM. x: returns (any scale). None if the sample is too small."""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    if len(x) < 50 or np.std(x) == 0:
        return None
    s = np.std(x)
    mu = np.array([np.mean(x), np.mean(x)])
    sd = np.array([s * 0.7, s * 1.6])
    P = np.array([[0.98, 0.02], [0.05, 0.95]])
    pi = np.array([0.8, 0.2])
    T = len(x)
    last = -np.inf
    for _ in range(iters):
        B = _norm(x, mu, sd)
        a = np.empty((T, 2))
        c = np.empty(T)
        a[0] = pi * B[0]
        c[0] = a[0].sum()
        a[0] /= c[0]
        for t in range(1, T):
            a[t] = (a[t - 1] @ P) * B[t]
            c[t] = a[t].sum()
            a[t] /= c[t]
        b = np.ones((T, 2))
        for t in range(T - 2, -1, -1):
            b[t] = (P @ (B[t + 1] * b[t + 1])) / c[t + 1]
        g = a * b
        g /= g.sum(axis=1, keepdims=True)
        xi = np.zeros((2, 2))
        for t in range(T - 1):
            m = a[t][:, None] * P * (B[t + 1] * b[t + 1])[None, :] / c[t + 1]
            xi += m
        P = xi / xi.sum(axis=1, keepdims=True)
        w = g.sum(axis=0)
        mu = (g * x[:, None]).sum(axis=0) / w
        sd = np.sqrt((g * (x[:, None] - mu[None, :]) ** 2).sum(axis=0) / w)
        sd = np.maximum(sd, s * 0.05)
        pi = g[0]
        ll = np.log(c).sum()
        if ll - last < tol * abs(ll):
            break
        last = ll
    if sd[0] > sd[1]:                                   # state 1 is always the turbulent one
        mu, sd, pi = mu[::-1], sd[::-1], pi[::-1]
        P = P[::-1, ::-1]
    return {"mu": mu, "sd": sd, "P": P, "pi": pi}


def filtered(x: np.ndarray, params: Dict, start: Optional[np.ndarray] = None) -> np.ndarray:
    """P(turbulent | x_1..x_t) for each t (NaN inputs keep the previous probability)."""
    mu, sd, P = params["mu"], params["sd"], params["P"]
    p = np.asarray(start if start is not None else params["pi"], float)
    out = np.empty(len(x))
    for t, v in enumerate(np.asarray(x, float)):
        p = p @ P
        if np.isfinite(v):
            p = p * _norm(np.array([v]), mu, sd)[0]
            p = p / p.sum()
        out[t] = p[1]
    return out


def session_returns(df: pd.DataFrame) -> pd.Series:
    """Bar-to-bar log returns inside each New York session (the overnight gap is left out)."""
    day = pd.Series(df.index.tz_convert(NY).date, index=df.index)
    r = np.log(df.close).diff()
    return r.where(day == day.shift(1))


def deseasonalize(r: pd.Series, df: pd.DataFrame, sessions: int = 20) -> pd.Series:
    """Each intraday return divided by the average size of returns at that time of day over the
    previous `sessions` sessions (today excluded), so the model sees unusual turbulence rather
    than the normal open / close rhythm."""
    ny = df.index.tz_convert(NY)
    slot = ny.hour * 60 + ny.minute
    day = np.asarray(ny.date)
    a = r.abs().to_frame("a")
    a["slot"], a["day"] = slot, day
    daily = a.groupby(["day", "slot"]).a.mean().unstack("slot")         # one row per session
    scale = daily.rolling(sessions, min_periods=sessions // 2).mean().shift(1)
    s = scale.stack().rename("s")
    key = pd.MultiIndex.from_arrays([day, slot])
    return r / s.reindex(key).to_numpy()


def turbulence(df: pd.DataFrame, train_sessions: int = 60, refit_every: int = 5, intraday: bool = True) -> pd.Series:
    """Walk-forward filtered P(turbulent) per bar; NaN until the first fit. intraday=True uses
    within-session returns (5-minute bars); False uses close-to-close returns (daily bars, where
    one bar is one session)."""
    r = deseasonalize(session_returns(df), df) if intraday else np.log(df.close).diff() * 1e4
    days = np.asarray(df.index.tz_convert(NY).date)
    uniq = sorted(set(days))
    out = pd.Series(np.nan, index=df.index)
    state = None
    for k in range(train_sessions, len(uniq), refit_every):
        train = (days >= uniq[k - train_sessions]) & (days < uniq[k])
        params = fit(r[train].to_numpy())
        if params is None:
            continue
        end = uniq[k + refit_every] if k + refit_every < len(uniq) else None
        test = (days >= uniq[k]) & ((days < end) if end is not None else True)
        if state is None:                               # start the filter from the end of the training sample
            p0 = filtered(r[train].to_numpy(), params)[-1]
            state = np.array([1 - p0, p0])
        idx = np.flatnonzero(test)
        if not len(idx):
            continue
        prob = filtered(r.iloc[idx].to_numpy(), params, state)
        out.iloc[idx] = prob
        state = np.array([1 - prob[-1], prob[-1]])
    return out
