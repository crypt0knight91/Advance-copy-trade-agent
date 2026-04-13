# 🐋 Whale Mirror Bot v2

Automated Polymarket copy-trading bot for Android (Termux). Monitors whale wallets you choose and mirrors their trades on your account in real time.

---

## Features

- **Real-time monitoring** — polls whale wallets every 1 second
- **Smart copy logic** — copies BUY signals, mirrors exits when whale closes
- **EV gate** — Expected Value + Kelly Criterion + Bayesian updating (skip negative EV trades)
- **Market order support** — copies market orders if spread < 3¢ and price is stable
- **NO outcome support** — correctly copies YES and NO trades
- **Duplicate guard** — one position per market, no double exposure
- **Cluster detection** — 2+ whales same market = boosted size ($6 instead of $4)
- **Sybil detection** — ignores coordinated fake volume
- **Stop loss** — auto-closes at -27% from entry
- **Max hold** — force-closes positions after 72 hours
- **Loss protection** — daily soft pause (-10%), hard halt (-20%), total halt (-40%)
- **Auto-resume** — restarts cleanly after crash, reconciles with Polymarket
- **Unfilled order cleanup** — cancels stuck limit orders automatically
- **Dark mobile dashboard** — live positions, PnL, whale scores at `localhost:8080`
- **Kill switch** — `touch KILL` stops all trading instantly

---

## Requirements

- Android phone with [Termux](https://f-droid.org/packages/com.termux/) (F-Droid version)
- Polymarket account (Gmail/Magic wallet)
- Python 3.10+

---

## Installation

```bash
# Install dependencies
pkg update -y && pkg install python git -y
pip install requests python-dotenv flask py-clob-client

# Clone repo
git clone https://github.com/YOURUSERNAME/whale-mirror-bot.git
cd whale-mirror-bot

# Configure
cp env.example .env
nano .env
```

---

## Configuration

Edit `.env` with your credentials:

```env
# Get from: reveal.magic.link/polymarket
POLY_PRIVATE_KEY=0xYourPrivateKeyHere

# Your Polymarket proxy wallet address
MY_PROXY_WALLET=0xYourProxyWalletHere

# Start with true, flip to false after testing
DRY_RUN=true

BANKROLL=100.0
TRADE_SIZE_USD=4.0
```

> ⚠️ **Never share your `.env` file or commit it to Git. Your private key is in there.**

---

## Add Your Whales

Edit `whales.txt` — one whale per line:

```
0xc2e7800b5af46e6093872b177b7a5e7f0563be51 | https://polymarket.com/@beachboy4 | beachboy4
0x507e52ef684ca2dd91f90a9d26d149dd3288beae | https://polymarket.com/@gamblingisallyouneed
```

Lines starting with `#` are comments. Restart bot after editing.

---

## Running

```bash
# Simple run
python main.py

# Background (survives closing Termux)
screen -S whale
python main.py
# Ctrl+A then D to detach
# screen -r whale to reattach
```

**Dashboard:** Open Chrome on your phone → `http://localhost:8080`

---

## Controls

| Action | Command |
|---|---|
| Stop all trading | `touch KILL` |
| Pause one whale | `touch PAUSE_0xWHALEADDRESS` |
| Resume | `rm KILL` then restart |
| Check state | `cat state/BOT_MEMORY.md` |
| View audit log | `tail -f state/audit.log` |

---

## File Structure

```
whale-mirror-bot/
├── main.py          # Orchestrator — startup, main loop, decision engine
├── whales.txt       # Your whale list (edit anytime, restart to apply)
├── config.py        # All settings with defaults
├── env.example      # Template for your .env file
├── monitor.py       # Whale polling, position diff, market data
├── executor.py      # Order placement via py-clob-client
├── positions.py     # Position lifecycle, SL/TP/runner logic
├── filters.py       # Spread, depth, slippage, pump, cluster, sybil
├── ev_gate.py       # EV formula, Kelly criterion, Bayesian update
├── protection.py    # Loss limits, kill switch, per-whale pause
├── state.py         # SQLite persistence, resume logic
├── scoring.py       # Trust scoring (used by whale_scanner.py)
├── dashboard.py     # Flask dark mobile UI
├── whale_scanner.py # Standalone tool to rank whale wallets
└── state/           # Runtime data (excluded from git)
    ├── bot_state.db # SQLite: positions, audit, session
    ├── BOT_MEMORY.md# Human-readable state snapshot
    └── audit.log    # Full action log
```

---

## Trade Flow

```
Whale opens position
        ↓
Bot detects (within 1 second)
        ↓
Filters: capacity → market quality → pump check
      → spread/depth/slippage → EV gate
        ↓
Pass → place limit order at whale's price
        ↓
Position tracked: SL / max hold / whale exit monitoring
        ↓
Exit: whale closes / SL hits / 72h timeout
```

---

## Key Settings

| Setting | Default | Description |
|---|---|---|
| `TRADE_SIZE_USD` | $4.00 | Per trade size (Kelly may size lower) |
| `CLUSTER_BOOST_USD` | $6.00 | Size when 2+ whales agree |
| `SL_PCT` | 27% | Stop loss from entry |
| `MAX_HOLD_HOURS` | 72h | Force-close after this long |
| `MAX_OPEN_POSITIONS` | 5 | Max simultaneous trades |
| `DAILY_SOFT_PCT` | 10% | Pause new entries at this daily loss |
| `DAILY_HARD_PCT` | 20% | Full halt at this daily loss |
| `MIN_WHALE_USD` | $6 | Ignore whale trades smaller than this |

---

## Whale Scanner

Rank all wallets by performance:

```bash
python whale_scanner.py           # 30-day window
python whale_scanner.py --days 60 # wider window
python whale_scanner.py --out csv # save to whale_ranks.csv
```

---

## Disclaimer

This bot trades real money on prediction markets. Past whale performance does not guarantee future results. Use at your own risk. Start with `DRY_RUN=true` and small amounts.

---

*Built for Termux on Android. Tested on OnePlus 12R.*
