"""
filters.py — Market quality filters + cluster detection.
All filters return (passed: bool, reason: str).
"""
import os
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple

import config

# ─────────────────────────────────────────────────────────────────────────────
# SPREAD VWAP  (5-minute rolling)
# ─────────────────────────────────────────────────────────────────────────────
class SpreadVWAP:
    """Track rolling VWAP of spread per market for dynamic threshold."""
    def __init__(self, window_secs: int = 300):
        self._data: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))
        self._win = window_secs

    def update(self, market_slug: str, best_bid: float, best_ask: float):
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return
        spread = best_ask - best_bid
        self._data[market_slug].append((time.time(), spread))

    def vwap_spread(self, market_slug: str) -> float:
        """5-min VWAP spread. Returns 0 if no data."""
        cutoff = time.time() - self._win
        entries = [(ts, sp) for ts, sp in self._data.get(market_slug, [])
                   if ts >= cutoff]
        if not entries:
            return 0.0
        return sum(sp for _, sp in entries) / len(entries)

    def dynamic_threshold(self, market_slug: str,
                           base_pct: float = None) -> float:
        """
        Dynamic spread threshold: use VWAP × 1.5 as the threshold.
        If no history → use static base_pct from config.
        """
        base = base_pct or config.MAX_SPREAD_PCT
        vwap = self.vwap_spread(market_slug)
        if vwap <= 0:
            return base
        # Active market (high vwap) → allow up to vwap × 1.5
        # Quiet market (low vwap) → tighter
        return min(vwap * 1.5, base * 1.5)


spread_tracker = SpreadVWAP(config.SPREAD_VWAP_MINS * 60)

# ─────────────────────────────────────────────────────────────────────────────
# ORDERBOOK SLIPPAGE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────
def simulate_slippage(asks: List[dict], size_usd: float,
                      target_price: float) -> Tuple[float, float]:
    """
    Walk the ask side for a BUY order of size_usd.
    asks: [{"price": float, "size": float}, ...] sorted ascending
    Returns: (avg_fill_price, slippage_from_target)
    """
    remaining = size_usd
    total_cost = 0.0
    total_shares = 0.0

    for level in sorted(asks, key=lambda x: float(x.get("price", 1))):
        p = float(level.get("price", 1))
        s = float(level.get("size", 0))   # shares available
        usd_avail = s * p
        fill_usd = min(remaining, usd_avail)
        fill_shares = fill_usd / p
        total_cost   += fill_usd
        total_shares += fill_shares
        remaining    -= fill_usd
        if remaining <= 0.001:
            break

    if total_shares <= 0:
        return target_price, 0.0

    avg_fill = total_cost / total_shares
    slippage = avg_fill - target_price
    return round(avg_fill, 4), round(slippage, 4)

def check_slippage(asks: List[dict], size_usd: float,
                   target_price: float) -> Tuple[bool, str]:
    """Returns (ok, reason). Fails if slippage > MAX_SLIP_CENTS."""
    if not asks:
        return True, "no_book_data"   # can't verify — allow
    avg_fill, slip = simulate_slippage(asks, size_usd, target_price)
    if slip > config.MAX_SLIP_CENTS:
        return False, f"slippage={slip:.4f}>{config.MAX_SLIP_CENTS}"
    return True, f"slippage={slip:.4f}_ok"

# ─────────────────────────────────────────────────────────────────────────────
# BOOK DEPTH CHECK
# ─────────────────────────────────────────────────────────────────────────────
def check_book_depth(asks: List[dict], price: float,
                     within_cents: float = 0.03) -> Tuple[bool, str]:
    """Check there's MIN_DEPTH_USD within 3¢ of target price."""
    available = sum(
        float(a.get("size", 0)) * float(a.get("price", 0))
        for a in asks
        if abs(float(a.get("price", price)) - price) <= within_cents
    )
    if available < config.MIN_DEPTH_USD:
        return False, f"depth=${available:.0f}<${config.MIN_DEPTH_USD}"
    return True, f"depth=${available:.0f}_ok"

