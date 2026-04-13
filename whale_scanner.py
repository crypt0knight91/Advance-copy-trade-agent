#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║  WHALE SCANNER — Rank all 47 wallets by real performance             ║
║                                                                      ║
║  Run:  python whale_scanner.py                                       ║
║  Or:   python whale_scanner.py --days 30  (wider window)            ║
║  Or:   python whale_scanner.py --out csv  (save to whale_ranks.csv) ║
╚══════════════════════════════════════════════════════════════════════╝

Data sources tried in order for each wallet:
  1. Goldsky PnL subgraph  — real on-chain realized profit/loss
  2. Polymarket Data API   — activity feed, positions, profile
  3. BUY/SELL pair inference — win/loss from price movement
"""
import json, time, sys, os, argparse
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

try:
    import requests
    _sess = requests.Session()
    _sess.headers.update({"User-Agent": "WhaleScanner/1.0"})
    REQ_OK = True
except ImportError:
    print("Install requests first:  pip install requests")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# ALL 47 WHALES
# ─────────────────────────────────────────────────────────────────────────────
WHALES: Dict[str, str] = {
    "0xeee92f1cc6d6e0ad0b4ffda20b01cf3678e27ecb": "comtruise",
    "0x0f37cb80dee49d55b5f6d9e595d52591d6371410": "hans323",
    "0x751a2b86cab503496efd325c8344e10159349ea1": "sharky6999",
    "0xa4b366ad22fc0d06f1e934ff468e8922431a87b8": "holymoses7",
    "0x9d84ce0306f8551e02efef1680475fc0f1dc1344": "imjustken",
    "0x7744bfd749a70020d16a1fcbac1d064761c9999e": "chungguskhan",
    "0xe594336603f4fb5d3ba4125a67021ab3b4347052": "0xe594_anon",
    "0x63ce342161250d705dc0b16df89036c8e5f9ba9a": "0x8dxd",
    "0xd0d6053c3c37e727402d84c14069780d360993aa": "k9q2mx4l8a7zp3r",
    "0x50936370f48b7c7f87016ae8ec1462d0200a272c": "samhain4ik",
    "0x7c3db723f1d4d8cb9c550095203b686cb11e5c6b": "car",
    "0x8278252ebbf354eca8ce316e680a0eaf02859464": "0xf2e346ab",
    "0x118689b24aead1d6e9507b8068d056b2ec4f051b": "russell110320",
    "0xa0bca9bdd8540da95060ed1fafb78aa03835d428": "porx",
    "0xe9c6312464b52aa3eff13d822b003282075995c9": "kingofcoinflips",
    "0xf58b1c1340d6f8c0871e8ea8ee7b80ec6b8a5f34": "muddyrc",
    "0x000d257d2dc7616feaef4ae0f14600fdf50a758e": "scottilicious",
    "0x6a72f61820b26b1fe4d956e17b6dc2a1ea3033ee": "kch123",
    "0x204f72f35326db932158cba6adff0b9a1da95e14": "swisstony",
    "0x05670a9813243e7a5af6ffa2aa013b4960fd2c55": "ua2",
    "0x16b29c50f2439faf627209b2ac0c7bbddaa8a881": "seriouslysirius",
    "0x0afc7ce56285bde1fbe3a75efaffdfc86d6530b2": "rundeep",
    "0x78fc863612706e4ce215cd7bc88216f35e2528a6": "memeretirement",
    "0xcf6a714618a328c608a1c70cb62a31a6bef3f9d0": "knureknume",
    "0x2652dd1140c3b9e69c8852264cf8dbe727672a1d": "punisher2022",
    "0xc2e7800b5af46e6093872b177b7a5e7f0563be51": "beachboy4",
    "0x7f69983eb28245bba0d5083502a78744a8f66162": "account88888",
    "0xa49becb692927d455924583b5e3e5788246f4c40": "sleepy-panda",
    "0x8ad71d502126d9531bf0a8ba4c7ae24281e0c55b": "imgone",
    "0xb3f15cc1478d529862282c40c1e91399724dbdc6": "tomdnc",
    "0x2005d16a84ceefa912d4e380cd32e7ff827875ea": "rn1",
    "0x75e765216a57942d738d880ffcda854d9f869080": "25usdc",
    "0x6c16abad96d6989efe1b0333cb9af9158f548bfa": "xpredicter1",
    "0xd8f8c13644ea84d62e1ec88c5d1215e436eb0f11": "automatedaitradingbot",
    "0xe598435df0cdf5d22bdd5082d557f75f9180a0a8": "bamesjond",
    "0x5350afcd8bd8ceffdf4da32420d6d31be0822fda": "simonbanza",
    "0x8c80d213c0cbad777d06ee3f58f6ca4bc03102c3": "secondwindcapital",
    "0x7523cafcee7bcf2db9a79d80e0d79b88a9a54c4c": "donaldinhotrumpito",
    "0x589222a5124a96765443b97a3498d89ffd824ad2": "purplethunderbicyclemountain",
    "0xe00740bce98a594e26861838885ab310ec3b548c": "distinct-baguette",
    "0x6ffb4354cbe6e0f9989e3b55564ec5fb8646a834": "agriculturesecretary",
    "0x689ae12e11aa489adb3605afd8f39040ff52779e": "annica",
    "0x7e507842c280238a62301146a592a27486d82a28": "economancy",
    "0x07e78f5f58f8fa839f298cfe3fefd258883aa343": "spon",
    "0x71ca04d689bc38c5e4dcda8a4d743f279c5a3501": "ronaldo2100",
    "0x033f0346c007323030eb420305ffede19a95618e": "theverygoodcow",
    "0x507e52ef684ca2dd91f90a9d26d149dd3288beae": "gamblingisallyouneed",
}

DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
GS_PNL    = ("https://api.goldsky.com/api/public/"
             "project_cl6mb8i9h0003e201j6li0diw/"
             "subgraphs/pnl-subgraph/0.0.14/gn")

# ─────────────────────────────────────────────────────────────────────────────
# HTTP
# ─────────────────────────────────────────────────────────────────────────────
def _get(url, params=None, timeout=12):
    for i in range(3):
        try:
            r = _sess.get(url, params=params, timeout=timeout)
            if r.status_code == 404: return None
            r.raise_for_status()
            return r.json()
        except Exception:
            time.sleep(1.5 ** i)
    return None

def _gql(query, variables):
    for i in range(3):
        try:
            r = _sess.post(GS_PNL, json={"query": query, "variables": variables}, timeout=12)
            r.raise_for_status()
            return r.json().get("data", {})
        except Exception:
            time.sleep(1.5 ** i)
    return {}

def sf(v, d=0.0):
    try: return float(v) if v is not None else d
    except: return d

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHERS
# ─────────────────────────────────────────────────────────────────────────────
GS_QUERY = """
query Wallet($w: String!) {
  account(id: $w) {
    scaledRealizedPNL
    scaledCollateralVolume
    numPositions
    lastSeenTime
  }
  userPositions(first:100 where:{user:$w} orderBy:scaledRealizedPNL orderDirection:desc) {
    scaledRealizedPNL
    scaledCollateralVolume
    quantityBought
    quantitySold
  }
}
"""

def fetch_goldsky(addr: str) -> dict:
    data = _gql(GS_QUERY, {"w": addr.lower()})
    acc  = data.get("account") or {}
    positions = data.get("userPositions", [])

    total_pnl = sf(acc.get("scaledRealizedPNL", 0)) / 1e6
    volume    = sf(acc.get("scaledCollateralVolume", 0)) / 1e6
    last_seen = int(acc.get("lastSeenTime", 0) or 0)

    wins = losses = 0
    pos_pnls = []
    for p in positions:
        if sf(p.get("quantitySold", 0)) == 0:
            continue   # not closed
        pnl = sf(p.get("scaledRealizedPNL", 0)) / 1e6
        pos_pnls.append(pnl)
        if pnl > 0: wins += 1
        else: losses += 1

    return {
        "total_pnl":  total_pnl,
        "volume":     volume,
        "last_seen":  last_seen,
        "wins":       wins,
        "losses":     losses,
        "pos_pnls":   pos_pnls,
        "source":     "goldsky",
    }

def fetch_activity(addr: str, days: int = 30) -> List[dict]:
    cutoff = int(time.time() - days * 86400)
    data = _get(f"{DATA_API}/activity", {
        "user": addr.lower(), "start": cutoff,
        "limit": 500, "type": "TRADE",
    })
    if not data: return []
    return data if isinstance(data, list) else data.get("data", [])

def fetch_profile(addr: str) -> dict:
    data = _get(f"{DATA_API}/profiles", {"user": addr.lower()})
    if not data: return {}
    obj = data[0] if isinstance(data, list) and data else data
    return obj if isinstance(obj, dict) else {}

def infer_wins_losses(activity: List[dict], days: int) -> Tuple[int, int, float, List[float]]:
    """Infer wins/losses from BUY+SELL pairs on same conditionId."""
    cutoff = time.time() - days * 86400
    by_mkt = defaultdict(list)
    for t in activity:
        cid = t.get("conditionId", t.get("conditionID", ""))
        if cid: by_mkt[cid].append(t)

    wins = losses = 0
    pnls = []
    for cid, trades in by_mkt.items():
        trades_s = sorted(trades, key=lambda x: sf(x.get("timestamp",0)))
        buys  = [t for t in trades_s if t.get("side","").upper()=="BUY"]
        sells = [t for t in trades_s if t.get("side","").upper()=="SELL"]
        if not buys or not sells: continue
        bp = sf(buys[0].get("price", 0))
        sp = sf(sells[-1].get("price", 0))
        ts = sf(sells[-1].get("timestamp", 0))
        if ts < cutoff or bp <= 0 or sp <= 0: continue
        size = sf(buys[0].get("usdcSize", 10), 10)
        pnl  = (sp - bp) * (size / bp)
        pnls.append(pnl)
        if sp > bp * 1.02: wins += 1
        else: losses += 1

    total_pnl = sum(pnls)
    return wins, losses, total_pnl, pnls

# ─────────────────────────────────────────────────────────────────────────────
# SCORE ONE WHALE
# ─────────────────────────────────────────────────────────────────────────────
def score_whale(addr: str, name: str, days: int) -> dict:
    result = {
        "addr": addr, "name": name,
        "total_pnl": 0.0, "volume": 0.0,
        "wins": 0, "losses": 0, "wr": 0.0,
        "trades": 0, "last_seen": 0,
        "avg_pnl": 0.0, "consistency": 0.0,
        "trust": 0.0, "source": "none",
        "active_days": days,
    }

    # ── Source 1: Goldsky subgraph ─────────────────────────────────────────
    gs = fetch_goldsky(addr)
    if gs["wins"] + gs["losses"] >= 3:
        result.update({
            "total_pnl": gs["total_pnl"],
            "volume":    gs["volume"],
            "wins":      gs["wins"],
            "losses":    gs["losses"],
            "last_seen": gs["last_seen"],
            "source":    "goldsky",
        })
        pnls = gs["pos_pnls"]
    else:
        pnls = []

    # ── Source 2: Activity feed ────────────────────────────────────────────
    activity = fetch_activity(addr, days)
    n_activity = len(activity)

    # Try explicit PnL from activity
    act_pnls = []
    for t in activity:
        for key in ("cashPnl","realizedPnl","pnl"):
            v = t.get(key)
            if v is not None:
                act_pnls.append(sf(v))
                break

    # ── Source 3: Infer from pairs ─────────────────────────────────────────
    inf_wins, inf_losses, inf_pnl, inf_pnls = infer_wins_losses(activity, days)

    # Merge: prefer goldsky if good, else use inference
    if result["source"] == "goldsky":
        # Add volume from activity if goldsky volume is 0
        if result["volume"] == 0 and activity:
            result["volume"] = sum(sf(t.get("usdcSize",0)) for t in activity)
    else:
        # Use inferred data
        if inf_wins + inf_losses >= 3:
            result.update({
                "wins":   inf_wins,
                "losses": inf_losses,
                "total_pnl": inf_pnl,
                "source": "inferred",
            })
            pnls = inf_pnls
        else:
            result["source"] = "activity_only"

        # Volume from activity
        result["volume"] = sum(sf(t.get("usdcSize",0)) for t in activity)

        # Last seen from activity
        ts_list = [int(sf(t.get("timestamp",0))) for t in activity if t.get("timestamp")]
        result["last_seen"] = max(ts_list) if ts_list else 0

    # ── Compute derived metrics ────────────────────────────────────────────
    total = result["wins"] + result["losses"]
    result["trades"] = max(total, n_activity)
    result["wr"]     = round(result["wins"] / total, 4) if total > 0 else 0.0

    if pnls:
        result["avg_pnl"] = round(sum(pnls) / len(pnls), 4)
        if len(pnls) >= 3:
            mean = sum(pnls) / len(pnls)
            std  = (sum((p-mean)**2 for p in pnls) / len(pnls)) ** 0.5
            cv   = std / mean if mean > 0 else 3.0
            result["consistency"] = round(max(0.0, 1.0 - cv/3.0), 4)
        else:
            result["consistency"] = 0.5
    else:
        result["avg_pnl"]     = 0.0
        result["consistency"] = 0.5

    # ── Composite trust score ──────────────────────────────────────────────
    wr_norm    = result["wr"]
    pnl_norm   = min(max(result["avg_pnl"] / 200.0, 0.0), 1.0)
    vol_norm   = min(result["volume"] / 10000.0, 1.0)   # $10k vol = 1.0
    act_norm   = min(n_activity / 50.0, 1.0)             # 50 trades = 1.0

    if total >= 5:
        result["trust"] = round(
            0.40 * wr_norm  +
            0.25 * pnl_norm +
            0.20 * result["consistency"] +
            0.15 * vol_norm, 4
        )
    else:
        # Not enough resolved trades — partial score from activity
        result["trust"] = round(
            0.30 * act_norm +
            0.20 * vol_norm +
            0.50 * 0.5,  # neutral consistency
        4)

    return result

# ─────────────────────────────────────────────────────────────────────────────
# RANK
# ─────────────────────────────────────────────────────────────────────────────
def ranking_score(w: dict) -> float:
    """Composite score for ranking. PnL + WR + Volume + Consistency."""
    pnl_score  = min(max((w["total_pnl"] + 5000) / 10000, 0.0), 1.0)  # -5k..+5k
    wr_score   = w["wr"]
    vol_score  = min(w["volume"] / 50000.0, 1.0)
    cons_score = w["consistency"]
    trades_ok  = min(w["trades"] / 20.0, 1.0)

    return (
        0.35 * pnl_score  +
        0.30 * wr_score   +
        0.15 * vol_score  +
        0.10 * cons_score +
        0.10 * trades_ok
    )

def tier(score: float) -> str:
    if score >= 0.70: return "S"
    if score >= 0.55: return "A"
    if score >= 0.40: return "B"
    if score >= 0.25: return "C"
    return "D"

def format_pnl(v: float) -> str:
    if v >= 0:  return f"+${v:>10,.2f}"
    return f"-${abs(v):>10,.2f}"

def last_seen_str(ts: int) -> str:
    if ts <= 0: return "unknown"
    d = (time.time() - ts) / 86400
    if d < 1:   return f"{int(d*24)}h ago"
    if d < 30:  return f"{int(d)}d ago"
    return f"{int(d/30)}mo ago"

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rank all 47 Polymarket whale wallets")
    parser.add_argument("--days",  type=int,   default=30,   help="Lookback window in days (default 30)")
    parser.add_argument("--out",   type=str,   default="",   help="Output format: csv or table (default table)")
    parser.add_argument("--limit", type=int,   default=47,   help="Show top N whales (default all 47)")
    args = parser.parse_args()

    days  = args.days
    total = len(WHALES)

    print()
    print("╔══════════════════════════════════════════════════════╗")
    print(f"║  WHALE SCANNER — Ranking {total} wallets                 ║")
    print(f"║  Lookback: {days} days  |  Sources: Goldsky + Data API  ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print(f"Scanning {total} whales (this takes ~2 min for all 47)...\n")

    results = []
    for i, (addr, name) in enumerate(WHALES.items(), 1):
        print(f"  [{i:>2}/{total}] @{name:<28} ", end="", flush=True)
        try:
            w = score_whale(addr, name, days)
            rs = ranking_score(w)
            w["rank_score"] = rs
            w["tier"]       = tier(rs)
            results.append(w)
            wr_str = f"{w['wr']*100:.0f}%WR" if w["wins"]+w["losses"]>0 else "no data"
            src_str = w["source"][:8]
            print(f"✓  pnl={format_pnl(w['total_pnl']).strip():>13}  {wr_str:<8}  [{src_str}]")
        except Exception as e:
            print(f"✗  error: {e}")
        time.sleep(0.5)   # gentle rate limiting

    # Sort by rank score
    results.sort(key=lambda x: x["rank_score"], reverse=True)

    # ── PRINT TABLE ────────────────────────────────────────────────────────
    print()
    print("═" * 110)
    print(f"  FINAL RANKINGS — Last {days} days")
    print("═" * 110)
    hdr = (f"{'Rank':>4}  {'Tier':^4}  {'Name':<28}  {'Total PnL':>13}  "
           f"{'W':>5} {'L':>5}  {'WR':>6}  {'Trades':>7}  "
           f"{'Avg PnL':>9}  {'Volume':>11}  {'Last Active':<12}  Source")
    print(hdr)
    print("─" * 110)

    tier_colors = {"S": "🥇", "A": "🥈", "B": "🥉", "C": "⚪", "D": "⚫"}

    for i, w in enumerate(results[:args.limit], 1):
        icon  = tier_colors.get(w["tier"], "  ")
        pnl_s = format_pnl(w["total_pnl"])
        vol_s = f"${w['volume']:>10,.0f}"
        avg_s = f"${w['avg_pnl']:>+8.2f}"
        wr_s  = f"{w['wr']*100:5.1f}%" if w["wins"]+w["losses"]>0 else "  n/a"
        ls    = last_seen_str(w["last_seen"])
        src   = w["source"][:10]
        print(
            f"  {i:>3}.  {icon}{w['tier']:^3}  {('@'+w['name']):<28}  "
            f"{pnl_s}  {w['wins']:>5} {w['losses']:>5}  {wr_s}  "
            f"{w['trades']:>7}  {avg_s}  {vol_s}  {ls:<12}  {src}"
        )

    # ── TIER SUMMARY ───────────────────────────────────────────────────────
    print()
    print("═" * 110)
    print("  TIER SUMMARY")
    print("─" * 110)
    for t_name, t_label in [("S","🥇 S-Tier (Best — copy these first)"),
                              ("A","🥈 A-Tier (Strong — reliable)"),
                              ("B","🥉 B-Tier (Decent — copy with caution)"),
                              ("C","⚪ C-Tier (Weak — avoid or skip)"),
                              ("D","⚫ D-Tier (Poor — remove from list)")]:
        group = [w for w in results if w["tier"]==t_name]
        if group:
            names = ", ".join(f"@{w['name']}" for w in group)
            print(f"  {t_label}: {names}")
    print()

    # ── KEY INSIGHT ────────────────────────────────────────────────────────
    top5 = results[:5]
    bottom5 = results[-5:]
    print("  TOP 5 WHALES TO PRIORITIZE:")
    for i, w in enumerate(top5, 1):
        print(f"    {i}. @{w['name']:<25}  "
              f"pnl={format_pnl(w['total_pnl']).strip():>13}  "
              f"wr={w['wr']*100:.0f}%  trust={w['trust']:.3f}")
    print()
    print("  BOTTOM 5 — CONSIDER REMOVING:")
    for i, w in enumerate(bottom5, 1):
        print(f"    {i}. @{w['name']:<25}  "
              f"pnl={format_pnl(w['total_pnl']).strip():>13}  "
              f"wr={w['wr']*100:.0f}%  trust={w['trust']:.3f}")
    print()

    # ── CSV OUTPUT ─────────────────────────────────────────────────────────
    if args.out == "csv" or args.out:
        fname = "whale_ranks.csv"
        with open(fname, "w") as f:
            f.write("rank,name,address,tier,total_pnl,wins,losses,win_rate,"
                    "trades,avg_pnl,volume,consistency,trust_score,last_seen,source\n")
            for i, w in enumerate(results, 1):
                f.write(
                    f"{i},{w['name']},{w['addr']},{w['tier']},"
                    f"{w['total_pnl']:.2f},{w['wins']},{w['losses']},"
                    f"{w['wr']:.4f},{w['trades']},{w['avg_pnl']:.4f},"
                    f"{w['volume']:.2f},{w['consistency']:.4f},"
                    f"{w['trust']:.4f},{last_seen_str(w['last_seen'])},{w['source']}\n"
                )
        print(f"  📄 Saved to {fname}")

    # ── PASTE INTO .env ────────────────────────────────────────────────────
    print()
    print("  SUGGESTED SEED_ADDRESSES (top 10 by score):")
    top10_addrs = ",".join(w["addr"] for w in results[:10])
    print(f"  SEED_ADDRESSES={top10_addrs}")
    print()

if __name__ == "__main__":
    main()
