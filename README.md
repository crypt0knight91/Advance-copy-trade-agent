# Advance-copy-trade-agent

![License](https://img.shields.io/badge/license-MIT-blue) ![Python](https://img.shields.io/badge/python-3.8+-green) ![Stars](https://img.shields.io/github/stars/YOUR_USERNAME/REPO?style=social)

> A trading bot built with Python

---

## 📋 Table of Contents

- [Features](#-features)
- [Requirements](#-requirements)
- [Installation](#-installation)
- [Usage](#-usage)
- [Project Structure](#-project-structure)
- [Configuration](#-configuration)
- [Contributing](#-contributing)
- [License](#-license)
- [Support](#-support-the-developer)

---

## ✨ Features

- ✅ Automated task execution
- ✅ Configurable via environment variables
- ✅ Lightweight and fast
- ✅ Easy to extend and customize

CORE TRADING

• Polls preloaded 21+ whale wallets every 1 second via Polymarket Data API

• Copies BUY signals within 1 second of whale opening position

• Mirrors whale exits — closes your position when whale closes theirs

• Supports YES and NO outcome trades correctly

• Copies both limit orders (GTC) and market orders (FOK/FAK)

• Smart execution: market orders fill at live ask price, max whale_fill + 3¢

• Cluster boost: 2+ whales same market within 5 min → size $4 → $6

• One position per market — no duplicate exposure from multiple whales

SIGNAL FILTERS (all must pass before copying)

• Spread gate: market orders only if bid-ask spread < 3¢

• Price stability: skip if price moved >2¢ above whale's fill

• Book depth: $200+ liquidity within 3¢ required

• Slippage simulation: walks real orderbook, skips if avg fill >2¢ above target

• Pump detection: skips if whale trade >10× yesterday's market volume

• Expiry filter: skips markets resolving in <1 hour

• Price ceiling: skips near-resolved markets (>97¢)

• Sybil detection: identical size + timing across wallets = fake cluster, denied

QUANTITATIVE FRAMEWORK

• Bayesian updating: P(A|B) = P(B|A) × P(A) / P(B) — updates market prior with whale signal

• EV gate: EV = (P_win × profit) - (P_loss × stake) — requires ≥5% edge

• Kelly Criterion: f = (p×b - q)/b ÷ 4 — mathematically optimal quarter-Kelly sizing

• Nash equilibrium: limit orders earn +1.12% maker rebate, baked into EV

RISK MANAGEMENT

• Stop loss: auto-closes at -27% from actual fill price

• Max hold: force-closes any position after 72 hours (dead-man switch)

• Runner protection: +900% profit → alert only, never auto-closes (lets winners run)

• Daily soft pause: stops new entries at -10% daily loss

• Daily hard halt: full stop + KILL file written at -20% daily loss

• Total halt: permanent stop at -40% total loss from starting bankroll

• Kill switch: touch KILL file → all trading stops instantly

• Per-whale pause: touch PAUSE_0xADDRESS → freeze one whale without stopping bot

• Max 5 simultaneous positions enforced at all times

• Trade buffer: minimum $20 always kept in account

POSITION SYNC (every 30 seconds)

• Unfilled limit orders >2 min → auto-cancelled via CLOB, slot freed

• Entry price corrected from actual avgPrice on Polymarket (not whale's price)

• SL recalculated from real fill price after sync

• Detects positions closed/resolved during downtime

SMART STARTUP

• DB cleanup: clears stale positions from previous session on every start

• Startup snapshot: locks whale positions >30 min old (never copies these)

• Recent eligible: positions opened in last 30 min are still copied

• Resume: verifies open positions against Polymarket API, resumes SL/TP monitoring

AUTO-WITHDRAWAL (Polygon → Base chain)

• Triggers when available USDC balance ≥ configurable threshold (default $100)

• Withdraws configurable amount (default $50) via Across Protocol bridge

• Arrives on Base chain in ~10 seconds, ~$0.05-0.15 relay fee

• 7 safety gates: address validation, no active trades, buffer check, cooldown, gas check

• Cooldown: 1 hour minimum between withdrawals (persisted across restarts)

• Double-send protection: in_progress flag + DB-persisted last_withdraw_ts

• Full retry logic with exponential backoff (3 attempts)

PERSISTENCE & AUDIT

• SQLite database: full position history, audit log, session KV, cluster events

• Every action logged: POSITION_OPENED, POSITION_CLOSED, WITHDRAWAL_SUCCESS, SKIPPED (with reason)

• BOT_MEMORY.md: human-readable state snapshot updated every 5 minutes

• Survives crashes: reads DB on restart, reconciles with live Polymarket data

DASHBOARD

• Mobile-first dark UI at localhost:8080

• Live positions with entry, current price, PnL%, SL, hours held

• Today's PnL, daily loss gauge, active whale count

• Real-time audit log tail

• Emergency kill switch button

WHALE MANAGEMENT

• whales.txt: add/remove wallets anytime, restart to apply

• Whale scanner tool: ranks all wallets by PnL, win rate, consistency

• Scoring: 35% PnL + 30% WR + 15% volume + 10% consistency + 10% trade count

TECH STACK
• Python 3.12 · Flask · SQLite · py-clob-client· web3.py

• Polymarket CLOB API · Gamma API · Data API · Goldsky subgraph

• Across Protocol bridge · Gmail/Magic wallet (signature_type=1)

• Runs 24/7 on Android via Termux — no server required

---

## 📦 Requirements

- Python 3.8+
- Dependencies listed in `requirements.txt`

---

## 🚀 Installation

### Step 1: Clone the repository

```bash
git clone https://github.com/crypt0knight91/Advance-copy-trade-agent
cd Advance-copy-trade-agent
```

### Step 2: Create virtual environment

```bash
python -m venv venv

# Linux/macOS
source venv/bin/activate

# Windows
venv\Scripts\activate

# Termux (Android)
source venv/bin/activate
```

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure environment

```bash
cp .env.example .env
# Edit .env with your configuration
```

---

## 💻 Usage

### Basic Usage

```bash
python main.py
```

### Run in background (Linux/Termux)

```bash
nohup python main.py > bot.log 2>&1 &
```

### Run with screen session

```bash
screen -S advance-copy-trade-agent
python main.py
# Detach: Ctrl+A then D
```

---

## 📁 Project Structure

```
Advance-copy-trade-agent/
├── config.py
├── dashboard.py
├── ev_gate.py
├── executor.py
├── filters.py
├── main.py
├── monitor.py
├── positions.py
├── protection.py
├── scoring.py
├── state.py
├── whale_scanner.py
└── ... (2 more files)
```

---

## ⚙️ Configuration

Create a `.env` file based on `.env.example`:

```env
# Add your configuration here
# Example:
# API_KEY=your_api_key_here
# DEBUG=false
```

> **Never commit `.env` to version control.**

---

## 🤝 Contributing

Contributions are welcome! Here's how:

1. Fork the repository
2. Create your feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'feat: add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

Please follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

---

## 📄 License

```
MIT License

Copyright (c) 2024

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

---

## 💖 Support the Developer

If this project helped you, consider supporting its development:

**ETH / EVM Chains:**
```
0x1c6A81A22b97441E58c976819E9e413f28e35F18
```


> Every contribution, no matter how small, keeps this project alive. 🙏


<div align="center">

**Built with ❤️ and Python**

*Star ⭐ this repo if you find it useful!*

</div>
