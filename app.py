"""
NSE 500 & Crypto Screener - Dashboard (v7)
==============================================
Two tabs: "Screener" (the NSE + crypto tables) and "Paper Trades"
(simulated trades logged per user, no real broker connection).

Accounts are stored in a local SQLite DB (screener.db, via db.py).
Login persists across page refreshes using a session token in the
URL query string.

RUN:
    pip install streamlit yfinance pandas numpy openpyxl requests
    streamlit run app.py
"""

import time
from datetime import datetime
import pandas as pd
import streamlit as st
from concurrent.futures import ThreadPoolExecutor, as_completed

from nse500_screener import (
    get_nse500_symbols, get_fundamentals, get_technicals_batch, get_current_prices,
    CONDITION_MAP, write_excel, MAX_WORKERS,
    load_daily_result, save_daily_result,
    get_crypto_screener, load_crypto_result, save_crypto_result,
)
import db

db.init_db()

st.set_page_config(page_title="NSE 500 Screener", page_icon="📈", layout="wide")

# ---------------------------------------------------------------------
# Dark-mode-safe professional styling
# Uses Streamlit's theme CSS variables (--text-color, --background-color,
# --secondary-background-color) instead of hardcoded colors, so it
# reads correctly whether the user is on light or dark theme.
# ---------------------------------------------------------------------
st.markdown("""
<style>
    #MainMenu, footer {visibility: hidden;}
    .main .block-container {padding-top: 1.2rem; padding-bottom: 2.5rem; max-width: 1440px;}
    html, body, [class*="css"] {font-family: 'Segoe UI', Inter, Roboto, sans-serif;}

    .topbar {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 18px; margin-bottom: 10px;
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.25); border-radius: 12px;
    }
    .topbar-left {display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;}
    .app-title {font-size: 1.25rem; font-weight: 800; color: var(--text-color); white-space: nowrap;}
    .app-subtitle {color: var(--text-color); opacity: 0.6; font-size: 0.82rem;}

    .stat-chip {
        display: inline-flex; align-items: baseline; gap: 5px;
        padding: 3px 12px; border-radius: 16px; font-size: 0.82rem;
        background: rgba(128,128,128,0.15); color: var(--text-color); font-weight: 500; margin-right: 6px;
    }
    .stat-chip b {font-weight: 800; color: var(--text-color); font-size: 0.9rem;}

    .status-pill {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 3px 12px; border-radius: 16px; font-size: 0.78rem; font-weight: 600;
    }
    .pill-cached {background: rgba(26,95,180,0.15); color: #4a90e2;}
    .pill-live {background: rgba(26,122,76,0.18); color: #34c98a;}
    .pill-open {background: rgba(26,122,76,0.18); color: #34c98a;}
    .pill-closed {background: rgba(128,128,128,0.2); color: var(--text-color);}

    div.stButton > button[kind="primary"] {
        border-radius: 8px; font-weight: 600; padding: 0.4rem 1.1rem; font-size: 0.85rem;
        background: #1a5fb4; border: none; color: white;
    }
    div.stButton > button[kind="primary"]:hover {background: #144990;}

    .section-title {font-size: 1.15rem; font-weight: 700; color: var(--text-color); margin-bottom: 1px;}
    .section-subtitle {color: var(--text-color); opacity: 0.6; font-size: 0.88rem; margin-bottom: 12px;}

    hr {margin: 0.9rem 0; border-color: rgba(128,128,128,0.25);}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------
# Auth: signup / login with session persistence via URL query param
# ---------------------------------------------------------------------
if "username" not in st.session_state:
    st.session_state.username = None

if st.session_state.username is None:
    token = st.query_params.get("session")
    user_from_token = db.get_user_by_session(token) if token else None
    if user_from_token:
        st.session_state.username = user_from_token

if st.session_state.username is None:
    st.markdown("### 👤 Welcome")
    st.caption("Sign in or create an account. Your paper trades are saved per account.")
    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            u = st.text_input("Username", key="login_u")
            p = st.text_input("Password", type="password", key="login_p")
            submitted = st.form_submit_button("Log In")
        if submitted:
            if db.verify_user(u, p):
                token = db.create_session(u)
                st.session_state.username = u.strip()
                st.query_params["session"] = token
                st.rerun()
            else:
                st.error("Incorrect username or password.")

    with tab_signup:
        with st.form("signup_form"):
            su = st.text_input("Choose a username", key="signup_u")
            sp = st.text_input("Choose a password", type="password", key="signup_p")
            sp2 = st.text_input("Confirm password", type="password", key="signup_p2")
            signed_up = st.form_submit_button("Create Account")
        if signed_up:
            if sp != sp2:
                st.error("Passwords don't match.")
            else:
                ok, msg = db.create_user(su, sp)
                if ok:
                    token = db.create_session(su)
                    st.session_state.username = su.strip()
                    st.query_params["session"] = token
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)
    st.stop()

# ---------------------------------------------------------------------
# Session state defaults
# ---------------------------------------------------------------------
if "result" not in st.session_state:
    st.session_state.result = None
if "meta" not in st.session_state:
    st.session_state.meta = None
if "selected_symbol" not in st.session_state:
    st.session_state.selected_symbol = None
if "selected_crypto_symbol" not in st.session_state:
    st.session_state.selected_crypto_symbol = None
if "crypto_result" not in st.session_state:
    cached_crypto = load_crypto_result()
    st.session_state.crypto_result = cached_crypto.get("df") if cached_crypto else None
    st.session_state.crypto_meta = (
        {"source": "cache", "week": cached_crypto.get("week"), "saved_at": cached_crypto.get("saved_at")}
        if cached_crypto else None
    )

if st.session_state.result is None:
    cached = load_daily_result()
    if cached is not None:
        st.session_state.result = {
            "df_all": cached["df_all"], "df_sound": cached["df_sound"], "df_tech": cached["df_tech"],
        }
        st.session_state.meta = {"source": "cache", "week": cached.get("week"), "saved_at": cached.get("saved_at")}


# ---------------------------------------------------------------------
# TSL rule: for every TSL_TRIGGER_PCT move in the trade's favor, the
# stoploss trails by TSL_STEP_PCT (of entry price). Applied live in
# the Paper Trades tab against the current price - not a background
# job, since this is a simulator with no server-side scheduler.
TSL_TRIGGER_PCT = 10
TSL_STEP_PCT = 3


def compute_trailing_stoploss(side, entry_price, initial_stoploss, current_price):
    """Returns the current effective stoploss for an open position,
    accounting for TSL trailing. direction: +1 for BUY, -1 for SELL."""
    if initial_stoploss is None or current_price is None:
        return initial_stoploss
    direction = 1 if side == "BUY" else -1
    move_pct = ((current_price - entry_price) / entry_price) * 100 * direction
    if move_pct <= 0:
        return initial_stoploss
    steps = int(move_pct // TSL_TRIGGER_PCT)
    if steps <= 0:
        return initial_stoploss
    shift = direction * (TSL_STEP_PCT / 100) * entry_price * steps
    trailed = initial_stoploss + shift
    # SL only ever moves in the favorable direction, never back
    return max(initial_stoploss, trailed) if direction == 1 else min(initial_stoploss, trailed)


def is_stoploss_hit(side, stoploss, current_price):
    if stoploss is None or current_price is None:
        return False
    return current_price <= stoploss if side == "BUY" else current_price >= stoploss


# ---------------------------------------------------------------------
# Paper trade dialog (works for both NSE and crypto symbols)
# ---------------------------------------------------------------------
@st.dialog("Paper Trade")
def paper_trade_dialog(symbol, market, current_price, supertrend_weekly=None):
    st.markdown(f"**{symbol}** ({market}) — current price: {current_price if current_price else 'n/a'}")
    tab_buy, tab_sell = st.tabs(["🟢 Buy", "🔴 Sell"])

    def order_form(side, key_prefix):
        qty = st.number_input("Quantity", min_value=0.0001 if market == "CRYPTO" else 1.0,
                                value=1.0, step=1.0, key=f"{key_prefix}_qty")
        order_type = st.radio("Order Type", ["MARKET", "LIMIT"], horizontal=True, key=f"{key_prefix}_ot")
        price = current_price
        if order_type == "LIMIT":
            price = st.number_input("Limit Price", min_value=0.0, step=0.05,
                                       value=float(current_price or 0), key=f"{key_prefix}_price")

        st.markdown("---")
        default_sl = float(supertrend_weekly) if supertrend_weekly else 0.0
        if supertrend_weekly:
            st.caption(f"📍 Recommended Stoploss Value: **{supertrend_weekly}** (Supertrend Weekly)")
        stoploss = st.number_input("Stoploss", min_value=0.0, step=0.05,
                                     value=default_sl, key=f"{key_prefix}_sl")
        tsl_on = st.checkbox(
            f"Enable Trailing Stop-Loss — every +{TSL_TRIGGER_PCT}% move trails SL by {TSL_STEP_PCT}%",
            key=f"{key_prefix}_tsl")

        if st.button(f"Log {side} Paper Trade", type="primary", key=f"{key_prefix}_submit"):
            if order_type == "MARKET":
                suffix = "-USD" if market == "CRYPTO" else ".NS"
                fresh = get_current_prices([symbol], suffix=suffix)
                fill_price = fresh.get(symbol, current_price)
            else:
                fill_price = price

            if not fill_price:
                st.error("No price available for this symbol right now - try again shortly.")
            else:
                db.add_paper_trade(st.session_state.username, market, symbol, side, qty, order_type, fill_price,
                                    initial_stoploss=stoploss or None, tsl_enabled=tsl_on)
                st.success(f"Paper {side} logged: {qty} {symbol} @ {fill_price}"
                            + (f" | SL {stoploss}" if stoploss else ""))
                st.toast("Paper trade saved.", icon="📝")

    with tab_buy:
        order_form("BUY", "pb")
    with tab_sell:
        order_form("SELL", "ps")


# ---------------------------------------------------------------------
# Top-level tabs
# ---------------------------------------------------------------------
top_left, top_right = st.columns([6, 1])
with top_left:
    st.markdown(f"<span style='opacity:0.6;font-size:0.85rem;'>Signed in as <b>{st.session_state.username}</b></span>",
                 unsafe_allow_html=True)
with top_right:
    if st.button("🚪 Logout", width="stretch"):
        db.clear_session(st.session_state.username)
        st.session_state.username = None
        st.query_params.clear()
        st.rerun()

tab_screener, tab_paper = st.tabs(["📊 Screener", "📝 Paper Trades"])

with tab_screener:
    res = st.session_state.result

    # Compute selection from the table widget's persisted state BEFORE
    # rendering the button - this is what fixes the "disabled at the
    # wrong time" bug. Streamlit syncs widget state (results_table's
    # selection) before the script re-runs, so the value read here
    # already reflects the click that triggered this rerun. Reading it
    # later (after the table itself renders) meant the button below
    # was always one click behind.
    _sel_rows = st.session_state.get("results_table", {}).get("selection", {}).get("rows", [])
    selected_supertrend_weekly = None
    if res is not None and res.get("df_tech") is not None and len(res["df_tech"]) and _sel_rows:
        _srow = res["df_tech"].iloc[_sel_rows[0]]
        st.session_state.selected_symbol = _srow["Symbol"]
        selected_supertrend_weekly = _srow.get("Supertrend (Weekly)")
    else:
        st.session_state.selected_symbol = None

    tb_left, tb_right = st.columns([5, 1.6])
    with tb_left:
        stats_html = ""
        if res is not None:
            n_scanned = len(res["df_all"])
            n_passed = len(res["df_sound"])
            best_score = int(res["df_tech"]["Green Hits"].max()) if res["df_tech"] is not None and len(res["df_tech"]) else 0
            stats_html = (
                f"<span class='stat-chip'>Scanned <b>{n_scanned}</b></span>"
                f"<span class='stat-chip'>Passed Fundamentals <b>{n_passed}</b></span>"
                f"<span class='stat-chip'>Best Score <b>{best_score}/8</b></span>"
            )
        status_html = ""
        if st.session_state.meta:
            m = st.session_state.meta
            if m["source"] == "cache":
                status_html = f"<span class='status-pill pill-cached'>📦 Week {m['week']} · saved {m['saved_at']}</span>"
            else:
                status_html = "<span class='status-pill pill-live'>✅ Freshly updated</span>"

        st.markdown(
            f"<div class='topbar'><div class='topbar-left'>"
            f"<span class='app-title'>📈 NSE 500 Screener</span>"
            f"<span class='app-subtitle'>Fundamentally strong stocks, ranked by live technical breakout signals</span>"
            f"</div><div>{stats_html}{status_html}"
            f"<span class='stat-chip'>👤 {st.session_state.username}</span></div></div>",
            unsafe_allow_html=True,
        )
    with tb_right:
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            run_btn = st.button("▶️ Run", type="primary", width="stretch")
        with btn_col2:
            trade_btn = st.button("📝 Paper Trade", width="stretch")

    _LOADING_MESSAGES = [
        "Gathering market data…", "Reviewing financials…", "Checking price trends…",
        "Applying screening filters…", "Almost there…",
    ]

    def run_full_screen():
        t_start = time.time()
        placeholder = st.empty()

        def show_msg(i):
            placeholder.info(_LOADING_MESSAGES[i % len(_LOADING_MESSAGES)])

        show_msg(0)
        symbols = get_nse500_symbols()
        name_map = {sym: name for sym, name in symbols}

        rows = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(get_fundamentals, s): s for s, _ in symbols}
            last_switch = time.time()
            msg_idx = 0
            for i, fut in enumerate(as_completed(futures), 1):
                sym = futures[fut]
                r = fut.result()
                r["Company Name"] = name_map.get(sym, "")
                rows.append(r)
                if time.time() - last_switch > 2.5:
                    msg_idx += 1
                    show_msg(msg_idx)
                    last_switch = time.time()

        df_all = pd.DataFrame(rows)
        n_failed = df_all["_error"].notna().sum() if "_error" in df_all.columns else 0
        df_all = df_all.drop(columns=["_error"], errors="ignore")

        df_sound = df_all[
            (df_all["Market Cap (Cr)"] > 10000) & (df_all["ROCE (%)"] > 15) &
            (df_all["Sales Growth (%)"] > 10) & (df_all["Profit Growth (%)"] > 10) &
            (df_all["Debt to Equity"] < 0.5)
        ].copy().reset_index(drop=True)

        df_tech = None
        if not df_sound.empty:
            show_msg(msg_idx + 1)
            tech_results = get_technicals_batch(list(df_sound["Symbol"]))
            tech_rows = [dict(t, Symbol=s) for s, t in tech_results.items()]
            df_tech = df_sound.merge(pd.DataFrame(tech_rows), on="Symbol", how="left")

            hits = pd.DataFrame({name: df_tech.apply(
                lambda r: bool(pd.notna(r.get("LTP")) and pd.notna(r.get(name)) and fn(r)), axis=1)
                for name, fn in CONDITION_MAP.items()})
            df_tech["Green Hits"] = hits.sum(axis=1)
            df_tech = df_tech.sort_values("Green Hits", ascending=False).reset_index(drop=True)

        placeholder.empty()

        st.session_state.result = {"df_all": df_all, "df_sound": df_sound, "df_tech": df_tech}
        save_daily_result({"df_all": df_all, "df_sound": df_sound, "df_tech": df_tech})
        reloaded = load_daily_result()
        st.session_state.meta = {"source": "live", "week": reloaded.get("week"), "saved_at": reloaded.get("saved_at")}

        elapsed = time.time() - t_start
        if n_failed:
            st.toast(f"Done in {elapsed:.0f}s — {n_failed} stocks had incomplete data.", icon="⚠️")
        else:
            st.toast(f"Screener updated in {elapsed:.0f}s.", icon="✅")
        st.rerun()

    if run_btn:
        run_full_screen()

    if res is not None and res["df_tech"] is not None and not res["df_tech"].empty:
        df_tech = res["df_tech"]
        current_price = None

        st.markdown(f"<div class='section-title'>Results — {len(df_tech)} stocks, sorted by Green Hits "
                     f"<span style='opacity:0.6;font-weight:500;font-size:0.85em;'>(Indian Market)</span></div>"
                     f"<div class='section-subtitle'>Swing trading resistance breakout stocks with good financials</div>",
                     unsafe_allow_html=True)

        def highlight_row(row):
            styles = [""] * len(row)
            for i, col in enumerate(row.index):
                if col in CONDITION_MAP:
                    try:
                        if pd.notna(row.get("LTP")) and pd.notna(row.get(col)) and CONDITION_MAP[col](row):
                            styles[i] = "background-color: #C6EFCE; color: #006100; font-weight: 600;"
                    except Exception:
                        pass
            return styles

        display_df = df_tech.copy()
        display_df["Symbol"] = display_df["Symbol"] + ","

        st.dataframe(
            display_df.style.apply(highlight_row, axis=1), width="stretch", height=600,
            on_select="rerun", selection_mode="single-row", key="results_table",
        )

        sel = st.session_state.get("results_table", {}).get("selection", {}).get("rows", [])
        if sel:
            row = df_tech.iloc[sel[0]]
            current_price = row.get("LTP")
            st.caption(f"Selected: **{st.session_state.selected_symbol}** (LTP {current_price}) — "
                        f"use **Paper Trade** above.")

        if trade_btn:
            if st.session_state.selected_symbol:
                paper_trade_dialog(st.session_state.selected_symbol, "NSE", current_price,
                                    supertrend_weekly=selected_supertrend_weekly)
            else:
                st.warning("Select a row in the table first, then click Paper Trade.")

        with st.expander("📋 Copy Symbols (comma-separated)"):
            st.code(", ".join(df_tech["Symbol"].tolist()), language=None)

        dl_col1, _ = st.columns([1, 4])
        with dl_col1:
            if st.button("📊 Generate Excel"):
                write_excel(res["df_all"], res["df_sound"], df_tech, output_file="_dashboard_export.xlsx")
                with open("_dashboard_export.xlsx", "rb") as f:
                    st.download_button(
                        "📥 Download NSE500_Screener.xlsx", data=f.read(),
                        file_name="NSE500_Screener.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
    elif res is not None and res["df_tech"] is not None:
        st.markdown("<div class='section-title'>No stocks passed the technical stage</div>", unsafe_allow_html=True)
        st.dataframe(res["df_sound"], width="stretch")
    elif res is not None:
        st.markdown("<div class='section-title'>No stocks passed the fundamental filters</div>", unsafe_allow_html=True)
    else:
        st.info("Click **Run** to get started. Results stay cached for the rest of this week automatically.")

    # -------------------------------------------------------------
    # Crypto section
    # -------------------------------------------------------------
    st.markdown("<hr/>", unsafe_allow_html=True)

    _csel_rows = st.session_state.get("crypto_table", {}).get("selection", {}).get("rows", [])
    selected_crypto_supertrend_weekly = None
    if st.session_state.crypto_result is not None and len(st.session_state.crypto_result) and _csel_rows:
        _crow = st.session_state.crypto_result.iloc[_csel_rows[0]]
        st.session_state.selected_crypto_symbol = _crow["Symbol"]
        selected_crypto_supertrend_weekly = _crow.get("Supertrend (Weekly)")
    else:
        st.session_state.selected_crypto_symbol = None

    crypto_tb_left, crypto_tb_right = st.columns([5, 1.6])
    with crypto_tb_left:
        crypto_stats_html = ""
        if st.session_state.crypto_result is not None and len(st.session_state.crypto_result):
            best_crypto_score = int(st.session_state.crypto_result["Green Hits"].max())
            crypto_stats_html = (
                f"<span class='stat-chip'>Coins <b>{len(st.session_state.crypto_result)}</b></span>"
                f"<span class='stat-chip'>Best Score <b>{best_crypto_score}/8</b></span>"
            )
        crypto_status_html = ""
        if st.session_state.get("crypto_meta"):
            cm = st.session_state.crypto_meta
            if cm["source"] == "cache":
                crypto_status_html = f"<span class='status-pill pill-cached'>📦 Week {cm['week']} · saved {cm['saved_at']}</span>"
            else:
                crypto_status_html = "<span class='status-pill pill-live'>✅ Freshly updated</span>"

        st.markdown(
            f"<div class='topbar'><div class='topbar-left'>"
            f"<span class='app-title'>🪙 Top Crypto</span>"
            f"<span class='app-subtitle'>Top 50 coins by market cap, ranked by the same technical breakout signals</span>"
            f"</div><div>{crypto_stats_html}{crypto_status_html}</div></div>",
            unsafe_allow_html=True,
        )
    with crypto_tb_right:
        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
        cbtn1, cbtn2 = st.columns(2)
        with cbtn1:
            crypto_run_btn = st.button("▶️ Run", type="primary", width="stretch", key="crypto_run")
        with cbtn2:
            crypto_trade_btn = st.button("📝 Paper Trade", width="stretch", key="crypto_trade")

    if crypto_run_btn:
        with st.spinner("Fetching top 50 crypto technicals (batched)…"):
            df_crypto = get_crypto_screener()
        st.session_state.crypto_result = df_crypto
        save_crypto_result(df_crypto)
        reloaded_crypto = load_crypto_result()
        st.session_state.crypto_meta = {"source": "live", "week": reloaded_crypto.get("week"),
                                          "saved_at": reloaded_crypto.get("saved_at")}
        st.toast("Crypto screener updated.", icon="✅")
        st.rerun()

    if st.session_state.crypto_result is not None and len(st.session_state.crypto_result):
        df_crypto = st.session_state.crypto_result
        crypto_current_price = None

        st.markdown(f"<div class='section-title'>Results — {len(df_crypto)} coins, sorted by Green Hits "
                     f"<span style='opacity:0.6;font-weight:500;font-size:0.85em;'>(Crypto)</span></div>"
                     f"<div class='section-subtitle'>Same breakout logic as the equity screener, applied to top-cap coins</div>",
                     unsafe_allow_html=True)

        def highlight_crypto_row(row):
            styles = [""] * len(row)
            for i, col in enumerate(row.index):
                if col in CONDITION_MAP:
                    try:
                        if pd.notna(row.get("LTP")) and pd.notna(row.get(col)) and CONDITION_MAP[col](row):
                            styles[i] = "background-color: #C6EFCE; color: #006100; font-weight: 600;"
                    except Exception:
                        pass
            return styles

        display_df_crypto = df_crypto.copy()
        display_df_crypto["Symbol"] = display_df_crypto["Symbol"] + ","

        st.dataframe(
            display_df_crypto.style.apply(highlight_crypto_row, axis=1), width="stretch", height=500,
            on_select="rerun", selection_mode="single-row", key="crypto_table",
        )

        csel = st.session_state.get("crypto_table", {}).get("selection", {}).get("rows", [])
        if csel:
            crow = df_crypto.iloc[csel[0]]
            st.session_state.selected_crypto_symbol = crow["Symbol"]
            crypto_current_price = crow.get("LTP")
            st.caption(f"Selected: **{st.session_state.selected_crypto_symbol}** (LTP {crypto_current_price}) — "
                        f"use **Paper Trade** above.")
        else:
            st.session_state.selected_crypto_symbol = None

        if crypto_trade_btn:
            if st.session_state.selected_crypto_symbol:
                paper_trade_dialog(st.session_state.selected_crypto_symbol, "CRYPTO", crypto_current_price,
                                    supertrend_weekly=selected_crypto_supertrend_weekly)
            else:
                st.warning("Select a row in the table first, then click Paper Trade.")

        with st.expander("📋 Copy Symbols (comma-separated)"):
            st.code(", ".join(df_crypto["Symbol"].tolist()), language=None)
    else:
        st.info("Click **Run** above to fetch the top-50 crypto technical screen.")


with tab_paper:
    st.markdown("<div class='section-title'>📝 Paper Trades</div>"
                  "<div class='section-subtitle'>Simulated trades - no real money, no broker connection.</div>",
                  unsafe_allow_html=True)

    trades = db.get_paper_trades(st.session_state.username)

    if not trades:
        st.info("No paper trades yet. Select a stock or coin in the Screener tab and click **Paper Trade**.")
    else:
        open_trades = [t for t in trades if t["status"] == "OPEN"]
        closed_trades = [t for t in trades if t["status"] == "CLOSED"]

        if open_trades:
            symbols_nse = [t["symbol"] for t in open_trades if t["market"] == "NSE"]
            symbols_crypto = [t["symbol"] for t in open_trades if t["market"] == "CRYPTO"]
            live_prices = {}
            if symbols_nse:
                live_prices.update(get_current_prices(list(set(symbols_nse)), suffix=".NS"))
            if symbols_crypto:
                live_prices.update(get_current_prices(list(set(symbols_crypto)), suffix="-USD"))

            st.markdown("#### Open Positions")
            any_auto_closed = False
            for t in open_trades:
                cur = live_prices.get(t["symbol"])
                pnl = None
                if cur is not None:
                    direction = 1 if t["side"] == "BUY" else -1
                    pnl = round((cur - t["entry_price"]) * direction * t["quantity"], 2)

                effective_sl = compute_trailing_stoploss(t["side"], t["entry_price"], t["initial_stoploss"], cur) \
                    if t["tsl_enabled"] else t["initial_stoploss"]

                if is_stoploss_hit(t["side"], effective_sl, cur):
                    db.close_paper_trade(t["id"], effective_sl, reason="SL HIT")
                    any_auto_closed = True
                    continue

                pnl_color = "#34c98a" if (pnl or 0) >= 0 else "#e05252"
                c1, c2, c3, c4, c4b, c5, c6 = st.columns([1.3, 0.8, 0.9, 1, 1.1, 1, 0.9])
                c1.markdown(f"**{t['symbol']}** <span style='opacity:0.6;font-size:0.8em;'>({t['market']})</span>", unsafe_allow_html=True)
                c2.markdown(f"<span class='status-pill {'pill-open' if t['side']=='BUY' else 'pill-closed'}'>{t['side']}</span>", unsafe_allow_html=True)
                c3.markdown(f"Qty: {t['quantity']}")
                c4.markdown(f"Entry: {t['entry_price']}")
                c4.markdown(f"<span style='opacity:0.85;'>LTP: <b>{cur if cur is not None else '—'}</b></span>", unsafe_allow_html=True)
                if effective_sl is not None:
                    trail_note = " 📈 trailing" if t["tsl_enabled"] and effective_sl != t["initial_stoploss"] else ""
                    c4b.markdown(f"<span style='opacity:0.7;font-size:0.82em;'>SL: {round(effective_sl, 2)}{trail_note}</span>", unsafe_allow_html=True)
                c5.markdown(f"<span style='color:{pnl_color};font-weight:700;'>P&L: {pnl if pnl is not None else '—'}</span>", unsafe_allow_html=True)
                with c6:
                    if st.button("Close", key=f"close_{t['id']}"):
                        close_price = cur if cur is not None else t["entry_price"]
                        db.close_paper_trade(t["id"], close_price)
                        st.rerun()

            if any_auto_closed:
                st.toast("A position hit its stoploss and was auto-closed.", icon="🛑")
                st.rerun()

        if closed_trades:
            st.markdown("#### Closed Trades")
            rows = []
            for t in closed_trades:
                direction = 1 if t["side"] == "BUY" else -1
                pnl = round((t["close_price"] - t["entry_price"]) * direction * t["quantity"], 2) if t["close_price"] else None
                rows.append({
                    "Symbol": t["symbol"], "Market": t["market"], "Side": t["side"],
                    "Qty": t["quantity"], "Entry": t["entry_price"], "Close": t["close_price"],
                    "Reason": t["close_reason"] or "MANUAL",
                    "P&L": pnl, "Opened": t["created_at"][:16], "Closed": (t["closed_at"] or "")[:16],
                })
            st.dataframe(pd.DataFrame(rows), width="stretch")