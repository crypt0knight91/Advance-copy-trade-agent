"""
ev_gate.py — Polymarket Quantitative Trading Framework
Implements all 4 formulas from the image exactly.

Step 01 — EV+ Calculation
    EV = (P_win × profit) - (P_loss × stake)
    Skip trade if EV ≤ MIN_EV threshold.

Step 02 — Kelly Criterion (Quarter Kelly)
    b = (1 - price) / price          # net odds
    f = (p × b - q) / b              # full kelly fraction
    quarter_kelly = f / 4            # always use quarter kelly
    trade_size = min(quarter_kelly × bankroll, config.TRADE_SIZE_USD)

Step 03 — Bayesian Updating
    The whale buying IS new evidence. We update our prior (market price)
    based on the whale's trust score (how reliable their signal is).
    P(A|B) = P(B|A) × P(A) / P(B)
    where:
      P(A)   = market price (crowd's prior probability)
      P(B|A) = whale_wr    (prob whale buys IF market will resolve YES)
      P(B)   = normalizer  = P(B|A)×P(A) + P(B|¬A)×(1-P(A))
    Result: P_win = posterior probability after whale signal

Step 04 — Nash Equilibrium / Maker Preference
    Makers earn +1.12% rebate, takers pay -1.12%.
    Prefer limit orders (GTC) — already enforced by is_limit_order().
    If the EV is borderline, maker fees tip the balance.
    Adjust effective EV for fee direction.
"""
from typing import Tuple
import config


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
MIN_EV            = 0.05   # 5% minimum edge (skip below this)
MAKER_FEE_RATE    = 0.0112 # +1.12% maker rebate (limit orders)
TAKER_FEE_RATE    = 0.0112 # -1.12% taker cost  (market orders)
QUARTER_KELLY     = 0.25   # always use quarter kelly
MIN_KELLY_SIZE    = 1.0    # never size below $1


# ─────────────────────────────────────────────────────────────────────────────
# STEP 03 — BAYESIAN UPDATE
# P_win = posterior probability after incorporating whale buy signal
# ─────────────────────────────────────────────────────────────────────────────
def bayesian_update(
    market_price: float,
    whale_wr: float,
    trust_score: float,
) -> float:
    """
    Update our prior (market_price) given the whale's buy signal.

    P(A)    = market_price       — crowd's probability YES resolves
    P(B|A)  = whale_wr           — prob whale buys IF YES is correct
    P(B|¬A) = 1 - whale_wr      — prob whale buys IF NO is correct (i.e. whale is wrong)
    P(B)    = P(B|A)×P(A) + P(B|¬A)×(1-P(A))   — total prob of whale buying

    Posterior: P(A|B) = P(B|A) × P(A) / P(B)

    trust_score scales how much we weight the whale signal vs the market.
    At trust=1.0: full bayesian update. At trust=0.0: return market price unchanged.
    """
    # Clamp inputs
    prior   = max(0.01, min(0.99, market_price))
    wr      = max(0.01, min(0.99, whale_wr if whale_wr > 0 else 0.50))
    trust   = max(0.0,  min(1.0,  trust_score))

    # Bayesian update
    p_b_given_a     = wr          # P(whale buys | YES correct)
    p_b_given_not_a = 1.0 - wr    # P(whale buys | NO correct)
    p_b             = p_b_given_a * prior + p_b_given_not_a * (1.0 - prior)

    if p_b <= 0:
        return prior

    posterior = (p_b_given_a * prior) / p_b

    # Blend: at low trust, stay closer to market price
    # At high trust, fully accept the posterior
    p_win = prior + trust * (posterior - prior)
    return round(max(0.01, min(0.99, p_win)), 6)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 01 — EV CALCULATION
