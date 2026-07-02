#!/usr/bin/env python3
"""Fund an agent wallet with native USDC on Arc testnet (#788 slice 2).

Agent EOAs created for SIWE auth (see scripts/agent_journey.py) start empty; to
DEPLOY they need gas — and on Arc, **USDC IS the gas token** (chain 5042002).
Two funding modes:

  treasury (default)
      A plain native-value transfer from the dev treasury wallet
      (``DEV_WALLET_PRIVATE_KEY`` from your .env — never a CLI arg, never
      printed) to ``--to``. Implemented with eth_account signing + raw JSON-RPC
      over httpx (both already backend deps — no web3 provider needed). The RPC
      URL comes from ``--rpc``, else ``$RPC`` (arc-canteen), else
      ``$ARC_ARC_RPC_URL``. Native value is denominated in wei-style 18-decimal
      units at the RPC level (repo precedent: chain/executor.py,
      bootstrap_vaults.py); ``--decimals`` is the escape hatch if Arc ever
      reads differently.

  faucet
      POST https://api.circle.com/v1/faucet/drips with ``Bearer $CIRCLE_API_KEY``
      and body ``{"address": ..., "blockchain": <--blockchain>, "usdc": true}``.
      CAVEAT: the exact Circle blockchain enum for Arc testnet is UNVERIFIED —
      ``--blockchain`` defaults to ``ARC-TESTNET`` and the script prints the RAW
      API response so the right enum can be learned empirically (a 400 naming
      valid enums is useful output, not a bug).

SAFETY: dry-run by default — prints exactly what would happen; add ``--execute``
to actually send. Testnet only; never point this at a funded/mainnet key.

Usage:
    python scripts/fund_agent_wallet.py --to 0xAbc... --amount 5            # dry-run
    python scripts/fund_agent_wallet.py --to 0xAbc... --amount 5 --execute  # send
    python scripts/fund_agent_wallet.py --to 0xAbc... --mode faucet --execute
    python scripts/fund_agent_wallet.py --to 0xAbc... --mode faucet --blockchain ARC-SEPOLIA --execute
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from decimal import ROUND_DOWN, Decimal

import httpx

ARC_CHAIN_ID = 5042002  # Arc testnet (0x4cef52)
CIRCLE_FAUCET_URL = "https://api.circle.com/v1/faucet/drips"
_RECEIPT_POLL_SECONDS = 2.0
_RECEIPT_POLL_ATTEMPTS = 15


def _load_dotenv_if_available() -> None:
    """Pick up the repo .env (DEV_WALLET_PRIVATE_KEY, RPC, CIRCLE_API_KEY) when
    python-dotenv is installed (it's a backend dep). Existing env vars win."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        # Optional convenience only: without python-dotenv the caller simply
        # exports the env vars themselves — nothing to do here.
        return


def _rpc_call(client: httpx.Client, url: str, method: str, params: list) -> object:
    """One JSON-RPC call; raises RuntimeError on a JSON-RPC error response."""
    resp = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} → RPC error: {body['error']}")
    return body.get("result")


