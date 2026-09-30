"""News scoring: keyword rules, the Claude path (with a fake client), scored-once caching."""
import json
from types import SimpleNamespace

import pytest

from autobot import news, news_score as ns


@pytest.mark.parametrize("headline,symbol,category", [
    ("Fed's Williams Says Economy Remains Resilient; Sees Solid Growth", "SPY", "macro"),
    ("Apple Agrees To Acquire AI Startup For $1B", "AAPL", "m&a"),
    ("Tesla Q3 Earnings Beat Estimates", "TSLA", "earnings"),
    ("Jefferies Upgrades Apple to Hold, Raises Price Target to $188", "AAPL", "analyst_rating"),
    ("FTC Sues Apple Over App Store Rules", "AAPL", "legal_regulatory"),
    ("Nvidia Raises Full-Year Outlook", "NVDA", "guidance"),
    ("President Trump Says We Should Buy More Mortgage Bonds", "SPY", "other"),
])
def test_rule_categories(headline, symbol, category):
    assert ns.rules_score(headline, "", symbol, [symbol])["category"] == category


def test_rule_surprise_from_actual_vs_estimate():
    assert ns.rules_score("USA Core CPI (MoM) For Dec. 0.2% Vs 0.3% Est.", "", "SPY", ["SPY"])["surprise"] == "unexpected"
    assert ns.rules_score("USA Core PCE (YoY) For Feb 3.0% Vs 3.0% Est", "", "SPY", ["SPY"])["surprise"] == "expected"


def test_relevance_main_vs_mention():
    assert ns.rules_score("Nvidia Unveils New Chip", "", "NVDA", ["NVDA"])["relevance"] == "main"
    assert ns.rules_score("Palantir Stock Surges", "", "NVDA", ["PLTR", "NVDA"])["relevance"] == "mention"


class FakeClaude:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.calls = payload, stop, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(stop_reason=self.stop, content=[SimpleNamespace(type="text", text=json.dumps(self.payload))])


EV = {"id": 1, "created_at": "2026-09-29T20:05:00+00:00", "symbols": json.dumps(["AAPL", "MSFT"]),
      "headline": "Apple beats", "summary": "s"}


def test_claude_output_is_validated_and_clamped():
    fake = FakeClaude({"scores": [
        {"symbol": "aapl", "relevance": "main", "sentiment": 1.7, "surprise": "unexpected", "category": "earnings", "materiality": 9, "rationale": "r"},
        {"symbol": "MSFT", "relevance": "mention", "sentiment": 0, "surprise": "unclear", "category": "other", "materiality": 1, "rationale": "r"}]})
    out = ns.claude_score(fake, EV, ["AAPL"])
    assert out == [{"symbol": "AAPL", "relevance": "main", "sentiment": 1.0, "surprise": "unexpected", "category": "earnings",
                    "materiality": 5, "rationale": "r"}]
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-4-5" and call["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
def test_claude_refusal_or_cutoff_is_not_stored(stop):
    with pytest.raises(RuntimeError):
        ns.claude_score(FakeClaude({"scores": []}, stop=stop), EV, ["AAPL"])


def test_each_event_scored_once_per_version(tmp_path, monkeypatch):
    db = tmp_path / "n.db"
    monkeypatch.setattr(news, "DB_PATH", db)
    news.store([{"id": 1, "headline": "Tesla Q3 Earnings Beat Estimates", "symbols": ["TSLA"], "created_at": "2026-09-29T20:05:00Z"}], "backfill", path=db)
    news.collapse_events(db)
    assert ns.run_rules() == 1
    assert ns.run_rules() == 0  # cached: nothing left to score
    assert ns.estimate()["events"] == 1  # Claude hasn't scored it yet


