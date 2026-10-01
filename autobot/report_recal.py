"""
"Strategy changes" and "Universe" sections of the daily report (Phase 6).

The nightly recalibration applies its changes about 15 minutes after the close, after that
day's report has been written. So a day's report shows the changes applied since the previous
day's close (in effect for this session) plus any applied during the period, each with the
plain-English reason from its proposal. The universe part summarises the latest trading list.
Wording: plain.REPORT_RECAL. Read-only: it only reads data/recalibration and data/universe.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import json
import pandas as pd

from . import plain, recal_apply, recalibrate, universe

LOOKBACK = pd.Timedelta(hours=24)  # the night before the period's first day


def _proposal(date: str, out_dir: Optional[Path]) -> Optional[Dict]:
    p = (out_dir or recalibrate.OUT_DIR) / f"{date}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def collect(a: pd.Timestamp, b: pd.Timestamp, out_dir: Optional[Path] = None, uni_dir: Optional[Path] = None,
            mode: Optional[str] = None) -> Dict:
    """Applied changes in [a - 24 h, b), pending items, the mode and the latest universe."""
    st = recal_apply.status(out_dir)
    changes: List[Dict] = []
    for date, rec in sorted(st["decisions"].items()):
        prop = None
        for h in rec.get("history", []):
            if not h.get("applied"):
                continue
            at = pd.Timestamp(h["at"])
            if not (a - LOOKBACK <= at < b):
                continue
            prop = prop or _proposal(date, out_dir) or {}
            for sym in h["applied"]:
                row = (prop.get("symbols") or {}).get(sym, {})
                changes.append({"symbol": sym, "at": at, "by": h.get("by", "lab"), "proposal": date,
                                "old": (row.get("incumbent") or {}).get("label", "–"),
                                "new": (row.get("proposed") or {}).get("label", "–"),
                                "reason": (row.get("decision") or {}).get("reason", "reason not recorded")})
    try:
        mode = mode or recalibrate.settings().get("mode", "propose")
    except Exception:  # noqa: BLE001
        mode = mode or "propose"
    end_day = (b - pd.Timedelta(seconds=1)).tz_convert("America/New_York").date()
    uni = universe.latest(before=(end_day + pd.Timedelta(days=1)).isoformat(), out_dir=uni_dir)
    return {"changes": changes, "pending": st["pending"], "mode": mode, "universe": uni}


def change_lines(rd: Dict) -> List[str]:
    if not rd:
        return []
    t = plain.REPORT_RECAL
    out = []
    for c in rd["changes"]:
        line = t["change"].format(sym=c["symbol"], old=c["old"], new=c["new"], reason=c["reason"])
        when = c["at"].tz_convert("America/New_York").strftime("%a %d %b %H:%M")
        out.append(line + " " + t["applied_at"].format(when=when, by="automatically" if "scheduler" in c["by"] else "from the lab"))
    if not out:
        out.append(t["none"])
    if rd.get("pending") and rd["pending"].get("symbols"):
        out.append(t["pending"].format(syms=", ".join(rd["pending"]["symbols"])))
    if rd.get("mode") == "propose":
        out.append(t["mode_propose"])
    return out


def universe_lines(rd: Dict) -> List[str]:
    if not rd:
        return []
    t = plain.REPORT_RECAL
    u = rd.get("universe")
    if not u:
        return [t["universe_none"]]
    out = [t["universe"].format(date=u["date"], n=len(u["selected"]), syms=", ".join(u["selected"]))]
    if u.get("added") or u.get("dropped"):
        out.append(t["universe_moves"].format(added=", ".join(u.get("added") or []) or "none",
                                              dropped=", ".join(u.get("dropped") or []) or "none"))
    out += list(u.get("warnings") or [])
    return out