# ─────────────────────────────────────────────────────────────────────────────
# SPREAD CHECK (dynamic)
# ─────────────────────────────────────────────────────────────────────────────
def check_spread(market_slug: str, best_bid: float,
                 best_ask: float) -> Tuple[bool, str]:
    spread = best_ask - best_bid
    threshold = spread_tracker.dynamic_threshold(market_slug)
    if spread > threshold:
        return False, f"spread={spread:.3f}>{threshold:.3f}"
    return True, f"spread={spread:.3f}_ok"

# ─────────────────────────────────────────────────────────────────────────────
# PUMP / ANOMALY DETECTION
# ─────────────────────────────────────────────────────────────────────────────
def check_pump_anomaly(whale_trade_usd: float,
                       market_prev_volume: float) -> Tuple[bool, str]:
    """
    Reject if whale's trade is >PUMP_RATIO × previous market volume.
    e.g., whale buys $50k into market that had $500 volume → pump → skip.
    """
    if market_prev_volume <= 0:
        return True, "no_volume_data"
    ratio = whale_trade_usd / market_prev_volume
    if ratio > config.PUMP_RATIO:
        return False, f"pump_ratio={ratio:.1f}>{config.PUMP_RATIO}"
    return True, f"ratio={ratio:.2f}_ok"

# ─────────────────────────────────────────────────────────────────────────────
# MARKET META CHECKS
# ─────────────────────────────────────────────────────────────────────────────
def check_market_meta(market: dict) -> Tuple[bool, str]:
    """Check volume, expiry, and price range."""
    volume = float(market.get("volume", market.get("volumeNum", 0)) or 0)
    if volume < config.MIN_VOLUME:
        return False, f"volume=${volume:.0f}<${config.MIN_VOLUME}"

    # Hours to resolution
    end_ts = market.get("endDate", market.get("end_date_iso", ""))
    if end_ts:
        try:
            from datetime import datetime, timezone
            if isinstance(end_ts, (int, float)):
                dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(end_ts).replace("Z", "+00:00"))
            from datetime import datetime as dt_
            now = dt_.now(timezone.utc)
            hours_left = (dt - now).total_seconds() / 3600
            if hours_left < config.MIN_HOURS_LEFT:
                return False, f"expires_in={hours_left:.1f}h<{config.MIN_HOURS_LEFT}h"
        except Exception:
            pass

    # Price range
    prices_raw = market.get("outcomePrices", market.get("prices", "[0.5,0.5]"))
    try:
        import json
        if isinstance(prices_raw, str):
            prices = json.loads(prices_raw)
        else:
            prices = prices_raw
        yes_price = float(prices[0]) if prices else 0.5
    except Exception:
        yes_price = 0.5

    if yes_price > config.MAX_YES_PRICE:
        return False, f"yes_price={yes_price:.3f}>{config.MAX_YES_PRICE}"

    return True, "market_ok"

# ─────────────────────────────────────────────────────────────────────────────
# CLUSTER DETECTOR
# ─────────────────────────────────────────────────────────────────────────────
class ClusterDetector:
    """
    Detects when 2+ whales enter same market within CLUSTER_SECS.
    Also flags potential sybil clusters (same entity with multiple wallets).
    """
    def __init__(self):
        # market_slug → [(whale_addr, ts, size_usd, price), ...]
        self._events: Dict[str, List[tuple]] = defaultdict(list)

    def record(self, market_slug: str, whale_addr: str,
               size_usd: float, price: float):
        now = time.time()
        self._events[market_slug].append((whale_addr.lower(), now, size_usd, price))
        # Prune old events
        cutoff = now - config.CLUSTER_SECS
        self._events[market_slug] = [
            e for e in self._events[market_slug] if e[1] >= cutoff
        ]

    def check_cluster(self, market_slug: str,
                       new_addr: str) -> Tuple[bool, int]:
        """
        Returns (is_cluster, whale_count) for a potential new entry.
        is_cluster=True when ≥2 different whales entered this market recently.
        """
        events = self._events.get(market_slug, [])
        unique_whales = {e[0] for e in events if e[0] != new_addr.lower()}
        count = len(unique_whales) + 1  # +1 for new one
        return count >= config.CLUSTER_MIN, count

    def detect_sybil(self, market_slug: str) -> bool:
        """
        Returns True if events look sybil: multiple wallets, same market,
        nearly identical sizes and timing (within 30s of each other).
        """
        events = self._events.get(market_slug, [])
        if len(events) < 2:
            return False

        # Check timing correlation: all within 30s of each other
        ts_list = [e[1] for e in events]
        if max(ts_list) - min(ts_list) > 30:
            return False   # spread out → probably different actors

        # Check size correlation: if all sizes are within 5% → suspicious
        sizes = [e[2] for e in events]
        mean_sz = sum(sizes) / len(sizes)
        if mean_sz <= 0:
            return False
        cv = (sum((s - mean_sz)**2 for s in sizes) / len(sizes))**0.5 / mean_sz
        if cv < 0.05:   # very uniform sizes
            return True

        return False

    def get_cluster_size_usd(self, market_slug: str) -> float:
        """Total USD committed to this cluster."""
        return sum(e[2] for e in self._events.get(market_slug, []))