# EV = (P_win × profit) - (P_loss × stake)
# ─────────────────────────────────────────────────────────────────────────────
def expected_value(
    p_win: float,
    price: float,
    is_maker: bool = True,
) -> float:
    """
    EV = (P_win × profit) - (P_loss × stake)

    For a binary YES contract bought at `price`:
      profit per $1 staked = (1.0 - price) / price × 1  [payout if wins]
      stake  per $1 staked = 1.0                         [loss if wrong]

    Simplified to per-dollar:
      profit_if_win  = 1.0 - price   (you receive $1 payout, paid `price`)
      loss_if_lose   = price         (you lose your stake)
      EV = P_win × (1 - price) - (1 - P_win) × price

    Step 04 (Nash): adjust for maker/taker fee
      maker (limit order) earns +1.12% rebate → effective EV += MAKER_FEE_RATE
      taker (market order) pays -1.12% fee    → effective EV -= TAKER_FEE_RATE
    """
    p_loss     = 1.0 - p_win
    profit     = 1.0 - price   # net gain per $1 if YES resolves
    stake      = price         # amount at risk per $1 staked

    raw_ev = (p_win * profit) - (p_loss * stake)

    # Step 04: fee adjustment
    fee_adj = MAKER_FEE_RATE if is_maker else -TAKER_FEE_RATE
    return round(raw_ev + fee_adj, 6)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 02 — KELLY CRITERION (Quarter Kelly)
# f = (p × b - q) / b   where b = (1 - price) / price
# ─────────────────────────────────────────────────────────────────────────────
def kelly_size(
    p_win: float,
    price: float,
    bankroll: float,
    max_size: float = None,
) -> float:
    """
    Full Kelly: f* = (p × b - q) / b
    where b = (1 - price) / price  [net odds]
          p = P_win
          q = 1 - p

    Always use Quarter Kelly (f* / 4) for safety.
    Returns position size in USD, capped at max_size (default: config.TRADE_SIZE_USD).
    """
    if price <= 0 or price >= 1:
        return max_size or config.TRADE_SIZE_USD

    b = (1.0 - price) / price   # net odds
    p = p_win
    q = 1.0 - p_win

    if b <= 0:
        return MIN_KELLY_SIZE

    full_kelly = (p * b - q) / b

    if full_kelly <= 0:
        return 0.0   # negative kelly = don't bet

    quarter_kelly  = full_kelly * QUARTER_KELLY
    raw_size       = quarter_kelly * bankroll
    cap            = max_size or config.TRADE_SIZE_USD

    return round(max(MIN_KELLY_SIZE, min(raw_size, cap)), 2)


# ─────────────────────────────────────────────────────────────────────────────
# GATE FUNCTION — called from should_copy()
# ─────────────────────────────────────────────────────────────────────────────
def ev_gate(
    market_price: float,
    whale_wr: float,
    trust_score: float,
    bankroll: float,
    is_maker: bool = True,
    min_ev: float = MIN_EV,
) -> Tuple[bool, str, float, float, float]:
    """
    Run all 4 steps and return gate decision.

    Returns:
        (passed, reason, p_win, ev, kelly_size_usd)

    Usage in should_copy():
        ok, reason, p_win, ev, size = ev_gate(price, whale_wr, trust, bankroll)
        if not ok: skip with reason
        else: use size as trade size
    """
    # Step 03: Bayesian update
    p_win = bayesian_update(market_price, whale_wr, trust_score)

    # Step 01: EV check
    ev = expected_value(p_win, market_price, is_maker=is_maker)

    if ev < min_ev:
        reason = (
            f"EV_FAIL(ev={ev:.3f}<{min_ev:.2f} "
            f"p_win={p_win:.2f} price={market_price:.2f})"
        )
        return False, reason, p_win, ev, 0.0

    # Step 02: Kelly size
    size = kelly_size(p_win, market_price, bankroll,
                      max_size=config.TRADE_SIZE_USD)

    if size <= 0:
        return False, f"KELLY_ZERO(p_win={p_win:.2f})", p_win, ev, 0.0

    reason = (
        f"EV_OK(ev={ev:.3f} p_win={p_win:.2f} "
        f"prior={market_price:.2f} kelly=${size:.2f})"
    )
    return True, reason, p_win, ev, size


# ─────────────────────────────────────────────────────────────────────────────
# QUICK REFERENCE (matches image variable table)
# ─────────────────────────────────────────────────────────────────────────────
"""
Variable Reference (from image):
  EV      Expected value ($)
  f       Fraction of bankroll to wager
  b       Net odds = (1 - price) / price
  P(A|B)  Posterior probability after evidence
  P(A)    Prior probability = market price
  P_win   Estimated true probability of winning
  p       Probability of winning
  q       1 - p (probability of losing)
  P(B|A)  Likelihood of evidence given A is true = whale_wr
  P(B)    Total probability of evidence
"""
