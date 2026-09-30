"""
autobot strategy lab API.

    python -m api            # serves on 127.0.0.1:$API_PORT (default 8710)
"""
from __future__ import annotations

import os
import tempfile
import threading
from typing import Dict

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from autobot.backtest import (CostModel, best_per_strategy, compare_strategies, fills, metrics,
                              parameter_search, round_trips, run_backtest, walk_forward)
from autobot.data import TIMEFRAMES, fetch_bars, synthetic_bars
from autobot.indicators import plot_indicators
from autobot.strategies import STRATEGIES, get_signal

from . import jobs
from .models import CompareReq, DataReq, DetailReq, SearchReq, WalkForwardReq
from .serialize import frame, plain, records, times, values
from .settings import CONFIG_PATH, WEB_PORT

app = FastAPI(title="autobot lab", version="0.1")
app.add_middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"],
                   allow_origins=[f"http://127.0.0.1:{WEB_PORT}", f"http://localhost:{WEB_PORT}"])

LOW_TRADES = 30

# ----------------------------------------------------------------- data
_cache: Dict[tuple, Dict[str, pd.DataFrame]] = {}
_cache_lock = threading.Lock()


def load(req: DataReq) -> Dict[str, pd.DataFrame]:
    key = (req.source, tuple(req.symbols), req.timeframe, req.days, req.rth_only,
           pd.Timestamp.now(tz="UTC").strftime("%Y%m%d"))
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        if req.source == "demo":
            data = synthetic_bars(req.symbols, req.timeframe, req.days)
        else:
            data = fetch_bars(req.symbols, req.timeframe, req.days, rth_only=req.rth_only)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Could not load bars: {e}") from e
    empty = [s for s, df in data.items() if len(df) < 30]
    if empty:
        raise HTTPException(422, f"Too few bars for {', '.join(empty)} (need 30+). Try more days.")
    with _cache_lock:
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = data
    return data


def _strategy(name: str):
    if name not in STRATEGIES:
        raise HTTPException(404, f"Unknown strategy '{name}'")
    return STRATEGIES[name]


def _spec_json(spec) -> Dict:
    return {"name": spec.name, "family": spec.family, "description": spec.description,
            "defaults": plain(spec.defaults), "grid": plain(spec.grid), "source": spec.source,
            "warmup_days": spec.warmup_days}


