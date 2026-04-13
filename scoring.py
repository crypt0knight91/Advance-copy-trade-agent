"""
scoring.py — Composite whale trust scoring with time-decay.

Score = (WR_15d×0.40) + (avg_profit×0.25) + (consistency×0.20) + (exec_quality×0.15)
Decay: trades 0-3d=100%, 3-7d=80%, 7-15d=50% weight

Data source priority for PnL:
  1. Activity feed with explicit pnl/cashPnl fields
  2. Goldsky PnL subgraph (real on-chain data)
  3. Inferred from BUY/SELL pairs in same market
  4. Activity-only volume/frequency score (last resort — never returns 0.17)
"""
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import config

_GS_PNL = ("https://api.goldsky.com/api/public/"
           "project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn")
_PNL_QUERY = """
query WalletPnl($wallet: String!) {
  userPositions(first: 50 where: {user: $wallet} orderBy: scaledRealizedPNL orderDirection: desc) {
    scaledRealizedPNL
    scaledCollateralVolume
    quantitySold
  }
}
"""

def _safe(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d

def _now(): return time.time()

def _decay_weight(ts: float) -> float:
    d = (_now() - ts) / 86400
    if d <= 3:    return config.DECAY["0_3d"]
    elif d <= 7:  return config.DECAY["3_7d"]
    elif d <= 15: return config.DECAY["7_15d"]
    return 0.0

def _fetch_goldsky_pnl(addr: str) -> List[dict]:
    try:
        import requests
        r = requests.post(_GS_PNL,
            json={"query": _PNL_QUERY, "variables": {"wallet": addr.lower()}},
            timeout=12)
        r.raise_for_status()
        positions = r.json().get("data", {}).get("userPositions", [])
        return [
            {"pnl": _safe(p.get("scaledRealizedPNL", 0)) / 1e6,
             "volume": _safe(p.get("scaledCollateralVolume", 0)) / 1e6}
            for p in positions if _safe(p.get("quantitySold", 0)) > 0
        ]
    except Exception:
        return []

def _infer_from_pairs(activity: List[dict], cutoff: float) -> List[dict]:
    """Infer wins/losses by matching BUY+SELL on same conditionId."""
    by_mkt: Dict[str, list] = defaultdict(list)
    for t in activity:
        cid = t.get("conditionId", t.get("conditionID", t.get("condition_id", "")))
        if cid:
            by_mkt[cid].append(t)
    inferred = []
    for cid, trades in by_mkt.items():
        trades_s = sorted(trades, key=lambda x: _safe(x.get("timestamp", 0)))
        buys  = [t for t in trades_s if t.get("side","").upper() == "BUY"]
        sells = [t for t in trades_s if t.get("side","").upper() == "SELL"]
        if not buys or not sells:
            continue
        bp = _safe(buys[0].get("price", 0))
        sp = _safe(sells[-1].get("price", 0))
        ts = _safe(sells[-1].get("timestamp", 0))
        if ts < cutoff or bp <= 0 or sp <= 0:
            continue
        size = _safe(buys[0].get("usdcSize", 10.0), 10.0)
        inferred.append({
            "pnl":       (sp - bp) * (size / bp),
            "timestamp": ts,
            "source":    "inferred",
        })
    return inferred

def _activity_partial(activity: List[dict], cutoff: float) -> dict:
    """Last-resort: give partial credit based on activity volume/frequency."""
    recent = [t for t in activity if _safe(t.get("timestamp", 0)) >= cutoff]
    n      = len(recent)
    vol    = sum(_safe(t.get("usdcSize", 0)) for t in recent)
    avg_sz = vol / n if n > 0 else 0
    freq   = min(n / 20.0, 1.0)
    size_s = min(avg_sz / 100.0, 1.0)
    score  = round(0.20 + freq * 0.20 + size_s * 0.15, 4)
    return {
        "trust_score": score, "wr_15d": 0.0, "avg_profit": 0.0,
        "consistency": 0.5, "exec_quality": 0.5, "trade_count": n,
        "is_active": (score >= config.MIN_TRUST_SCORE and
                      n >= max(config.MIN_TRADES_FOR_WR, 3)),
        "data_source": "activity_only",
    }

def compute_trust_score(activity: List[dict],
                         addr: str = "",
                         use_goldsky: bool = True) -> dict:
    """
    Compute composite trust score for a whale wallet.
    Tries multiple data sources — never returns a 0.17 score for active traders.
    """
    now    = _now()
    cutoff = now - config.LOOKBACK_DAYS * 86400
    recent = [t for t in activity if _safe(t.get("timestamp", 0)) >= cutoff]

    # ── Source 1: explicit PnL fields ────────────────────────────────────────
    resolved = []
    for t in recent:
        pnl = None
        for key in ("pnl", "cashPnl", "realizedPnl"):
            if t.get(key) is not None:
                pnl = _safe(t[key]); break
        if pnl is None and t.get("percentPnl") is not None:
            pct  = _safe(t["percentPnl"])
            size = _safe(t.get("usdcSize", 10.0), 10.0)
            pnl  = pct * size / 100.0
        if pnl is not None:
            resolved.append({"pnl": pnl, "timestamp": _safe(t.get("timestamp", now)),
                              "source": "activity"})
    data_source = "activity_direct"

    # ── Source 2: Goldsky subgraph ────────────────────────────────────────────
    if len(resolved) < config.MIN_TRADES_FOR_WR and addr and use_goldsky:
        gs = _fetch_goldsky_pnl(addr)
        if gs:
            for g in gs:
                resolved.append({"pnl": g["pnl"], "timestamp": now - 7*86400,
                                  "source": "goldsky"})
            data_source = "goldsky"

    # ── Source 3: Infer from BUY/SELL pairs ───────────────────────────────────
    if len(resolved) < config.MIN_TRADES_FOR_WR and recent:
        inf = _infer_from_pairs(recent, cutoff)
        if inf:
            resolved.extend(inf)
            data_source = "inferred_pairs"

    # ── Source 4: Activity-only partial score ────────────────────────────────
    if len(resolved) < config.MIN_TRADES_FOR_WR:
        return _activity_partial(activity, cutoff)

    # ── Compute scores ────────────────────────────────────────────────────────
    # 1. Win rate with decay
    wins_w = total_w = 0.0
    for t in resolved:
        w = _decay_weight(_safe(t.get("timestamp", now)))
        if w == 0: continue
        total_w += w
        if _safe(t.get("pnl", 0)) > 0: wins_w += w
    wr_15d = (wins_w / total_w) if total_w > 0 else 0.0

    # 2. Avg profit (normalized, $200 avg = 1.0)
    profits = [_safe(t.get("pnl", 0)) for t in resolved]
    avg_p   = sum(profits) / len(profits) if profits else 0.0
    avg_profit_score = min(max(avg_p / 200.0, 0.0), 1.0)

    # 3. Consistency
    if len(profits) >= 3:
        mean = sum(profits) / len(profits)
        std  = (sum((p - mean)**2 for p in profits) / len(profits)) ** 0.5
        consistency = max(0.0, 1.0 - (std / mean) / 3.0) if mean > 0 else 0.0
    else:
        consistency = 0.5

    # 4. Execution quality
    ex = [t for t in recent if t.get("market_price_at_detection") is not None]
    if ex:
        beats = sum(
            1 for t in ex
            if (t.get("side","BUY").upper()=="BUY" and
                _safe(t.get("price",0)) <= _safe(t.get("market_price_at_detection",1)))
            or (t.get("side","").upper()=="SELL" and
                _safe(t.get("price",0)) >= _safe(t.get("market_price_at_detection",0)))
        )
        exec_quality = beats / len(ex)
    else:
        exec_quality = 0.5

    # 5. Composite score
    w = config.TRUST_W
    trust = round(min(max(
        w["wr_last15d"]   * wr_15d           +
        w["avg_profit"]   * avg_profit_score +
        w["consistency"]  * consistency      +
        w["exec_quality"] * exec_quality,
    0.0), 1.0), 4)

    # is_active gate: pass if trust >= threshold AND
    # either WR is known+meets threshold OR data is inferred (no WR available)
    wr_ok = (wr_15d >= config.MIN_WR_15D or
             data_source in ("inferred_pairs", "activity_only"))
    is_active = (trust >= config.MIN_TRUST_SCORE and
                 len(resolved) >= config.MIN_TRADES_FOR_WR and
                 wr_ok)
    return {
        "trust_score":  trust,
        "wr_15d":       round(wr_15d, 4),
        "avg_profit":   round(avg_profit_score, 4),
        "consistency":  round(consistency, 4),
        "exec_quality": round(exec_quality, 4),
        "trade_count":  len(resolved),
        "is_active":    is_active,
        "data_source":  data_source,
    }

def is_limit_order(fill_price: float, best_ask: float, side: str = "BUY") -> bool:
    THRESHOLD = 0.015
    if best_ask <= 0: return True
    if side.upper() == "BUY":
        return fill_price < (best_ask - THRESHOLD)
    return fill_price > ((1.0 - best_ask) + THRESHOLD)


# ─────────────────────────────────────────────────────────────────────────────
# BAYESIAN TRUST UPDATE  (Step 3 from Quant Framework)
# P(trustworthy | outcome) = P(outcome | trustworthy) × P(trustworthy) / P(outcome)
#
# After a copied trade resolves, update whale's trust score using Bayes:
# - WIN:  trust increases (evidence whale was right)
# - LOSS: trust decreases (evidence whale was wrong)
#
# Uses a simple Beta distribution update (conjugate prior for Bernoulli).
# alpha = prior wins, beta = prior losses
# posterior_mean = (alpha + win) / (alpha + beta + 1)
# ─────────────────────────────────────────────────────────────────────────────
def bayesian_update(current_trust: float, current_wr: float,
                     trade_count: int, outcome_win: bool) -> Tuple[float, float]:
    """
    Bayesian update of trust score after a trade resolves.

    Maps current_wr + trade_count to Beta(alpha, beta) prior, adds one
    observation, returns updated (new_trust, new_wr).

    current_trust : current composite trust score (0-1)
    current_wr    : current win rate (0-1)
    trade_count   : number of trades used to estimate current_wr
    outcome_win   : True if the copied trade resolved as a win

    Returns (updated_trust, updated_wr)
    """
    # Reconstruct Beta parameters from WR + count
    # alpha = wins so far, beta = losses so far
    # Use effective sample size = min(trade_count, 20) to avoid over-anchoring
    eff_count = max(min(trade_count, 20), 1)
    alpha = current_wr * eff_count           # estimated wins
    beta  = (1 - current_wr) * eff_count     # estimated losses

    # Add new observation
    if outcome_win:
        alpha += 1
    else:
        beta  += 1

    # Posterior mean WR
    new_wr = alpha / (alpha + beta)

    # Update trust score using the same formula but with new WR
    # Keep other components (consistency, exec_quality) from prior
    import config as _cfg
    w = _cfg.TRUST_W
    # Approximate: adjust trust proportionally to WR change
    wr_change    = new_wr - current_wr
    new_trust    = current_trust + w["wr_last15d"] * wr_change
    new_trust    = round(min(max(new_trust, 0.0), 1.0), 4)
    new_wr       = round(new_wr, 4)

    return new_trust, new_wr
