"""
protection.py — Loss limits, kill switch, per-whale pause.
Kill switch: touch KILL file → halt all.
Per-whale pause: touch PAUSE_0xABC file → pause that whale.
Daily loss: -10% soft pause, -20% hard halt.
"""
import os, time
from datetime import datetime, timezone
from typing import Tuple

import config


# ─────────────────────────────────────────────────────────────────────────────
# KILL SWITCH + PER-WHALE PAUSE
# ─────────────────────────────────────────────────────────────────────────────
def is_killed() -> bool:
    """Global kill: check for KILL file."""
    return os.path.exists(config.KILL_FILE)

def is_whale_paused(addr: str) -> bool:
    """Per-whale pause: check for PAUSE_0xABC file."""
    return (os.path.exists(f"{config.PAUSE_PFX}{addr.lower()}") or
            os.path.exists(f"{config.PAUSE_PFX}{addr.lower()[:10]}"))

def kill_bot():
    """Create kill file programmatically (e.g., on hard halt)."""
    with open(config.KILL_FILE, "w") as f:
        f.write(f"HALTED at {datetime.now(timezone.utc).isoformat()}\n")


# ─────────────────────────────────────────────────────────────────────────────
# LOSS PROTECTION
# ─────────────────────────────────────────────────────────────────────────────
class LossProtection:
    def __init__(self, bankroll: float = None):
        self.start       = bankroll or config.BANKROLL
        self.daily_start = self.start
        self.current     = self.start
        self.peak        = self.start
        self.halted      = False
        self.soft_paused = False
        self.reason      = ""
        self._daily_reset_ts = self._today_midnight()

    def _today_midnight(self) -> float:
        now = datetime.now(timezone.utc)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()

    def _check_daily_reset(self):
        if time.time() >= self._daily_reset_ts + 86400:
            self.daily_start = self.current
            self._daily_reset_ts += 86400
            self.soft_paused = False
            print("[PROTECTION] Daily loss counter reset (new day UTC).")

    def update(self, bankroll: float):
        self._check_daily_reset()
        self.current = bankroll
        self.peak    = max(self.peak, bankroll)

    def can_trade(self) -> Tuple[bool, str]:
        self._check_daily_reset()

        # Check programmatic halt FIRST (preserves halt reason over kill file)
        if self.halted:
            return False, f"HARD_HALT:{self.reason}"

        # Daily loss checks (before kill file, so we capture the real cause)
        if self.daily_start > 0:
            daily_loss = (self.daily_start - self.current) / self.daily_start
            if daily_loss >= config.DAILY_HARD_PCT:
                self.halted  = True
                self.reason  = f"DailyLoss={daily_loss*100:.1f}%>={config.DAILY_HARD_PCT*100:.0f}%"
                kill_bot()   # write KILL file so restarts also halt
                return False, f"DAILY_HARD_HALT:{self.reason}"
            if daily_loss >= config.DAILY_SOFT_PCT:
                self.soft_paused = True
                return False, f"DAILY_SOFT_PAUSE:{daily_loss*100:.1f}%"

        # Total loss check
        if self.start > 0:
            total_loss = (self.start - self.current) / self.start
            if total_loss >= config.TOTAL_HALT_PCT:
                self.halted = True
                self.reason = f"TotalLoss={total_loss*100:.1f}%"
                kill_bot()
                return False, f"TOTAL_HALT:{self.reason}"

        # Kill file check (after loss checks — lets loss reason take priority)
        if is_killed():
            return False, "KILL_FILE_DETECTED"

        return True, "OK"

    def daily_loss_pct(self) -> float:
        if self.daily_start <= 0:
            return 0.0
        return (self.daily_start - self.current) / self.daily_start

    def total_loss_pct(self) -> float:
        if self.start <= 0:
            return 0.0
        return (self.start - self.current) / self.start

    def status_str(self) -> str:
        dl = self.daily_loss_pct() * 100
        tl = self.total_loss_pct() * 100
        mode = "HALTED" if self.halted else ("SOFT_PAUSE" if self.soft_paused else "OK")
        return (f"mode={mode} daily={dl:.1f}% total={tl:.1f}% "
                f"bankroll=${self.current:.2f}")