def fund_via_treasury(to: str, amount: float, rpc: str, decimals: int, execute: bool) -> int:
    """Native-USDC value transfer from the DEV_WALLET_PRIVATE_KEY treasury."""
    from eth_account import Account

    key = os.environ.get("DEV_WALLET_PRIVATE_KEY", "").strip()
    if not key:
        print("✗ DEV_WALLET_PRIVATE_KEY not set (fill it in .env — testnet dev wallet only).")
        return 2
    try:
        acct = Account.from_key(key)
    except (ValueError, TypeError):
        print("✗ DEV_WALLET_PRIVATE_KEY is not a valid private key.")  # never echo the value
        return 2

    # Decimal, not binary float: int(0.1 * 10**6) style truncation silently
    # under/over-funds on-chain amounts (Copilot, #851). The CLI string parses
    # exactly; quantize to whole base units.
    value = int((Decimal(str(amount)) * (10**decimals)).to_integral_value(rounding=ROUND_DOWN))
    print(f"treasury transfer plan (chain {ARC_CHAIN_ID}, native USDC = gas):")
    print(f"  from:    {acct.address}")
    print(f"  to:      {to}")
    print(f"  amount:  {amount} USDC  (value={value} @ 10^{decimals})")
    print(f"  rpc:     {rpc}")

    with httpx.Client(timeout=30.0) as client:
        chain_id = int(str(_rpc_call(client, rpc, "eth_chainId", [])), 16)
        if chain_id != ARC_CHAIN_ID:
            print(f"✗ RPC reports chain id {chain_id}, expected Arc testnet {ARC_CHAIN_ID} — refusing.")
            return 2
        balance = int(str(_rpc_call(client, rpc, "eth_getBalance", [acct.address, "latest"])), 16)
        nonce = int(str(_rpc_call(client, rpc, "eth_getTransactionCount", [acct.address, "pending"])), 16)
        gas_price = int(str(_rpc_call(client, rpc, "eth_gasPrice", [])), 16)
        print(f"  treasury balance: {balance / 10**decimals:.6f} USDC   nonce: {nonce}   gasPrice: {gas_price}")

        tx = {
            "to": to,
            "value": value,
            "gas": 21_000,  # plain value transfer
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": ARC_CHAIN_ID,
        }
        if balance < value + 21_000 * gas_price:
            print("✗ treasury balance is insufficient for amount + gas.")
            return 2

        if not execute:
            print("\nDRY RUN — no transaction sent. Re-run with --execute to send.")
            return 0

        signed = Account.sign_transaction(tx, key)
        tx_hash = _rpc_call(
            client, rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex().removeprefix("0x")]
        )
        print(f"  → sent: {tx_hash}")

        for _ in range(_RECEIPT_POLL_ATTEMPTS):
            receipt = _rpc_call(client, rpc, "eth_getTransactionReceipt", [tx_hash])
            if isinstance(receipt, dict):
                status = int(str(receipt.get("status", "0x0")), 16)
                print(
                    f"  → receipt: status={'success' if status == 1 else 'FAILED'} block={receipt.get('blockNumber')}"
                )
                return 0 if status == 1 else 1
            time.sleep(_RECEIPT_POLL_SECONDS)
        print("  · no receipt yet (still pending) — check the explorer for the tx hash above.")
        return 0


def fund_via_faucet(to: str, blockchain: str, execute: bool) -> int:
    """Request testnet USDC from the Circle faucet API. Prints the RAW response
    so the correct Arc blockchain enum can be learned empirically."""
    api_key = os.environ.get("CIRCLE_API_KEY", "").strip()
    body = {"address": to, "blockchain": blockchain, "usdc": True}
    print("Circle faucet request plan:")
    print(f"  POST {CIRCLE_FAUCET_URL}")
    print(f"  body: {json.dumps(body)}")
    print(f"  auth: Bearer $CIRCLE_API_KEY ({'set' if api_key else 'NOT SET'})")
    print("  note: blockchain enum for Arc is UNVERIFIED — default ARC-TESTNET; adjust with --blockchain")

    if not execute:
        print("\nDRY RUN — no request sent. Re-run with --execute to send.")
        return 0
    if not api_key:
        print("✗ CIRCLE_API_KEY not set — cannot call the faucet.")
        return 2

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(CIRCLE_FAUCET_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
    # Print the raw response verbatim: on an enum mismatch Circle's error names
    # the valid values — exactly the empirical signal we're after.
    print(f"\nRAW response — HTTP {resp.status_code}:")
    print(resp.text)
    return 0 if resp.status_code < 300 else 1


def main() -> int:
    _load_dotenv_if_available()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--to", required=True, help="target wallet address (0x...)")
    ap.add_argument("--amount", type=float, default=5.0, help="USDC amount for treasury mode (default 5)")
    ap.add_argument(
        "--mode", choices=["treasury", "faucet"], default="treasury", help="funding source (default treasury)"
    )
    ap.add_argument("--rpc", default=None, help="Arc RPC URL (default: $RPC, then $ARC_ARC_RPC_URL)")
    ap.add_argument(
        "--decimals", type=int, default=18, help="native value decimals at the RPC level (default 18, repo precedent)"
    )
    ap.add_argument(
        "--blockchain",
        default="ARC-TESTNET",
        help="Circle faucet blockchain enum (faucet mode). The Arc enum is UNVERIFIED — "
        "the raw API response is printed so the right value can be learned empirically.",
    )
    ap.add_argument("--execute", action="store_true", help="actually send (default is dry-run)")
    args = ap.parse_args()

    if not (args.to.startswith("0x") and len(args.to) == 42):
        print(f"✗ --to does not look like an EVM address: {args.to!r}")
        return 2

    if args.mode == "faucet":
        return fund_via_faucet(args.to, args.blockchain, args.execute)

    rpc = args.rpc or os.environ.get("RPC") or os.environ.get("ARC_ARC_RPC_URL", "")
    if not rpc:
        print("✗ no RPC URL — pass --rpc or set RPC in .env (from `arc-canteen login`).")
        return 2
    return fund_via_treasury(args.to, args.amount, rpc, args.decimals, args.execute)


if __name__ == "__main__":
    sys.exit(main())
