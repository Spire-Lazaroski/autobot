"""
Build the operating procedure (SOP) for the paper-trading bot.

    python scripts/build_sop.py            # docs/SOP.md + docs/SOP.html + docs/SOP.pdf
    python scripts/build_sop.py --shots    # first refresh the screenshots from the running lab

Everything that can go stale is read from the code and config.yaml each time: strategy
descriptions (autobot/plain.py + the strategy registry), limits and jobs (config.yaml),
the bot's own thresholds (heartbeat, watchdog, switching), and the close-of-day times in
Skopje for the current year (they move when US and EU daylight-saving dates differ).
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from autobot import plain  # noqa: E402
from autobot.liveconfig import HEARTBEAT_STALE_S  # noqa: E402
from autobot.report import SWITCH_LIMIT, to_pdf  # noqa: E402
from autobot.strategies import STRATEGIES  # noqa: E402

DOCS = ROOT / "docs"
NY, SKOPJE = ZoneInfo("America/New_York"), ZoneInfo("Europe/Skopje")
FAMILY = {"benchmark": "Benchmark", "trend": "Trend following", "mean_reversion": "Buy the dip / mean reversion",
          "breakout": "Breakout", "intraday": "Intraday (flat every night)", "meta": "Strategy shifting",
          "news": "News-aware"}


# ----------------------------------------------------------------- facts from code and config
def facts() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    live = cfg["live"]
    risk = live["risk"]
    return dict(cfg=cfg, live=live, risk=risk, stall=float(live.get("stall_seconds", 300)),  # live.py's default
                heartbeat_stale=HEARTBEAT_STALE_S, switch_limit=SWITCH_LIMIT,
                flatten_min=int(risk.get("flatten_before_close_min", 0)), year=date.today().year, news=news_facts(cfg),
                auto=auto_facts(cfg))


def auto_facts(cfg: dict) -> dict:
    """Settings for section 7, from config.yaml through the modules' own settings()."""
    from autobot import recalibrate, sizing, universe
    r = recalibrate.settings(cfg)
    return {"mode": r["mode"], "take_over": bool(r.get("take_over_hand_made")), "r": r, "u": universe.settings(cfg),
            "sizing": sizing.settings(cfg["live"]["risk"]), "lock": cfg["live"]["risk"].get("profit_lock") or {}}


def news_facts(cfg: dict) -> dict:
    """News findings for section 6, taken from the saved studies by fixed rules (a key is
    missing when its study hasn't been run), plus the news settings in config.yaml."""
    import json

    from autobot import event_study, news_guard_study
    from autobot import news_guard as ng
    out = {"symbols": (cfg.get("news") or {}).get("symbols") or [],
           "listed": {s: sorted(str(d) for d in v) for s, v in ((cfg.get("news") or {}).get("earnings_dates") or {}).items()},
           "guard_jobs": [j["name"] for j in cfg["live"].get("jobs", []) if j.get("enabled", True) and j["strategy"] == "news_guard"]}
    if event_study.OUT.exists():
        es = json.loads(event_study.OUT.read_text(encoding="utf-8"))
        bigger = [(g["group"], g["ratio"]["eod"]["mean"]) for g in es["volatility"]["by_category"]
                  if not g["low"] and (g["ratio"].get("eod") or {}).get("lo") and g["ratio"]["eod"]["lo"] > 1]
        if bigger:
            out["bigger"] = dict(lo=round((min(m for _, m in bigger) - 1) * 100), hi=round((max(m for _, m in bigger) - 1) * 100),
                                 groups=[g.replace("_", " ") for g, _ in bigger])
        cells = [c for g in es["news"]["by_sentiment_signed"] for h, c in g["horizons"].items()
                 if h in es["horizons"] and c.get("lo") is not None]
        out["direction"] = dict(beyond=sum(1 for c in cells if c["lo"] > 0 or c["hi"] < 0), total=len(cells))
        cvr = {r["group"]: r["continued_eod"] for r in es["spikes"]["continue_vs_revert"]}
        out["spikes"] = dict(news=round(cvr.get("with news", 0)), no_news=round(cvr.get("no news", 0)))
        out["event_period"] = es["period"]
    if news_guard_study.OUT.exists():
        gs = json.loads(news_guard_study.OUT.read_text(encoding="utf-8"))
        better, worse = [], []
        for g, gr in gs["groups"].items():
            rows = [(base, r["variant"], r["vs_base"]) for base, c in gr["compare"].items() for r in c["summary"] if r["vs_base"]]
            rows += [(base, "walk-forward", w["vs_base"]) for base, w in (gr.get("walk_forward") or {}).items()]
            for base, v, d in rows:
                if d["significant"]:
                    (better if d["mean_bps"] > 0 else worse).append((g, base, v))
        out["guard"] = dict(better=better, worse=worse, start=gs["start"], end=gs["end"])
    try:
        chk = news_guard_study.detector_check(news_guard_study.STOCKS)
        out["detector"] = dict(exact=sum(c["exact"] for c in chk.values()), total=sum(c["published"] for c in chk.values()))
    except Exception:  # noqa: BLE001  no news database
        pass
    today = date.today()
    out["next"] = {s: next((str(d) for d in ng.earnings_sessions(s) if d > today), None) for s in out["listed"]}
    return out


