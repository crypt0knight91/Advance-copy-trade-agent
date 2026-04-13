"""
executor.py — Order execution. Paper mode and live mode.
Live mode uses py-clob-client with Gmail/Magic wallet (signature type 1 = POLY_PROXY).
Correct API: create_order() → post_order()  (NOT create_and_post_order)
"""
import time, json, uuid
from datetime import datetime, timezone
from typing import Optional, Tuple

import config

# ── Optional imports ───────────────────────────────────────────────────────────
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import OrderArgs, OrderType
    from py_clob_client.order_builder.constants import BUY, SELL
    from py_clob_client.constants import POLYGON
    CLOB_OK = True
except ImportError:
    CLOB_OK = False

try:
    import requests as _req
    REQ_OK = True
except ImportError:
    REQ_OK = False


# ─────────────────────────────────────────────────────────────────────────────
# CLOB CLIENT (singleton, lazy-init)
# ─────────────────────────────────────────────────────────────────────────────
_client = None

def _get_client() -> Optional[object]:
    global _client
    if _client is not None:
        return _client
    if not CLOB_OK:
        return None
    if not config.POLY_PRIVATE_KEY:
        return None
    try:
        _client = ClobClient(
            host=config.CLOB_API,
            chain_id=POLYGON,
            key=config.POLY_PRIVATE_KEY,
            signature_type=1,        # POLY_PROXY for Magic/Gmail wallet
            funder=config.MY_PROXY_WALLET or None,
        )
        _client.set_api_creds(_client.create_or_derive_api_creds())
        return _client
    except Exception as e:
        print(f"[EXECUTOR] CLOB client init failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# PLACE LIMIT BUY
# Correct pattern: create_order() → post_order(signed, OrderType.GTC)
# ─────────────────────────────────────────────────────────────────────────────
def place_limit_buy(
    token_id: str,
    price: float,
    size_usd: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> Tuple[bool, str]:
    shares = round(size_usd / max(price, 0.001), 4)

    if config.DRY_RUN:
        fake_id = f"DRY_{uuid.uuid4().hex[:12]}"
        _log_exec("DRY_BUY", token_id, price, shares, size_usd, fake_id)
        return True, fake_id

    client = _get_client()
    if not client:
        return False, "CLOB_CLIENT_UNAVAILABLE"

    try:
        signed = client.create_order(OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=shares,
            side=BUY,
        ))
        resp = client.post_order(signed, OrderType.GTC)
        order_id = resp.get("orderID", resp.get("id", resp.get("order_id", "")))
        _log_exec("LIVE_BUY", token_id, price, shares, size_usd, order_id)
        return True, order_id
    except Exception as e:
        return False, f"ORDER_FAIL:{str(e)[:80]}"


# ─────────────────────────────────────────────────────────────────────────────
# PLACE LIMIT SELL
# ─────────────────────────────────────────────────────────────────────────────
def place_limit_sell(
    token_id: str,
    price: float,
    shares: float,
    tick_size: str = "0.01",
    neg_risk: bool = False,
) -> Tuple[bool, str]:
    if config.DRY_RUN:
        fake_id = f"DRY_SELL_{uuid.uuid4().hex[:10]}"
        _log_exec("DRY_SELL", token_id, price, shares, shares * price, fake_id)
        return True, fake_id

    client = _get_client()
    if not client:
        return False, "CLOB_CLIENT_UNAVAILABLE"

    try:
        signed = client.create_order(OrderArgs(
            token_id=token_id,
            price=round(price, 4),
            size=shares,
            side=SELL,
        ))
        resp = client.post_order(signed, OrderType.GTC)
        order_id = resp.get("orderID", resp.get("id", resp.get("order_id", "")))
        _log_exec("LIVE_SELL", token_id, price, shares, shares * price, order_id)
        return True, order_id
    except Exception as e:
        return False, f"SELL_FAIL:{str(e)[:80]}"


# ─────────────────────────────────────────────────────────────────────────────
# CANCEL ORDER
# ─────────────────────────────────────────────────────────────────────────────
def cancel_order(order_id: str) -> bool:
    if config.DRY_RUN or order_id.startswith("DRY_"):
        return True
    client = _get_client()
    if not client:
        return False
    try:
        client.cancel(order_id=order_id)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# GET MY POSITIONS
# ─────────────────────────────────────────────────────────────────────────────
def get_my_positions() -> list:
    """Get filled positions from Data API."""
    if not REQ_OK or not config.MY_PROXY_WALLET:
        return []
    try:
        r = _req.get(
            f"{config.DATA_API}/positions",
            params={"user": config.MY_PROXY_WALLET.lower()},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list):
            return data
        return data.get("data", data.get("positions", []))
    except Exception:
        return []


def get_my_open_orders() -> list:
    """Get unfilled GTC limit orders from CLOB API."""
    if not REQ_OK or not config.MY_PROXY_WALLET:
        return []
    try:
        r = _req.get(
            f"{config.CLOB_API}/orders",
            params={
                "market":   "",
                "maker":    config.MY_PROXY_WALLET.lower(),
                "asset_id": "",
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        orders = data if isinstance(data, list) else data.get("data", [])
        # Only return open/pending orders
        return [o for o in orders if o.get("status","").upper()
                in ("OPEN","LIVE","PENDING","UNMATCHED","")]
    except Exception:
        return []


def cancel_all_open_orders() -> int:
    """Cancel all open limit orders via CLOB client. Returns count cancelled."""
    client = _get_client()
    if not client:
        return 0
    try:
        resp = client.cancel_all()
        return 1 if resp else 0
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# PRICE LOOKUP
# ─────────────────────────────────────────────────────────────────────────────
def get_current_price(token_id: str) -> Optional[float]:
    if not REQ_OK:
        return None
    try:
        r = _req.get(
            f"{config.CLOB_API}/midpoint",
            params={"token_id": token_id},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        mid = data.get("mid", data.get("price"))
        return float(mid) if mid is not None else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _log_exec(action: str, token_id: str, price: float,
              shares: float, usd: float, order_id: str):
    ts   = datetime.now(timezone.utc).strftime("%H:%M:%S")
    mode = "DRY" if config.DRY_RUN else "LIVE"
    print(f"[{ts}][{mode}][{action}] token={token_id[:20]}.. "
          f"price={price:.4f} shares={shares:.4f} usd=${usd:.2f} "
          f"order={order_id}")
