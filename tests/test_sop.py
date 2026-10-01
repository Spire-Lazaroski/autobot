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


def test_news_section_numbers_come_from_the_studies(monkeypatch, tmp_path):
    import json

    from autobot import event_study, news_guard_study
    f = build_sop.facts()
    md = build_sop.to_md(build_sop.build(f))
    assert "## 6. News and earnings" in md and "## 7. Automatic strategy selection" in md
    assert "## 8. What to watch: daily checklist" in md and "## 10. Weekly review" in md
    assert "start_news.bat" in md and "context only" in md
    if event_study.OUT.exists():
        es = json.loads(event_study.OUT.read_text(encoding="utf-8"))
        assert f"{es['period'][0]} to {es['period'][1]}" in md
        assert f"{f['news']['spikes']['news']}% of the time with news" in md
    for s, dates in f["news"]["listed"].items():
        assert f"| {s} | {dates[-1]} |" in md
    # no study files: the section says so instead of inventing findings
    monkeypatch.setattr(event_study, "OUT", tmp_path / "none.json")
    monkeypatch.setattr(news_guard_study, "OUT", tmp_path / "none2.json")
    md = build_sop.to_md(build_sop.build(build_sop.facts()))
    assert "The studies haven't been run yet" in md


def test_auto_section_follows_the_config():
    import copy
    f = build_sop.facts()
    md = build_sop.to_md(build_sop.build(f))
    r = f["auto"]["r"]
    assert f"at least {r['min_edge']} better, made {r['min_trades']}+ trades" in md
    for name, b in f["auto"]["u"]["buckets"].items():
        assert ", ".join(b["candidates"]) in md
    cfg = copy.deepcopy(f["cfg"])
    cfg["recalibration"]["mode"] = "propose"
    cfg["live"]["risk"]["profit_lock"] = {"enabled": True, "trigger_pct": 2.0, "giveback_pct": 40}
    md2 = build_sop.to_md(build_sop.build({**f, "auto": build_sop.auto_facts(cfg)}))
    assert "It only **proposes** changes" in md2 and "**On:** once the day's gain reaches 2.0%" in md2