def skopje_times(year: int, ny_hhmm: tuple) -> list:
    """(from, to, Skopje time) ranges in a year for a fixed New York clock time."""
    out, cur = [], None
    d = date(year, 1, 1)
    while d.year == year:
        ny = datetime(d.year, d.month, d.day, *ny_hhmm, tzinfo=NY)
        sk = ny.astimezone(SKOPJE).strftime("%H:%M")
        if cur and cur[2] == sk:
            cur[1] = d
        else:
            if cur:
                out.append(tuple(cur))
            cur = [d, d, sk]
        d += timedelta(days=1)
    out.append(tuple(cur))
    return out


def time_table(year: int, ny_hhmm: tuple) -> list:
    return [[f"{a:%d %b} to {b:%d %b}", t] for a, b, t in skopje_times(year, ny_hhmm)]


# ----------------------------------------------------------------- document model
# blocks: ("h2"|"h3"|"p"|"note"|"ol"|"ul"|"table"|"img"|"cards", ...)
def build(f: dict) -> list:
    L, R = f["live"], f["risk"]
    fm = f["flatten_min"]
    close_ny = (16, 0)
    flat_ny = (15, 60 - fm) if fm else (16, 0)
    usual_flat = skopje_times(f["year"], flat_ny)
    common_flat = max(usual_flat, key=lambda r: (r[1] - r[0]).days)[2]
    open_common = max(skopje_times(f["year"], (9, 30)), key=lambda r: (r[1] - r[0]).days)[2]
    tf = L["timeframe"]
    jobs = [j for j in L.get("jobs", [])]
    B = []
    B += [("p", f"Version generated {datetime.now(SKOPJE):%d %B %Y %H:%M} (Skopje) from the bot's code and config.yaml. "
                "If something here disagrees with the app, regenerate this document (see the end).")]

    # 1
    B += [("h2", "1. What the bot is, and what it isn't")]
    B += [("ul", [
        "It trades a <b>paper account</b> at Alpaca: pretend money, real market prices. The code is hard-wired to paper trading; there is no switch to real money.",
        f"It follows <b>jobs</b>: each job is one strategy on one or more symbols with a share of the account. It checks the market once per {tf} bar and sends market orders."
        + (" Every night it re-checks which strategy each symbol should use and changes the jobs itself when something is clearly better (section 7)."
           if f["auto"]["mode"] == "apply" else " A nightly check proposes better strategies; you decide on the Recalibrate page (section 7)."),
        f"It protects itself: if the account is down {R['max_daily_loss_pct']}% in a day it sells everything and stops for the day; no single symbol gets more than ${R['max_position_usd']:,}; stock positions are closed {fm} minutes before the US close.",
        "It writes down why it acted (logs/decisions.jsonl) and produces a plain-English report after every trading day.",
    ])]
    B += [("p", "<b>What it can't do</b>")]
    B += [("ul", [
        "It can't predict the market or guarantee a profit. Every strategy loses in some markets.",
        ("It doesn't trade on the news: its strategies only see prices. A separate news collector saves stories so the daily report can show them (section 6)."
         if not f["news"]["guard_jobs"] else
         f"Only the job(s) {', '.join(f['news']['guard_jobs'])} use news (the 'step aside for news' strategy); every other job only sees prices (section 6)."),
        "It can't trade while the PC is off, asleep or offline, and it can't fix a broken internet connection.",
        "Market orders fill at the best available price, which can differ from the last price it saw, especially at the open and in fast markets.",
        "It never trades real money, and results on paper can be better than real trading would be.",
    ])]

    # 2
    B += [("h2", "2. Before the market opens")]
    B += [("p", f"The US market opens at 09:30 New York time, which is usually <b>{open_common}</b> in Skopje (see the table in section 4 for the weeks when it differs).")]
    B += [("ol", [
        "Switch the PC on and make sure it won't sleep: Windows Settings → System → Power → <i>Sleep</i> = <b>Never</b> (when plugged in). The bot stops trading if the PC sleeps.",
        "Double-click <b>start_lab.bat</b> in E:\\autobot. Two windows open (\"autobot API\" and \"autobot web\") and the lab opens in the browser at http://127.0.0.1:5190. Leave both windows open all day.",
        "Open the <b>Live</b> page. Check the small dot next to <i>live</i> is <b>green</b> (connected) and the header says <b>API</b> with a green dot and <b>paper only</b>.",
        "Check <i>Paper equity</i> shows a dollar amount and the account status is ACTIVE.",
        "Scroll to <b>Jobs · config.yaml</b> and check which jobs are switched <b>on</b> and on which symbols (below). Change nothing on the day unless it was tested in the Lab first.",
    ])]
    B += [("img", "img/live_top.png", "The Live page when everything is connected: green dots next to API and live, the paper-only badge, the account figures.")]
    B += [("table", ["Job", "On?", "Strategy", "Symbols", "Share of account per symbol"],
           [[j["name"], "on" if j.get("enabled", True) else "off", f"{j['strategy']} ({plain.nick(j['strategy'])})",
             ", ".join(j["symbols"]), f"{j['allocation_pct']}%"] for j in jobs])]
    B += [("img", "img/jobs_editor.png", "The Jobs editor. Switch a job on or off with its checkbox, then Save. The bot applies saved changes at its next bar.")]

    B += [("h3", "Automatic start (optional)")]
    B += [("p", "The bot and the lab can start by themselves 2 minutes after you log on to Windows (Task Scheduler tasks "
                "<b>Spire autobot</b> and <b>Spire autobot lab</b>). Turn it on with <b>autostart_on.bat</b> and off with "
                "<b>autostart_off.bat</b> in E:\\autobot; turning it off doesn't stop anything already running. "
                "Started this way, a supervisor restarts the bot after a crash (at most 3 times an hour) and writes each crash "
                "to logs/bot.log. If a bot is already running, a second one exits quietly.")]

    # 3
    B += [("h2", "3. Starting the bot")]
    B += [("ol", [
        "On the <b>Live</b> page press <b>▶ Start bot</b> (top right), or double-click <b>start_bot.bat</b> "
        "(this one also restarts the bot after a crash).",
        "Within a few seconds the status changes to <b>BOT RUNNING</b> with a green dot, a process number (pid) and <i>beat 1s ago</i>. The beat number must stay small (under 10 seconds).",
        f"Wait for the first tick: within one {tf} bar, <i>Bot last tick</i> shows the current time and the <i>Bot view per symbol</i> table fills in.",
        "If Start is greyed out, a bot is already running (or a stuck one is still alive, shown as <i>unresponsive</i>). Never start a second bot.",
    ])]
    B += [("img", "img/live_state.png", "What 'running' looks like: BOT RUNNING, the pid, and a heartbeat a second or two old.")]
    B += [("h3", "Reading the trigger and peak lines")]
    B += [("ul", [plain.LIVE_VIEW[k] for k in ("trigger", "already_past", "none", "peak", "display_only")])]

    # 4
    B += [("h2", "4. Stopping the bot")]
    B += [("h3", "Normal stop")]
    B += [("ol", [
        "Live page → <b>⏹ Stop + flatten</b> (or double-click <b>stop_bot.bat</b>).",
        "Read the confirmation and press <b>Stop + flatten</b>. The bot closes <b>every</b> open position in the paper account (including ones it didn't open) and exits within a few seconds.",
        "Confirm all is flat (see below).",
    ])]
    B += [("img", "img/stop_dialog.png", "The confirmation dialog. Cancel changes nothing.")]
    B += [("h3", "Emergency stop")]
    B += [("ol", [
        "If the page doesn't respond: create an empty file named <b>STOP</b> (no extension) in E:\\autobot. In PowerShell: <code>New-Item E:\\autobot\\STOP</code>. The bot checks for it every few seconds, closes everything and exits; if the bot is stuck, its watchdog does the same within about 10 seconds.",
        "If that doesn't work within a minute: on the Alpaca website (paper account) → Positions → <b>Close all positions</b>, then end the <i>python … live.py</i> process in Task Manager.",
    ])]
    B += [("h3", "Automatic close-of-day flatten")]
    B += [("p", f"The bot closes all stock positions {fm} minutes before the US close (15:{60 - fm:02d} New York). "
                f"That is usually <b>{common_flat}</b> in Skopje, but it shifts for a few weeks when the US and Europe change their clocks on different dates:")]
    B += [("table", [f"Dates in {f['year']}", "Close-of-day flatten, Skopje time"], time_table(f["year"], flat_ny))]
    B += [("h3", "How to confirm all is flat")]
    B += [("ul", ["Live page → <b>Positions</b> says <i>No open positions</i>.",
                  "The Alpaca website (paper account) shows no positions and no open orders.",
                  "The status shows <b>BOT STOPPED</b> (unless you intend it to keep running)."])]

    # 5
    B += [("h2", "5. The strategies in plain words")]
    by_fam = {}
    for name, spec in STRATEGIES.items():
        by_fam.setdefault(spec.family, []).append(name)
    for fam in ["benchmark", "trend", "mean_reversion", "breakout", "intraday", "meta", "news"]:
        names = by_fam.get(fam, [])
        if not names:
            continue
        B += [("h3", FAMILY.get(fam, fam))]
        B += [("cards", [dict(title=f"{plain.nick(n).capitalize()}", tag=n, lines=[
            plain.STRATEGIES[n]["idea"], f"<b>Likes:</b> {plain.STRATEGIES[n]['likes']}",
            f"<b>Hates:</b> {plain.STRATEGIES[n]['hates']}", f"<b>Typical behaviour:</b> {plain.STRATEGIES[n]['behavior']}"])
            for n in names if n in plain.STRATEGIES])]
    B += [("h3", "Market moods (regimes)")]
    B += [("cards", [dict(title=v["name"].capitalize(), tag=k, lines=[v["explain"]]) for k, v in plain.REGIMES.items()])]
    B += [("h3", "Shifting methods")]
    B += [("cards", [dict(title=v["name"], tag=k, lines=[v["explain"]]) for k, v in plain.METHODS.items()])]

    # 6
    B += news_section(f)

    # 7
    B += auto_section(f)

    # 8
    B += [("h2", "8. What to watch: daily checklist")]
    lim = R["max_daily_loss_pct"]
    B += [("p", "Look at the Live page a few times a day and read the daily report after the close. "
                "<span class='dot green'></span>green = fine, <span class='dot amber'></span>amber = keep an eye on it, "
                "<span class='dot red'></span>red = act now.")]
    B += [("table", ["Check", "Green", "Amber", "Red: what to do"], [
        ["Day P&L vs the daily loss limit", f"above -{lim / 2:g}%", f"-{lim / 2:g}% to -{lim * 0.8:g}%",
         f"below -{lim * 0.8:g}% or the bot halted: the limit ({lim}%) will stop it; don't restart it the same day"],
        ["Deepest dip this week (weekly report)", f"less than {lim:g}%", f"{lim:g}% to {lim * 2:g}%", f"more than {lim * 2:g}%: review the strategies (section 10)"],
        ["Number of trades vs a normal day", "similar to the last 5 reports", "twice as many or none at all",
         "many times more: check the log and the jobs; stop the bot if unsure"],
        ["Errors in the bot log (Live page)", "none", "an occasional data error (the bot skips that bar)",
         "errors every bar, or CRITICAL/WATCHDOG lines: see troubleshooting"],
        ["Last tick / heartbeat", f"last tick within one {tf} bar; beat under 10 s", "last tick 1-3 bars old",
         f"heartbeat over {f['heartbeat_stale']} s, status 'unresponsive', or last tick more than 3 bars old: stop and restart"],
        ["Strategy switching (reports, Shifting jobs)", "0-4 switches a day per symbol", f"5-{f['switch_limit']} a day",
         f"more than {f['switch_limit']} a day: switching costs money; test a larger confirm_bars in the Lab"],
        ["A symbol stuck in a position", "positions change when signals change", "an intraday strategy still holding late in the day",
         "a position held with no job on that symbol: close it on the Live page or the Alpaca website"],
        ["Nightly strategy check (Recalibrate page)", "the proposal date is the last trading day; status applied (or nothing to change)",
         "items <i>waiting to apply</i> for more than a day (a coin position that never closes)",
         "<i>Last automatic run failed</i>: read the message; the bot keeps yesterday's jobs. Press Run now once the cause is fixed"],
        ["News collector (News page, top)","<i>news collector running</i>, last story received within the hour during market hours",
         "nothing received for 1-2 hours while the market is open",
         "<i>stopped</i>, or the daily report says it wasn't receiving stories live: double-click <b>start_news.bat</b> (trading isn't affected)"],
    ])]

    # 9
    B += [("h2", "9. Troubleshooting")]
    B += [("table", ["Symptom", "Likely cause", "What to do"], [
        ["Header shows <b>API down</b>", "The 'autobot API' window was closed or crashed", "Run start_lab.bat again. The bot keeps running on its own; check it on the Live page."],
        ["Live page says <i>reconnecting…</i>", "The API is restarting or not running", "Wait 10 seconds; if it stays, run start_lab.bat."],
        ["Status <b>unresponsive</b>", f"The bot process is alive but hasn't beaten for {f['heartbeat_stale']} s (stuck)",
         f"Press Stop + flatten. If nothing happens within a minute, use the emergency stop. The watchdog also exits a bot stuck for {f['stall']:.0f} s."],
        ["Bot stopped by itself", "Daily loss limit, a stall, or the PC slept", "Read the bot log tail on the Live page (look for FLATTEN, WATCHDOG, CRITICAL). Restart only if you understand why."],
        ["<b>Start</b> refused: 'already running'", "A bot is still running", "Don't start another. Stop the running one first if needed."],
        ["Jobs editor won't save", "A rule was broken (message in red), e.g. one symbol in two jobs", "Fix what the message says, then Save."],
        ["No orders all day", "Market closed, signals said 'stay out', or the allocation buys less than 1 share", "Check the Bot view per symbol table (signal and target) and the reasons in the daily report."],
        ["Report says <i>reason not recorded</i>", "The trade happened while an older bot version without the decision log was running", "Nothing to fix; new trades will have reasons."],
        ["No daily report", "The lab wasn't running 10 minutes after the close", "Reports page → pick the date → Generate."],
        ["'data/signal error' lines in the log", "Internet or Alpaca data problem", "The bot skips those bars. If it lasts, check your connection and Alpaca's status page."],
        ["News page says <i>news collector stopped</i>", "The 'autobot news' window was closed, or the PC slept",
         "Double-click <b>start_news.bat</b>. It fills in the stories it missed, then collects live again. Trading is not affected."],
        ["Report's news section says <i>No news database yet</i>", "The news collector has never run on this PC", "Double-click <b>start_news.bat</b> and leave it running; the first start downloads two years of stories (a few minutes)."],
        ["Report says a story was 'filled in afterwards'", "The collector wasn't running live that day; it caught up later", "Nothing to fix for that day; keep the 'autobot news' window open on trading days."],
        ["A symbol from the trading list has no job", "On the test days nothing beat doing nothing (or too few trades)",
         "Nothing to fix: the reason is on the Recalibrate page. It is re-checked every night."],
        ["You want to undo a strategy change", "The nightly check switched a job you preferred",
         "Copy the newest file from data\\recalibration\\config_backups over config.yaml (the bot uses it from its next bar), then set "
         "<code>recalibration: mode: propose</code> so it isn't changed again tonight without your Apply."],
        ["Account result in a report looks far too big","The broker's history counted a deposit (it does this in a paper account's first days)",
         "The report leaves out jumps over 10% in 5 minutes and says so in a note at the top; if there is no note, check the Alpaca website."],
    ])]

    # 10
    B += [("h2", "10. Weekly review")]
    B += [("ol", [
        "Reports page → <b>Last 7 days</b>. Read the headline, then the charts.",
        "Check: the result against simply holding SPY, the number of trades, the worst trade, the deepest dip, and the 'What didn't' list.",
        "Compare with the last few weeks' reports; one bad week alone says little.",
        "Earnings dates: if a company we trade has announced its next results date, add it to config.yaml (section 6, <i>Earnings dates</i>).",
        "Recalibrate page: how many strategy changes happened this week and why (the reasons are on the page and in the daily reports). "
        "Several changes on the same symbol week after week means no strategy works well there; consider leaving it out of the trading list.",
    ])]
    B += [("p", "<b>When to change or switch off a strategy</b>")]
    B += [("ul", [
        "It trailed simply holding SPY for three weeks in a row while trading a lot.",
        f"Its deepest dip in a week was more than {lim * 2:g}%.",
        "The report keeps saying 'many small losses in a sideways market' for a trend strategy (or the reverse for a dip-buying strategy).",
        "It switches strategies more than 8 times a day on a symbol, week after week.",
    ])]
    B += [("note", "Always test a change in the Lab first: Lab → run it on Alpaca data (at least 90 days), then Search → Walk-forward. "
                   "Only change the live job if the walk-forward result is better than what runs today. Change one thing at a time, and note the date so the reports can be compared.")]

    B += [("h2", "Keeping this document up to date")]
    B += [("p", "Run <code>python scripts/build_sop.py --shots</code> with the lab running. It re-reads the strategies, limits, jobs and times, takes fresh screenshots and rebuilds this document (Markdown, HTML and PDF).")]
    return B


