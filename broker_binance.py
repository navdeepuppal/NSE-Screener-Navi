"""
Broker Connection - Binance
==============================
Handles per-user Binance credential storage and order placement for
the crypto screener. Simpler flow than Kite Connect: Binance uses a
plain API Key + Secret (no browser login/redirect step needed).

IMPORTANT - READ BEFORE USE:
  - This connects to a REAL Binance account. Orders placed through
    "Place Trade" use REAL MONEY on the live market, unless the API
    key is a Binance Testnet key.
  - Credentials are encrypted with a key derived from the user's PIN
    and stored locally (.broker_store/<username>_binance.enc). Basic
    protection for personal single-machine use - not bank-grade.
  - Binance API keys can be scoped (read-only vs trade-enabled) in
    your Binance account settings - use a trade-enabled key only if
    you actually intend to place orders here.

REQUIREMENTS:
    pip install python-binance cryptography
"""

import json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

from broker import _key_from_pin  # reuse the same PIN -> key derivation

try:
    from binance.client import Client
except ImportError:
    Client = None

STORE_DIR = Path(__file__).resolve().parent / ".broker_store"


def _store_path(username):
    STORE_DIR.mkdir(exist_ok=True)
    safe = "".join(c for c in username if c.isalnum() or c in "_-").lower() or "user"
    return STORE_DIR / f"{safe}_binance.enc"


def save_binance_credentials(username, pin, api_key, api_secret):
    fernet = Fernet(_key_from_pin(username, pin))
    payload = json.dumps({"api_key": api_key, "api_secret": api_secret}).encode()
    _store_path(username).write_bytes(fernet.encrypt(payload))


def load_binance_credentials(username, pin):
    path = _store_path(username)
    if not path.exists():
        return None
    try:
        fernet = Fernet(_key_from_pin(username, pin))
        return json.loads(fernet.decrypt(path.read_bytes()))
    except InvalidToken:
        return None
    except Exception:
        return None


def connect(api_key, api_secret):
    """Connects and does a live sanity check via account info.
    Returns (client, account_info)."""
    if Client is None:
        raise RuntimeError("python-binance is not installed - run: pip install python-binance")
    client = Client(api_key, api_secret)
    account = client.get_account()  # raises if credentials are invalid
    return client, account


def place_order(client, symbol, side, quantity, order_type="MARKET", price=None):
    """Places a real order via Binance.
    symbol: e.g. 'BTCUSDT' (no dash - Binance's own format)
    side: 'BUY' or 'SELL'
    order_type: 'MARKET' or 'LIMIT'
    Returns the order response dict on success; raises on failure."""
    kwargs = dict(symbol=symbol, side=side, type=order_type, quantity=quantity)
    if order_type == "LIMIT":
        if not price:
            raise ValueError("Limit orders require a price.")
        kwargs["price"] = str(price)
        kwargs["timeInForce"] = "GTC"

    return client.create_order(**kwargs)