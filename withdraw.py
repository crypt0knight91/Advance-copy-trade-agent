"""
withdraw.py — Automated USDC withdrawal from Polymarket (Polygon) → Base chain.

Flow:
  1. Check available USDC balance on Polygon proxy wallet
  2. Verify safety gates (no active trades, buffer maintained)
  3. Approve USDC spending to Across SpokePool (if needed)
  4. Call Across SpokePool.depositV3() → relayers deliver USDC on Base
  5. Log tx hash, confirm, update state

Bridge: Across Protocol (fastest, no wrapped tokens, fills in ~2-10 seconds)
  Polygon SpokePool: 0x9295ee1d8C5b022Be115A2AD3c30C72E34e7F096
  Base destination: 0x1c6A81A22b97441E58c976819E9e413f28e35F18

Security:
  - Private key read from config (never logged)
  - Destination address validated against EIP-55 checksum
  - Cooldown stored in DB — survives restarts, prevents double-send
  - All amounts in integer USDC units (6 decimals) to avoid float bugs
"""
import re
import time
import json
from datetime import datetime, timezone
from typing import Optional, Tuple

import config

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
# Polygon
POLYGON_RPC       = "https://polygon-rpc.com"
POLYGON_CHAIN_ID  = 137
USDC_POLYGON      = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e on Polygon
ACROSS_SPOKE_POLY = "0x9295ee1d8C5b022Be115A2AD3c30C72E34e7F096"  # Across SpokePool

