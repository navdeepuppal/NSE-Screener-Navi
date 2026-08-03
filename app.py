"""
NSE 500 & Crypto Screener - Dashboard (v9)
==============================================
Four top-level tabs: "Screener" (with NSE 500 / Top Crypto sub-tabs),
"Watchlist", "Paper Trades", and "Notifications". On mobile the
top-level tabs are pinned as a bottom navigation bar via CSS.

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
    get_watchlist_batch,
)
import db

db.init_db()

st.set_page_config(page_title="NSE 500 Screener", page_icon="📈", layout="wide")

# ---------------------------------------------------------------------
# Dark-mode-safe, mobile-responsive professional styling.
# ---------------------------------------------------------------------
st.markdown("""
<style>
    #MainMenu, footer {visibility: hidden;}
    .main .block-container {padding-top: 0.6rem; padding-bottom: 2.5rem; max-width: 1440px;}
    html, body, [class*="css"] {font-family: 'Segoe UI', Inter, Roboto, sans-serif;}

    .signed-in-line {opacity: 0.55; font-size: 0.78rem; margin-bottom: 4px;}

    .topbar {
        display: flex; justify-content: space-between; align-items: center;
        padding: 10px 18px; margin-bottom: 10px; gap: 10px;
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.25); border-radius: 12px;
    }
    .topbar-left {display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; min-width: 0;}
    .app-title {font-size: 1.15rem; font-weight: 800; color: var(--text-color); white-space: nowrap;}
    .app-subtitle {color: var(--text-color); opacity: 0.6; font-size: 0.78rem;}
    .topbar-right {display: flex; align-items: center; flex-wrap: wrap; gap: 6px; flex-shrink: 0;}

    .stat-chip {
        display: inline-flex; align-items: baseline; gap: 5px;
        padding: 3px 12px; border-radius: 16px; font-size: 0.78rem;
        background: rgba(128,128,128,0.15); color: var(--text-color); font-weight: 500; margin-right: 6px;
        white-space: nowrap;
    }
    .stat-chip b {font-weight: 800; color: var(--text-color); font-size: 0.86rem;}

    .status-pill {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 3px 12px; border-radius: 16px; font-size: 0.75rem; font-weight: 600;
        white-space: nowrap;
    }
    .pill-cached {background: rgba(26,95,180,0.15); color: #4a90e2;}
    .pill-live {background: rgba(26,122,76,0.18); color: #34c98a;}
    .pill-open {background: rgba(26,122,76,0.18); color: #34c98a;}
    .pill-closed {background: rgba(128,128,128,0.2); color: var(--text-color);}
    .pill-fear {background: rgba(224,82,82,0.18); color: #e05252;}
    .pill-extreme-fear {background: rgba(224,82,82,0.32); color: #c0392b; font-weight: 800;}
    .pill-greed {background: rgba(26,122,76,0.18); color: #34c98a;}
    .pill-extreme-greed {background: rgba(26,122,76,0.32); color: #1f8f5f; font-weight: 800;}
    .pill-neutral {background: rgba(128,128,128,0.2); color: var(--text-color);}

    div.stButton > button[kind="primary"] {
        border-radius: 8px; font-weight: 600; padding: 0.4rem 1.1rem; font-size: 0.85rem;
        background: #1a5fb4; border: none; color: white;
    }
    div.stButton > button[kind="primary"]:hover {background: #144990;}

    .trade-cta {
        background: rgba(26,122,76,0.1); border: 1px solid rgba(26,122,76,0.35);
        border-radius: 10px; padding: 10px 14px; margin: 8px 0; display: flex;
        justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
    }
    .trade-cta-text {font-size: 0.88rem; color: var(--text-color);}

    .section-title {font-size: 1.05rem; font-weight: 700; color: var(--text-color); margin-bottom: 1px;}
    .section-subtitle {color: var(--text-color); opacity: 0.6; font-size: 0.83rem; margin-bottom: 10px;}

    .watch-card {
        border: 1px solid rgba(128,128,128,0.25); border-radius: 10px;
        padding: 10px 14px; margin-bottom: 8px; background: var(--secondary-background-color);
        display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
    }
    .watch-card-sym {font-weight: 700; font-size: 0.95rem; color: var(--text-color);}
    .watch-card-meta {font-size: 0.78rem; opacity: 0.7; color: var(--text-color);}

    .footer-logout {opacity: 0.7;}

    hr {margin: 0.8rem 0; border-color: rgba(128,128,128,0.25);}

    /* ---------------- Mobile: bottom nav bar for the TOP-LEVEL tabs ---------------- */
    @media (max-width: 680px) {
        .main .block-container {padding-left: 0.6rem; padding-right: 0.6rem; padding-top: 0.4rem; padding-bottom: 76px;}
        .topbar {flex-direction: column; align-items: stretch; padding: 8px 12px;}
        .topbar-left {flex-direction: column; gap: 2px;}
        .app-title {font-size: 1rem;}
        .app-subtitle {font-size: 0.7rem;}
        .stat-chip {font-size: 0.7rem; padding: 2px 9px;}
        .section-title {font-size: 0.95rem;}
        .section-subtitle {font-size: 0.76rem;}
        div[data-testid="stDataFrame"] {font-size: 0.76rem;}

        /* Pin ONLY the top-level tab bar (marked by #toplevel-tabs-marker
           immediately before it) to the bottom of the screen, mimicking a
           native mobile bottom nav. The nested NSE 500 / Top Crypto
           sub-tabs live inside a different DOM parent (a tab-panel), so
           they are never siblings of this marker and are correctly left
           untouched - unlike a :first-of-type guess, this can't
           accidentally match both tab bars. */
        #toplevel-tabs-marker + div[data-testid="stTabs"] div[data-baseweb="tab-list"] {
            position: fixed; bottom: 0; left: 0; right: 0; z-index: 999;
            background: var(--background-color);
            border-top: 1px solid rgba(128,128,128,0.3);
            padding: 4px 4px calc(4px + env(safe-area-inset-bottom));
            justify-content: space-around;
        }
        #toplevel-tabs-marker + div[data-testid="stTabs"] button[data-baseweb="tab"] {
            flex: 1; font-size: 0.72rem;
        }
    }
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
    st.caption("Sign in or create an account. Your paper trades and watchlist are saved per account.")
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

db.ensure_default_watchlist(st.session_state.username)

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
if "watchlist_fetch_done" not in st.session_state:
    st.session_state.watchlist_fetch_done = False
if "watchlist_fetch_errors" not in st.session_state:
    st.session_state.watchlist_fetch_errors = {}

if st.session_state.result is None:
    cached = load_daily_result()
    if cached is not None:
        st.session_state.result = {
            "df_all": cached["df_all"], "df_sound": cached["df_sound"], "df_tech": cached["df_tech"],
        }
        st.session_state.meta = {"source": "cache", "week": cached.get("week"), "saved_at": cached.get("saved_at")}


TSL_TRIGGER_PCT = 10
TSL_STEP_PCT = 3


def compute_trailing_stoploss(side, entry_price, initial_stoploss, current_price):
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
    return max(initial_stoploss, trailed) if direction == 1 else min(initial_stoploss, trailed)


def is_stoploss_hit(side, stoploss, current_price):
    if stoploss is None or current_price is None:
        return False
    return current_price <= stoploss if side == "BUY" else current_price >= stoploss


def condition_pill(condition):
    cls_map = {
        "Fear": "pill-fear", "Extreme Fear": "pill-extreme-fear",
        "Greed": "pill-greed", "Extreme Greed": "pill-extreme-greed",
        "Neutral": "pill-neutral", "N/A": "pill-neutral",
    }
    icon_map = {
        "Fear": "😟", "Extreme Fear": "😱", "Greed": "🤑",
        "Extreme Greed": "🚀", "Neutral": "😐", "N/A": "❔",
    }
    cls = cls_map.get(condition, "pill-neutral")
    icon = icon_map.get(condition, "")
    return f"<span class='status-pill {cls}'>{icon} {condition}</span>"


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


def render_notification_settings(username):
    """Inline notification settings (used in the Notifications tab)."""
    settings = db.get_notification_settings(username)
    st.caption("Get pinged when a symbol in your watchlist moves into Fear / Extreme Fear "
               "(or Greed, if you choose). Connect a channel below — actual message delivery "
               "is coming soon; for now this just saves your preferences.")

    channel = st.radio("Channel", ["WhatsApp", "Telegram"], horizontal=True,
                        index=0 if not settings.get("telegram_chat_id") else 1)

    whatsapp_number = settings.get("whatsapp_number") or ""
    telegram_chat_id = settings.get("telegram_chat_id") or ""

    if channel == "WhatsApp":
        whatsapp_number = st.text_input("WhatsApp number (with country code)",
                                          value=whatsapp_number, placeholder="+91XXXXXXXXXX")
    else:
        telegram_chat_id = st.text_input("Telegram chat ID or @username",
                                           value=telegram_chat_id, placeholder="@yourusername")

    st.markdown("---")
    notify_fear = st.checkbox("Notify on Fear / Extreme Fear", value=bool(settings.get("notify_fear", 1)))
    notify_extreme_only = st.checkbox("Only notify on the Extreme variants",
                                        value=bool(settings.get("notify_extreme_only", 0)))
    frequency = st.selectbox("Frequency", ["daily", "weekly"],
                               index=0 if settings.get("frequency", "daily") == "daily" else 1)

    if st.button("Save Notification Settings", type="primary"):
        db.save_notification_settings(
            username, whatsapp_number, telegram_chat_id,
            notify_fear, notify_extreme_only, frequency,
        )
        st.success("Saved. We'll wire up actual message delivery soon.")
        st.toast("Notification preferences saved.", icon="🔔")


# ---------------------------------------------------------------------
# Minimal header - just who's signed in, no menu/logout clutter here
# ---------------------------------------------------------------------
st.markdown(f"<div class='signed-in-line'>Signed in as <b>{st.session_state.username}</b></div>",
             unsafe_allow_html=True)

st.markdown("<div id='toplevel-tabs-marker'></div>", unsafe_allow_html=True)
tab_screener, tab_watchlist, tab_paper, tab_notifications = st.tabs(
    ["📊 Screener", "👁️ Watchlist", "📝 Paper Trades", "🔔 Notifications"]
)

with tab_screener:
    subtab_nse, subtab_crypto = st.tabs(["🇮🇳 NSE 500", "🪙 Top Crypto"])

    # =================================================================
    # NSE 500 sub-tab
    # =================================================================
    with subtab_nse:
        res = st.session_state.result

        _sel_rows = st.session_state.get("results_table", {}).get("selection", {}).get("rows", [])
        selected_supertrend_weekly = None
        if res is not None and res.get("df_tech") is not None and len(res["df_tech"]) and _sel_rows:
            _srow = res["df_tech"].iloc[_sel_rows[0]]
            st.session_state.selected_symbol = _srow["Symbol"]
            selected_supertrend_weekly = _srow.get("Supertrend (Weekly)")
        else:
            st.session_state.selected_symbol = None

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
            f"</div><div class='topbar-right'>{stats_html}{status_html}</div></div>",
            unsafe_allow_html=True,
        )

        run_btn = st.button("▶️ Run", type="primary", key="nse_run")

        _LOADING_MESSAGES = [
            "Gathering market data…", "Reviewing financials…", "Checking price trends…",
            "Applying screening filters…", "Almost there…",
        ]

        def run_full_screen():
            t_start = time.time()
            placeholder = st.empty()
            progress_bar = st.progress(0)

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
                    progress_bar.progress(min(int(i / len(symbols) * 85), 85))
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
                progress_bar.progress(90)
                tech_results = get_technicals_batch(list(df_sound["Symbol"]))
                tech_rows = [dict(t, Symbol=s) for s, t in tech_results.items()]
                df_tech = df_sound.merge(pd.DataFrame(tech_rows), on="Symbol", how="left")

                hits = pd.DataFrame({name: df_tech.apply(
                    lambda r: bool(pd.notna(r.get("LTP")) and pd.notna(r.get(name)) and fn(r)), axis=1)
                    for name, fn in CONDITION_MAP.items()})
                df_tech["Green Hits"] = hits.sum(axis=1)
                df_tech = df_tech.sort_values("Green Hits", ascending=False).reset_index(drop=True)

            progress_bar.progress(100)
            placeholder.empty()
            progress_bar.empty()

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
                cta_l, cta_r = st.columns([3, 1])
                with cta_l:
                    st.markdown(
                        f"<div class='trade-cta'><span class='trade-cta-text'>Selected "
                        f"<b>{st.session_state.selected_symbol}</b> — LTP {current_price}</span></div>",
                        unsafe_allow_html=True)
                with cta_r:
                    if st.button("📝 Add to Paper Trade", type="primary", width="stretch", key="nse_trade_btn"):
                        paper_trade_dialog(st.session_state.selected_symbol, "NSE", current_price,
                                            supertrend_weekly=selected_supertrend_weekly)

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

    # =================================================================
    # Crypto sub-tab
    # =================================================================
    with subtab_crypto:
        _csel_rows = st.session_state.get("crypto_table", {}).get("selection", {}).get("rows", [])
        selected_crypto_supertrend_weekly = None
        if st.session_state.crypto_result is not None and len(st.session_state.crypto_result) and _csel_rows:
            _crow = st.session_state.crypto_result.iloc[_csel_rows[0]]
            st.session_state.selected_crypto_symbol = _crow["Symbol"]
            selected_crypto_supertrend_weekly = _crow.get("Supertrend (Weekly)")
        else:
            st.session_state.selected_crypto_symbol = None

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
            f"</div><div class='topbar-right'>{crypto_stats_html}{crypto_status_html}</div></div>",
            unsafe_allow_html=True,
        )

        crypto_run_btn = st.button("▶️ Run", type="primary", key="crypto_run")

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
                cc_l, cc_r = st.columns([3, 1])
                with cc_l:
                    st.markdown(
                        f"<div class='trade-cta'><span class='trade-cta-text'>Selected "
                        f"<b>{st.session_state.selected_crypto_symbol}</b> — LTP {crypto_current_price}</span></div>",
                        unsafe_allow_html=True)
                with cc_r:
                    if st.button("📝 Add to Paper Trade", type="primary", width="stretch", key="crypto_trade_btn"):
                        paper_trade_dialog(st.session_state.selected_crypto_symbol, "CRYPTO", crypto_current_price,
                                            supertrend_weekly=selected_crypto_supertrend_weekly)

            with st.expander("📋 Copy Symbols (comma-separated)"):
                st.code(", ".join(df_crypto["Symbol"].tolist()), language=None)
        else:
            st.info("Click **Run** above to fetch the top-50 crypto technical screen.")


with tab_watchlist:
    st.markdown(
        "<div class='topbar'><div class='topbar-left'>"
        "<span class='app-title'>👁️ Watchlist</span>"
        "<span class='app-subtitle'>Fear / Greed tracker based on weekly EMA9 &amp; EMA11 bands</span>"
        "</div></div>", unsafe_allow_html=True,
    )

    username = st.session_state.username
    watchlist_symbols = db.get_watchlist(username)

    with st.form("add_watchlist_form", clear_on_submit=True):
        add_col1, add_col2 = st.columns([4, 1])
        with add_col1:
            new_symbol = st.text_input("Add a symbol (NSE ticker, e.g. TCS, RELIANCE)", label_visibility="collapsed",
                                         placeholder="Add a symbol (NSE ticker, e.g. TCS, RELIANCE)")
        with add_col2:
            add_submitted = st.form_submit_button("➕ Add", width="stretch")
    if add_submitted and new_symbol.strip():
        db.add_watchlist_symbol(username, new_symbol)
        st.rerun()

    if not watchlist_symbols:
        st.info("Your watchlist is empty. Add a symbol above.")
    else:
        fresh_cached = db.get_watchlist_cache(watchlist_symbols, fresh_only=True)
        stale_symbols = [s for s in watchlist_symbols if s not in fresh_cached]
        fetch_errors = st.session_state.get("watchlist_fetch_errors", {})

        if stale_symbols and not st.session_state.watchlist_fetch_done:
            with st.spinner(f"Fetching today's watchlist data for {len(stale_symbols)} symbol(s) "
                              f"(shared cache — refreshed once per day for all users)…"):
                fetched = get_watchlist_batch(stale_symbols)
                errors = {}
                for sym, data in fetched.items():
                    if data.get("_error"):
                        errors[sym] = data["_error"]
                    else:
                        db.save_watchlist_cache(sym, data)
            st.session_state.watchlist_fetch_errors = errors
            # Latch "done" after a single attempt regardless of partial
            # failures - previously this only latched when EVERY symbol
            # succeeded, which meant one permanently-bad ticker (e.g. an
            # ETF symbol Yahoo doesn't recognize) caused an endless
            # fetch -> error -> rerun loop that never let the page settle,
            # leaving LTP/EMA/Condition stuck showing NaN/N-A for
            # everyone. One attempt per session is enough; the manual
            # "Refresh watchlist now" button below covers retries.
            st.session_state.watchlist_fetch_done = True
            st.rerun()

        all_cached = db.get_watchlist_cache(watchlist_symbols, fresh_only=False)

        if fetch_errors:
            with st.expander(f"⚠️ {len(fetch_errors)} symbol(s) failed to fetch — click to see why", expanded=False):
                for sym, err in fetch_errors.items():
                    st.caption(f"**{sym}**: {err}")

        rows = []
        for sym in watchlist_symbols:
            d = all_cached.get(sym, {})
            rows.append({
                "Symbol": sym,
                "LTP": d.get("LTP"),
                "Condition": d.get("Condition", "N/A"),
                "EMA 11 Low (Weekly)": d.get("EMA 11 Low (Weekly)"),
                "EMA 11 High (Weekly)": d.get("EMA 11 High (Weekly)"),
            })
        wdf = pd.DataFrame(rows)

        n_fear = (wdf["Condition"].isin(["Fear", "Extreme Fear"])).sum()
        n_greed = (wdf["Condition"].isin(["Greed", "Extreme Greed"])).sum()
        st.markdown(
            f"<span class='stat-chip'>Tracking <b>{len(wdf)}</b></span>"
            f"<span class='stat-chip'>😟 In Fear <b>{n_fear}</b></span>"
            f"<span class='stat-chip'>🤑 In Greed <b>{n_greed}</b></span>",
            unsafe_allow_html=True,
        )
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        for _, r in wdf.iterrows():
            ltp_str = r["LTP"] if pd.notna(r["LTP"]) else "—"
            lo_str = r["EMA 11 Low (Weekly)"] if pd.notna(r["EMA 11 Low (Weekly)"]) else "—"
            hi_str = r["EMA 11 High (Weekly)"] if pd.notna(r["EMA 11 High (Weekly)"]) else "—"
            card_col, remove_col = st.columns([9, 1])
            with card_col:
                st.markdown(
                    f"<div class='watch-card'>"
                    f"<div><div class='watch-card-sym'>{r['Symbol']}</div>"
                    f"<div class='watch-card-meta'>LTP {ltp_str} &nbsp;·&nbsp; EMA11 Low {lo_str} &nbsp;·&nbsp; EMA11 High {hi_str}</div></div>"
                    f"{condition_pill(r['Condition'])}"
                    f"</div>", unsafe_allow_html=True,
                )
            with remove_col:
                if st.button("✕", key=f"rm_{r['Symbol']}", help=f"Remove {r['Symbol']}"):
                    db.remove_watchlist_symbol(username, r["Symbol"])
                    st.rerun()

        rcol1, _ = st.columns([1, 4])
        with rcol1:
            if st.button("🔄 Refresh watchlist now"):
                st.session_state.watchlist_fetch_done = False
                st.session_state.watchlist_fetch_errors = {}
                st.rerun()


with tab_paper:
    st.markdown("<div class='section-title'>📝 Paper Trades</div>"
                  "<div class='section-subtitle'>Simulated trades - no real money, no broker connection.</div>",
                  unsafe_allow_html=True)

    trades = db.get_paper_trades(st.session_state.username)

    if not trades:
        st.info("No paper trades yet. Select a stock or coin in the Screener tab and click **Add to Paper Trade**.")
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


with tab_notifications:
    st.markdown("<div class='section-title'>🔔 Notifications</div>"
                  "<div class='section-subtitle'>Set up alerts for your watchlist symbols.</div>",
                  unsafe_allow_html=True)
    render_notification_settings(st.session_state.username)


# ---------------------------------------------------------------------
# Small logout button at the very bottom of the page
# ---------------------------------------------------------------------
st.markdown("<hr/>", unsafe_allow_html=True)
foot_l, foot_r = st.columns([5, 1])
with foot_r:
    if st.button("🚪 Logout", key="footer_logout", help="Sign out"):
        db.clear_session(st.session_state.username)
        st.session_state.username = None
        st.query_params.clear()
        st.rerun()