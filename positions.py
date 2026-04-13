"""
positions.py — Open position lifecycle management.
Handles SL (-27%), runner alert (+900%), max hold (72h), whale-close mirroring.
Computes PnL exactly as Polymarket profile view: (cur - entry) / entry × 100%
"""
import time, uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

import config
from state import StateStore
import executor


# ─────────────────────────────────────────────────────────────────────────────
# POSITION FACTORY
# ─────────────────────────────────────────────────────────────────────────────
def make_position(
    whale_addr: str, whale_name: str,
    market_slug: str, market_title: str,
    condition_id: str, asset_id: str,
    outcome: str, entry_price: float,
    size_usd: float, order_id: str,
    is_cluster: bool = False,
) -> dict:
    shares   = round(size_usd / max(entry_price, 0.001), 4)
    sl_price = round(entry_price * (1 - config.SL_PCT), 6)   # -27% from entry
    return {
        "id":           uuid.uuid4().hex,
        "whale_addr":   whale_addr.lower(),
        "whale_name":   whale_name,
        "market_slug":  market_slug,
        "market_title": market_title,
        "condition_id": condition_id,
        "asset_id":     asset_id,
        "outcome":      outcome.upper(),
        "entry_price":  entry_price,
        "size_usd":     size_usd,
        "shares":       shares,
        "sl_price":     sl_price,
        "order_id":     order_id,
        "opened_ts":    time.time(),
        "status":       "OPEN",
        "is_cluster":   is_cluster,
        "dry_run":      config.DRY_RUN,
        # Live tracking
        "cur_price":    entry_price,
        "pnl_pct":      0.0,
        "pnl_usd":      0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PNL CALCULATION  (matches Polymarket profile view)
# ─────────────────────────────────────────────────────────────────────────────
def calc_pnl(pos: dict, current_price: float) -> tuple:
    """Returns (pnl_pct, pnl_usd) matching Polymarket's profile view."""
    entry = pos["entry_price"]
    if entry <= 0:
        return 0.0, 0.0
    pnl_pct = (current_price - entry) / entry    # e.g. 0.15 = +15%
    pnl_usd = pnl_pct * pos["size_usd"]
    return round(pnl_pct, 6), round(pnl_usd, 4)


# ─────────────────────────────────────────────────────────────────────────────
# POSITION MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class PositionManager:
    def __init__(self, db: StateStore):
        self.db        = db
        self._open:    Dict[str, dict] = {}   # pos_id → pos
        self._mkts:    Set[str]        = set() # market slugs in use
        self._whales:  Dict[str, Set[str]] = {}  # whale_addr → set of pos_ids
        # Price cache from WS (asset_id → price)
        self._prices:  Dict[str, float] = {}

    # ── LOAD FROM DB (on resume) ──────────────────────────────────────────────
    def load_from_db(self, positions: List[dict]):
        """Restore in-memory state from DB records (called on resume)."""
        for p in positions:
            p["cur_price"] = p.get("cur_price", p["entry_price"])
            pnl_p, pnl_u   = calc_pnl(p, p["cur_price"])
            p["pnl_pct"]   = pnl_p
            p["pnl_usd"]   = pnl_u
            self._open[p["id"]] = p
            self._mkts.add(p["market_slug"])
            self._whales.setdefault(p["whale_addr"], set()).add(p["id"])
        print(f"[POSITIONS] Loaded {len(self._open)} position(s) from DB.")

    # ── OPEN ──────────────────────────────────────────────────────────────────
    def can_open(self, market_slug: str) -> tuple:
        if len(self._open) >= config.MAX_OPEN_POSITIONS:
            return False, "MAX_POSITIONS"
        if market_slug in self._mkts:
            return False, "MARKET_ALREADY_OPEN"
        return True, "OK"

    def open(self, pos: dict):
        self._open[pos["id"]] = pos
        self._mkts.add(pos["market_slug"])
        self._whales.setdefault(pos["whale_addr"], set()).add(pos["id"])
        self.db.save_position(pos)

    # ── UPDATE PRICE ──────────────────────────────────────────────────────────
    def update_price(self, asset_id: str, price: float):
        self._prices[asset_id] = price
        # Update all positions using this asset
        for pos in self._open.values():
            if pos["asset_id"] == asset_id:
                p, u = calc_pnl(pos, price)
                pos["cur_price"] = price
                pos["pnl_pct"]   = p
                pos["pnl_usd"]   = u

    # ── CLOSE ─────────────────────────────────────────────────────────────────
    def close(self, pos_id: str, reason: str) -> Optional[dict]:
        pos = self._open.pop(pos_id, None)
        if not pos:
            return None
        self._mkts.discard(pos["market_slug"])
        self._whales.get(pos["whale_addr"], set()).discard(pos_id)

        cur = pos.get("cur_price", pos["entry_price"])
        _, pnl_usd = calc_pnl(pos, cur)
        pos["exit_price"]  = cur
        pos["exit_reason"] = reason
        pos["pnl_usd"]     = pnl_usd
        pos["closed_ts"]   = time.time()
        pos["status"]      = "CLOSED"

        # Execute sell order
        ok, oid = executor.place_limit_sell(
            pos["asset_id"], cur, pos["shares"])
        pos["close_order_id"] = oid

        # Persist
        self.db.close_position(pos_id, cur, reason, pnl_usd)
        self.db.audit("POSITION_CLOSED",
                      f"entry={pos['entry_price']:.3f} exit={cur:.3f} "
                      f"pnl_usd={pnl_usd:.2f} reason={reason}",
                      pos["whale_addr"], pos["market_slug"],
                      "CLOSED")
        return pos

    # ── MONITOR ───────────────────────────────────────────────────────────────
    def monitor(self) -> List[dict]:
        """
        Check all open positions for SL/runner/maxhold triggers.
        Returns list of closed positions this cycle.
        """
        closed = []
        now    = time.time()

        for pos_id, pos in list(self._open.items()):
            cur     = pos.get("cur_price", pos["entry_price"])
            entry   = pos["entry_price"]
            sl      = pos["sl_price"]
            held_h  = (now - pos["opened_ts"]) / 3600
            pnl_pct = pos.get("pnl_pct", 0.0)

            reason = None

            # 1. Stop loss (-27% from entry, floating-point safe)
            if cur <= sl:
                reason = f"STOP_LOSS(cur={cur:.4f}<=sl={sl:.4f})"

            # 2. Max hold (72h dead-man switch)
            elif held_h >= config.MAX_HOLD_HOURS:
                reason = f"MAX_HOLD({held_h:.1f}h)"

            # 3. Runner alert (+900%) — DO NOT auto-close, just alert
            elif pnl_pct >= config.TP_RUNNER_PCT:
                _runner_alert(pos)

            if reason:
                closed.append(self.close(pos_id, reason))

        return [c for c in closed if c]

    # ── WHALE EXIT MIRROR ─────────────────────────────────────────────────────
    def mirror_whale_exit(self, whale_addr: str, market_slug: str) -> Optional[dict]:
        """Close our position when whale closes theirs."""
        whale_addr = whale_addr.lower()
        for pos_id, pos in list(self._open.items()):
            if (pos["whale_addr"] == whale_addr and
                    pos["market_slug"] == market_slug):
                return self.close(pos_id, "WHALE_CLOSED")
        return None

    # ── GETTERS ───────────────────────────────────────────────────────────────
    def get_all(self) -> List[dict]:
        return list(self._open.values())

    def count(self) -> int:
        return len(self._open)

    def open_slugs(self) -> Set[str]:
        return set(self._mkts)

    def get_by_whale(self, whale_addr: str) -> List[dict]:
        ids = self._whales.get(whale_addr.lower(), set())
        return [self._open[i] for i in ids if i in self._open]

    def is_whale_in_market(self, whale_addr: str, market_slug: str) -> bool:
        for pos in self.get_by_whale(whale_addr):
            if pos["market_slug"] == market_slug:
                return True
        return False

    def get_asset_ids(self) -> List[str]:
        return [p["asset_id"] for p in self._open.values()]


# ─────────────────────────────────────────────────────────────────────────────
# RUNNER ALERT
# ─────────────────────────────────────────────────────────────────────────────
def _runner_alert(pos: dict):
    """
    +900% gain detected. This is a RUNNER — do NOT auto-close.
    Alert only. Human decides whether to exit or let it ride to $1.00 resolution.
    """
    pct = pos.get("pnl_pct", 0) * 100
    print(f"\n{'='*55}")
    print(f"  🏆 RUNNER ALERT: +{pct:.0f}%")
    print(f"  Market: {pos['market_slug']}")
    print(f"  Entry: {pos['entry_price']:.4f} → Current: {pos.get('cur_price',0):.4f}")
    print(f"  PnL: ${pos.get('pnl_usd',0):.4f}")
    print(f"  Recommendation: Let run to resolution ($1.00)")
    print(f"  To close manually: set FORCE_CLOSE_{pos['id'][:8]} file")
    print(f"{'='*55}\n")
