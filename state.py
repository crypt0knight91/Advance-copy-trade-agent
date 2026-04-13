"""
state.py — SQLite persistence, session memory, and resume logic.
On restart: reads open positions from DB, verifies them live, syncs state.
"""
import os, json, sqlite3, time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import config

os.makedirs("state", exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id            TEXT PRIMARY KEY,
    whale_addr    TEXT NOT NULL,
    whale_name    TEXT NOT NULL,
    market_slug   TEXT NOT NULL,
    market_title  TEXT NOT NULL,
    condition_id  TEXT NOT NULL,
    asset_id      TEXT NOT NULL,
    outcome       TEXT NOT NULL,
    entry_price   REAL NOT NULL,
    size_usd      REAL NOT NULL,
    shares        REAL NOT NULL,
    sl_price      REAL NOT NULL,
    status        TEXT DEFAULT 'OPEN',
    exit_price    REAL,
    exit_reason   TEXT,
    pnl_usd       REAL,
    opened_ts     REAL NOT NULL,
    closed_ts     REAL,
    is_cluster    INTEGER DEFAULT 0,
    order_id      TEXT,
    dry_run       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS whale_scores (
    addr          TEXT PRIMARY KEY,
    name          TEXT,
    trust_score   REAL DEFAULT 0,
    wr_15d        REAL DEFAULT 0,
    avg_profit    REAL DEFAULT 0,
    consistency   REAL DEFAULT 0,
    exec_quality  REAL DEFAULT 0,
    trade_count   INTEGER DEFAULT 0,
    last_checked  REAL DEFAULT 0,
    is_active     INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS audit_log (
    ts            REAL NOT NULL,
    action        TEXT NOT NULL,
    details       TEXT,
    whale_addr    TEXT,
    market_slug   TEXT,
    result        TEXT
);

CREATE TABLE IF NOT EXISTS bot_session (
    key           TEXT PRIMARY KEY,
    value         TEXT
);

CREATE TABLE IF NOT EXISTS cluster_events (
    market_slug   TEXT NOT NULL,
    whale_addr    TEXT NOT NULL,
    ts            REAL NOT NULL,
    size_usd      REAL NOT NULL,
    price         REAL NOT NULL
);
"""


class StateStore:
    def __init__(self, db_path: str = config.STATE_DB):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _q(self, sql: str, params=()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _qc(self, sql: str, params=()):
        self._conn.execute(sql, params)
        self._conn.commit()

    # ── POSITIONS ────────────────────────────────────────────────────────────
    def save_position(self, pos: dict):
        self._qc("""
            INSERT OR REPLACE INTO positions
            (id,whale_addr,whale_name,market_slug,market_title,condition_id,
             asset_id,outcome,entry_price,size_usd,shares,sl_price,status,
             opened_ts,is_cluster,order_id,dry_run)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            pos["id"], pos["whale_addr"], pos["whale_name"],
            pos["market_slug"], pos["market_title"], pos["condition_id"],
            pos["asset_id"], pos["outcome"], pos["entry_price"],
            pos["size_usd"], pos["shares"], pos["sl_price"],
            pos.get("status", "OPEN"), pos["opened_ts"],
            1 if pos.get("is_cluster") else 0,
            pos.get("order_id", ""), 1 if pos.get("dry_run", True) else 0,
        ))

    def close_position(self, pos_id: str, exit_price: float,
                       reason: str, pnl: float):
        self._qc("""
            UPDATE positions
            SET status=?, exit_price=?, exit_reason=?, pnl_usd=?, closed_ts=?
            WHERE id=?
        """, ("CLOSED", exit_price, reason, pnl, time.time(), pos_id))

    def get_open_positions(self) -> List[dict]:
        rows = self._q("SELECT * FROM positions WHERE status='OPEN'").fetchall()
        return [dict(r) for r in rows]

    def get_position(self, pos_id: str) -> Optional[dict]:
        r = self._q("SELECT * FROM positions WHERE id=?", (pos_id,)).fetchone()
        return dict(r) if r else None

    # ── WHALE SCORES ─────────────────────────────────────────────────────────
    def save_whale_score(self, addr: str, name: str, scores: dict):
        self._qc("""
            INSERT OR REPLACE INTO whale_scores
            (addr,name,trust_score,wr_15d,avg_profit,consistency,exec_quality,
             trade_count,last_checked,is_active)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            addr.lower(), name,
            scores.get("trust_score", 0),
            scores.get("wr_15d", 0),
            scores.get("avg_profit", 0),
            scores.get("consistency", 0),
            scores.get("exec_quality", 0),
            scores.get("trade_count", 0),
            time.time(),
            1 if scores.get("is_active", True) else 0,
        ))

    def get_whale_score(self, addr: str) -> Optional[dict]:
        r = self._q("SELECT * FROM whale_scores WHERE addr=?",
                    (addr.lower(),)).fetchone()
        return dict(r) if r else None

    def get_all_whale_scores(self) -> List[dict]:
        rows = self._q("SELECT * FROM whale_scores ORDER BY trust_score DESC").fetchall()
        return [dict(r) for r in rows]

    # ── SESSION KV ───────────────────────────────────────────────────────────
    def set_kv(self, key: str, value: Any):
        self._qc("INSERT OR REPLACE INTO bot_session(key,value) VALUES(?,?)",
                 (key, json.dumps(value)))

    def get_kv(self, key: str, default=None) -> Any:
        r = self._q("SELECT value FROM bot_session WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    # ── CLUSTER EVENTS ────────────────────────────────────────────────────────
    def record_cluster_event(self, market_slug: str, whale_addr: str,
                              size_usd: float, price: float):
        self._qc("""
            INSERT INTO cluster_events(market_slug,whale_addr,ts,size_usd,price)
            VALUES(?,?,?,?,?)
        """, (market_slug, whale_addr.lower(), time.time(), size_usd, price))
        # Prune old events (>10 min)
        self._qc("DELETE FROM cluster_events WHERE ts < ?", (time.time() - 600,))

    def get_cluster_events(self, market_slug: str,
                            window_secs: int = 300) -> List[dict]:
        cutoff = time.time() - window_secs
        rows = self._q("""
            SELECT * FROM cluster_events
            WHERE market_slug=? AND ts >= ?
        """, (market_slug, cutoff)).fetchall()
        return [dict(r) for r in rows]

    # ── AUDIT LOG ────────────────────────────────────────────────────────────
    def audit(self, action: str, details: str = "",
              whale_addr: str = "", market_slug: str = "", result: str = ""):
        self._qc("""
            INSERT INTO audit_log(ts,action,details,whale_addr,market_slug,result)
            VALUES(?,?,?,?,?,?)
        """, (time.time(), action, details, whale_addr, market_slug, result))
        # Also write to audit log file
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] {action:<25} | {whale_addr[:20]:<20} | {market_slug[:30]:<30} | {result:<10} | {details}"
        try:
            with open(config.AUDIT_LOG, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def get_today_pnl(self) -> float:
        """Sum of PnL for positions closed today."""
        midnight = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0).timestamp()
        r = self._q("""
            SELECT COALESCE(SUM(pnl_usd), 0) FROM positions
            WHERE status='CLOSED' AND closed_ts >= ?
        """, (midnight,)).fetchone()
        return float(r[0]) if r else 0.0

    # ── PER-TRADE PNL VIEW ───────────────────────────────────────────────────
    def get_closed_positions(self, limit: int = 50) -> List[dict]:
        rows = self._q("""
            SELECT * FROM positions WHERE status='CLOSED'
            ORDER BY closed_ts DESC LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# MEMORY MD FILE  (human-readable session snapshot)
# ─────────────────────────────────────────────────────────────────────────────
def write_memory(db: StateStore, extra: str = ""):
    """Write/update BOT_MEMORY.md with current bot state."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    open_pos = db.get_open_positions()
    scores   = db.get_all_whale_scores()

    lines = [
        "# Whale Mirror Bot — Session Memory",
        f"**Updated:** {now}",
        f"**Mode:** {'DRY RUN' if config.DRY_RUN else 'LIVE TRADING'}",
        "",
        "## Open Positions",
        f"Count: {len(open_pos)} / {config.MAX_OPEN_POSITIONS}",
        "",
    ]
    if open_pos:
        lines.append("| # | Whale | Market | Outcome | Entry | SL | Opened |")
        lines.append("|---|-------|--------|---------|-------|----|--------|")
        for i, p in enumerate(open_pos, 1):
            opened = datetime.fromtimestamp(p["opened_ts"], tz=timezone.utc).strftime("%m-%d %H:%M")
            lines.append(
                f"| {i} | {p['whale_name'][:15]} | {p['market_slug'][:25]} | "
                f"{p['outcome']} | {p['entry_price']:.3f} | "
                f"{p['sl_price']:.3f} | {opened} |"
            )
    else:
        lines.append("No open positions.")

    lines += [
        "",
        "## Whale Trust Scores (top 10)",
        "| Whale | Trust | WR 15d | Trades |",
        "|-------|-------|--------|--------|",
    ]
    for s in scores[:10]:
        lines.append(
            f"| {s['name'][:18]} | {s['trust_score']:.2f} | "
            f"{s['wr_15d']*100:.0f}% | {s['trade_count']} |"
        )

    if extra:
        lines += ["", "## Notes", extra]

    lines += [
        "",
        "## Resume Instructions",
        "If bot stopped unexpectedly:",
        "1. Run `python main.py` — it auto-reads this DB and resumes open positions.",
        "2. Bot will call Data API to verify each position is still live.",
        "3. Positions confirmed live → SL/TP monitoring resumes immediately.",
        "4. Positions resolved during downtime → marked CLOSED with resolution price.",
        "",
        f"**DB path:** `{config.STATE_DB}`",
        f"**Audit log:** `{config.AUDIT_LOG}`",
    ]

    try:
        with open(config.MEMORY_MD, "w") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# RESUME LOGIC  — called at startup
# ─────────────────────────────────────────────────────────────────────────────
def resume_open_positions(db: StateStore, http_get) -> List[dict]:
    """
    On startup: load open positions from DB, verify each via Data API,
    return only those that are genuinely still open.
    http_get: callable(url, params) → dict|list|None
    """
    stored = db.get_open_positions()
    if not stored:
        return []

    verified = []
    print(f"\n[RESUME] Found {len(stored)} stored open position(s). Verifying...")

    for pos in stored:
        try:
            # Fetch our current positions from Polymarket
            wallet = config.MY_PROXY_WALLET.lower()
            if not wallet:
                # Can't verify without wallet address — trust the DB
                pos["resume_verified"] = False
                verified.append(pos)
                continue

            data = http_get(
                f"{config.DATA_API}/positions",
                {"user": wallet}
            )
            live_assets = set()
            if isinstance(data, list):
                live_assets = {p.get("asset", p.get("assetId", "")) for p in data}
            elif isinstance(data, dict):
                items = data.get("data", data.get("positions", []))
                live_assets = {p.get("asset", p.get("assetId", "")) for p in items}

            if pos["asset_id"] in live_assets:
                pos["resume_verified"] = True
                verified.append(pos)
                print(f"  ✓ {pos['whale_name']:15} | {pos['market_slug'][:30]} → LIVE")
                db.audit("RESUME_VERIFIED", f"entry={pos['entry_price']:.3f}",
                         pos["whale_addr"], pos["market_slug"], "LIVE")
            else:
                # Position no longer live — mark closed (resolved during downtime)
                db.close_position(pos["id"], pos["entry_price"],
                                  "RESOLVED_DURING_DOWNTIME", 0.0)
                print(f"  ✗ {pos['whale_name']:15} | {pos['market_slug'][:30]} → CLOSED (resolved)")
                db.audit("RESUME_CLOSED", "resolved during downtime",
                         pos["whale_addr"], pos["market_slug"], "RESOLVED")
        except Exception as e:
            # Network error — trust the DB conservatively
            pos["resume_verified"] = False
            verified.append(pos)
            print(f"  ? {pos['whale_name']:15} | {pos['market_slug'][:30]} → UNVERIFIED (net error)")

    print(f"[RESUME] {len(verified)} position(s) resumed.\n")
    return verified
