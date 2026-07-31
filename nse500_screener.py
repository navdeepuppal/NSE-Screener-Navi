"""
NSE 500 Fundamental + Technical Screener - Core (v4, weekly cache)
=====================================================================
Technicals for the shortlisted stocks are fetched in ONE batched call
via yf.download() instead of looping one Ticker() per stock - ~2
requests total instead of ~150-240.

Results are cached per ISO week (Mon-Sun). Running again within the
same week loads the cached result instantly with zero network calls;
a new week (or an explicit force-refresh from the dashboard) triggers
a fresh fetch.

REQUIREMENTS (run locally, needs internet):
    pip install yfinance pandas numpy openpyxl requests

USAGE:
    python nse500_screener.py
"""

import time
import io
import pickle
import sys
import threading
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
OUTPUT_FILE = "NSE500_Screener.xlsx"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
CACHE_FILE = CACHE_DIR / "weekly_result.pkl"
MAX_WORKERS = 16
MIN_SECONDS_BETWEEN_CALLS = 0.5
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 5

MIN_MCAP_CR = 10_000
MIN_ROCE = 15
MIN_SALES_GROWTH = 10
MIN_PROFIT_GROWTH = 10
MAX_DEBT_TO_EQUITY = 0.5

GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
GREEN_FONT = Font(color="006100")
NSE500_CSV_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"

print_lock = threading.Lock()
_rate_lock = threading.Lock()
_last_call_time = [0.0]


def _week_key(d=None):
    d = d or date.today()
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def ensure_cache_dir():
    CACHE_DIR.mkdir(exist_ok=True)


def save_daily_result(payload):
    ensure_cache_dir()
    payload = dict(payload)
    payload["week"] = _week_key()
    payload["saved_at"] = date.today().isoformat()
    with open(CACHE_FILE, "wb") as f:
        pickle.dump(payload, f)


def load_daily_result():
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def should_refresh_cached_result(cached):
    if not cached:
        return True
    cached_week = cached.get("week")
    if not cached_week:
        return True
    try:
        return cached_week != _week_key()
    except Exception:
        return True


def rate_limit():
    with _rate_lock:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.time() - _last_call_time[0])
        if wait > 0:
            time.sleep(wait)
        _last_call_time[0] = time.time()


def with_retry(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_BASE * attempt)
    raise last_exc


