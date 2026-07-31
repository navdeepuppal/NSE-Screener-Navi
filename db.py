"""
Local SQLite store - user accounts, sessions, and paper trades.
====================================================================
This is a simple local database (screener.db, created automatically
next to this file) - fine for personal/local use. Passwords are
never stored in plain text (salted + hashed with PBKDF2).

REQUIREMENTS: none beyond the Python standard library (sqlite3).
"""

import sqlite3
import hashlib
import secrets
import os
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).resolve().parent / "screener.db"


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
            market TEXT NOT NULL,          -- 'NSE' or 'CRYPTO'
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,            -- 'BUY' or 'SELL'
            quantity REAL NOT NULL,
            order_type TEXT NOT NULL,      -- 'MARKET' or 'LIMIT'
            entry_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',  -- 'OPEN' or 'CLOSED'
            close_price REAL,
            close_reason TEXT,
            initial_stoploss REAL,
            tsl_enabled INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            closed_at TEXT
        )
    """)
    conn.commit()
    # Idempotent migration for DBs created before these columns existed
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
    """Returns (ok: bool, message: str)."""
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
    return True, "Account created."


def verify_user(username, password):
    """Returns True if the username/password combination is correct."""
    conn = _conn()
    row = conn.execute("SELECT salt, password_hash FROM users WHERE username = ?", (username.strip(),)).fetchone()
    conn.close()
    if not row:
        return False
    _, pw_hash = _hash_password(password, row["salt"])
    return secrets.compare_digest(pw_hash, row["password_hash"])


def create_session(username):
    """Creates a new session token for this user and returns it. Used
    to persist login across page refreshes via a URL query param."""
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


# ---------------------------------------------------------------------
# Paper trades
# ---------------------------------------------------------------------
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