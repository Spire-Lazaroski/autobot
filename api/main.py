"""
autobot strategy lab API.

    python -m api            # serves on 127.0.0.1:$API_PORT (default 8710)
"""
from __future__ import annotations

import os
import tempfile
import threading
from typing import Dict, List

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from autobot.backtest import ruled_signal
from autobot.backtest import (CostModel, best_per_strategy, compare_strategies, fills, metrics,
                              parameter_search, round_trips, run_backtest, walk_forward)
from autobot.data import TIMEFRAMES, fetch_bars, synthetic_bars
from autobot.indicators import plot_indicators
from autobot.strategies import STRATEGIES, get_signal

from autobot.liveconfig import validate_live

from . import jobs
from .live import router as live_router
from .news import router as news_router
from .recal import router as recal_router
from .reports import router as reports_router
from .reports import start_scheduler
from .models import CompareReq, DataReq, DetailReq, SearchReq, ShiftReq, WalkForwardReq
from .serialize import frame, plain, records, times, values
from .settings import CONFIG_PATH, WEB_PORT

app = FastAPI(title="autobot lab", version="0.1")
app.include_router(live_router)
app.include_router(reports_router)
app.include_router(news_router)
app.include_router(recal_router)


@app.on_event("startup")
def _start_daily_report():
    start_scheduler()


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
    summary, equity, per_sym = compare_strategies(data, req.strategies, costs, req.allow_short, live_rules())
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
def live_rules():
    """The live bot's signal rules (config live.trade_rules, e.g. no quick re-entry): every Lab backtest uses
    them, so the Lab describes the bot as it actually trades. Read fresh each call (the Jobs editor may change it)."""
    try:
        return (yaml.safe_load(CONFIG_PATH.read_text()).get("live") or {}).get("trade_rules") or None
    except (OSError, ValueError, AttributeError):
        return None


@app.get("/api/holdings-studies")
def holdings_studies():
    """The long-term holdings' backtests (docs/STRATEGY_EVIDENCE.md sections 12, 15, 23-24), read from the saved
    study files: the core's volatility targeting and the sector rotation with its mixes. Read-only."""
    import json as _json
    root = CONFIG_PATH.parent / "data"
    out = {}
    try:
        v = _json.loads((root / "ml" / "study_vol_target.json").read_text(encoding="utf-8"))
        out["core"] = {"period": [v.get("test_from"), v.get("test_to")], "rules": v.get("rules"),
                       "methods": v["cash_0"]["as_traded"], "same_amount": v["cash_0"]["same_amount"],
                       "crises": v["cash_0"].get("crises_%"), "accuracy": v.get("forecast_accuracy")}
    except (OSError, ValueError, KeyError):
        out["core"] = None
    try:
        r = _json.loads((root / "rs_study.json").read_text(encoding="utf-8"))
        out["rotation"] = {"rule": r.get("rule"), "long": r["combine_french"]["whole"], "long_vs_market": r["combine_french"]["vs_market_same_vol"],
                           "long_periods": r["combine_french"].get("periods"), "recent": r["combine_etf"]["whole"],
                           "recent_vs_market": r["combine_etf"]["vs_market_same_vol"], "correlation": r["combine_french"].get("correlation")}
    except (OSError, ValueError, KeyError):
        out["rotation"] = None
    return out


@app.get("/api/trade-rules")
def trade_rules_in_use():
    return {"trade_rules": live_rules() or {}}


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
    sig = ruled_signal(req.strategy, df, params, req.allow_short, live_rules())
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
    if spec.explain:
        out["active"] = plain(spec.explain(df, params, req.allow_short))
    return out


# ----------------------------------------------------------------- strategy shifting
def _segments(t: List[int], names: List) -> List[Dict]:
    """Collapse a per-bar label list into [start, end) runs for the timeline band."""
    out = []
    for i, n in enumerate(names):
        if out and out[-1]["name"] == n:
            out[-1]["end"] = t[i]
        else:
            out.append({"name": n, "start": t[i], "end": t[i]})
    return [o for o in out if o["name"] is not None]


def _underlying(spec_name: str, p: Dict) -> List[str]:
    if spec_name == "regime_switch":
        names = [p["trend_strategy"], p["range_strategy"], p["high_vol_strategy"]]
    else:
        names = list(p["candidates"])
    return [n for n in dict.fromkeys(names) if n != "flat"]


@app.post("/api/shift")
def shift(req: ShiftReq):
    spec = _strategy(req.strategy)
    data = load(req.data)
    costs = CostModel(req.fee_bps, req.slippage_bps)
    p = {**spec.defaults, **req.params}
    try:
        unders = _underlying(req.strategy, p)
        for n in unders:
            _strategy(n)
        cand = p.get("candidate_params") or {}
        runs = {req.strategy: p, **{n: cand.get(n, {}) for n in unders}, "buy_and_hold": {}}
        summary, equity, per_sym = compare_strategies(data, runs, costs, req.allow_short, live_rules())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    total = per_sym.groupby("strategy")["trades"].sum()
    summary = summary.assign(total_trades=total.reindex(summary.index).astype(int))
    summary = summary.reset_index().rename(columns={"index": "strategy"})
    role = {req.strategy: "meta", "buy_and_hold": "benchmark"}
    summary["role"] = [role.get(n, "underlying") for n in summary.strategy]

    timelines, time_in, switches = {}, {}, {}
    for sym, df in data.items():
        ex = spec.explain(df, p, req.allow_short)
        names = ex["names"]
        pos = ruled_signal(req.strategy, df, p, req.allow_short, live_rules()).to_numpy()
        t = times(df.index)
        changes = [i for i in range(1, len(names)) if names[i] != names[i - 1] and names[i - 1] is not None]
        switches[sym] = {"switches": len(changes),
                         "switches_that_traded": sum(1 for i in changes if pos[i] != pos[i - 1])}
        timelines[sym] = {"active": _segments(t, names),
                          "regimes": _segments(t, ex["regimes"]) if "regimes" in ex else None}
        for n in names:
            if n is not None:
                time_in[n] = time_in.get(n, 0) + 1
    tot = sum(time_in.values()) or 1
    return {
        "strategy": req.strategy, "params": plain(p), "underlying": unders,
        "summary": records(summary.assign(low_trades=summary.total_trades < LOW_TRADES)),
        "per_symbol": records(per_sym),
        "equity": frame(equity, 6),
        "timelines": timelines, "switches": switches,
        "time_in": {k: round(v / tot * 100, 2) for k, v in sorted(time_in.items(), key=lambda kv: -kv[1])},
        "low_trades_threshold": LOW_TRADES,
    }


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
                               progress=progress, grids=req.grids, rules=live_rules())
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
                         req.allow_short, req.rank_by, req.grid, req.fixed_params, progress, rules=live_rules())
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
    errs = validate_live(cfg["live"])  # the same rules live.py applies when it reloads
    if errs:
        raise HTTPException(422, "; ".join(errs))


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