def test_run_stops_at_the_first_credit_error(tmp_path, monkeypatch):
    import anthropic
    import httpx2
    db = tmp_path / "n.db"
    monkeypatch.setattr(news, "DB_PATH", db)
    news.store([{"id": i, "headline": f"Tesla story {i}", "symbols": ["TSLA"], "created_at": f"2026-09-2{i}T20:05:00Z"} for i in range(1, 4)], "backfill", path=db)
    news.collapse_events(db)
    calls = []

    class Broke:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            calls.append(1)
            req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            raise anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                            response=httpx2.Response(400, request=req), body=None)
    monkeypatch.setattr(ns, "claude_client", Broke)
    with pytest.raises(RuntimeError, match="credit balance"):
        ns.run_claude(10)
    assert len(calls) == 1


@pytest.mark.parametrize("headline,kind", [
    ("IREN, Riot, Cipher, Other HPC Stocks Climb After Nvidia's Blockbuster Q3 Report", "price_move_recap"),
    ("Benzinga Market Summary: S&P And Nasdaq Drop As NVIDIA Falls", "price_move_recap"),
    ("USA Core CPI (MoM) For Dec. 0.2% Vs 0.3% Est.", "data_release"),
    ("Will DeepSeek Hurt Magnificent 7 Stocks Valuation?", "opinion_analysis"),
    ("Beijing Blocks Nvidia Chips Amid Antitrust Probe", "new_information"),
])
def test_rule_story_type(headline, kind):
    assert ns.story_type_rule(headline) == kind


def test_story_type_is_in_the_claude_schema():
    item = ns.OUTPUT_SCHEMA["properties"]["scores"]["items"]
    assert item["properties"]["story_type"]["enum"] == ns.STORY_TYPES and "story_type" in item["required"]


def _db(tmp_path, monkeypatch, stories):
    db = tmp_path / "n.db"
    monkeypatch.setattr(news, "DB_PATH", db)
    news.store([{"id": i, "headline": h, "symbols": ["NVDA"], "created_at": t} for i, (h, t) in enumerate(stories, 1)], "backfill", path=db)
    news.collapse_events(db)
    ns.run_rules()
    with news.connect() as con:
        return {r["headline"]: r["id"] for r in con.execute("SELECT id, headline FROM events")}


def test_results_follow_up_overnight(tmp_path, monkeypatch):
    ids = _db(tmp_path, monkeypatch, [("Nvidia Q3 EPS $1.30 Beats $1.25 Estimate, Revenue Beats", "2026-11-19T21:23:00Z"),
                                      ("Nvidia Shares Rise After Record Quarterly Earnings", "2026-11-20T09:00:00Z")])
    f = ns.first_story_flags([(ids["Nvidia Shares Rise After Record Quarterly Earnings"], "NVDA")])
    assert not next(iter(f.values()))["first"]  # 11.6 h later is inside the results window
    f = ns.first_story_flags([(ids["Nvidia Q3 EPS $1.30 Beats $1.25 Estimate, Revenue Beats"], "NVDA")])
    assert next(iter(f.values()))["first"]  # a later story never makes an earlier one a follow-up


def test_previews_and_unrelated_stories_do_not_count(tmp_path, monkeypatch):
    ids = _db(tmp_path, monkeypatch, [("Nvidia Stock Sits On Knife's Edge Before Earnings", "2026-11-19T15:00:00Z"),
                                      ("China's SMIC Pilots New Lithography Machine For Nvidia Rival Chips In Beijing", "2026-09-17T08:00:00Z"),
                                      ("Beijing Blocks Nvidia Chips Amid Antitrust Probe In China", "2026-09-17T10:00:00Z"),
                                      ("Nvidia Q3 EPS $1.30 Beats $1.25 Estimate", "2026-11-19T21:23:00Z")])
    f = ns.first_story_flags([(ids["Nvidia Q3 EPS $1.30 Beats $1.25 Estimate"], "NVDA"),
                              (ids["Beijing Blocks Nvidia Chips Amid Antitrust Probe In China"], "NVDA")])
    assert all(v["first"] for v in f.values())  # a preview isn't news; shared broad themes aren't the same story
