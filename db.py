"""
Local SQLite store - user accounts, sessions, paper trades, watchlist,
and notification settings.
====================================================================
This is a simple local database (screener.db, created automatically
next to this file) - fine for personal/local use. Passwords are
never stored in plain text (salted + hashed with PBKDF2).

REQUIREMENTS: none beyond the Python standard library (sqlite3).
"""

import sqlite3
import hashlib
import secrets
import json
import os  # noqa: F401 (kept for parity with earlier version, harmless)
from pathlib import Path
from datetime import datetime, date

DB_PATH = Path(__file__).resolve().parent / "screener.db"

DEFAULT_WATCHLIST_ETFS = ["SETFNIF50", "MID150BEES", "CNXSMALLCAP", "SILVERBEES", "SETFGOLD"]


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            session_token TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            market TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL,
            entry_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            close_price REAL,
            close_reason TEXT,
            initial_stoploss REAL,
            tsl_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            username TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market TEXT NOT NULL DEFAULT 'NSE',
            added_at TEXT NOT NULL,
            PRIMARY KEY (username, symbol)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS watchlist_cache (
            symbol TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            cached_date TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS notification_settings (
            username TEXT PRIMARY KEY,
            whatsapp_number TEXT,
            telegram_chat_id TEXT,
            notify_fear INTEGER NOT NULL DEFAULT 1,
            notify_extreme_only INTEGER NOT NULL DEFAULT 0,
            frequency TEXT NOT NULL DEFAULT 'daily',
            updated_at TEXT
        )
    """)
    conn.commit()

    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(paper_trades)").fetchall()}
    for col_def in [
        ("close_reason", "TEXT"),
        ("initial_stoploss", "REAL"),
        ("tsl_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        if col_def[0] not in existing_cols:
            try:
                conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {col_def[0]} {col_def[1]}")
            except Exception:
                pass
    conn.commit()
    conn.close()


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return salt, h


def create_user(username, password):
    username = username.strip()
    if not username or not password:
        return False, "Username and password are required."
    conn = _conn()
    existing = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
    if existing:
        conn.close()
        return False, "That username is already taken."
    salt, pw_hash = _hash_password(password)
    conn.execute(
        "INSERT INTO users (username, salt, password_hash, created_at) VALUES (?, ?, ?, ?)",
        (username, salt, pw_hash, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    ensure_default_watchlist(username)
    return True, "Account created."


def verify_user(username, password):
    conn = _conn()
    row = conn.execute("SELECT salt, password_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
    conn.close()
    if not row:
        return False
    _, pw_hash = _hash_password(password, row["salt"])
    return secrets.compare_digest(pw_hash, row["password_hash"])


def create_session(username):
    token = secrets.token_urlsafe(24)
    conn = _conn()
    conn.execute("UPDATE users SET session_token = ? WHERE username = ?", (token, username.strip()))
    conn.commit()
    conn.close()
    return token


def get_user_by_session(token):
    if not token:
        return None
    conn = _conn()
    row = conn.execute("SELECT username FROM users WHERE session_token = ?", (token,)).fetchone()
    conn.close()
    return row["username"] if row else None


def clear_session(username):
    conn = _conn()
    conn.execute("UPDATE users SET session_token = NULL WHERE username = ?", (username,))
    conn.commit()
    conn.close()


def add_paper_trade(username, market, symbol, side, quantity, order_type, entry_price,
                     initial_stoploss=None, tsl_enabled=False):
    conn = _conn()
    conn.execute("""
        INSERT INTO paper_trades
            (username, market, symbol, side, quantity, order_type, entry_price,
             initial_stoploss, tsl_enabled, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (username, market, symbol, side, quantity, order_type, entry_price,
          initial_stoploss, 1 if tsl_enabled else 0, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_paper_trades(username):
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM paper_trades WHERE username = ? ORDER BY created_at DESC", (username,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def close_paper_trade(trade_id, close_price, reason="MANUAL"):
    conn = _conn()
    conn.execute(
        "UPDATE paper_trades SET status = 'CLOSED', close_price = ?, close_reason = ?, closed_at = ? WHERE id = ?",
        (close_price, reason, datetime.utcnow().isoformat(), trade_id),
    )
    conn.commit()
    conn.close()


def ensure_default_watchlist(username):
    conn = _conn()
    existing = {r["symbol"] for r in conn.execute(
        "SELECT symbol FROM watchlist WHERE username = ?", (username,)).fetchall()}
    now = datetime.utcnow().isoformat()
    for sym in DEFAULT_WATCHLIST_ETFS:
        if sym not in existing:
            conn.execute(
                "INSERT OR IGNORE INTO watchlist (username, symbol, market, added_at) VALUES (?, ?, 'NSE', ?)",
                (username, sym, now),
            )
    conn.commit()
    conn.close()


def get_watchlist(username):
    conn = _conn()
    rows = conn.execute(
        "SELECT symbol FROM watchlist WHERE username = ? ORDER BY added_at ASC", (username,)
    ).fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


def add_watchlist_symbol(username, symbol):
    symbol = symbol.strip().upper()
    if not symbol:
        return
    conn = _conn()
    conn.execute(
        "INSERT OR IGNORE INTO watchlist (username, symbol, market, added_at) VALUES (?, ?, 'NSE', ?)",
        (username, symbol, datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


def remove_watchlist_symbol(username, symbol):
    conn = _conn()
    conn.execute("DELETE FROM watchlist WHERE username = ? AND symbol = ?", (username, symbol))
    conn.commit()
    conn.close()


def _today_str():
    return date.today().isoformat()


def get_watchlist_cache(symbols, fresh_only=True):
    if not symbols:
        return {}
    conn = _conn()
    placeholders = ",".join("?" for _ in symbols)
    rows = conn.execute(
        f"SELECT symbol, data, cached_date FROM watchlist_cache WHERE symbol IN ({placeholders})",
        symbols,
    ).fetchall()
    conn.close()
    today = _today_str()
    out = {}
    for r in rows:
        if fresh_only and r["cached_date"] != today:
            continue
        try:
            out[r["symbol"]] = json.loads(r["data"])
        except Exception:
            continue
    return out


def save_watchlist_cache(symbol, data_dict):
    conn = _conn()
    conn.execute("""
        INSERT INTO watchlist_cache (symbol, data, cached_date, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET data=excluded.data, cached_date=excluded.cached_date,
            updated_at=excluded.updated_at
    """, (symbol, json.dumps(data_dict), _today_str(), datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_notification_settings(username):
    conn = _conn()
    row = conn.execute("SELECT * FROM notification_settings WHERE username = ?", (username,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return {
        "username": username, "whatsapp_number": "", "telegram_chat_id": "",
        "notify_fear": 1, "notify_extreme_only": 0, "frequency": "daily", "updated_at": None,
    }


def save_notification_settings(username, whatsapp_number, telegram_chat_id,
                                 notify_fear, notify_extreme_only, frequency):
    conn = _conn()
    conn.execute("""
        INSERT INTO notification_settings
            (username, whatsapp_number, telegram_chat_id, notify_fear, notify_extreme_only, frequency, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            whatsapp_number=excluded.whatsapp_number, telegram_chat_id=excluded.telegram_chat_id,
            notify_fear=excluded.notify_fear, notify_extreme_only=excluded.notify_extreme_only,
            frequency=excluded.frequency, updated_at=excluded.updated_at
    """, (username, whatsapp_number.strip(), telegram_chat_id.strip(),
          1 if notify_fear else 0, 1 if notify_extreme_only else 0, frequency,
          datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()