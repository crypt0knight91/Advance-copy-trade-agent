"""config.py — All settings. Override via .env"""
import os
from dotenv import load_dotenv
load_dotenv()

# ── API ─────────────────────────────────────────────────────────
DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# ── CREDENTIALS (Gmail/Magic wallet) ───────────────────────────
# Get private key from: reveal.magic.link/polymarket
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_API_KEY     = os.getenv("POLY_API_KEY", "")
POLY_SECRET      = os.getenv("POLY_SECRET", "")
POLY_PASSPHRASE  = os.getenv("POLY_PASSPHRASE", "")
MY_PROXY_WALLET  = os.getenv("MY_PROXY_WALLET", "")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ── MODE ────────────────────────────────────────────────────────
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8080"))

# ── TRADING ─────────────────────────────────────────────────────
TRADE_SIZE_USD     = float(os.getenv("TRADE_SIZE_USD",    "4.0"))
CLUSTER_BOOST_USD  = float(os.getenv("CLUSTER_BOOST_USD", "6.0"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS",  "5"))
SL_PCT             = float(os.getenv("SL_PCT",            "0.27"))
MAX_HOLD_HOURS     = float(os.getenv("MAX_HOLD_HOURS",    "72.0"))
TP_RUNNER_PCT      = float(os.getenv("TP_RUNNER_PCT",     "9.0"))   # 900% — alert only

# ── TRUST SCORING ───────────────────────────────────────────────
MIN_TRUST_SCORE    = float(os.getenv("MIN_TRUST_SCORE",   "0.55"))
MIN_WR_15D         = float(os.getenv("MIN_WR_15D",        "0.65"))
LOOKBACK_DAYS      = int(os.getenv("LOOKBACK_DAYS",       "15"))
MIN_TRADES_FOR_WR  = int(os.getenv("MIN_TRADES_FOR_WR",   "5"))
TRUST_RECHECK_MINS = int(os.getenv("TRUST_RECHECK_MINS",  "30"))
MIN_WHALE_USD      = float(os.getenv("MIN_WHALE_USD",     "10.0"))

DECAY = {"0_3d": 1.0, "3_7d": 0.8, "7_15d": 0.5}
TRUST_W = {"wr_last15d": 0.40, "avg_profit": 0.25, "consistency": 0.20, "exec_quality": 0.15}

# ── MARKET FILTERS ──────────────────────────────────────────────
MIN_VOLUME      = float(os.getenv("MIN_VOLUME",      "0.0"))
MAX_SPREAD_PCT  = float(os.getenv("MAX_SPREAD_PCT",  "0.08"))
MAX_SLIP_CENTS  = float(os.getenv("MAX_SLIP_CENTS",  "0.02"))
MIN_DEPTH_USD   = float(os.getenv("MIN_DEPTH_USD",   "200.0"))
MIN_HOURS_LEFT  = float(os.getenv("MIN_HOURS_LEFT",  "6.0"))
MAX_YES_PRICE   = float(os.getenv("MAX_YES_PRICE",   "0.97"))
PUMP_RATIO      = float(os.getenv("PUMP_RATIO",      "10.0"))

# ── CLUSTER DETECTION ───────────────────────────────────────────
CLUSTER_SECS  = int(os.getenv("CLUSTER_SECS",   "300"))
CLUSTER_MIN   = int(os.getenv("CLUSTER_MIN",    "2"))

# ── LOSS PROTECTION ─────────────────────────────────────────────
DAILY_SOFT_PCT  = float(os.getenv("DAILY_SOFT_PCT", "0.10"))
DAILY_HARD_PCT  = float(os.getenv("DAILY_HARD_PCT", "0.20"))
TOTAL_HALT_PCT  = float(os.getenv("TOTAL_HALT_PCT", "0.40"))
BANKROLL        = float(os.getenv("BANKROLL",       "100.0"))

# ── POLLING ─────────────────────────────────────────────────────
POLL_SECS  = float(os.getenv("POLL_SECS",  "1.0"))
SYNC_SECS  = float(os.getenv("SYNC_SECS", "30.0"))

# ── FILES ───────────────────────────────────────────────────────
STATE_DB    = "state/bot_state.db"
MEMORY_MD   = "state/BOT_MEMORY.md"
AUDIT_LOG   = "state/audit.log"
KILL_FILE   = "KILL"
PAUSE_PFX   = "PAUSE_"

# ── WITHDRAWAL ───────────────────────────────────────────────────
# Automated USDC withdrawal: Polymarket (Polygon) → Base chain
# Set WITHDRAW_ENABLED=true in .env to activate
WITHDRAW_ENABLED       = os.getenv("WITHDRAW_ENABLED", "false").lower() == "true"
WITHDRAW_THRESHOLD     = float(os.getenv("WITHDRAW_THRESHOLD",     "100.0"))  # trigger at $100
WITHDRAW_AMOUNT        = float(os.getenv("WITHDRAW_AMOUNT",         "50.0"))  # withdraw $50
WITHDRAW_COOLDOWN_SECS = int(os.getenv("WITHDRAW_COOLDOWN_SECS",   "3600"))  # 1h cooldown
WITHDRAW_MIN_BUFFER    = float(os.getenv("WITHDRAW_MIN_BUFFER",     "20.0"))  # keep $20 min
WITHDRAW_DESTINATION   = os.getenv("WITHDRAW_DESTINATION",
                         "0x1c6A81A22b97441E58c976819E9e413f28e35F18")

SPREAD_VWAP_MINS = int(os.getenv('SPREAD_VWAP_MINS', '5'))
