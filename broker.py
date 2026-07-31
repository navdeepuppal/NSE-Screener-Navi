"""
Broker Connection - Zerodha Kite Connect
===========================================
Handles per-user broker credential storage and order placement.

IMPORTANT - READ BEFORE USE:
  - This connects to a REAL Zerodha trading account. Orders placed
    through "Place Trade" use REAL MONEY on a live market, unless you
    are using Kite Connect's sandbox/paper environment.
  - Credentials (API secret, access token) are encrypted with a key
    derived from the user's PIN and stored in a local file
    (.broker_store/<username>.enc). This is basic protection suitable
    for personal single-machine use - it is NOT bank-grade security.
    Do not deploy this on a shared or public server as-is.
  - Kite access tokens expire daily (Zerodha's policy) - you will
    need to reconnect (repeat the login flow) once a day.

REQUIREMENTS:
    pip install kiteconnect cryptography
"""

import base64
import hashlib
import json
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None  # handled gracefully in the UI if not installed

STORE_DIR = Path(__file__).resolve().parent / ".broker_store"


def _key_from_pin(username, pin):
    """Derives a Fernet-compatible key from username+PIN. Same PIN
    always produces the same key for that username - this is what
    lets us decrypt on the next login without storing the PIN itself."""
    raw = hashlib.pbkdf2_hmac("sha256", pin.encode(), username.encode(), 200_000, dklen=32)
    return base64.urlsafe_b64encode(raw)


def _store_path(username):
    STORE_DIR.mkdir(exist_ok=True)
    safe = "".join(c for c in username if c.isalnum() or c in "_-").lower() or "user"
    return STORE_DIR / f"{safe}.enc"


def save_broker_credentials(username, pin, api_key, api_secret, access_token=None):
    """Encrypts and saves this user's broker credentials to disk."""
    fernet = Fernet(_key_from_pin(username, pin))
    payload = json.dumps({
        "api_key": api_key, "api_secret": api_secret, "access_token": access_token,
    }).encode()
    token = fernet.encrypt(payload)
    _store_path(username).write_bytes(token)


def load_broker_credentials(username, pin):
    """Returns the decrypted credentials dict, or None if none saved
    yet or the PIN is wrong (decryption fails silently -> None)."""
    path = _store_path(username)
    if not path.exists():
        return None
    try:
        fernet = Fernet(_key_from_pin(username, pin))
        payload = fernet.decrypt(path.read_bytes())
        return json.loads(payload)
    except InvalidToken:
        return None
    except Exception:
        return None


def get_login_url(api_key):
    if KiteConnect is None:
        raise RuntimeError("kiteconnect is not installed - run: pip install kiteconnect")
    kite = KiteConnect(api_key=api_key)
    return kite.login_url()


def exchange_request_token(api_key, api_secret, request_token):
    """Completes the Kite login flow: exchanges the request_token
    (obtained after the user logs in via the browser URL) for an
    access_token. Also does a live sanity check by fetching the
    account profile."""
    if KiteConnect is None:
        raise RuntimeError("kiteconnect is not installed - run: pip install kiteconnect")
    kite = KiteConnect(api_key=api_key)
    session_data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session_data["access_token"]
    kite.set_access_token(access_token)
    profile = kite.profile()  # raises if the token/connection is bad
    return kite, access_token, profile


def reconnect_with_saved_token(api_key, access_token):
    """Rebuilds a KiteConnect client from a previously-saved access
    token, without repeating the browser login (works only if the
    token hasn't expired yet - Kite tokens expire daily)."""
    if KiteConnect is None:
        raise RuntimeError("kiteconnect is not installed - run: pip install kiteconnect")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    profile = kite.profile()  # raises if the token has expired
    return kite, profile


def place_order(kite, symbol, transaction_type, quantity, order_type="MARKET",
                 price=None, product="MIS", exchange="NSE"):
    """Places a real order via Kite Connect.
    transaction_type: 'BUY' or 'SELL'
    order_type: 'MARKET' or 'LIMIT'
    product: 'MIS' (intraday) or 'CNC' (delivery)
    Returns the order_id on success; raises on failure."""
    kwargs = dict(
        variety=kite.VARIETY_REGULAR,
        exchange=exchange,
        tradingsymbol=symbol,
        transaction_type=transaction_type,
        quantity=int(quantity),
        order_type=order_type,
        product=product,
    )
    if order_type == "LIMIT":
        if not price:
            raise ValueError("Limit orders require a price.")
        kwargs["price"] = float(price)

    order_id = kite.place_order(**kwargs)
    return order_id