cluster_detector = ClusterDetector()


# ─────────────────────────────────────────────────────────────────────────────
# EV GATE  (Step 1 from Quantitative Trading Framework)
# EV = (P_win × profit_per_share) - (P_loss × stake_per_share)
# For a binary outcome contract priced at p_market:
#   profit_per_share = (1 - p_market)   [win: share pays $1, cost p_market]
#   stake_per_share  = p_market          [loss: lose what you paid]
# EV = p_true × (1 - p_market) - (1 - p_true) × p_market
# Skip if EV < MIN_EV_THRESHOLD (default 0.04 = 4¢ per dollar)
# ─────────────────────────────────────────────────────────────────────────────
MIN_EV_THRESHOLD = float(os.getenv("MIN_EV_THRESHOLD", "0.04"))  # 4% minimum edge

def compute_ev(p_true: float, p_market: float) -> float:
    """
    Expected Value per dollar staked.
    p_true   = estimated true probability (whale WR as proxy)
    p_market = current market price (what we'd pay per share)
    Returns EV in dollars per dollar staked. Positive = edge in our favour.
    """
    if p_market <= 0 or p_market >= 1:
        return 0.0
    return round(p_true * (1 - p_market) - (1 - p_true) * p_market, 4)

def check_ev(whale_wr: float, market_price: float,
              min_ev: float = None) -> Tuple[bool, str]:
    """
    EV gate: only copy if expected value is positive and above threshold.
    whale_wr     = whale's recent win rate (proxy for p_true)
    market_price = current YES price (what we pay per share)
    """
    min_e = min_ev if min_ev is not None else MIN_EV_THRESHOLD
    ev    = compute_ev(whale_wr, market_price)
    if ev < min_e:
        return False, f"EV={ev:.4f}<{min_e}"
    return True, f"EV={ev:.4f}_ok"


# ─────────────────────────────────────────────────────────────────────────────
# QUARTER KELLY SIZER  (Step 2 from Quantitative Trading Framework)
# f* = (p × b - q) / b   where b = (1 - price) / price
# Use Quarter Kelly (f*/4) for safety — Full Kelly is too volatile.
# Result capped between min_size and max_size.
# ─────────────────────────────────────────────────────────────────────────────
def kelly_size(bankroll: float, p_win: float, price: float,
               base_size: float = 4.0,
               max_size:  float = 6.0,
               fraction:  float = 0.25) -> float:
    """
    Quarter Kelly position sizing.
    bankroll  = current bankroll in USDC
    p_win     = estimated probability of winning (whale WR)
    price     = market price (cost per share)
    fraction  = Kelly fraction to use (0.25 = Quarter Kelly)

    Returns recommended size in USD, clamped between base_size and max_size.
    """
    if price <= 0 or price >= 1 or p_win <= 0 or bankroll <= 0:
        return base_size

    b = (1 - price) / price      # net odds
    q = 1 - p_win
    f_star = (p_win * b - q) / b  # Full Kelly fraction

    if f_star <= 0:
        return base_size   # no edge — use minimum

    quarter_kelly_fraction = f_star * fraction
    raw_size = bankroll * quarter_kelly_fraction

    # Clamp: never below base_size, never above max_size
    return round(max(base_size, min(raw_size, max_size)), 2)
