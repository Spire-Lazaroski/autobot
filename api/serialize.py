"""pandas -> JSON helpers. Times are UTC unix seconds; NaN/inf become null."""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd

MAX_POINTS = 5000  # line charts get thinned above this; candles are never thinned


def times(index: pd.DatetimeIndex) -> List[int]:
    idx = index if index.tz is not None else index.tz_localize("UTC")
    return idx.tz_convert("UTC").as_unit("s").asi8.tolist()


def values(s: pd.Series | np.ndarray, digits: int = 6) -> List[Any]:
    a = np.round(np.asarray(s, dtype=float), digits)
    return [x if math.isfinite(x) else None for x in a.tolist()]


def thin_step(n: int, limit: int = MAX_POINTS) -> int:
    return max(1, math.ceil(n / limit))


def frame(df: pd.DataFrame, digits: int = 6, limit: int = MAX_POINTS) -> Dict:
    """{"t": [...], "series": {col: [...]}}, thinned to at most `limit` points (last point kept)."""
    k = thin_step(len(df), limit)
    if k > 1:
        keep = np.r_[np.arange(0, len(df) - 1, k), len(df) - 1]
        df = df.iloc[keep]
    return {"t": times(df.index), "series": {str(c): values(df[c], digits) for c in df.columns}}


def records(df: pd.DataFrame) -> List[Dict]:
    clean = df.replace([np.inf, -np.inf], np.nan)
    out = []
    for row in clean.to_dict(orient="records"):
        out.append({k: _scalar(v) for k, v in row.items()})
    return out


def _scalar(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not math.isfinite(float(v)) else float(v)
    if isinstance(v, pd.Timestamp):
        return int(v.timestamp())
    if isinstance(v, dict):
        return {k: _scalar(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_scalar(x) for x in v]
    return v


def plain(obj):
    """Recursively make numpy/pandas scalars JSON-safe."""
    return _scalar(obj)