def get_nse500_symbols():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Accept": "text/csv,application/csv",
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        resp = session.get(NSE500_CSV_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        symbols = df["Symbol"].astype(str).str.strip().tolist()
        names = df["Company Name"].astype(str).str.strip().tolist()
        print(f"Fetched {len(symbols)} NSE 500 symbols from NSE.")
        return list(zip(symbols, names))
    except Exception as e:
        print(f"!! Could not auto-fetch NSE 500 list from NSE ({e}). "
              f"Falling back to local 'nse500_list.csv' if present.")
        try:
            df = pd.read_csv("nse500_list.csv")
            return list(zip(df["Symbol"].astype(str).str.strip(),
                             df["Company Name"].astype(str).str.strip()))
        except Exception as e2:
            print(f"!! No local fallback found either ({e2}). "
                  f"Create nse500_list.csv with columns Symbol,Company Name.")
            sys.exit(1)


def safe_get(d, key, default=np.nan):
    v = d.get(key, default) if hasattr(d, "get") else default
    return v if v is not None else default


def pct_growth(series):
    series = series.dropna()
    if len(series) < 2:
        return np.nan
    latest, prev = series.iloc[0], series.iloc[1]
    if prev == 0 or pd.isna(prev) or pd.isna(latest):
        return np.nan
    return round(((latest - prev) / abs(prev)) * 100, 2)


def _fetch_ticker_bundle(ticker_yf):
    rate_limit()
    t = yf.Ticker(ticker_yf)
    info = t.info or {}
    if not info:
        raise ValueError("empty info response (likely rate-limited)")
    fin = t.financials
    bs = t.balance_sheet
    return info, fin, bs


def get_fundamentals(symbol):
    ticker_yf = symbol + ".NS"
    row = {
        "Symbol": symbol, "NSE Ticker": ticker_yf,
        "Market Cap (Cr)": np.nan, "ROCE (%)": np.nan,
        "Sales Growth (%)": np.nan, "Profit Growth (%)": np.nan,
        "Debt to Equity": np.nan,
    }
    try:
        info, fin, bs = with_retry(_fetch_ticker_bundle, ticker_yf)

        mcap = safe_get(info, "marketCap")
        if pd.notna(mcap):
            row["Market Cap (Cr)"] = round(mcap / 1e7, 2)

        if fin is not None and not fin.empty and "Total Revenue" in fin.index:
            row["Sales Growth (%)"] = pct_growth(fin.loc["Total Revenue"])
        if fin is not None and not fin.empty and "Net Income" in fin.index:
            row["Profit Growth (%)"] = pct_growth(fin.loc["Net Income"])

        try:
            ebit = fin.loc["EBIT"].iloc[0] if "EBIT" in fin.index else np.nan
            total_assets = bs.loc["Total Assets"].iloc[0] if "Total Assets" in bs.index else np.nan
            current_liab = bs.loc["Current Liabilities"].iloc[0] if "Current Liabilities" in bs.index else np.nan
            if pd.notna(ebit) and pd.notna(total_assets) and pd.notna(current_liab):
                cap_employed = total_assets - current_liab
                if cap_employed != 0:
                    row["ROCE (%)"] = round((ebit / cap_employed) * 100, 2)
        except Exception:
            pass

        dte = safe_get(info, "debtToEquity")
        if pd.notna(dte):
            row["Debt to Equity"] = round(dte / 100, 2)
        else:
            try:
                total_debt = bs.loc["Total Debt"].iloc[0] if "Total Debt" in bs.index else np.nan
                equity = bs.loc["Common Stock Equity"].iloc[0] if "Common Stock Equity" in bs.index else np.nan
                if pd.notna(total_debt) and pd.notna(equity) and equity != 0:
                    row["Debt to Equity"] = round(total_debt / equity, 2)
            except Exception:
                pass

    except Exception as e:
        row["_error"] = str(e)
        with print_lock:
            print(f"   [warn] {symbol}: {e}")

    return row


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    return 100 - (100 / (1 + avg_gain / avg_loss))


def supertrend(df, period=10, multiplier=1):
    hl2 = (df["High"] + df["Low"]) / 2
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr
    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(len(df)):
        if i == 0:
            st.iloc[i] = upperband.iloc[i]
            direction.iloc[i] = 1
            continue
        if df["Close"].iloc[i] > upperband.iloc[i - 1]:
            direction.iloc[i] = 1
        elif df["Close"].iloc[i] < lowerband.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            if direction.iloc[i] == 1 and lowerband.iloc[i] < lowerband.iloc[i - 1]:
                lowerband.iloc[i] = lowerband.iloc[i - 1]
            if direction.iloc[i] == -1 and upperband.iloc[i] > upperband.iloc[i - 1]:
                upperband.iloc[i] = upperband.iloc[i - 1]
        st.iloc[i] = lowerband.iloc[i] if direction.iloc[i] == 1 else upperband.iloc[i]
    return st


def _extract_symbol_frame(batch_df, ticker_yf, single_ticker):
    if single_ticker:
        return batch_df
    if ticker_yf in batch_df.columns.get_level_values(0):
        return batch_df[ticker_yf]
    return pd.DataFrame()


def get_technicals_batch(symbols, suffix=".NS"):
    """Fetch LTP + RSI/Supertrend/EMA (monthly & weekly) for ALL given
    symbols in just 2 network calls total. `suffix` is appended to each
    symbol to build the yfinance ticker - use '.NS' for NSE stocks
    (default) or '' for tickers that are already complete, like crypto
    pairs (e.g. 'BTC-USD')."""
    tickers = [s + suffix for s in symbols]
    single = len(tickers) == 1
    results = {s: {
        "LTP": np.nan, "RSI (Monthly)": np.nan, "Supertrend (Monthly)": np.nan,
        "EMA 20 (Monthly)": np.nan, "EMA 10 (Monthly)": np.nan,
        "RSI (Weekly)": np.nan, "Supertrend (Weekly)": np.nan,
        "EMA 20 (Weekly)": np.nan, "EMA 10 (Weekly)": np.nan,
    } for s in symbols}

    try:
        monthly = with_retry(
            yf.download, tickers=tickers, period="5y", interval="1mo",
            group_by="ticker", threads=True, progress=False, auto_adjust=True,
        )
    except Exception as e:
        print(f"   [warn-tech] monthly batch download failed: {e}")
        monthly = None

    time.sleep(1.5)

    try:
        weekly = with_retry(
            yf.download, tickers=tickers, period="2y", interval="1wk",
            group_by="ticker", threads=True, progress=False, auto_adjust=True,
        )
    except Exception as e:
        print(f"   [warn-tech] weekly batch download failed: {e}")
        weekly = None

    for sym in symbols:
        ticker_yf = sym + suffix
        try:
            hist_m = _extract_symbol_frame(monthly, ticker_yf, single).dropna() if monthly is not None else pd.DataFrame()
            hist_w = _extract_symbol_frame(weekly, ticker_yf, single).dropna() if weekly is not None else pd.DataFrame()
            if hist_m.empty or hist_w.empty:
                continue

            out = results[sym]
            out["LTP"] = round(float(hist_w["Close"].iloc[-1]), 2)

            out["RSI (Monthly)"] = round(rsi(hist_m["Close"]).iloc[-1], 2)
            out["Supertrend (Monthly)"] = round(supertrend(hist_m, 10, 1).iloc[-1], 2)
            out["EMA 20 (Monthly)"] = round(ema(hist_m["Close"], 20).iloc[-1], 2)
            out["EMA 10 (Monthly)"] = round(ema(hist_m["Close"], 10).iloc[-1], 2)

            out["RSI (Weekly)"] = round(rsi(hist_w["Close"]).iloc[-1], 2)
            out["Supertrend (Weekly)"] = round(supertrend(hist_w, 10, 1).iloc[-1], 2)
            out["EMA 20 (Weekly)"] = round(ema(hist_w["Close"], 20).iloc[-1], 2)
            out["EMA 10 (Weekly)"] = round(ema(hist_w["Close"], 10).iloc[-1], 2)
        except Exception as e:
            with print_lock:
                print(f"   [warn-tech] {sym}: {e}")

    return results


# ----------------------------------------------------------------------
# Crypto screener (top ~50 coins by market cap, technicals only)
# ----------------------------------------------------------------------
# yfinance has no "top N crypto" endpoint, so this is a curated static
# list of major coins by approximate market cap (as of early-mid 2026).
# It will drift over time as rankings shift - update manually if needed.
CRYPTO_TOP50 = [
    "BTC-USD", "ETH-USD", "USDT-USD", "BNB-USD", "SOL-USD", "XRP-USD", "USDC-USD",
    "ADA-USD", "AVAX-USD", "DOGE-USD", "TRX-USD", "DOT-USD", "MATIC-USD", "LTC-USD",
    "SHIB-USD", "LINK-USD", "BCH-USD", "NEAR-USD", "UNI-USD", "ATOM-USD", "XLM-USD",
    "ICP-USD", "ETC-USD", "FIL-USD", "HBAR-USD", "APT-USD", "ARB-USD", "VET-USD",
    "OP-USD", "MKR-USD", "INJ-USD", "IMX-USD", "GRT-USD", "RNDR-USD", "AAVE-USD",
    "ALGO-USD", "QNT-USD", "EGLD-USD", "SAND-USD", "MANA-USD", "THETA-USD", "AXS-USD",
    "XTZ-USD", "EOS-USD", "FLOW-USD", "CHZ-USD", "KAVA-USD", "XMR-USD", "CRV-USD",
    "LDO-USD", "FTM-USD",
]

CRYPTO_CACHE_FILE = CACHE_DIR / "weekly_crypto_result.pkl"


def save_crypto_result(df):
    ensure_cache_dir()
    payload = {"df": df, "week": _week_key(), "saved_at": date.today().isoformat()}
    with open(CRYPTO_CACHE_FILE, "wb") as f:
        pickle.dump(payload, f)


def load_crypto_result():
    if not CRYPTO_CACHE_FILE.exists():
        return None
    try:
        with open(CRYPTO_CACHE_FILE, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def should_refresh_crypto_result(cached):
    if not cached:
        return True
    return cached.get("week") != _week_key()


def get_crypto_screener():
    """Fetches technicals for the top-50 crypto list and returns a
    dataframe sorted by Green Hits - same technical conditions as the
    NSE screener, no fundamentals stage (doesn't apply to crypto)."""
    symbols = [t.replace("-USD", "") for t in CRYPTO_TOP50]
    tech_results = get_technicals_batch(symbols, suffix="-USD")
    rows = [dict(tech, Symbol=sym) for sym, tech in tech_results.items()]
    df = pd.DataFrame(rows)

    hits = pd.DataFrame({name: df.apply(
        lambda r: bool(pd.notna(r.get("LTP")) and pd.notna(r.get(name)) and fn(r)), axis=1)
        for name, fn in CONDITION_MAP.items()})
    df["Green Hits"] = hits.sum(axis=1)
    df = df.sort_values("Green Hits", ascending=False).reset_index(drop=True)
    return df


def get_current_prices(symbols, suffix=".NS"):
    """Lightweight batched LTP-only lookup (no indicators) - used for
    paper-trade P&L, where all we need is the current price for a
    handful of symbols, not the full technicals fetch."""
    if not symbols:
        return {}
    tickers = [s + suffix for s in symbols]
    single = len(tickers) == 1
    try:
        data = with_retry(
            yf.download, tickers=tickers, period="5d", interval="1d",
            group_by="ticker", threads=True, progress=False, auto_adjust=True,
        )
    except Exception as e:
        print(f"   [warn] current price batch fetch failed: {e}")
        return {}

    prices = {}
    for sym in symbols:
        ticker_yf = sym + suffix
        try:
            frame = _extract_symbol_frame(data, ticker_yf, single).dropna()
            if not frame.empty:
                prices[sym] = round(float(frame["Close"].iloc[-1]), 2)
        except Exception:
            pass
    return prices


CONDITION_MAP = {
    "Supertrend (Monthly)": lambda r: r["LTP"] > r["Supertrend (Monthly)"],
    "EMA 20 (Monthly)": lambda r: r["LTP"] > r["EMA 20 (Monthly)"],
    "EMA 10 (Monthly)": lambda r: r["LTP"] > r["EMA 10 (Monthly)"],
    "RSI (Monthly)": lambda r: r["RSI (Monthly)"] < 30,
    "Supertrend (Weekly)": lambda r: r["LTP"] > r["Supertrend (Weekly)"],
    "EMA 20 (Weekly)": lambda r: r["LTP"] > r["EMA 20 (Weekly)"],
    "EMA 10 (Weekly)": lambda r: r["LTP"] > r["EMA 10 (Weekly)"],
    "RSI (Weekly)": lambda r: r["RSI (Weekly)"] < 30,
}


def write_excel(df1, df2, df3, output_file=OUTPUT_FILE):
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        df1.to_excel(writer, sheet_name="All NSE500", index=False)
        df2.to_excel(writer, sheet_name="Fundamentally Sound", index=False)
        df3.to_excel(writer, sheet_name="Technical Screen", index=False)

    wb = load_workbook(output_file)
    ws = wb["Technical Screen"]
    headers = [c.value for c in ws[1]]
    for r_idx, row in df3.iterrows():
        excel_row = r_idx + 2
        for col_name, cond_fn in CONDITION_MAP.items():
            if col_name not in headers:
                continue
            col_idx = headers.index(col_name) + 1
            try:
                if pd.notna(row["LTP"]) and pd.notna(row.get(col_name)) and cond_fn(row):
                    cell = ws.cell(row=excel_row, column=col_idx)
                    cell.fill = GREEN_FILL
                    cell.font = GREEN_FONT
            except Exception:
                pass

    for ws_ in wb.worksheets:
        for col_cells in ws_.columns:
            length = max(len(str(c.value)) if c.value is not None else 0 for c in col_cells)
            col_letter = get_column_letter(col_cells[0].column)
            ws_.column_dimensions[col_letter].width = min(max(length + 2, 10), 30)
    wb.save(output_file)


def main():
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cached = load_daily_result()
    if cached is not None and not should_refresh_cached_result(cached):
        df1, df2, df3 = cached["df_all"], cached["df_sound"], cached["df_tech"]
        print(f"Using this week's cached result (week {cached.get('week')}, "
              f"saved {cached.get('saved_at')}). Delete .cache/weekly_result.pkl "
              f"to force a refresh.")
        write_excel(df1, df2, df3 if df3 is not None else df2)
        print(f"Done. Saved to {OUTPUT_FILE}")
        return

    symbols = get_nse500_symbols()
    name_map = {sym: name for sym, name in symbols}

    print(f"\nFetching fundamentals for {len(symbols)} stocks ({MAX_WORKERS} workers)...")
    rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_fundamentals, s): s for s, _ in symbols}
        for i, fut in enumerate(as_completed(futures), 1):
            sym = futures[fut]
            r = fut.result()
            r["Company Name"] = name_map.get(sym, "")
            rows.append(r)
            if i % 25 == 0 or i == len(symbols):
                print(f"  {i}/{len(symbols)} done")
    print(f"Fundamentals done in {time.time()-t0:.0f}s")

    df1 = pd.DataFrame(rows)
    df1 = df1[["Symbol", "Company Name", "NSE Ticker", "Market Cap (Cr)", "ROCE (%)",
               "Sales Growth (%)", "Profit Growth (%)", "Debt to Equity"]]

    df2 = df1[
        (df1["Market Cap (Cr)"] > MIN_MCAP_CR) & (df1["ROCE (%)"] > MIN_ROCE) &
        (df1["Sales Growth (%)"] > MIN_SALES_GROWTH) & (df1["Profit Growth (%)"] > MIN_PROFIT_GROWTH) &
        (df1["Debt to Equity"] < MAX_DEBT_TO_EQUITY)
    ].copy().reset_index(drop=True)
    print(f"\n{len(df2)} stocks passed fundamentals.")

    if df2.empty:
        empty_tech = df2.assign(**{c: [] for c in CONDITION_MAP})
        write_excel(df1, df2, empty_tech)
        save_daily_result({"df_all": df1, "df_sound": df2, "df_tech": empty_tech})
        print("No stocks passed - nothing to run technicals on.")
        return

    print(f"\nFetching technicals for {len(df2)} stocks (batched)...")
    t1 = time.time()
    tech_results = get_technicals_batch(list(df2["Symbol"]))
    print(f"Technicals done in {time.time()-t1:.0f}s")

    tech_rows = [dict(tech, Symbol=sym) for sym, tech in tech_results.items()]
    df3 = df2.merge(pd.DataFrame(tech_rows), on="Symbol", how="left")

    hits = pd.DataFrame({name: df3.apply(
        lambda r: bool(pd.notna(r.get("LTP")) and pd.notna(r.get(name)) and fn(r)), axis=1)
        for name, fn in CONDITION_MAP.items()})
    df3["Green Hits"] = hits.sum(axis=1)
    df3 = df3.sort_values("Green Hits", ascending=False).reset_index(drop=True)

    write_excel(df1, df2, df3)
    save_daily_result({"df_all": df1, "df_sound": df2, "df_tech": df3})
    print(f"\nDone. Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()