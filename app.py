"""
autobot dashboard:  streamlit run app.py
Settings, strategy comparison, parameter search and paper-bot control in one place.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml

from autobot.backtest import CostModel, best_per_strategy, compare_strategies, parameter_search
from autobot.data import TIMEFRAMES, fetch_bars, synthetic_bars
from autobot.strategies import STRATEGIES

ROOT = Path(__file__).resolve().parent
CFG_PATH = ROOT / "config.yaml"
LOGS = ROOT / "logs"

st.set_page_config(page_title="autobot", page_icon="📈", layout="wide")


def load_cfg():
    return yaml.safe_load(CFG_PATH.read_text())


def save_cfg(cfg):
    CFG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))


cfg = load_cfg()
bt_cfg = cfg["backtest"]

# ======================================================================= sidebar
st.sidebar.title("📈 autobot")
st.sidebar.caption("Alpaca paper trading · strategy lab")
source = st.sidebar.radio("Data source", ["Alpaca (real)", "Demo (synthetic)"],
                          help="Demo data only tests the plumbing. Use Alpaca data for real conclusions.")
symbols = st.sidebar.text_input("Symbols (comma-separated, crypto like BTC/USD)",
                                ", ".join(bt_cfg["symbols"]))
symbols = [s.strip().upper() for s in symbols.split(",") if s.strip()]
tf_opts = list(TIMEFRAMES)
timeframe = st.sidebar.selectbox("Bar timeframe", tf_opts, index=tf_opts.index(bt_cfg["timeframe"]))
days = st.sidebar.slider("History (days)", 5, 730, int(bt_cfg["days"]))
rth = st.sidebar.checkbox("Regular hours only (stocks)", bt_cfg["rth_only"])
c1, c2 = st.sidebar.columns(2)
fee = c1.number_input("Fee (bps)", 0.0, 50.0, float(bt_cfg["fee_bps"]), 0.5)
slip = c2.number_input("Slippage (bps)", 0.0, 50.0, float(bt_cfg["slippage_bps"]), 0.5)
allow_short = st.sidebar.checkbox("Allow shorting", bt_cfg["allow_short"])
train_frac = st.sidebar.slider("Train / test split", 0.5, 0.9, float(bt_cfg["train_frac"]), 0.05)
if st.sidebar.button("💾 Save as default settings", use_container_width=True):
    cfg["backtest"].update(symbols=symbols, timeframe=timeframe, days=days, rth_only=rth,
                           fee_bps=fee, slippage_bps=slip, allow_short=allow_short, train_frac=train_frac)
    save_cfg(cfg)
    st.sidebar.success("Saved to config.yaml")
costs = CostModel(fee, slip)


@st.cache_data(show_spinner="Loading bars…")
def get_data(src, syms, tf, d, rth_):
    if src.startswith("Demo"):
        return synthetic_bars(list(syms), tf, d)
    return fetch_bars(list(syms), tf, d, rth_only=rth_)


def data_or_stop():
    try:
        return get_data(source, tuple(symbols), timeframe, days, rth)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load data: {e}")
        st.stop()


if source.startswith("Demo"):
    st.warning("Demo mode: synthetic prices. Results show the tool works, **not** which strategy is good.")

tab_strat, tab_cmp, tab_search, tab_bot = st.tabs(
    ["⚙️ Strategies", "📊 Compare", "🔎 Parameter search", "🤖 Paper bot"])

# ======================================================================= strategies
with tab_strat:
    st.subheader("Strategy settings")
    st.caption("Pick which strategies to compare and tune their parameters. Buy-and-hold is the benchmark to beat.")
    fam_icon = {"benchmark": "🏁", "trend": "📈", "mean_reversion": "↩️", "breakout": "🚀", "intraday": "⏱️"}
    chosen = {}
    cols = st.columns(3)
    for i, (name, spec) in enumerate(STRATEGIES.items()):
        with cols[i % 3].container(border=True):
            on = st.toggle(f"{fam_icon.get(spec.family, '')} **{name}**", value=True, key=f"on_{name}")
            st.caption(f"*{spec.family}* · {spec.description}")
            params = {}
            for p, v in spec.defaults.items():
                if isinstance(v, float):
                    params[p] = st.number_input(p, value=v, step=0.05, key=f"{name}_{p}", disabled=not on)
                else:
                    params[p] = int(st.number_input(p, value=int(v), step=1, key=f"{name}_{p}", disabled=not on))
            if on:
                chosen[name] = params
    st.session_state["chosen"] = chosen

# ======================================================================= compare
with tab_cmp:
    st.subheader("Head-to-head comparison")
    if st.button("▶ Run backtest", type="primary"):
        data = data_or_stop()
        summary, equity, per_sym = compare_strategies(data, st.session_state["chosen"], costs, allow_short)
        st.session_state["cmp"] = (summary, equity, per_sym)
    if "cmp" in st.session_state:
        summary, equity, per_sym = st.session_state["cmp"]
        best = summary.index[0]
        bh = summary.loc["buy_and_hold"] if "buy_and_hold" in summary.index else None
        k = st.columns(4)
        k[0].metric(f"Best: {best}", f"Sharpe {summary.loc[best, 'sharpe']}")
        k[1].metric("Its return", f"{summary.loc[best, 'total_return_%']}%",
                    None if bh is None else f"{summary.loc[best, 'total_return_%'] - bh['total_return_%']:+.2f}% vs B&H")
        k[2].metric("Its max drawdown", f"{summary.loc[best, 'max_drawdown_%']}%")
        k[3].metric("Avg trades / symbol", f"{summary.loc[best, 'trades']:.0f}")

        fig = go.Figure()
        for c in equity.columns:
            fig.add_trace(go.Scatter(x=equity.index, y=(equity[c] - 1) * 100, name=c,
                                     line=dict(width=3 if c == best else 1.5,
                                               dash="dot" if c == "buy_and_hold" else None)))
        fig.update_layout(title="Equity curve (equal-weight across symbols)", yaxis_title="Return %",
                          height=430, hovermode="x unified", legend=dict(orientation="h", y=-0.15))
        st.plotly_chart(fig, use_container_width=True)

        a, b = st.columns(2)
        dd = (equity / equity.cummax() - 1) * 100
        fig_dd = px.line(dd, title="Drawdown %", height=320)
        fig_dd.update_layout(showlegend=False, yaxis_title="", xaxis_title="")
        a.plotly_chart(fig_dd, use_container_width=True)
        fig_rr = px.scatter(summary.reset_index(), x="max_drawdown_%", y="total_return_%", text="strategy",
                            size=summary["trades"].clip(lower=1).values, title="Return vs drawdown (bubble = trades)",
                            height=320)
        fig_rr.update_traces(textposition="top center")
        b.plotly_chart(fig_rr, use_container_width=True)

        st.markdown("**Metrics**")
        st.dataframe(summary.style.background_gradient(subset=["sharpe", "total_return_%"], cmap="RdYlGn")
                     .format(precision=2), use_container_width=True)
        heat = per_sym.pivot(index="strategy", columns="symbol", values="total_return_%")
        st.plotly_chart(px.imshow(heat, text_auto=".1f", color_continuous_scale="RdYlGn", aspect="auto",
                                  color_continuous_midpoint=0, title="Return % by strategy × symbol"),
                        use_container_width=True)

# ======================================================================= search
with tab_search:
    st.subheader("Search the parameter space")
    st.caption("Every strategy's grid is tested. Parameters are **chosen on the train slice** and "
               "**scored on the unseen test slice**. Points far below the diagonal are overfit.")
    names = st.multiselect("Strategies to search", [n for n in STRATEGIES if STRATEGIES[n].grid],
                           default=[n for n in st.session_state.get("chosen", STRATEGIES) if STRATEGIES[n].grid])
    if st.button("🔎 Run search", type="primary"):
        data = data_or_stop()
        with st.spinner("Searching…"):
            st.session_state["search"] = parameter_search(data, names, costs, train_frac, allow_short)
    if "search" in st.session_state and not st.session_state["search"].empty:
        res = st.session_state["search"]
        fig = px.scatter(res, x="train_sharpe", y="test_sharpe", color="strategy", hover_data=["params"],
                         title="Train vs test Sharpe (every parameter set)", height=460)
        lo, hi = float(min(res.train_sharpe.min(), res.test_sharpe.min())), float(max(res.train_sharpe.max(), res.test_sharpe.max()))
        fig.add_shape(type="line", x0=lo, y0=lo, x1=hi, y1=hi, line=dict(dash="dot", color="gray"))
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("**Best parameters per strategy** (ranked by out-of-sample Sharpe)")
        bps = best_per_strategy(res)
        st.dataframe(bps.drop(columns="_params"), use_container_width=True, hide_index=True)
        with st.expander("All results"):
            st.dataframe(res.drop(columns="_params"), use_container_width=True, hide_index=True)

        st.markdown("**Promote a winner to a paper-trading job**")
        pick = st.selectbox("Strategy", bps.strategy.tolist())
        row = bps[bps.strategy == pick].iloc[0]
        jc1, jc2, jc3 = st.columns([2, 1, 1])
        job_syms = jc1.text_input("Symbols for this job", ", ".join(symbols[:2]))
        alloc = jc2.number_input("Allocation % per symbol", 1, 100, 10)
        if jc3.button("➕ Add job"):
            cfg = load_cfg()
            params = {k: v for k, v in row["_params"].items()}
            cfg["live"]["jobs"].append({"name": f"{pick}_{len(cfg['live']['jobs']) + 1}", "strategy": pick,
                                        "params": params, "symbols": [s.strip().upper() for s in job_syms.split(",")],
                                        "allocation_pct": int(alloc), "enabled": True})
            save_cfg(cfg)
            st.success(f"Added {pick} {params}. See the Paper bot tab.")

# ======================================================================= bot
with tab_bot:
    cfg = load_cfg()
    live = cfg["live"]
    st.subheader("Paper trading bot")
    left, right = st.columns([3, 2])

    with left:
        st.markdown("**Jobs** · one strategy per job, a symbol can only be in one job")
        jobs_df = pd.DataFrame([{**j, "symbols": ", ".join(j["symbols"]), "params": json.dumps(j.get("params", {}))}
                                for j in live["jobs"]])
        edited = st.data_editor(
            jobs_df, num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "strategy": st.column_config.SelectboxColumn(options=list(STRATEGIES), required=True),
                "allocation_pct": st.column_config.NumberColumn("alloc %", min_value=1, max_value=100),
                "enabled": st.column_config.CheckboxColumn(),
                "params": st.column_config.TextColumn(help='JSON, e.g. {"fast": 9, "slow": 21}'),
            })
        st.markdown("**Risk limits**")
        r = live["risk"]
        rc = st.columns(4)
        tf_live = rc[0].selectbox("Bar timeframe", tf_opts, index=tf_opts.index(live["timeframe"]), key="tf_live")
        mdl = rc[1].number_input("Max daily loss %", 0.1, 50.0, float(r["max_daily_loss_pct"]), 0.1)
        mpu = rc[2].number_input("Max $ per symbol", 100, 1_000_000, int(r["max_position_usd"]), 500)
        fbc = rc[3].number_input("Flat N min before close", 0, 60, int(r["flatten_before_close_min"]))
        if st.button("💾 Save bot settings", type="primary"):
            try:
                jobs = []
                for _, j in edited.dropna(subset=["strategy"]).iterrows():
                    jobs.append({"name": j["name"] or j["strategy"], "strategy": j["strategy"],
                                 "params": json.loads(j["params"] or "{}"),
                                 "symbols": [s.strip().upper() for s in str(j["symbols"]).split(",") if s.strip()],
                                 "allocation_pct": int(j["allocation_pct"]), "enabled": bool(j["enabled"])})
                flat = [s for j in jobs if j["enabled"] for s in j["symbols"]]
                dup = {s for s in flat if flat.count(s) > 1}
                if dup:
                    st.error(f"Symbols in more than one enabled job: {', '.join(dup)}")
                else:
                    live.update(timeframe=tf_live, jobs=jobs)
                    live["risk"].update(max_daily_loss_pct=mdl, max_position_usd=mpu, flatten_before_close_min=fbc)
                    save_cfg(cfg)
                    st.success("Saved. Restart the bot to apply.")
            except json.JSONDecodeError as e:
                st.error(f"Bad params JSON: {e}")

    with right:
        st.markdown("**Control**")
        b1, b2, b3 = st.columns(3)
        if b1.button("▶ Start", use_container_width=True):
            (ROOT / "STOP").unlink(missing_ok=True)
            proc = subprocess.Popen([sys.executable, str(ROOT / "live.py")], cwd=ROOT)
            st.session_state["bot_pid"] = proc.pid
            st.success(f"Bot started (pid {proc.pid}). Logs: logs/bot.log")
        if b2.button("⏹ Stop + flatten", use_container_width=True):
            (ROOT / "STOP").touch()
            st.warning("Stop requested. The bot closes all positions on its next tick.")
        if b3.button("🔄 Refresh", use_container_width=True):
            st.rerun()

        status_f = LOGS / "status.json"
        if status_f.exists():
            s = json.loads(status_f.read_text())
            m = st.columns(3)
            m[0].metric("Equity", f"${s['equity']:,.0f}", f"{s['day_pnl_%']:+.2f}% today")
            m[1].metric("Market", "open" if s["market_open"] else "closed")
            m[2].metric("State", "HALTED" if s["halted"] else "running")
            st.caption(f"last tick {s['time']}")
            if s["symbols"]:
                st.dataframe(pd.DataFrame(s["symbols"]).T, use_container_width=True)
        else:
            st.info("No bot status yet. Start the bot to see live state here.")

        st.markdown("**Alpaca paper account**")
        try:
            from alpaca.trading.client import TradingClient
            from autobot.data import _keys
            tc = TradingClient(*_keys(), paper=True)
            acct = tc.get_account()
            st.write(f"Equity **${float(acct.equity):,.2f}** · buying power ${float(acct.buying_power):,.2f}")
            pos = tc.get_all_positions()
            if pos:
                st.dataframe(pd.DataFrame([{"symbol": p.symbol, "qty": p.qty, "avg": p.avg_entry_price,
                                            "price": p.current_price, "P/L $": p.unrealized_pl} for p in pos]),
                             hide_index=True, use_container_width=True)
            else:
                st.caption("No open positions.")
        except Exception as e:  # noqa: BLE001
            st.caption(f"Not connected ({e}). Add paper keys to .env.")

    trades_f = LOGS / "trades.csv"
    if trades_f.exists():
        st.markdown("**Recent orders from the bot**")
        st.dataframe(pd.read_csv(trades_f).tail(50).iloc[::-1], use_container_width=True, hide_index=True)
