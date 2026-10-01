"""
Applying a recalibration proposal to config.yaml, safely (Phase 3).

- Only the changed symbols of the proposal are touched; hand-made jobs and unchanged auto jobs
  stay exactly as they are. A symbol that a hand-made job has taken over since the proposal
  was made is skipped.
- Never mid-position: a stock/ETF change is applied only while the market is closed (the bot
  is flat overnight because it flattens before the close); a crypto change only when the
  account holds no position in that coin. Anything not ready stays PENDING and is retried by
  the API scheduler every minute; a newer proposal replaces older pending items.
- The new config must pass liveconfig.validate_live, or nothing is written. The previous
  config.yaml is copied to data/recalibration/config_backups/ first, and the write is atomic
  (temp file + rename), so the bot never reads half a file. The bot applies it at its next bar
  and closes the position of a symbol that left all enabled jobs (live.Bot.reload_config).
- Every apply / reject / pending step is recorded in data/recalibration/decisions.json, and
  applied changes update state.json (since / changes), which the min_hold_days and
  max_changes_per_week rules read.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import yaml

from . import recalibrate as rc
from .data import is_crypto
from .liveconfig import validate_live

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
NY = ZoneInfo("America/New_York")


def _paths(out_dir: Optional[Path]) -> Dict[str, Path]:
    d = out_dir or rc.OUT_DIR
    return {"dir": d, "backups": d / "config_backups", "pending": d / "pending.json",
            "decisions": d / "decisions.json", "state": d / "state.json"}


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")


def pos_key(sym: str) -> str:
    return sym.replace("/", "")  # Alpaca reports BTC/USD as BTCUSD


def ready(sym: str, market_open: bool, positions: Dict[str, float]) -> Tuple[bool, str]:
    """May this symbol's job change now without touching an open position?"""
    if is_crypto(sym):
        q = float(positions.get(pos_key(sym), 0) or 0)
        return (q == 0, "" if q == 0 else f"waiting until the bot holds no {sym} (holds {q:g})")
    return (not market_open, "" if not market_open else "waiting for the US market to close")


def build_live(live: Dict, targets: Dict[str, Optional[Dict]]) -> Dict:
    """New live section: auto jobs for `targets` replaced (job dict) or removed (None); everything
    else (hand-made jobs, other auto jobs, risk, timeframe) unchanged and in order."""
    new = copy.deepcopy(live)
    jobs = []
    seen = set()
    for j in new.get("jobs") or []:
        sym = (j.get("symbols") or [None])[0]
        if j["name"].startswith(rc.AUTO_PREFIX) and sym in targets:
            seen.add(sym)
            if targets[sym] is not None:
                jobs.append(copy.deepcopy(targets[sym]))
            continue
        jobs.append(j)
    for sym, job in targets.items():
        if sym not in seen and job is not None:
            jobs.append(copy.deepcopy(job))
    new["jobs"] = jobs
    return new


def _atomic_write(path: Path, cfg: Dict):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".yaml.tmp")
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(cfg, f, sort_keys=False)
    os.replace(tmp, path)


def targets_of(proposal: Dict) -> Dict[str, Optional[Dict]]:
    """The proposal's changes: {symbol: new auto job, or None to remove the symbol's job}."""
    by_sym = {j["symbols"][0]: j for j in proposal.get("proposed_jobs") or []}
    return {s: by_sym.get(s) for s in proposal.get("changes") or []}