# ----------------------------------------------------------------- basics
@app.get("/api/health")
def health():
    from dotenv import load_dotenv
    load_dotenv()
    return {"ok": True, "alpaca_keys": bool(os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")),
            "strategies": len(STRATEGIES), "timeframes": list(TIMEFRAMES)}


@app.get("/api/strategies")
def strategies():
    return [_spec_json(s) for s in STRATEGIES.values()]


@app.post("/api/bars")
def bars(req: DataReq):
    data = load(req)
    return {sym: {"t": times(df.index), "o": values(df.open, 4), "h": values(df.high, 4),
                  "l": values(df.low, 4), "c": values(df.close, 4), "v": values(df.volume, 0)}
            for sym, df in data.items()}


# ----------------------------------------------------------------- compare
@app.post("/api/compare")
def compare(req: CompareReq):
    for n in req.strategies:
        _strategy(n)
    if not req.strategies:
        raise HTTPException(422, "Pick at least one strategy")
    data = load(req.data)
    costs = CostModel(req.fee_bps, req.slippage_bps)
    summary, equity, per_sym = compare_strategies(data, req.strategies, costs, req.allow_short)
    total = per_sym.groupby("strategy")["trades"].sum()
    summary = summary.assign(total_trades=total.reindex(summary.index).astype(int),
                             family=[STRATEGIES[n].family for n in summary.index])
    summary = summary.reset_index().rename(columns={"index": "strategy"})
    per_sym = per_sym.assign(low_trades=per_sym.trades < LOW_TRADES)
    return {
        "summary": records(summary.assign(low_trades=summary.total_trades < LOW_TRADES)),
        "per_symbol": records(per_sym),
        "equity": frame(equity, 6),
        "bars": {s: len(df) for s, df in data.items()},
        "low_trades_threshold": LOW_TRADES,
    }


# ----------------------------------------------------------------- one strategy on one symbol
@app.post("/api/detail")
def detail(req: DetailReq):
    spec = _strategy(req.strategy)
    data = load(req.data)
    sym = req.symbol.upper()
    if sym not in data:
        raise HTTPException(404, f"{sym} is not in the loaded symbols")
    df = data[sym]
    params = {**spec.defaults, **req.params}
    costs = CostModel(req.fee_bps, req.slippage_bps)
    sig = get_signal(req.strategy, df, params, req.allow_short)
    bt = run_backtest(df, sig, costs)
    bh = run_backtest(df, pd.Series(1.0, index=df.index), costs)
    ind = plot_indicators(req.strategy, df, params)

    def lines(items):
        return [{**{k: v for k, v in it.items() if k != "values"}, "values": values(it["values"], 5)}
                for it in items]

    m = metrics(bt)
    out = {
        "symbol": sym, "strategy": req.strategy, "params": plain(params),
        "candles": {"t": times(df.index), "o": values(df.open, 4), "h": values(df.high, 4),
                    "l": values(df.low, 4), "c": values(df.close, 4), "v": values(df.volume, 0)},
        "overlays": lines(ind["overlays"]),
        "panes": [{**{k: v for k, v in p.items() if k != "series"}, "series": lines(p["series"])}
                  for p in ind["panes"]],
        "position": values(bt.position, 3),
        "equity": values(bt.equity, 6), "benchmark": values(bh.equity, 6),
        "fills": plain(fills(df, bt)),
        "trades": plain(round_trips(df, bt)),
        "metrics": plain(m), "benchmark_metrics": plain(metrics(bh)),
        "low_trades": m["trades"] < LOW_TRADES,
    }
    if spec.family == "meta" and getattr(spec, "explain", None):
        out["active"] = spec.explain(df, params, req.allow_short)
    return out


# ----------------------------------------------------------------- parameter search (async)
@app.post("/api/search")
def search(req: SearchReq):
    names = req.names or [n for n in STRATEGIES if STRATEGIES[n].grid]
    for n in names:
        _strategy(n)
    data = load(req.data)
    costs = CostModel(req.fee_bps, req.slippage_bps)

    def work(progress):
        res = parameter_search(data, names, costs, req.train_frac, req.allow_short,
                               progress=progress, grids=req.grids)
        if res.empty:
            return {"results": [], "best": []}
        res["test_trades_total"] = res["test_trades"] * len(data)  # test_trades is a per-symbol mean
        best = best_per_strategy(res)
        return {"results": records(res.rename(columns={"_params": "param_values"})),
                "best": records(best.rename(columns={"_params": "param_values"})),
                "grids": {n: plain((req.grids or {}).get(n, STRATEGIES[n].grid)) for n in names},
                "low_trades_threshold": LOW_TRADES}

    return jobs.submit("search", work).public()


@app.post("/api/walkforward")
def walkforward(req: WalkForwardReq):
    spec = _strategy(req.strategy)
    if not (req.grid or spec.grid):
        raise HTTPException(422, f"{req.strategy} has no parameter grid to re-optimize")
    data = load(req.data)
    costs = CostModel(req.fee_bps, req.slippage_bps)

    def work(progress):
        r = walk_forward(data, req.strategy, req.train_bars, req.test_bars, req.step_bars, costs,
                         req.allow_short, req.rank_by, req.grid, req.fixed_params, progress)
        w = r["windows"]
        return {"windows": records(w), "equity": frame(r["equity"], 6), "metrics": plain(r["metrics"]),
                "fixed_params": plain(r["fixed_params"]), "combos": r["combos"],
                "oos_start": int(r["oos_start"].timestamp()), "grid": plain(req.grid or spec.grid),
                "param_changes": int((w.params.astype(str) != w.params.astype(str).shift()).sum() - 1),
                "low_trades_threshold": LOW_TRADES}

    return jobs.submit("walkforward", work).public()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown or expired job")
    return job.public()


# ----------------------------------------------------------------- config.yaml
def _validate_config(cfg: Dict):
    if not isinstance(cfg, dict) or "backtest" not in cfg or "live" not in cfg:
        raise HTTPException(422, "config needs 'backtest' and 'live' sections")
    seen = {}
    for j in cfg["live"].get("jobs", []):
        if j.get("strategy") not in STRATEGIES:
            raise HTTPException(422, f"job '{j.get('name')}': unknown strategy '{j.get('strategy')}'")
        if not j.get("enabled", True):
            continue
        for s in j.get("symbols", []):
            if s in seen:
                raise HTTPException(422, f"{s} is in enabled jobs '{seen[s]}' and '{j['name']}'. One job per symbol.")
            seen[s] = j.get("name")


@app.get("/api/config")
def get_config():
    return yaml.safe_load(CONFIG_PATH.read_text())


@app.put("/api/config")
def put_config(cfg: Dict):
    _validate_config(cfg)
    # write-then-rename so a bot reading config.yaml never sees a half-written file
    fd, tmp = tempfile.mkstemp(dir=CONFIG_PATH.parent, suffix=".yaml.tmp")
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    os.replace(tmp, CONFIG_PATH)
    return {"ok": True}
