"""
whales.py — Load whale addresses from whales.txt (editable at any time).

Format in whales.txt:
  0xADDRESS | https://polymarket.com/@username | nickname
  # comment lines are ignored
  blank lines are ignored

The bot reads this file at startup. Edit it, restart bot, changes apply.
"""
import os
from typing import Dict, List

WHALES_FILE = os.getenv("WHALE_LIST_FILE", "whales.txt")

# ─────────────────────────────────────────────────────────────────────────────
# LOAD FROM FILE
# ─────────────────────────────────────────────────────────────────────────────
def _load() -> Dict[str, dict]:
    """
    Parse whales.txt. Returns dict of:
      addr → {"name": str, "url": str}
    """
    result = {}
    if not os.path.exists(WHALES_FILE):
        print(f"[WHALES] {WHALES_FILE} not found. Create it with your whale addresses.")
        return result

    with open(WHALES_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            # Support both | separator and plain address-only lines
            parts = [p.strip() for p in line.split("|")]
            addr  = parts[0].strip().lower()

            if not addr.startswith("0x") or len(addr) < 10:
                continue

            url  = parts[1].strip() if len(parts) > 1 else ""
            nick = parts[2].strip() if len(parts) > 2 else ""

            # Extract username from URL if no nickname given
            if not nick and "polymarket.com/@" in url:
                nick = url.split("@")[1].split("?")[0].split("/")[0]
            if not nick:
                nick = addr[:10] + "..."

            result[addr] = {"name": nick, "url": url}

    return result


# Loaded once at import time
_REGISTRY: Dict[str, dict] = _load()


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def get_all() -> List[str]:
    return list(_REGISTRY.keys())

def name(addr: str) -> str:
    return _REGISTRY.get(addr.lower(), {}).get("name", addr[:12] + "...")

def profile_url(addr: str) -> str:
    info = _REGISTRY.get(addr.lower(), {})
    url  = info.get("url", "")
    if url:
        return url
    n = info.get("name", addr[:10])
    return f"https://polymarket.com/@{n}"

def reload() -> int:
    """Reload whales.txt without restarting (call after editing file)."""
    global _REGISTRY
    _REGISTRY = _load()
    return len(_REGISTRY)

def summary() -> str:
    n = len(_REGISTRY)
    names = ", ".join(f"@{v['name']}" for v in list(_REGISTRY.values())[:5])
    if n > 5:
        names += f" ... +{n-5} more"
    return f"{n} whale(s): {names}"