def apply(proposal: Dict, market_open: bool, positions: Dict[str, float], now: Optional[datetime] = None,
          cfg_path: Optional[Path] = None, out_dir: Optional[Path] = None, only: Optional[List[str]] = None,
          by: str = "lab") -> Dict:
    """Apply what is ready now; keep the rest pending. Returns what happened."""
    now = now or datetime.now(timezone.utc)
    cfg_path = cfg_path or CONFIG
    p = _paths(out_dir)
    targets = targets_of(proposal)
    if only is not None:
        targets = {s: t for s, t in targets.items() if s in only}
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    hand = {s for j in cfg["live"].get("jobs") or [] if j.get("enabled", True)
            and not j["name"].startswith(rc.AUTO_PREFIX) for s in j["symbols"]}
    result = {"date": proposal["date"], "at": now.isoformat(), "by": by, "applied": [], "pending": [], "skipped": [],
              "error": None, "backup": None}
    go = {}
    for sym, job in targets.items():
        if sym in hand:
            result["skipped"].append({"symbol": sym, "why": "a hand-made job now trades this symbol"})
            continue
        ok, why = ready(sym, market_open, positions)
        if ok:
            go[sym] = job
        else:
            result["pending"].append({"symbol": sym, "why": why})
    if go:
        new = {**cfg, "live": build_live(cfg["live"], go)}
        errs = validate_live(new["live"])
        if errs:
            result["error"] = "The new settings failed validation, nothing was written: " + "; ".join(errs)
            result["pending"] += [{"symbol": s, "why": "not applied: validation failed"} for s in go]
            go = {}
        else:
            p["backups"].mkdir(parents=True, exist_ok=True)
            backup = p["backups"] / f"config_{now.astimezone():%Y%m%d_%H%M%S}.yaml"
            shutil.copy2(cfg_path, backup)
            _atomic_write(cfg_path, new)
            result["backup"] = str(backup)
            result["applied"] = sorted(go)
            state = _read(p["state"], {})
            today = now.astimezone(NY).date().isoformat()  # the same New York dates the hold rules use
            for sym in go:
                st = state.setdefault(sym, {})
                st["since"] = today
                st["changes"] = ([d for d in st.get("changes", []) if (date.fromisoformat(today) - date.fromisoformat(d)).days < 30]
                                 + [today])
            _write_json(p["state"], state)
    pend = {"date": proposal["date"], "symbols": [x["symbol"] for x in result["pending"]
                                                  if x["why"] != "not applied: validation failed"]}
    if pend["symbols"]:
        _write_json(p["pending"], pend)
    elif p["pending"].exists():
        p["pending"].unlink()
    decisions = _read(p["decisions"], {})
    rec = decisions.setdefault(proposal["date"], {"status": None, "history": []})
    rec["history"].append(result)
    rec["status"] = "error" if result["error"] else "pending" if pend["symbols"] else "applied"
    _write_json(p["decisions"], decisions)
    return result


def reject(proposal_date: str, now: Optional[datetime] = None, out_dir: Optional[Path] = None) -> Dict:
    p = _paths(out_dir)
    now = now or datetime.now(timezone.utc)
    decisions = _read(p["decisions"], {})
    rec = decisions.setdefault(proposal_date, {"status": None, "history": []})
    rec["status"] = "rejected"
    rec["history"].append({"at": now.isoformat(), "by": "lab", "rejected": True})
    _write_json(p["decisions"], decisions)
    pend = _read(p["pending"], {})
    if pend.get("date") == proposal_date:
        p["pending"].unlink(missing_ok=True)
    return rec


def tick(market_open: bool, positions: Dict[str, float], now: Optional[datetime] = None,
         cfg_path: Optional[Path] = None, out_dir: Optional[Path] = None) -> Optional[Dict]:
    """Scheduler: retry pending symbols of the LATEST proposal when they become ready. Pending
    items of an older proposal are dropped (the newer proposal decides)."""
    p = _paths(out_dir)
    pend = _read(p["pending"], None)
    if not pend:
        return None
    prop = rc.latest(p["dir"])
    if not prop or prop["date"] != pend["date"]:
        p["pending"].unlink(missing_ok=True)
        return None
    if not any(ready(s, market_open, positions)[0] for s in pend["symbols"]):
        return None
    return apply(prop, market_open, positions, now, cfg_path, out_dir, only=pend["symbols"], by="scheduler")


def status(out_dir: Optional[Path] = None) -> Dict:
    p = _paths(out_dir)
    return {"pending": _read(p["pending"], None), "decisions": _read(p["decisions"], {}),
            "backups": sorted(x.name for x in p["backups"].glob("*.yaml"))[-10:] if p["backups"].exists() else []}


def broker_view(tc) -> Tuple[bool, Dict[str, float]]:
    """Read-only: is the market open, and the paper account's positions."""
    clock = tc.get_clock()
    return bool(clock.is_open), {x.symbol: float(x.qty) for x in tc.get_all_positions()}
