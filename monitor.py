"""
monitor.py — Whale activity polling engine.
Polls data-api.polymarket.com/activity for each whale every POLL_SECS.
Detects new BUY (open) and SELL (close) by diffing against previous state.
"""
import time, json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple

try:
    import requests
    REQ_OK = True
except ImportError:
    REQ_OK = False

import config
import whales as wh
from scoring import is_limit_order

# ─────────────────────────────────────────────────────────────────────────────
# HTTP HELPER
# ─────────────────────────────────────────────────────────────────────────────
_session = None

def _sess():
    global _session
    if _session is None and REQ_OK:
        import requests as r
        _session = r.Session()
        _session.headers.update({"User-Agent": "WhaleMirrorBot/2.0"})
    return _session

def http_get(url: str, params: dict = None, timeout: int = 10) -> Optional[any]:
    s = _sess()
    if not s:
        return None
    for attempt in range(3):
        try:
            r = s.get(url, params=params, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(2 ** attempt)
    return None

def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default

# ─────────────────────────────────────────────────────────────────────────────
# POSITION SNAPSHOT (current state per whale)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_whale_positions(addr: str) -> Dict[str, dict]:
    """
    Returns dict of {unique_key: position_data} for whale's open positions.
    Key = conditionId + outcome so YES and NO on same market are tracked separately.
    Also fetches activity to get position open timestamps.
    """
    data = http_get(f"{config.DATA_API}/positions",
                    {"user": addr.lower()})
    if not data:
        return {}
    items = data if isinstance(data, list) else data.get("data", data.get("positions", []))

    # Also fetch recent activity to get open timestamps for each position
    activity_ts = {}
    act = http_get(f"{config.DATA_API}/activity", {
        "user": addr.lower(), "limit": 50, "type": "TRADE"
    })
    if act:
        act_list = act if isinstance(act, list) else act.get("data", [])
        for a in act_list:
            cid = a.get("conditionId", a.get("conditionID", ""))
            ts  = float(a.get("timestamp", 0) or 0)
            side = str(a.get("side","")).upper()
            if cid and side == "BUY" and ts > 0:
                # Keep most recent BUY timestamp per cid
                if cid not in activity_ts or ts > activity_ts[cid]:
                    activity_ts[cid] = ts

    result = {}
    now = time.time()
    for p in items:
        cid = p.get("conditionId", p.get("conditionID", p.get("condition_id", "")))
        if not cid:
            continue
        outcome_raw  = str(p.get("outcome", p.get("side", "YES"))).upper()
        outcome_norm = "NO" if outcome_raw in ("NO","1","2") else "YES"
        key = f"{cid}_{outcome_norm}"

        # Attach open timestamp from activity feed
        opened_ts = activity_ts.get(cid, 0)
        age_mins  = (now - opened_ts) / 60 if opened_ts > 0 else 9999

        result[key] = {
            **p,
            "_outcome_norm": outcome_norm,
            "_cid_raw":      cid,
            "_opened_ts":    opened_ts,
            "_age_mins":     age_mins,
        }
    return result

# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY FETCH (recent trades for scoring)
# ─────────────────────────────────────────────────────────────────────────────
def fetch_whale_activity(addr: str, days: int = 15) -> List[dict]:
    """Fetch last N days of activity for trust scoring."""
    cutoff = int(time.time() - days * 86400)
    data = http_get(f"{config.DATA_API}/activity", {
        "user":  addr.lower(),
        "start": cutoff,
        "limit": 200,
        "type":  "TRADE",
    })
    if not data:
        return []
    return data if isinstance(data, list) else data.get("data", [])

# ─────────────────────────────────────────────────────────────────────────────
# MARKET METADATA
# ─────────────────────────────────────────────────────────────────────────────
_market_cache: Dict[str, dict] = {}

def fetch_market(condition_id: str) -> Optional[dict]:
    """
    Fetch market metadata by conditionId.
    Validates that returned market actually matches the queried conditionId.
    This prevents cache poisoning where wrong market data gets stored.
    """
    if condition_id in _market_cache:
        cached = _market_cache[condition_id]
        # Validate cached entry belongs to this conditionId
        cached_cid = cached.get("conditionId", cached.get("condition_id", ""))
        if cached_cid and cached_cid.lower() != condition_id.lower():
            # Cache poisoned — remove and re-fetch
            del _market_cache[condition_id]
        else:
            return cached

    def _extract_tokens(m: dict) -> list:
        """Extract clobTokenIds from market or CLOB data."""
        for field in ("clobTokenIds", "clob_token_ids", "tokenIds"):
            ids = m.get(field, [])
            if ids:
                if isinstance(ids, str):
                    try:
                        import json as _j
                        ids = _j.loads(ids)
                    except Exception:
                        continue
                if isinstance(ids, list) and ids:
                    return [t.get("token_id", t) if isinstance(t, dict)
                            else t for t in ids]
        # Try tokens array
        tokens = m.get("tokens", [])
        if tokens:
            return [t.get("token_id", t) if isinstance(t, dict)
                    else t for t in tokens]
        return []

    def _validate_and_cache(m: dict, cid: str) -> Optional[dict]:
        """Return market only if it matches queried conditionId."""
        if not m:
            return None
        # Check conditionId matches
        m_cid = m.get("conditionId", m.get("condition_id", ""))
        if m_cid and m_cid.lower() != cid.lower():
            return None  # Wrong market — reject
        # Ensure tokens populated
        if not m.get("clobTokenIds"):
            m["clobTokenIds"] = _extract_tokens(m)
        _market_cache[cid] = m
        return m

    # Source 1: Gamma API with conditionId param
    for param_key in ("conditionId", "condition_id"):
        data = http_get(f"{config.GAMMA_API}/markets",
                        {param_key: condition_id})
        if not data:
            continue
        markets = data if isinstance(data, list) else data.get("markets", [])
        if not isinstance(markets, list) and isinstance(data, dict):
            markets = [data]
        for m in (markets or []):
            result = _validate_and_cache(m, condition_id)
            if result:
                return result

    # Source 2: CLOB API
    clob = http_get(f"{config.CLOB_API}/markets/{condition_id}")
    if clob and isinstance(clob, dict):
        result = _validate_and_cache(clob, condition_id)
        if result:
            return result

    # Source 3: CLOB market search
    clob2 = http_get(f"{config.CLOB_API}/markets",
                     {"condition_id": condition_id})
    if clob2:
        markets2 = clob2 if isinstance(clob2, list) else clob2.get("data", [])
        for m in (markets2 or []):
            result = _validate_and_cache(m, condition_id)
            if result:
                return result

    return None

# ─────────────────────────────────────────────────────────────────────────────
# ORDERBOOK — with multiple endpoint fallbacks
# ─────────────────────────────────────────────────────────────────────────────
def fetch_orderbook(token_id: str) -> dict:
    """
    Fetch orderbook snapshot. Tries multiple endpoints.
    Returns {"bids": [...], "asks": [...], "_empty": True/False}
    """
    if not token_id:
        return {"bids": [], "asks": [], "_empty": True}

    # Try CLOB /book endpoint
    for url, params in [
        (f"{config.CLOB_API}/book",     {"token_id": token_id}),
        (f"{config.CLOB_API}/orderbook",{"token_id": token_id}),
        (f"{config.CLOB_API}/books/{token_id}", None),
    ]:
        data = http_get(url, params)
        if not data:
            continue
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        # Validate: real orderbook has at least some entries
        if asks or bids:
            return {"bids": bids, "asks": asks, "_empty": False}

    # Empty result — mark so callers can skip orderbook-dependent checks
    return {"bids": [], "asks": [], "_empty": True}


def fetch_best_bid_ask(token_id: str) -> Tuple[float, float]:
    """
    Returns (best_bid, best_ask).
    Tries CLOB price endpoints, falls back to midpoint, then market data.
    Returns (0, 0) if truly unavailable — callers must handle this.
    """
    if not token_id:
        return 0.0, 0.0

    # Try /price endpoint (buy side gives ask, sell side gives bid)
    ask_data = http_get(f"{config.CLOB_API}/price",
                        {"token_id": token_id, "side": "BUY"})
    bid_data = http_get(f"{config.CLOB_API}/price",
                        {"token_id": token_id, "side": "SELL"})

    best_ask = _safe_float((ask_data or {}).get("price", 0))
    best_bid = _safe_float((bid_data or {}).get("price", 0))

    if best_ask > 0 and best_bid > 0 and best_ask > best_bid:
        return best_bid, best_ask

    # Try /midpoint
    mid_data = http_get(f"{config.CLOB_API}/midpoint",
                        {"token_id": token_id})
    if mid_data:
        mid = _safe_float(mid_data.get("mid", mid_data.get("price", 0)))
        if 0 < mid < 1:
            # Estimate bid/ask around midpoint (1% spread assumption)
            return round(mid - 0.005, 4), round(mid + 0.005, 4)

    # Return zeros — caller should skip spread check
    return 0.0, 0.0


def get_price_from_market(market: dict) -> Tuple[float, float]:
    """
    Extract best bid/ask from Gamma market object.
    Used as fallback when CLOB orderbook is empty.
    """
    prices_raw = market.get("outcomePrices", market.get("prices", "[0.5,0.5]"))
    try:
        if isinstance(prices_raw, str):
            import json as _j
            prices = _j.loads(prices_raw)
        else:
            prices = prices_raw
        yes_price = float(prices[0]) if prices else 0.5
    except Exception:
        yes_price = 0.5

    # Estimate bid/ask: ±1% around market price
    best_bid = round(yes_price - 0.01, 4)
    best_ask = round(yes_price + 0.01, 4)
    return max(0.01, best_bid), min(0.99, best_ask)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION DIFF ENGINE
# ─────────────────────────────────────────────────────────────────────────────
class WhaleDiffer:
    """
    Tracks previous position state per whale.
    On each poll, diffs old vs new to emit BUY/CLOSE events.
    """
    def __init__(self):
        self._prev: Dict[str, Dict[str, dict]] = {}
        self._startup_snapshot: Dict[str, Set[str]] = {}
        self._snapshot_done: Set[str] = set()

    def set_startup_snapshot(self, addr: str, positions: Dict[str, dict],
                               max_age_mins: float = 30.0):
        """
        Record whale positions at startup.
        Only ignore positions older than max_age_mins.
        Recent positions (< 10 min old) are still eligible to be copied —
        the whale may have just opened them and we want to catch them.
        """
        import time as _t
        now = _t.time()
        old_keys = set()
        for key, pos in positions.items():
            age = pos.get("_age_mins", 9999)
            if age > max_age_mins:
                old_keys.add(key)
            # else: recent position — do NOT snapshot, allow copying

        self._startup_snapshot[addr.lower()] = old_keys
        self._snapshot_done.add(addr.lower())
        self._prev[addr.lower()] = dict(positions)

    def diff(self, addr: str, new_positions: Dict[str, dict]) -> List[dict]:
        addr = addr.lower()
        old  = self._prev.get(addr, {})
        snap = self._startup_snapshot.get(addr, set())
        events = []

        all_cids = set(old) | set(new_positions)
        for cid in all_cids:
            if cid in snap:
                continue
            op = old.get(cid)
            np = new_positions.get(cid)
            old_sz = _safe_float((op or {}).get("size", 0))
            new_sz = _safe_float((np or {}).get("size", 0))

            if old_sz == 0.0 and new_sz > 0.0:
                # Use raw condition_id for market fetch, not composite key
                raw_cid = (np or {}).get("_cid_raw", cid.split("_")[0])
                events.append({"type": "BUY", "cid": raw_cid,
                                "data": np, "addr": addr,
                                "key": cid})
            elif old_sz > 0.0 and new_sz == 0.0:
                raw_cid = (op or {}).get("_cid_raw", cid.split("_")[0])
                events.append({"type": "CLOSE", "cid": raw_cid,
                                "data": op, "addr": addr,
                                "key": cid,
                                "slug": (op or {}).get("slug","")})
            elif (op is not None and np is not None and
                  new_sz < old_sz * 0.5):
                events.append({"type": "SIZE_DOWN", "cid": cid,
                                "data": np, "addr": addr,
                                "old_size": old_sz, "new_size": new_sz})

        self._prev[addr] = dict(new_positions)
        return events

    def is_snapshot_done(self, addr: str) -> bool:
        return addr.lower() in self._snapshot_done


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL EXTRACTOR
# ─────────────────────────────────────────────────────────────────────────────
def extract_signal(event: dict, market: dict, orderbook: dict) -> Optional[dict]:
    """
    Given a BUY event from a whale, extract a clean trading signal.
    Returns None if signal should be skipped (bad data).
    """
    pos_data = event["data"]

    entry_price = _safe_float(pos_data.get("price",
                  pos_data.get("avgPrice",
                  pos_data.get("curPrice", 0))))
    if entry_price <= 0 or entry_price >= 1:
        return None

    size_usd = _safe_float(pos_data.get("usdcSize",
               pos_data.get("cash",
               pos_data.get("currentValue", 0))))
    if size_usd < config.MIN_WHALE_USD:
        return None

    asks     = orderbook.get("asks", [])
    best_ask = min((float(a.get("price", 1)) for a in asks), default=0.0)

    # If orderbook empty, try to get price from market metadata
    if best_ask == 0.0 or orderbook.get("_empty"):
        _, best_ask = get_price_from_market(market)

    # Extract outcome — try multiple field names, normalize to YES/NO
    raw_outcome = (
        pos_data.get("outcome") or
        pos_data.get("outcomeIndex") or
        pos_data.get("side") or
        "YES"
    )
    # Normalize: "1"/"No"/"NO"/"no" → "NO", everything else → "YES"
    if str(raw_outcome).strip() in ("1", "No", "NO", "no", "2"):
        outcome = "NO"
    else:
        outcome = "YES"

    is_limit    = is_limit_order(entry_price, best_ask, "BUY")

    # Prefer slug from position data directly — more reliable than market fetch
    # Position data from Polymarket API often includes slug/market directly
    pos_slug  = (pos_data.get("slug") or pos_data.get("market") or
                 pos_data.get("marketSlug") or "")
    mkt_slug  = market.get("slug", "") if market else ""
    slug      = pos_slug or mkt_slug or condition_id[:20]

    pos_title = (pos_data.get("title") or pos_data.get("question") or
                 pos_data.get("marketQuestion") or "")
    mkt_title = market.get("question", market.get("title", "")) if market else ""
    title     = pos_title or mkt_title or slug
    outcome_idx = 0 if outcome == "YES" else 1

    # Extract token_id — try every known field name in priority order
    token_id = ""

    # 1. From market clobTokenIds array (most reliable)
    for field in ("clobTokenIds", "clob_token_ids", "tokenIds", "tokens"):
        ids = market.get(field, [])
        if isinstance(ids, list) and len(ids) > outcome_idx:
            token_id = str(ids[outcome_idx])
            break
        elif isinstance(ids, str):
            try:
                import json as _j
                ids_parsed = _j.loads(ids)
                if isinstance(ids_parsed, list) and len(ids_parsed) > outcome_idx:
                    token_id = str(ids_parsed[outcome_idx])
                    break
            except Exception:
                pass

    # 2. From whale position data (the asset they hold)
    if not token_id:
        for field in ("asset", "assetId", "asset_id", "tokenId", "token_id",
                      "conditionId", "outcomeToken"):
            v = pos_data.get(field, "")
            if v and len(str(v)) > 20:   # real token IDs are long hex strings
                token_id = str(v)
                break

    # 3. From market condition_id as last resort (won't work for ordering
    #    but at least surfaces the issue clearly)
    if not token_id:
        token_id = event.get("cid", "")

    if not token_id:
        return None   # can't place order without token ID

    return {
        "whale_addr":   event["addr"],
        "whale_name":   wh.name(event["addr"]),
        "cid":          event["cid"],
        "market_slug":  slug,
        "market_title": title[:80],
        "condition_id": event["cid"],
        "asset_id":     token_id,
        "outcome":      outcome.upper(),
        "entry_price":  entry_price,
        "size_usd":     size_usd,
        "is_limit":     is_limit,
        "best_ask_at_detection": best_ask,
        "market":       market,
        "orderbook":    orderbook,
    }