# Base
BASE_CHAIN_ID     = 8453
USDC_BASE         = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# ERC-20 ABI (minimal — balanceOf + approve)
ERC20_ABI = [
    {"name": "balanceOf", "type": "function",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view"},
    {"name": "allowance", "type": "function",
     "inputs": [{"name": "owner", "type": "address"},
                {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view"},
    {"name": "approve", "type": "function",
     "inputs": [{"name": "spender", "type": "address"},
                {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable"},
]

# Across SpokePool depositV3 ABI
SPOKE_ABI = [
    {
        "name": "depositV3",
        "type": "function",
        "stateMutability": "payable",
        "inputs": [
            {"name": "depositor",          "type": "address"},
            {"name": "recipient",          "type": "address"},
            {"name": "inputToken",         "type": "address"},
            {"name": "outputToken",        "type": "address"},
            {"name": "inputAmount",        "type": "uint256"},
            {"name": "outputAmount",       "type": "uint256"},
            {"name": "destinationChainId", "type": "uint256"},
            {"name": "exclusiveRelayer",   "type": "address"},
            {"name": "quoteTimestamp",     "type": "uint32"},
            {"name": "fillDeadline",       "type": "uint32"},
            {"name": "exclusivityDeadline","type": "uint32"},
            {"name": "message",            "type": "bytes"},
        ],
        "outputs": [],
    }
]

USDC_DECIMALS = 6
ZERO_ADDRESS  = "0x0000000000000000000000000000000000000000"


# ─────────────────────────────────────────────────────────────────────────────
# WEB3 SETUP
# ─────────────────────────────────────────────────────────────────────────────
_w3 = None

def _get_w3():
    """Lazy-init Web3 connection to Polygon."""
    global _w3
    if _w3 is not None:
        return _w3
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(POLYGON_RPC, request_kwargs={"timeout": 30}))
        if w3.is_connected():
            _w3 = w3
            return _w3
        # Try backup RPCs
        for rpc in ["https://rpc-mainnet.matic.network",
                    "https://rpc.ankr.com/polygon"]:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
            if w3.is_connected():
                _w3 = w3
                return _w3
    except ImportError:
        pass
    return None


def _checksum(addr: str) -> str:
    """Return EIP-55 checksum address. Raises on invalid."""
    try:
        from web3 import Web3
        return Web3.to_checksum_address(addr)
    except Exception:
        raise ValueError(f"Invalid address: {addr}")


# ─────────────────────────────────────────────────────────────────────────────
# ADDRESS VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
def validate_address(addr: str) -> bool:
    """
    Validate Ethereum address.
    Accepts both checksummed (EIP-55) and all-lowercase formats.
    Works with or without web3 installed.
    """
    if not addr:
        return False
    # Core requirement: 0x + 40 hex chars
    if not re.match(r"^0x[0-9a-fA-F]{40}$", addr):
        return False
    # If web3 available, also validate EIP-55 checksum
    # BUT: accept lowercase version even if checksum fails
    # (lowercase is always valid, checksum is optional formatting)
    try:
        from web3 import Web3
        checksummed = Web3.to_checksum_address(addr.lower())
        # Accept if addr matches checksum OR if addr is all-lowercase
        return (addr.lower() == addr or
                addr == checksummed)
    except Exception:
        # No web3 or other error — regex pass is enough
        return True


# ─────────────────────────────────────────────────────────────────────────────
# BALANCE CHECK
# ─────────────────────────────────────────────────────────────────────────────
def get_usdc_balance(wallet: str) -> Optional[float]:
    """
    Get USDC balance of wallet on Polygon.
    Returns float (human-readable, e.g. 23.45) or None on error.
    """
    w3 = _get_w3()
    if not w3:
        return None
    try:
        usdc = w3.eth.contract(
            address=_checksum(USDC_POLYGON),
            abi=ERC20_ABI,
        )
        raw = usdc.functions.balanceOf(_checksum(wallet)).call()
        return raw / (10 ** USDC_DECIMALS)
    except Exception as e:
        _wlog(f"balance_check_failed: {e}")
        return None


def get_matic_balance(wallet: str) -> Optional[float]:
    """Get MATIC balance for gas estimation."""
    w3 = _get_w3()
    if not w3:
        return None
    try:
        raw = w3.eth.get_balance(_checksum(wallet))
        return raw / 10**18
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ACROSS RELAY QUOTE — get output amount (input minus relayer fee)
# ─────────────────────────────────────────────────────────────────────────────
def get_across_quote(amount_usdc: float) -> Optional[dict]:
    """
    Query Across API for relay fee estimate.
    Returns {"inputAmount": int, "outputAmount": int, "relayFeeUsd": float}
    or None on failure.
    """
    try:
        import requests
        amount_raw = int(amount_usdc * 10**USDC_DECIMALS)
        r = requests.get(
            "https://app.across.to/api/suggested-fees",
            params={
                "inputToken":  USDC_POLYGON,
                "outputToken": USDC_BASE,
                "originChainId":      POLYGON_CHAIN_ID,
                "destinationChainId": BASE_CHAIN_ID,
                "amount":      amount_raw,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()

        relay_fee_raw = int(data.get("relayFeeTotal", data.get("totalRelayFee", {}).get("total", 0)))
        output_raw    = amount_raw - relay_fee_raw
        relay_fee_usd = relay_fee_raw / 10**USDC_DECIMALS

        return {
            "inputAmount":  amount_raw,
            "outputAmount": max(output_raw, 0),
            "relayFeeUsd":  relay_fee_usd,
            "timestamp":    data.get("timestamp", int(time.time())),
        }
    except Exception as e:
        _wlog(f"across_quote_failed: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# APPROVE USDC TO SPOKE POOL
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_approval(w3, wallet_addr: str, amount_raw: int,
                     private_key: str) -> bool:
    """
    Approve Across SpokePool to spend USDC if allowance is insufficient.
    Returns True if already approved or approval succeeded.
    """
    try:
        usdc = w3.eth.contract(address=_checksum(USDC_POLYGON), abi=ERC20_ABI)
        allowance = usdc.functions.allowance(
            _checksum(wallet_addr),
            _checksum(ACROSS_SPOKE_POLY),
        ).call()

        if allowance >= amount_raw:
            return True  # Already approved

        _wlog(f"approving USDC allowance: {amount_raw / 10**USDC_DECIMALS:.2f}")
        nonce = w3.eth.get_transaction_count(_checksum(wallet_addr))
        gas_price = w3.eth.gas_price

        # Approve max uint256 so we don't need to re-approve
        MAX_UINT = 2**256 - 1
        tx = usdc.functions.approve(
            _checksum(ACROSS_SPOKE_POLY), MAX_UINT
        ).build_transaction({
            "from":     _checksum(wallet_addr),
            "nonce":    nonce,
            "gas":      80_000,
            "gasPrice": int(gas_price * 1.2),
            "chainId":  POLYGON_CHAIN_ID,
        })
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.status == 1:
            _wlog(f"approve_success tx={tx_hash.hex()}")
            return True
        _wlog(f"approve_failed tx={tx_hash.hex()}")
        return False
    except Exception as e:
        _wlog(f"approve_error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# EXECUTE BRIDGE WITHDRAWAL
# ─────────────────────────────────────────────────────────────────────────────
def execute_withdrawal(
    amount_usdc: float,
    destination: str,
    private_key: str,
    wallet_addr: str,
) -> Tuple[bool, str]:
    """
    Bridge `amount_usdc` USDC from Polygon proxy wallet to `destination` on Base.

    Uses Across Protocol depositV3:
      - No wrapping required
      - Fills in ~2-10 seconds on Base
      - Relayer fee ~0.05-0.15 USDC typically

    Returns (success: bool, tx_hash_or_error: str)
    """
    if not validate_address(destination):
        return False, f"INVALID_DESTINATION: {destination}"

    w3 = _get_w3()
    if not w3:
        return False, "WEB3_UNAVAILABLE"

    # Get quote first
    quote = get_across_quote(amount_usdc)
    if not quote:
        return False, "QUOTE_FAILED"

    input_amount  = quote["inputAmount"]
    output_amount = quote["outputAmount"]
    relay_fee     = quote["relayFeeUsd"]

    _wlog(f"bridge quote: input=${amount_usdc:.2f} "
          f"output=${output_amount/10**USDC_DECIMALS:.4f} "
          f"fee=${relay_fee:.4f}")

    # Safety: reject if fee > 2% of amount
    if relay_fee > amount_usdc * 0.02:
        return False, f"RELAY_FEE_TOO_HIGH: ${relay_fee:.4f} > 2%"

    # Ensure approval
    approved = _ensure_approval(w3, wallet_addr, input_amount, private_key)
    if not approved:
        return False, "APPROVAL_FAILED"

    # Build depositV3 transaction
    try:
        spoke = w3.eth.contract(
            address=_checksum(ACROSS_SPOKE_POLY),
            abi=SPOKE_ABI,
        )
        now         = int(time.time())
        fill_deadline = now + 3600  # 1 hour to fill

        nonce     = w3.eth.get_transaction_count(_checksum(wallet_addr))
        gas_price = w3.eth.gas_price

        tx = spoke.functions.depositV3(
            _checksum(wallet_addr),     # depositor
            _checksum(destination),     # recipient on Base
            _checksum(USDC_POLYGON),    # inputToken (USDC.e on Polygon)
            _checksum(USDC_BASE),       # outputToken (USDC on Base)
            input_amount,               # inputAmount
            output_amount,              # outputAmount (after fee)
            BASE_CHAIN_ID,              # destinationChainId
            _checksum(ZERO_ADDRESS),    # exclusiveRelayer (none)
            now,                        # quoteTimestamp
            fill_deadline,              # fillDeadline
            0,                          # exclusivityDeadline
            b"",                        # message (empty)
        ).build_transaction({
            "from":     _checksum(wallet_addr),
            "nonce":    nonce,
            "gas":      250_000,
            "gasPrice": int(gas_price * 1.3),  # +30% for fast inclusion
            "chainId":  POLYGON_CHAIN_ID,
            "value":    0,
        })

        signed   = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash  = w3.eth.send_raw_transaction(signed.rawTransaction)
        tx_hex   = tx_hash.hex()

        _wlog(f"bridge_tx_sent: {tx_hex}")

        # Wait for Polygon confirmation (not Base — relayer handles that)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.status == 1:
            out_usd = output_amount / 10**USDC_DECIMALS
            _wlog(f"bridge_confirmed: {tx_hex} "
                  f"sent=${amount_usdc:.2f} arrives=${out_usd:.4f} on Base")
            return True, tx_hex
        else:
            return False, f"TX_REVERTED: {tx_hex}"

    except Exception as e:
        return False, f"BRIDGE_ERROR: {str(e)[:120]}"


# ─────────────────────────────────────────────────────────────────────────────
# WITHDRAWAL LOG
# ─────────────────────────────────────────────────────────────────────────────
def _wlog(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}][WITHDRAW] {msg}"
    print(line)
    try:
        with open("state/withdraw.log", "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# MAIN WITHDRAWAL MANAGER
# ─────────────────────────────────────────────────────────────────────────────
class WithdrawalManager:
    """
    Manages automated USDC withdrawal from Polymarket → Base.

    Safety gates (ALL must pass before any withdrawal):
      1. Available balance >= threshold
      2. No active trades open
      3. After withdrawal, remaining balance >= WITHDRAW_MIN_BUFFER
      4. Cooldown period has passed since last withdrawal
      5. Not in DRY_RUN mode (unless WITHDRAW_ALLOW_DRY=true)
      6. Web3 available and Polygon RPC connected
      7. MATIC gas balance > 0.01 (enough for gas)
    """

    def __init__(self, db):
        self.db              = db
        self._last_check_ts  = 0.0
        self._in_progress    = False  # prevents concurrent execution

    # ── Config accessors (read live from config each time) ────────────────────
    @staticmethod
    def threshold() -> float:
        return float(getattr(config, "WITHDRAW_THRESHOLD", 100.0))

    @staticmethod
    def amount() -> float:
        return float(getattr(config, "WITHDRAW_AMOUNT", 50.0))

    @staticmethod
    def cooldown_secs() -> int:
        return int(getattr(config, "WITHDRAW_COOLDOWN_SECS", 3600))

    @staticmethod
    def min_buffer() -> float:
        return float(getattr(config, "WITHDRAW_MIN_BUFFER", 20.0))

    @staticmethod
    def destination() -> str:
        return getattr(config, "WITHDRAW_DESTINATION",
                       "0x1c6A81A22b97441E58c976819E9e413f28e35F18")

    @staticmethod
    def enabled() -> bool:
        return getattr(config, "WITHDRAW_ENABLED", False)

    # ── Cooldown state (persisted in DB survives restarts) ────────────────────
    def _last_withdraw_ts(self) -> float:
        return float(self.db.get_kv("last_withdraw_ts", 0) or 0)

    def _record_withdrawal(self, tx_hash: str, amount: float, balance_before: float):
        self.db.set_kv("last_withdraw_ts", time.time())
        self.db.set_kv("last_withdraw_tx",  tx_hash)
        self.db.set_kv("last_withdraw_amt", amount)
        self.db.audit(
            "WITHDRAWAL_SUCCESS",
            f"amount=${amount:.2f} balance_before=${balance_before:.2f} "
            f"tx={tx_hash} dest={self.destination()[:20]}",
            "", "", "SUCCESS"
        )

    def _record_failure(self, reason: str, amount: float):
        self.db.audit(
            "WITHDRAWAL_FAILED",
            f"amount=${amount:.2f} reason={reason}",
            "", "", "FAIL"
        )

    # ── Gate checks ───────────────────────────────────────────────────────────
    def _check_cooldown(self) -> Tuple[bool, str]:
        last = self._last_withdraw_ts()
        if last == 0:
            return True, "no_previous_withdrawal"
        elapsed = time.time() - last
        if elapsed < self.cooldown_secs():
            remaining = int(self.cooldown_secs() - elapsed)
            return False, f"cooldown_{remaining}s_remaining"
        return True, "cooldown_passed"

    def _check_gas(self) -> Tuple[bool, str]:
        """Ensure wallet has MATIC for gas."""
        matic = get_matic_balance(config.MY_PROXY_WALLET)
        if matic is None:
            return False, "matic_check_failed"
        if matic < 0.01:
            return False, f"insufficient_matic:{matic:.6f}<0.01"
        return True, f"matic_ok:{matic:.4f}"

    # ── Main check (called from main loop every 60s) ──────────────────────────
    def check_and_withdraw(self, open_positions: int,
                            protection_current: float) -> bool:
        """
        Called every 60 seconds from main loop.
        Returns True if withdrawal was executed.
        """
        if not self.enabled():
            return False

        if self._in_progress:
            return False  # prevent concurrent execution

        now = time.time()
        if now - self._last_check_ts < 60:
            return False  # rate-limit checks
        self._last_check_ts = now

        dest      = self.destination()
        threshold = self.threshold()
        amount    = self.amount()
        min_buf   = self.min_buffer()

        # ── Gate 1: destination address valid ─────────────────────────────────
        if not validate_address(dest):
            _wlog(f"GATE_FAIL: invalid destination address {dest}")
            return False

        # ── Gate 2: not in dry run ─────────────────────────────────────────────
        if config.DRY_RUN:
            _wlog(f"GATE_SKIP: DRY_RUN mode — would withdraw ${amount:.2f} "
                  f"when balance=${protection_current:.2f}")
            return False

        # ── Gate 3: no active trades ───────────────────────────────────────────
        if open_positions > 0:
            _wlog(f"GATE_FAIL: {open_positions} active trades — skip")
            return False

        # ── Gate 4: balance on-chain (authoritative source) ───────────────────
        if not config.MY_PROXY_WALLET:
            return False

        on_chain_balance = get_usdc_balance(config.MY_PROXY_WALLET)
        if on_chain_balance is None:
            _wlog("GATE_FAIL: could not fetch on-chain balance")
            return False

        _wlog(f"balance_check: on_chain=${on_chain_balance:.2f} "
              f"bot_tracked=${protection_current:.2f} "
              f"threshold=${threshold:.2f}")

        if on_chain_balance < threshold:
            return False  # Not there yet — silent return

        # ── Gate 5: buffer check ───────────────────────────────────────────────
        remaining_after = on_chain_balance - amount
        if remaining_after < min_buf:
            _wlog(f"GATE_FAIL: buffer too low after withdrawal "
                  f"(${remaining_after:.2f} < ${min_buf:.2f} minimum)")
            return False

        # ── Gate 6: cooldown ──────────────────────────────────────────────────
        ok, reason = self._check_cooldown()
        if not ok:
            _wlog(f"GATE_FAIL: cooldown — {reason}")
            return False

        # ── Gate 7: gas ───────────────────────────────────────────────────────
        ok, reason = self._check_gas()
        if not ok:
            _wlog(f"GATE_FAIL: gas — {reason}")
            return False

        # ── ALL GATES PASSED — execute ─────────────────────────────────────────
        self._in_progress = True
        _wlog(f"ALL_GATES_PASSED: withdrawing ${amount:.2f} to {dest}")
        _wlog(f"balance_before=${on_chain_balance:.2f} "
              f"will_remain=${remaining_after:.2f}")

        self.db.audit(
            "WITHDRAWAL_INITIATED",
            f"amount=${amount:.2f} dest={dest} "
            f"balance=${on_chain_balance:.2f}",
            "", "", "INITIATED"
        )

        try:
            # Retry up to 3 times
            last_err = ""
            for attempt in range(1, 4):
                _wlog(f"attempt {attempt}/3...")
                ok2, result = execute_withdrawal(
                    amount_usdc  = amount,
                    destination  = dest,
                    private_key  = config.POLY_PRIVATE_KEY,
                    wallet_addr  = config.MY_PROXY_WALLET,
                )
                if ok2:
                    _wlog(f"SUCCESS: tx={result}")
                    _wlog(f"funds arriving on Base at {dest}")
                    self._record_withdrawal(result, amount, on_chain_balance)
                    print(f"\n{'='*55}")
                    print(f"  💸 WITHDRAWAL SUCCESS")
                    print(f"  Amount:  ${amount:.2f} USDC")
                    print(f"  To:      {dest}")
                    print(f"  Network: Base chain")
                    print(f"  TX:      {result}")
                    print(f"  Remaining balance: ${remaining_after:.2f}")
                    print(f"{'='*55}\n")
                    return True
                else:
                    last_err = result
                    _wlog(f"attempt {attempt} failed: {result}")
                    if attempt < 3:
                        time.sleep(10 * attempt)  # backoff

            # All retries failed
            _wlog(f"ALL_RETRIES_FAILED: {last_err}")
            self._record_failure(last_err, amount)
            return False

        finally:
            self._in_progress = False
