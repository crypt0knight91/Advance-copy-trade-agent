"""
╔══════════════════════════════════════════════════════════════════════════╗
║  WHALE MIRROR BOT v2.0                                                   ║
║  All Phase 1 + Phase 2 features.                                         ║
║  Gmail/Magic wallet support. Auto-resume after crash/restart.            ║
║                                                                          ║
║  Run: python main.py                                                     ║
║  Dashboard: http://localhost:8080                                        ║
║  Kill: touch KILL                                                        ║
║  Pause whale: touch PAUSE_0xABCDEF                                       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import os, sys, time, json
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
import whales as wh
from state      import StateStore, write_memory, resume_open_positions
from scoring    import compute_trust_score
from filters    import (check_spread, check_slippage, check_book_depth,
                         check_pump_anomaly, check_market_meta,
                         spread_tracker, cluster_detector)
from ev_gate    import ev_gate, expected_value, kelly_size
from executor   import place_limit_buy, get_my_positions, get_my_open_orders, cancel_all_open_orders, get_current_price
from positions  import PositionManager, make_position, calc_pnl
from protection import LossProtection, is_killed, is_whale_paused
from withdraw   import WithdrawalManager
from monitor    import (WhaleDiffer, http_get, fetch_whale_positions,
                         fetch_best_bid_ask, fetch_market,
                         fetch_orderbook, fetch_best_bid_ask, extract_signal)
from dashboard  import run_dashboard


# ─────────────────────────────────────────────────────────────────────────────
# BOOT BANNER
# ─────────────────────────────────────────────────────────────────────────────
def banner():
    mode = "🟡 DRY RUN" if config.DRY_RUN else "🔴 LIVE TRADING"
    print("═" * 62)
    print("  WHALE MIRROR BOT v2.0")
    print(f"  Mode:      {mode}")
    print(f"  Whales:    {len(wh.get_all())} unique addresses loaded")
    print(f"  Trade:     ${config.TRADE_SIZE_USD} per trade, max {config.MAX_OPEN_POSITIONS} positions")
    print(f"  SL:        -{config.SL_PCT*100:.0f}% from entry")
    print(f"  Whales:    edit whales.txt then restart")
    print(f"  Dashboard: http://localhost:{config.DASHBOARD_PORT}")
    print(f"  Kill:      touch KILL  |  Pause: touch PAUSE_0xADDRESS")
    print("═" * 62)


# Trust scoring removed — whale list is manually curated in whales.txt


# ─────────────────────────────────────────────────────────────────────────────
# COPY TRADE DECISION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def should_copy(signal: dict, pm: PositionManager,
                db: StateStore) -> tuple:
    """
    Run all filters on a signal. Returns (ok, reason, final_size_usd).
    """
    addr   = signal["whale_addr"]
    slug   = signal["market_slug"]
    market = signal["market"]
    ob     = signal["orderbook"]
    price  = signal["entry_price"]

    # 1. Global kill check
    if is_killed():
        return False, "KILL_SWITCH", 0

    # 2. Whale paused?
    if is_whale_paused(addr):
        return False, "WHALE_PAUSED", 0

    # 3. Trust check — DISABLED (you curate the list manually)
    # All addresses in whales.txt are trusted by definition.

    # 4. Market order check — allow if spread < 3¢ AND price is stable
    # Logic: whale used a market/FOK order. Safe to copy only if:
    #   a) bid-ask spread < 3¢ (tight, liquid market)
    #   b) current price hasn't moved more than 2¢ above whale's fill
    #      (meaning we can still get a similar price — not chasing a spike)

    # 5. Capacity
    ok, reason = pm.can_open(slug)
    if not ok:
        return False, reason, 0

    # 6. Market meta (volume, expiry, price)
    ok, reason = check_market_meta(market)
    if not ok:
        return False, f"MARKET:{reason}", 0

    # Also reject if the whale's own entry price is near-resolved
    # (e.g. whale buys No 98¢ — market is nearly done, no edge for us)
    if price >= config.MAX_YES_PRICE:
        return False, f"NEAR_RESOLVED(price={price:.3f}>={config.MAX_YES_PRICE})", 0

    # 7. Pump anomaly
    prev_vol = float(market.get("volume24hr", market.get("volume", 0)) or 0)
    ok, reason = check_pump_anomaly(signal["size_usd"], prev_vol)
    if not ok:
        return False, f"PUMP:{reason}", 0

    # 8. Spread + Depth + Slippage (skip gracefully if orderbook empty)
    asks = ob.get("asks", [])
    bids = ob.get("bids", [])
    ob_empty = ob.get("_empty", not asks and not bids)

    if not ob_empty:
        # Real orderbook data — run all three checks
        best_ask = min((float(a.get("price",1)) for a in asks), default=1.0)
        best_bid = max((float(b.get("price",0)) for b in bids), default=0.0)

        # Sanity check: bid=0 or ask=1 means effectively empty
        ob_empty = (best_bid == 0.0 and best_ask == 1.0)

    if not ob_empty:
        spread_tracker.update(slug, best_bid, best_ask)
        spread_cents = round(best_ask - best_bid, 4)

        # ── MARKET ORDER GATE ───────────────────────────────────────────────
        # Now we have live spread — decide whether to allow market orders
        is_limit = signal.get("is_limit", True)
        if not is_limit:
            # Condition A: spread must be under 3¢
            spread_ok = spread_cents < 0.03
            # Condition B: current ask within 2¢ of whale's fill price
            #   (price stable or only slightly higher — not a spike we'd chase)
            price_stable = best_ask <= price + 0.02
            if spread_ok and price_stable:
                # Allow — tight spread, stable price after whale's market order
                print(f"  ✅ MARKET_ORDER_ALLOWED "
                      f"(spread={spread_cents:.3f}<0.03, "
                      f"ask={best_ask:.3f} vs fill={price:.3f})")
            else:
                reason = (f"MARKET_ORDER_SKIP("
                          f"spread={spread_cents:.3f}"
                          f"{'✓' if spread_ok else '✗<0.03'} "
                          f"price_stable={'✓' if price_stable else '✗'})")
                return False, reason, 0
        # ───────────────────────────────────────────────────────────────────

        ok, reason = check_spread(slug, best_bid, best_ask)
        if not ok:
            return False, f"SPREAD:{reason}", 0

        ok, reason = check_book_depth(asks, price)
        if not ok:
            return False, f"DEPTH:{reason}", 0

        ok, reason = check_slippage(asks, config.TRADE_SIZE_USD, price)
        if not ok:
            return False, f"SLIPPAGE:{reason}", 0
    else:
        # Orderbook unavailable — use market metadata price as reference
        from monitor import get_price_from_market
        best_bid, best_ask = get_price_from_market(signal["market"])
        spread_cents = round(best_ask - best_bid, 4)

        # Without real orderbook — whale already executed successfully
        # which proves liquidity exists. Try CLOB price endpoint for spread.
        if not signal.get("is_limit", True):
            from monitor import fetch_best_bid_ask
            fb, fa = fetch_best_bid_ask(signal.get("asset_id", ""))
            if fb > 0 and fa > 0:
                estimated_spread = round(fa - fb, 4)
                price_stable     = fa <= price + 0.02
                if estimated_spread < 0.03 and price_stable:
                    print(f"  ✅ MARKET_ORDER_ALLOWED "
                          f"(clob_spread={estimated_spread:.3f} ask={fa:.3f})")
                else:
                    return False, (f"MARKET_ORDER_SKIP("
                                   f"spread={estimated_spread:.3f} "
                                   f"stable={price_stable})"), 0
            else:
                # Can't get any price data — whale executed so allow it
                print(f"  ✅ MARKET_ORDER_ALLOWED (no_orderbook_whale_executed)")
    
    size_usd = config.TRADE_SIZE_USD

    # 11. EV Gate
    # Market orders pay taker fee (-1.12%) — accounted for in EV
    is_limit_order = signal.get("is_limit", True)
    bankroll = float(db.get_kv("bankroll_current", config.BANKROLL) or config.BANKROLL)
    ev_ok, ev_reason, p_win, ev, kelly_usd = ev_gate(
        market_price = price,
        whale_wr     = 0.65,
        trust_score  = 0.70,
        bankroll     = bankroll,
        is_maker     = is_limit_order,  # limit=maker rebate, market=taker fee
    )
    if not ev_ok:
        return False, f"EV:{ev_reason}", 0

    size_usd = kelly_usd

    # 13. Cluster check
    cluster_detector.record(slug, addr, signal["size_usd"], price)
    is_cluster, count = cluster_detector.check_cluster(slug, addr)
    db.record_cluster_event(slug, addr, signal["size_usd"], price)

    if cluster_detector.detect_sybil(slug):
        return False, "SYBIL_DETECTED", 0

    # Cluster boost: override Kelly with max size when 2+ whales agree
    if is_cluster:
        size_usd = config.CLUSTER_BOOST_USD
        print(f"  🔥 CLUSTER ({count} whales): max size ${size_usd}")
    else:
        print(f"  📐 Kelly size: ${size_usd:.2f} (ev={ev:.3f} price={price:.3f})")

    return True, "OK", size_usd


# ─────────────────────────────────────────────────────────────────────────────
# PROCESS ONE SIGNAL
# ─────────────────────────────────────────────────────────────────────────────
def process_signal(signal: dict, pm: PositionManager,
                   protection: LossProtection, db: StateStore):
    addr = signal["whale_addr"]
    name = signal["whale_name"]
    slug = signal["market_slug"]

    # Loss protection
    ok, msg = protection.can_trade()
    if not ok:
        db.audit("SKIPPED", f"loss_protect:{msg}", addr, slug, "SKIP")
        return

    # Should we copy?
    ok, reason, size_usd = should_copy(signal, pm, db)
    if not ok:
        db.audit("SKIPPED", reason, addr, slug, "SKIP")
        print(f"  [SKIP] @{name:20} {slug[:30]:30} → {reason}")
        return

    # Place order
    entry  = signal["entry_price"]
    token  = signal["asset_id"]
    is_cluster = cluster_detector.check_cluster(slug, addr)[0]

    # Validate token ID before hitting API
    if not token or len(token) < 20:
        db.audit("SKIPPED", f"invalid_token_id:{token!r:.30}", addr, slug, "SKIP")
        print(f"  [SKIP] @{name:20} {slug[:30]:30} → INVALID_TOKEN_ID")
        return

    # ── PRICE LOGIC ────────────────────────────────────────────────────────
    # For market orders: use current ask price (fills instantly)
    #   cap at whale_fill + 3¢ max — don't chase beyond that
    # For limit orders: use whale's exact fill price (rests on book)
    is_market_order = not signal.get("is_limit", True)
    if is_market_order:
        _, live_ask = fetch_best_bid_ask(token)
        if live_ask > 0:
            max_chase = round(entry + 0.03, 4)   # whale fill + 3¢ max
            exec_price = round(min(live_ask, max_chase), 4)
            if live_ask > max_chase:
                print(f"  [SKIP] @{name:20} price chased too far "
                      f"(ask={live_ask:.3f} > fill+3¢={max_chase:.3f})")
                db.audit("SKIPPED", f"price_chased:{live_ask:.3f}>{max_chase:.3f}",
                         addr, slug, "SKIP")
                return
            print(f"  🎯 MARKET_EXEC: whale_fill={entry:.3f} "
                  f"live_ask={live_ask:.3f} exec={exec_price:.3f}")
        else:
            exec_price = round(entry + 0.01, 4)  # fallback: fill + 1¢
    else:
        exec_price = entry   # limit: use whale's exact price

    ok2, order_id = place_limit_buy(token, exec_price, size_usd)
    if not ok2:
        db.audit("ORDER_FAIL", order_id, addr, slug, "FAIL")
        print(f"  [FAIL] Order failed: {order_id}")
        return

    # Create position — use actual execution price not whale's fill
    pos = make_position(
        whale_addr   = addr,
        whale_name   = name,
        market_slug  = slug,
        market_title = signal["market_title"],
        condition_id = signal["condition_id"],
        asset_id     = token,
        outcome      = signal["outcome"],
        entry_price  = exec_price,
        size_usd     = size_usd,
        order_id     = order_id,
        is_cluster   = is_cluster,
    )
    pm.open(pos)
    protection.update(protection.current - size_usd)
    # Store current bankroll for Kelly sizer
    db.set_kv("bankroll_current", protection.current)

    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"\n  [OPEN] @{name:20} | {slug[:35]:35} | "
          f"{signal['outcome']} @ {entry:.4f} | "
          f"${size_usd} | SL={pos['sl_price']:.4f}")
    db.audit("POSITION_OPENED",
             f"entry={entry:.4f} size=${size_usd} sl={pos['sl_price']:.4f} "
             f"cluster={is_cluster} order={order_id}",
             addr, slug, "OPENED")


# ─────────────────────────────────────────────────────────────────────────────
# SHARED STATE FOR DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────
_dashboard_state: dict = {
    "dry_run": config.DRY_RUN,
    "positions": [],
    "whale_scores": [],
    "today_pnl": 0.0,
    "daily_loss_pct": 0.0,
    "active_whales": 0,
    "total_whales": len(wh.get_all()),
    "max_pos": config.MAX_OPEN_POSITIONS,
    "audit_tail": [],
}

def _update_dashboard(pm: PositionManager,
                       protection: LossProtection, db: StateStore):
    """Refresh shared state dict for dashboard API."""
    positions_data = []
    for p in pm.get_all():
        positions_data.append({
            **p,
            "profile_url": wh.profile_url(p["whale_addr"]),
        })

    scores_data = []
    for s in db.get_all_whale_scores():
        scores_data.append({
            **s,
            "profile_url": wh.profile_url(s["addr"]),
        })

    # Tail of audit log
    audit_tail = []
    try:
        with open(config.AUDIT_LOG) as f:
            lines = f.readlines()
            audit_tail = [l.rstrip() for l in lines[-20:]][::-1]
    except Exception:
        pass

    _dashboard_state.update({
        "positions":      positions_data,
        "whale_scores":   scores_data,
        "today_pnl":      db.get_today_pnl(),
        "daily_loss_pct": protection.daily_loss_pct(),
        "active_whales":  len(wh.get_all()),
        "audit_tail":     audit_tail,
    })


# ─────────────────────────────────────────────────────────────────────────────
# POSITION SYNC
# ─────────────────────────────────────────────────────────────────────────────
def _sync_my_positions(pm: PositionManager, db: StateStore,
                        protection: LossProtection):
    """
    Every 30s: reconcile bot state vs actual Polymarket.
    
    Checks TWO sources:
    1. /positions (filled, active positions)
    2. /orders    (pending, unfilled limit orders)
    
    If bot thinks something is open but it's only in orders (unfilled),
    cancel the order and free the slot.
    If bot thinks something is open but it's in neither → already closed.
    """
    if not config.MY_PROXY_WALLET:
        return

    # Fetch filled positions
    live = get_my_positions()
    live_assets = {}
    for p in live:
        asset = p.get("asset", p.get("assetId", p.get("asset_id", "")))
        size  = float(p.get("size", p.get("quantity", p.get("shares", 0))) or 0)
        if asset and size > 0:
            live_assets[asset] = p

    # Fetch pending/unfilled orders from CLOB
    open_orders = get_my_open_orders()
    order_assets = set()
    for o in open_orders:
        a = o.get("asset_id", o.get("asset", ""))
        if a:
            order_assets.add(a)

    bot_positions = pm.get_all()

    for pos in list(bot_positions):
        asset = pos.get("asset_id", "")
        slug  = pos.get("market_slug", "")
        if not asset:
            continue

        if asset in live_assets:
            # ── Filled position — update prices ───────────────────────────
            lp  = live_assets[asset]
            avg = float(lp.get("avgPrice", lp.get("initialValue", 0)) or 0)
            cur = float(lp.get("curPrice", lp.get("currentValue",
                        lp.get("price", 0))) or 0)

            if avg > 0 and abs(avg - pos["entry_price"]) > 0.005:
                old_e = pos["entry_price"]
                pos["entry_price"] = round(avg, 6)
                pos["sl_price"]    = round(avg * (1 - config.SL_PCT), 6)
                print(f"  [SYNC] Entry updated: {slug[:30]} "
                      f"{old_e:.4f}→{avg:.4f} SL→{pos['sl_price']:.4f}")

            if cur > 0:
                pm.update_price(asset, cur)

        elif asset in order_assets:
            # ── Unfilled limit order — check how long it's been sitting ───
            opened_secs = time.time() - pos.get("opened_ts", time.time())
            if opened_secs > 120:  # unfilled for >2 minutes → cancel + free slot
                print(f"  [SYNC] ⚠ Unfilled order >2min: {slug[:35]} "
                      f"(sitting {int(opened_secs)}s) — cancelling")
                # Cancel via CLOB
                order_id = pos.get("order_id", "")
                if order_id and not order_id.startswith("DRY"):
                    from executor import cancel_order
                    cancel_order(order_id)
                # Free slot in bot memory
                pm._open.pop(pos["id"], None)
                pm._mkts.discard(slug)
                db.close_position(pos["id"], pos["entry_price"],
                                  "UNFILLED_CANCELLED", 0.0)
                db.audit("SYNC_UNFILLED_CANCELLED",
                         f"order={order_id[:20]} age={int(opened_secs)}s",
                         pos.get("whale_addr",""), slug, "CANCELLED")

        else:
            # ── Not in positions or orders — resolved/closed externally ───
            pm._open.pop(pos["id"], None)
            pm._mkts.discard(slug)
            db.close_position(pos["id"], pos["entry_price"],
                              "RESOLVED_OR_CLOSED", 0.0)
            print(f"  [SYNC] Removed closed/resolved: {slug[:35]}")
            db.audit("SYNC_REMOVED", "not_on_polymarket",
                     pos.get("whale_addr",""), slug, "REMOVED")

    # Log orphan filled positions
    bot_assets = {p.get("asset_id","") for p in bot_positions}
    for asset, lp in live_assets.items():
        if asset not in bot_assets:
            avg  = float(lp.get("avgPrice", 0) or 0)
            slug = lp.get("slug", "")[:35]
            print(f"  [SYNC] ℹ Orphan position: {slug} avg={avg:.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    banner()

    # ── Init ──────────────────────────────────────────────────────────────────
    db           = StateStore(config.STATE_DB)
    pm           = PositionManager(db)
    protection   = LossProtection(config.BANKROLL)
    differ       = WhaleDiffer()
    withdrawer   = WithdrawalManager(db)

    # ── Start dashboard ───────────────────────────────────────────────────────
    run_dashboard(_dashboard_state)

    # ── STARTUP CLEANUP: cancel unfilled orders + reconcile DB ─────────────────
    print("\n[INIT] Checking for unfilled orders from previous session...")
    live_now   = get_my_positions()
    live_assets_now = set()
    for p in live_now:
        a = p.get("asset", p.get("assetId", p.get("asset_id", "")))
        sz = float(p.get("size", p.get("quantity", 0)) or 0)
        if a and sz > 0:
            live_assets_now.add(a)

    stale = db.get_open_positions()
    cleared = 0
    for pos in stale:
        if pos.get("asset_id","") not in live_assets_now:
            db.close_position(pos["id"], pos["entry_price"],
                              "STARTUP_CLEANUP_UNFILLED", 0.0)
            cleared += 1
    if cleared:
        print(f"  Cleared {cleared} unfilled/stale position(s) from DB")
        # Also cancel any open orders on CLOB
        cancelled = cancel_all_open_orders()
        print(f"  Cancelled open CLOB orders")
    else:
        print("  No stale positions found — DB is clean")

    # ── RESUME: check open positions from last session ─────────────────────────
    stored_open = resume_open_positions(db, http_get)
    if stored_open:
        pm.load_from_db(stored_open)
        # Re-establish snapshots for whales that have open positions
        # (so we don't accidentally re-copy existing positions)
        whale_addrs_with_pos = {p["whale_addr"] for p in stored_open}
        for addr in whale_addrs_with_pos:
            try:
                curr_pos = fetch_whale_positions(addr)
                differ.set_startup_snapshot(addr, curr_pos)
            except Exception:
                differ.set_startup_snapshot(addr, {})

    # ── Initial snapshot ────────────────────────────────────────────────────────
    # Only ignore positions older than 10 minutes.
    # Recent positions (< 10 min old) are still copied — whale may have just opened them.
    print("\n[INIT] Snapshotting whale positions (ignoring positions >10min old)...")
    for addr in wh.get_all():
        if differ.is_snapshot_done(addr):
            continue
        for attempt in range(3):
            try:
                curr = fetch_whale_positions(addr)
                if curr or attempt == 2:
                    # Count old vs recent
                    old_cnt    = sum(1 for p in curr.values() if p.get("_age_mins",9999) > 30)
                    recent_cnt = len(curr) - old_cnt
                    differ.set_startup_snapshot(addr, curr, max_age_mins=30.0)
                    print(f"  @{wh.name(addr)[:20]}: "
                          f"{old_cnt} locked (old>30min), "
                          f"{recent_cnt} eligible (recent <30min)")
                    break
                time.sleep(1.0)
            except Exception:
                if attempt == 2:
                    differ.set_startup_snapshot(addr, {})
                time.sleep(1.0)
        time.sleep(0.3)

    print("\n[INIT] ✓ Ready. Monitoring begins.\n")
    write_memory(db, "Bot started — monitoring active.")

    # ── Counters ──────────────────────────────────────────────────────────────
    poll_count        = 0
    last_sync         = 0.0
    last_memory_wr    = 0.0
    last_snap_refresh = time.time()   # refresh whale snapshots every 2h

    # ── MAIN LOOP ─────────────────────────────────────────────────────────────
    try:
        while True:
            # Global kill check
            if is_killed():
                print("[HALT] Kill file detected. Stopping.")
                db.audit("KILL_SWITCH", "Kill file detected", "", "", "HALT")
                break

            now = time.time()

            # Monitor our open positions (SL, max hold, runner)
            closed = pm.monitor()
            for c in closed:
                if c:
                    protection.update(
                        protection.current + c.get("size_usd", 0) +
                        c.get("pnl_usd", 0))
                    db.set_kv("bankroll_current", protection.current)
                    db.audit("POSITION_CLOSED_MONITOR",
                             f"reason={c['exit_reason']} pnl=${c.get('pnl_usd',0):.4f}",
                             c["whale_addr"], c["market_slug"], "CLOSED")

            # Poll each whale
            for addr in wh.get_all():
                if is_killed():
                    break
                if is_whale_paused(addr):
                    continue

                try:
                    curr_pos = fetch_whale_positions(addr)
                    events   = differ.diff(addr, curr_pos)

                    for event in events:
                        if event["type"] == "CLOSE":
                            # Mirror whale exit — use slug from position or cid
                            slug_key = (event.get("slug") or
                                        event["data"].get("slug") or
                                        event["cid"])
                            closed_pos = pm.mirror_whale_exit(addr, slug_key)
                            if closed_pos:
                                db.audit("WHALE_EXIT_MIRRORED",
                                         f"cid={event['cid'][:20]}",
                                         addr,
                                         closed_pos.get("market_slug", ""),
                                         "CLOSED")

                        elif event["type"] == "BUY":
                            # Fetch market metadata + orderbook
                            market = fetch_market(event["cid"])
                            if not market:
                                db.audit("SKIPPED", "market_not_found",
                                         addr, event["cid"][:20], "SKIP")
                                continue

                            ob = fetch_orderbook(
                                market.get("clobTokenIds", [""])[0])
                            signal = extract_signal(event, market, ob)
                            if not signal:
                                continue

                            # Add execution quality context
                            _, best_ask = fetch_best_bid_ask(signal["asset_id"])
                            signal["market_price_at_detection"] = best_ask

                            process_signal(
                                signal, pm, protection, db)

                except Exception as e:
                    print(f"  [WARN] Poll error for @{wh.name(addr)}: {e}")

                time.sleep(0.2)   # 200ms between whales

            poll_count += 1

            # Update current prices for open positions
            if poll_count % 6 == 0:
                for pos in pm.get_all():
                    try:
                        cur = get_current_price(pos["asset_id"])
                        if cur:
                            pm.update_price(pos["asset_id"], cur)
                    except Exception:
                        pass

            # ── POSITION SYNC every 30s ────────────────────────────────────
            if poll_count % 30 == 0 and config.MY_PROXY_WALLET:
                try:
                    _sync_my_positions(pm, db, protection)
                except Exception as e:
                    pass

            # ── WITHDRAWAL CHECK every 60s ─────────────────────────────────
            if poll_count % 60 == 0:
                try:
                    withdrawer.check_and_withdraw(
                        open_positions    = pm.count(),
                        protection_current = protection.current,
                    )
                except Exception as _we:
                    pass  # never crash main loop on withdrawal check

            # ── SNAPSHOT REFRESH every 2 hours ─────────────────────────────
            # Add positions >10min old to snapshot so they stop triggering.
            # Positions opened in last 10min remain eligible to copy.
            if now - last_snap_refresh >= 7200:
                print("[SNAP] Refreshing snapshots (locking positions >10min old)...")
                for addr in wh.get_all():
                    try:
                        curr = fetch_whale_positions(addr)
                        existing = differ._startup_snapshot.get(addr.lower(), set())
                        added = 0
                        for key, pos in curr.items():
                            if pos.get("_age_mins", 9999) > 30 and key not in existing:
                                existing.add(key)
                                added += 1
                        differ._startup_snapshot[addr.lower()] = existing
                        if added:
                            print(f"  @{wh.name(addr)[:20]}: +{added} positions locked")
                        time.sleep(0.2)
                    except Exception:
                        pass
                last_snap_refresh = now
                print(f"[SNAP] Done.")

            # Update dashboard state
            _update_dashboard(pm, protection, db)

            # Periodic status print
            if poll_count % 20 == 0:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[{ts}] polls={poll_count} open={pm.count()} "
                      f"pnl=${db.get_today_pnl():.2f} "
                      f"{protection.status_str()}")

            # Periodic memory file update (every 5 min)
            if now - last_memory_wr >= 300:
                write_memory(db, f"Poll #{poll_count}")
                last_memory_wr = now

            time.sleep(1.0)   # poll every 1 second

    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.")

    finally:
        write_memory(db, f"Bot stopped at poll #{poll_count}")
        print(f"\n[DONE] Sessions: {poll_count} polls | "
              f"PnL today: ${db.get_today_pnl():.4f} | "
              f"Open positions: {pm.count()}")


if __name__ == "__main__":
    main()
