"""The SOP is generated from code and config, so it can't drift from them."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_sop  # noqa: E402

from autobot.strategies import STRATEGIES  # noqa: E402


def test_sop_covers_every_strategy_and_job():
    f = build_sop.facts()
    md = build_sop.to_md(build_sop.build(f))
    for name in STRATEGIES:
        assert f"(`{name}`)" in md, name
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    for j in cfg["live"]["jobs"]:
        assert f"| {j['name']} |" in md
    assert f"{cfg['live']['risk']['max_daily_loss_pct']}%" in md


def test_close_of_day_times_follow_daylight_saving():
    rows = build_sop.skopje_times(2026, (15, 55))
    assert [r[2] for r in rows] == ["21:55", "20:55", "21:55", "20:55", "21:55"]
    assert rows[1][0].isoformat() == "2026-03-08" and rows[3][0].isoformat() == "2026-10-25"
