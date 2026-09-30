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
          "breakout": "Breakout", "intraday": "Intraday (flat every night)", "meta": "Strategy shifting"}


# ----------------------------------------------------------------- facts from code and config
def facts() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    live = cfg["live"]
    risk = live["risk"]
    return dict(cfg=cfg, live=live, risk=risk, stall=float(live.get("stall_seconds", 300)),  # live.py's default
                heartbeat_stale=HEARTBEAT_STALE_S, switch_limit=SWITCH_LIMIT,
                flatten_min=int(risk.get("flatten_before_close_min", 0)), year=date.today().year)


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
        f"It follows rules you choose (the <b>jobs</b>): each job is one strategy on one or more symbols with a share of the account. It checks the market once per {tf} bar and sends market orders.",
        f"It protects itself: if the account is down {R['max_daily_loss_pct']}% in a day it sells everything and stops for the day; no single symbol gets more than ${R['max_position_usd']:,}; stock positions are closed {fm} minutes before the US close.",
        "It writes down why it acted (logs/decisions.jsonl) and produces a plain-English report after every trading day.",
    ])]
    B += [("p", "<b>What it can't do</b>")]
    B += [("ul", [
        "It can't predict the market or guarantee a profit. Every strategy loses in some markets.",
        "It doesn't read the news; it only sees prices.",
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

    # 3
    B += [("h2", "3. Starting the bot")]
    B += [("ol", [
        "On the <b>Live</b> page press <b>▶ Start bot</b> (top right).",
        "Within a few seconds the status changes to <b>BOT RUNNING</b> with a green dot, a process number (pid) and <i>beat 1s ago</i>. The beat number must stay small (under 10 seconds).",
        f"Wait for the first tick: within one {tf} bar, <i>Bot last tick</i> shows the current time and the <i>Bot view per symbol</i> table fills in.",
        "If Start is greyed out, a bot is already running (or a stuck one is still alive, shown as <i>unresponsive</i>). Never start a second bot.",
    ])]
    B += [("img", "img/live_state.png", "What 'running' looks like: BOT RUNNING, the pid, and a heartbeat a second or two old.")]

    # 4
    B += [("h2", "4. Stopping the bot")]
    B += [("h3", "Normal stop")]
    B += [("ol", [
        "Live page → <b>⏹ Stop + flatten</b>.",
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
    for fam in ["benchmark", "trend", "mean_reversion", "breakout", "intraday", "meta"]:
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
    B += [("h2", "6. What to watch: daily checklist")]
    lim = R["max_daily_loss_pct"]
    B += [("p", "Look at the Live page a few times a day and read the daily report after the close. "
                "<span class='dot green'></span>green = fine, <span class='dot amber'></span>amber = keep an eye on it, "
                "<span class='dot red'></span>red = act now.")]
    B += [("table", ["Check", "Green", "Amber", "Red: what to do"], [
        ["Day P&L vs the daily loss limit", f"above -{lim / 2:g}%", f"-{lim / 2:g}% to -{lim * 0.8:g}%",
         f"below -{lim * 0.8:g}% or the bot halted: the limit ({lim}%) will stop it; don't restart it the same day"],
        ["Deepest dip this week (weekly report)", f"less than {lim:g}%", f"{lim:g}% to {lim * 2:g}%", f"more than {lim * 2:g}%: review the strategies (section 8)"],
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
    ])]

    # 7
    B += [("h2", "7. Troubleshooting")]
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
    ])]

    # 8
    B += [("h2", "8. Weekly review")]
    B += [("ol", [
        "Reports page → <b>Last 7 days</b>. Read the headline, then the charts.",
        "Check: the result against simply holding SPY, the number of trades, the worst trade, the deepest dip, and the 'What didn't' list.",
        "Compare with the last few weeks' reports; one bad week alone says little.",
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