def news_section(f: dict) -> list:
    """Section 6: the news collector, what the scores mean, what the studies found, earnings dates."""
    from autobot.news_score import PROMPT_VERSION, RULES_VERSION
    from autobot.report_news import MATERIALITY, TRADE_WINDOW, UPCOMING_DAYS
    n = f["news"]
    win = int(TRADE_WINDOW.total_seconds() // 60)
    B = [("h2", "6. News and earnings")]
    B += [("p", f"A separate program, the <b>news collector</b>, saves every Alpaca/Benzinga news story about "
                f"{', '.join(n['symbols']) or 'our symbols'} to data/news.db. It starts with start_lab.bat (window <i>autobot news</i>) "
                "or on its own with <b>start_news.bat</b>. It never trades, and the bot trades the same whether it runs or not; "
                "it only feeds the news sections of the reports and the News page.")]
    B += [("h3", "Checking it runs")]
    B += [("ul", [
        "News page, top line: a green dot and <i>news collector running</i>, with a beat a few seconds old.",
        "<i>Last received</i> shows a recent New York time while the market is open (stories arrive every few minutes on a normal day).",
        "The daily report warns under <i>Things to watch</i> if the collector wasn't receiving stories live that day.",
    ])]
    B += [("img", "img/news_top.png", "The top of the News page: collector status and the story counts.")]
    B += [("h3", "What the reports show")]
    B += [("ul", [
        f"<b>News on our symbols</b>: for each symbol, the number of material stories, up to 5 headlines, whether it was an earnings day, "
        "and the day's biggest 15-minute move with whether a story arrived in the hour before it. That is timing only: it does not mean the story caused the move.",
        f"<b>News near a trade</b>: when a material story arrived up to {win} minutes before a buy or sell, the trade's sentence names it and says it is context only.",
        "Small diamonds above the price charts mark material stories.",
        f"<b>Things to watch</b> lists the earnings dates in the next {UPCOMING_DAYS} days that are listed in config.yaml.",
    ])]
    B += [("h3", "What the scores mean")]
    B += [("p", f"Every story gets a <b>materiality</b> score from 1 (routine) to 5 (major) and a category (earnings, analyst rating, macro …). "
                f"Reports show stories at materiality {MATERIALITY['stock']} or more for companies and {MATERIALITY['etf']} or more for index funds "
                "(SPY, QQQ), because general market news names the index funds all day. "
                f"The scores come from fixed <b>keyword rules</b> ({RULES_VERSION}), which are rough and sometimes misfile a story. "
                f"Claude (prompt {PROMPT_VERSION}) scored only a sample; scoring more costs money and is started from the News page, "
                "which shows the cost first.")]
    B += [("h3", "What we found")]
    found = []
    if "bigger" in n:
        names = [{"other": "other news", "macro": "the economy"}.get(x, x + "s" if x.endswith("rating") else x) for x in n["bigger"]["groups"]]
        found.append(f"On days with news, prices moved <b>{n['bigger']['lo']}-{n['bigger']['hi']}% more than usual</b> by the close "
                     f"(stories on {', '.join(names[:-1]) + ' and ' + names[-1] if len(names) > 1 else names[0]}).")
    if "direction" in n:
        d = n["direction"]
        found.append(("Whether a story sounded good or bad <b>did not tell which way the price would go</b>: "
                      f"{d['beyond']} of {d['total']} measurements were beyond chance, no more than luck alone gives.")
                     if d["beyond"] <= max(1, round(d["total"] * 0.05)) else
                     f"Good or bad news was followed by a move in its direction beyond chance in {d['beyond']} of {d['total']} measurements; see the News page.")
    if "spikes" in n:
        found.append(f"After a big 15-minute move, the price kept going by the close {n['spikes']['news']}% of the time with news and "
                     f"{n['spikes']['no_news']}% without: about a coin flip either way.")
    if "guard" in n:
        g = n["guard"]
        if g["better"]:
            found.append("Pausing trading around news (the <i>step aside for news</i> strategy) did better than the strategy alone beyond chance in: "
                         + "; ".join(f"{grp} {base} {v.replace('_', ' ')}" for grp, base, v in g["better"]) + ". See the News page before using it.")
        else:
            found.append(f"Pausing trading around news (the <i>step aside for news</i> strategy) <b>did not do better than the strategy alone</b> "
                         f"beyond chance in a backtest from {g['start']} to {g['end']}"
                         + (", and going flat around index-fund news did slightly worse" if any(grp == "etfs" for grp, _, _ in g["worse"]) else "")
                         + (". No live job uses it." if not n["guard_jobs"] else f". Jobs using it now: {', '.join(n['guard_jobs'])}."))
    B += [("ul", found or ["The studies haven't been run yet (News page → Recompute)."])]
    if "event_period" in n:
        B += [("p", f"<span class='muted'>From the news studies on the News page ({n['event_period'][0]} to {n['event_period'][1]}); "
                    "this list is rebuilt from their latest results each time this document is generated.</span>")]
    B += [("img", "img/news_impact.png", "The event study on the News page: how prices moved after news, with 95% ranges.")]
    B += [("img", "img/news_guard.png", "The news-guard backtest: each variant against the strategy alone.")]
    B += [("h3", "Earnings dates")]
    det = n.get("detector")
    B += [("p", "Prices often move more around a company's quarterly results. The reports, and the earnings pause in the "
                "<i>step aside for news</i> strategy, use the dates listed in config.yaml under <code>news: earnings_dates</code>; "
                "after the last listed date, dates found in the news fill in. The news alone can't be relied on to find them"
                + (f" (it found {det['exact']} of {det['total']} correctly in a check)" if det else "") + ".")]
    B += [("table", ["Symbol", "Last listed date", "Next listed date"],
           [[s, v[-1] if v else "–", n["next"].get(s) or "<b>none: add it when announced</b>"] for s, v in n["listed"].items()])]
    B += [("ol", [
        "When a company announces its next results date (its investor-relations page or the news), work out the session that reacts: "
        "the same day if results come before the open, the <b>next trading day</b> if they come after the close (most do).",
        "Open config.yaml in E:\\autobot with Notepad and add the date under the symbol, in the same format: <code>- 'YYYY-MM-DD'</code>.",
        "Save. Reports pick it up at the next run; the bot doesn't need a restart. Saving jobs in the lab keeps these dates.",
    ])]
    return B


def auto_section(f: dict) -> list:
    """Section 7: universe, nightly recalibration, applying, sizing and the profit lock."""
    a = f["auto"]
    r, u, sz, lock = a["r"], a["u"], a["sizing"], a["lock"]
    B = [("h2", "7. Automatic strategy selection")]
    B += [("p", "Every night after the close the bot re-checks which strategy each symbol should trade. "
                + ("<b>It changes the jobs itself</b> (mode <i>apply</i>)" if a["mode"] == "apply"
                   else "It only <b>proposes</b> changes (mode <i>propose</i>); nothing changes until you press Apply")
                + (". Every symbol on the trading list is managed this way, including those that had hand-made jobs."
                   if a["take_over"] else ". Symbols of your own hand-made jobs are left alone."))]
    B += [("h3", "The trading list (universe)")]
    B += [("p", f"Every {u['every_days']} days the bot ranks the candidates of each group on how much they trade and how much they move "
                f"(the last {u['lookback_days']} trading days):")]
    B += [("table", ["Group", "How many", "Candidates"],
           [[n.replace("_", " "), f"{b['min']}-{b['max']}" if b["min"] != b["max"] else str(b["max"]), ", ".join(b["candidates"])]
            for n, b in u["buckets"].items()])]
    B += [("p", f"A symbol already on the list stays until it drops more than {u['hysteresis']} places below the cut, so the list doesn't change "
                "for small moves. The trading figures come from the free IEX feed, which sees only part of the market: they are only used "
                "to compare symbols with each other.")]
    B += [("h3", "The nightly check (recalibration)")]
    B += [("ol", [
        f"For each symbol, every strategy and setting is tested on the last {r['lookback_days']} days in turns: tuned on {r['train_days']} days, "
        f"then tried on the next {r['test_days']} days it had not seen. Costs are included ({r['costs']['stock']['fee_bps'] + r['costs']['stock']['slippage_bps']:g} bp a trade "
        f"for stocks, {r['costs']['crypto']['fee_bps'] + r['costs']['crypto']['slippage_bps']:g} bp for crypto), and stocks are sold before the close, like the bot does.",
        f"It also builds 'switch by market mood' and 'back the recent winner' combinations from the best {r['top_k']} strategies.",
        "Each option gets a score from the test days only (Sharpe, return, deepest dip and trading). Doing nothing scores 0.",
        f"A new strategy replaces the current one only if it scores at least {r['min_edge']} better, made {r['min_trades']}+ trades, "
        f"the current one has been in place {r['min_hold_days']}+ days, and the symbol hasn't already changed "
        f"{r['max_changes_per_week']} time{'s' if r['max_changes_per_week'] != 1 else ''} in the last 7 days. "
        "A symbol where nothing beats doing nothing gets no job.",
    ])]
    B += [("p", "Every decision has a plain-English reason, shown on the Recalibrate page and in the next daily report (<i>Strategy changes</i>).")]
    B += [("img", "img/recal.png", "The Recalibrate page: current and proposed strategy per symbol, the scores on unseen days, and the reason.")]
    B += [("h3", "When changes take effect")]
    B += [("ul", [
        "Stock jobs change only while the market is closed (the bot holds no stocks overnight), so a change starts at the next open.",
        "A crypto job changes only when the bot holds none of that coin; until then it shows as <i>waiting to apply</i>.",
        "The new settings are checked first; if they fail, nothing is written. A copy of the old config.yaml is kept in data\\recalibration\\config_backups.",
        "A symbol that leaves every job has its position closed by the bot.",
    ])]
    B += [("h3", "Position size")]
    B += [("p", (f"<b>Now: fixed.</b> Each symbol gets its job's share of the account, at most ${sz['max_position_usd']:,.0f}."
                 if sz["sizing"] != "risk_balanced" else
                 f"<b>Now: risk-balanced.</b> {sz['gross_budget_pct']:g}% of the account is shared out so calmer symbols get more dollars "
                 f"(based on the last {sz['atr_days']} days' daily range), at most ${sz['max_position_usd']:,.0f} per symbol"
                 + "".join(f" and {v:g}% of the budget for {k}" for k, v in sz["bucket_max_pct"].items()) + ".")
                + " The other mode is available in config.yaml (<code>risk: sizing</code>); compare it in the Lab first.")]
    B += [("h3", "Profit lock")]
    B += [("p", (f"<b>On:</b> once the day's gain reaches {lock.get('trigger_pct')}%, if it then falls back by more than {lock.get('giveback_pct')}% of the day's best, "
                 "the bot sells everything and stops for the day (like the loss limit)." if lock.get("enabled") else
                 f"<b>Off.</b> When switched on (<code>risk: profit_lock: enabled: true</code>), once the day's gain reaches {lock.get('trigger_pct', 1.5)}%, "
                 f"a fall of more than {lock.get('giveback_pct', 50)}% from the day's best makes the bot sell everything and stop for the day.")
                + " The Lab page (<i>Account rules</i>) shows what it would have done on recent prices.")]
    return B


# ----------------------------------------------------------------- renderers
def _md_inline(s: str) -> str:
    import re
    s = re.sub(r"<b>(.*?)</b>", r"**\1**", s)
    s = re.sub(r"<i>(.*?)</i>", r"*\1*", s)
    s = re.sub(r"<code>(.*?)</code>", r"`\1`", s)
    s = re.sub(r"<span class='dot (\w+)'></span>", lambda m: {"green": "🟢 ", "amber": "🟠 ", "red": "🔴 "}[m.group(1)], s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def to_md(blocks) -> str:
    out = ["# Operating procedure: Spire Analytics paper-trading bot", ""]
    for b in blocks:
        k = b[0]
        if k == "h2":
            out += [f"## {b[1]}", ""]
        elif k == "h3":
            out += [f"### {b[1]}", ""]
        elif k in ("p", "note"):
            out += [("> " if k == "note" else "") + _md_inline(b[1]), ""]
        elif k in ("ol", "ul"):
            out += [f"{i + 1}. {_md_inline(x)}" if k == "ol" else f"- {_md_inline(x)}" for i, x in enumerate(b[1])] + [""]
        elif k == "table":
            out += ["| " + " | ".join(b[1]) + " |", "|" + "---|" * len(b[1])]
            out += ["| " + " | ".join(_md_inline(str(c)) for c in row) + " |" for row in b[2]] + [""]
        elif k == "img":
            out += [f"![{b[2]}]({b[1]})", f"*{b[2]}*", ""]
        elif k == "cards":
            for c in b[1]:
                out += [f"**{c['title']}** (`{c['tag']}`)", ""] + [f"- {_md_inline(x)}" for x in c["lines"]] + [""]
    return "\n".join(out)


def to_html(blocks) -> str:
    css = (ROOT / "autobot/assets/brand.css").read_text(encoding="utf-8")
    body = []
    for b in blocks:
        k = b[0]
        if k == "h2":
            body.append(f"<h2>{b[1]}</h2>")
        elif k == "h3":
            body.append(f"<h3>{b[1]}</h3>")
        elif k == "p":
            body.append(f"<p>{b[1]}</p>")
        elif k == "note":
            body.append(f"<p class='note'>{b[1]}</p>")
        elif k in ("ol", "ul"):
            body.append(f"<{k}>" + "".join(f"<li>{x}</li>" for x in b[1]) + f"</{k}>")
        elif k == "table":
            body.append("<table><tr>" + "".join(f"<th>{h}</th>" for h in b[1]) + "</tr>"
                        + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in b[2]) + "</table>")
        elif k == "img":
            if (DOCS / b[1]).exists():
                body.append(f"<figure><img src='{b[1]}' alt='{html.escape(b[2])}' style='border:1px solid var(--line);border-radius:10px'>"
                            f"<figcaption>{html.escape(b[2])}</figcaption></figure>")
        elif k == "cards":
            body.append("<div class='grid'>" + "".join(
                f"<div class='card sunk'><h3 style='margin-top:0'>{html.escape(c['title'])} <span class='tag'>{c['tag']}</span></h3>"
                + "".join(f"<p style='margin:4px 0'>{x}</p>" for x in c["lines"]) + "</div>" for c in b[1]) + "</div>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Operating procedure · Spire Analytics</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Manrope:wght@600;700;800&display=swap" rel="stylesheet">
<style>{css}
body{{margin:0}} code{{background:var(--sunk);border:1px solid var(--line);border-radius:5px;padding:0 5px;font-size:12.5px}}
.sp-doc td:first-child{{font-weight:600}}
</style></head><body class="sp-doc">
<header class="spire-hdr"><span style="display:flex;align-items:center;gap:10px"><span class="mark" aria-hidden="true"></span>
<span><span class="word">SPIRE</span><span class="sub">ANALYTICS</span></span></span><span class="page">Operating procedure</span></header>
<main><h1>How to run the paper-trading bot</h1>
{"".join(body)}
<footer>Generated from the bot's code and config.yaml by scripts/build_sop.py. Paper trading only: no real money is involved.</footer>
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true", help="refresh screenshots from the running lab first")
    ap.add_argument("--no-pdf", action="store_true")
    a = ap.parse_args()
    if a.shots:
        subprocess.run(["node", str(ROOT / "web/scripts/sop-shots.mjs"), str(DOCS / "img")], check=True, cwd=ROOT / "web")
    blocks = build(facts())
    DOCS.mkdir(exist_ok=True)
    (DOCS / "SOP.md").write_text(to_md(blocks), encoding="utf-8")
    (DOCS / "SOP.html").write_text(to_html(blocks), encoding="utf-8")
    print("wrote docs/SOP.md, docs/SOP.html")
    if not a.no_pdf:
        pdf = to_pdf(DOCS / "SOP.html")
        print("wrote", pdf if pdf else "(no PDF: Edge not found)")


if __name__ == "__main__":
    main()
