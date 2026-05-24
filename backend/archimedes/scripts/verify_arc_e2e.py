"""End-to-end Arc testnet smoke test — vault lifecycle verification.

Exercises the entire happy path on Arc testnet (Chain ID 5042002):
  1. Connect to RPC, verify chain ID
  2. Create a vault via VaultFactory
  3. Approve USDC for deposit
  4. Deposit USDC into the vault
  5. Set target allocations
  6. Check agent runner picks up the vault
  7. Wait for a rebalance trace
  8. Verify trace on-chain via /api/traces/{id}/verify
  9. Write evidence to docs/runbooks/arc-testnet-e2e-evidence.md

Modes:
  --dry-run    Check prerequisites only — no signing, no on-chain writes.
  --execute    Run the full happy path and record evidence.

Usage:
  cd backend
  python -m archimedes.scripts.verify_arc_e2e --dry-run
  python -m archimedes.scripts.verify_arc_e2e --execute --wallet <private_key>

Based on docs/specs/phase5-execution-runbook.md.
Pattern follows scripts/bootstrap_vaults.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Load env before any archimedes imports
from dotenv import load_dotenv

load_dotenv("../.env", override=True)
load_dotenv(".env", override=False)

# ── Constants ─────────────────────────────────────────────────────

ARC_CHAIN_ID = 5042002
ARC_CHAIN_ID_HEX = "0x4cef52"
ARCSCAN_BASE = "https://testnet.arcscan.app"
EVIDENCE_PATH = Path(__file__).resolve().parents[3] / "docs" / "runbooks" / "arc-testnet-e2e-evidence.md"
RUNBOOK_PATH = Path(__file__).resolve().parents[3] / "docs" / "runbooks" / "arc-testnet-e2e.md"
API_BASE = os.getenv("API_BASE", "http://localhost:8000")
POLL_INTERVAL = 10  # seconds between status checks
MAX_POLLS = 30  # 5 minutes max wait for agent rebalance


# ── Evidence writer ───────────────────────────────────────────────


class EvidenceRecorder:
    """Accumulates evidence entries and writes them to the evidence file."""

    def __init__(self):
        self.entries: list[dict[str, Any]] = []
        self.started_at = datetime.now(UTC).isoformat()
        self.wallet_address: str = ""
        self.vault_address: str = ""

    def record(self, step: str, status: str, **details: Any) -> None:
        self.entries.append(
            {
                "step": step,
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
                **details,
            }
        )

    def write(self) -> Path:
        """Write evidence to the markdown file."""
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Arc Testnet E2E Evidence",
            "",
            f"**Generated:** {self.started_at}",
            f"**Wallet:** `{self.wallet_address or 'N/A'}`",
            f"**Vault:** `{self.vault_address or 'N/A'}`",
            f"**Chain ID:** {ARC_CHAIN_ID}",
            "",
            "## Steps",
            "",
        ]
        for i, entry in enumerate(self.entries, 1):
            status_icon = "✅" if entry["status"] == "pass" else "❌" if entry["status"] == "fail" else "⏭️"
            lines.append(f"### {i}. {entry['step']} {status_icon}")
            lines.append("")
            lines.append(f"- **Timestamp:** {entry['timestamp']}")
            for key, val in entry.items():
                if key in ("step", "status", "timestamp"):
                    continue
                if isinstance(val, str) and val.startswith("0x") and len(val) >= 32:
                    # Transaction hash or address — add arcscan link
                    lines.append(f"- **{key}:** `{val[:18]}…`")
                    lines.append(f"  - [View on Arcscan]({ARCSCAN_BASE}/tx/{val})")
                else:
                    lines.append(f"- **{key}:** `{val}`")
            lines.append("")

        # Summary
        passed = sum(1 for e in self.entries if e["status"] == "pass")
        failed = sum(1 for e in self.entries if e["status"] == "fail")
        lines.append("---")
        lines.append("")
        lines.append(f"**Summary:** {passed} passed, {failed} failed out of {len(self.entries)} steps.")
        lines.append("")

        EVIDENCE_PATH.write_text("\n".join(lines), encoding="utf-8")
        return EVIDENCE_PATH


# ── Dry-run checks ────────────────────────────────────────────────


def check_prerequisites() -> list[str]:
    """Return a list of issues found. Empty = all good."""
    issues: list[str] = []

    # RPC URL
    rpc = os.getenv("RPC") or os.getenv("ARC_ARC_RPC_URL", "")
    if not rpc:
        issues.append("RPC URL not set. Set RPC in .env or run `arc-canteen login`.")

    # Wallet
    wallet_key = os.getenv("DEV_WALLET_PRIVATE_KEY") or os.getenv("ARC_AGENT_PRIVATE_KEY", "")
    if not wallet_key:
        issues.append("No wallet private key found. Set DEV_WALLET_PRIVATE_KEY or ARC_AGENT_PRIVATE_KEY in .env.")

    # VaultFactory address
    vf = os.getenv("VAULT_FACTORY_ADDRESS") or os.getenv("ARC_VAULT_FACTORY_ADDRESS", "")
    if not vf:
        issues.append("VAULT_FACTORY_ADDRESS not set in .env.")

    # USDC address
    usdc = os.getenv("ARC_USDC_ADDRESS", "0x3600000000000000000000000000000000000000")
    if not usdc:
        issues.append("USDC address not configured.")

    # ReasoningTraceRegistry address
    rtr = os.getenv("REASONING_TRACE_REGISTRY_ADDRESS") or os.getenv("ARC_REASONING_TRACE_REGISTRY_ADDRESS", "")
    if not rtr:
        issues.append("REASONING_TRACE_REGISTRY_ADDRESS not set in .env.")

    # Backend health
    # (not a hard requirement for dry-run, just informational)

    return issues


def print_dry_run_report(issues: list[str]) -> None:
    """Print a clear dry-run report."""
    print("=" * 60)
    print("  ARC TESTNET E2E SMOKE TEST — DRY RUN")
    print("=" * 60)
    print()

    rpc = os.getenv("RPC") or os.getenv("ARC_ARC_RPC_URL", "(not set)")
    wallet_addr = os.getenv("DEV_WALLET_ADDRESS") or os.getenv("ARC_AGENT_ADDRESS", "(not set)")
    vf = os.getenv("VAULT_FACTORY_ADDRESS") or os.getenv("ARC_VAULT_FACTORY_ADDRESS", "(not set)")
    rtr = os.getenv("REASONING_TRACE_REGISTRY_ADDRESS") or os.getenv(
        "ARC_REASONING_TRACE_REGISTRY_ADDRESS", "(not set)"
    )
    api = API_BASE

    print("Configuration:")
    print(f"  RPC URL:       {rpc[:50]}{'…' if len(rpc) > 50 else ''}")
    print(f"  Wallet:        {wallet_addr}")
    print(f"  VaultFactory:  {vf}")
    print(f"  TraceRegistry: {rtr}")
    print(f"  Backend API:   {api}")
    print(f"  Chain ID:      {ARC_CHAIN_ID} ({ARC_CHAIN_ID_HEX})")
    print()

    if issues:
        print(f"❌ {len(issues)} issue(s) found:")
        for issue in issues:
            print(f"  • {issue}")
        print()
        print("Next steps to fix:")
        print("  1. Set RPC in .env (from `arc-canteen login`)")
        print("  2. Set DEV_WALLET_PRIVATE_KEY in .env (fresh dev wallet)")
        print("  3. Set VAULT_FACTORY_ADDRESS in .env")
        print("  4. Set REASONING_TRACE_REGISTRY_ADDRESS in .env")
        print("  5. Ensure docker compose stack is running: `docker compose up -d`")
    else:
        print("✅ All prerequisites met. Ready to execute.")
        print()
        print("The following steps will run with --execute:")
        print("  1. Connect to Arc testnet, verify chain ID")
        print("  2. Create a vault via VaultFactory")
        print("  3. Approve USDC for vault deposit")
        print("  4. Deposit 10 USDC into vault")
        print("  5. Set target allocations (sSPY 60%, USDC 40%)")
        print("  6. Wait for agent runner to pick up vault (~60s)")
        print("  7. Wait for rebalance trace")
        print("  8. Verify trace on-chain via /api/traces/{id}/verify")
        print("  9. Write evidence to docs/runbooks/arc-testnet-e2e-evidence.md")
        print()
        print("Run with:")
        print("  python -m archimedes.scripts.verify_arc_e2e --execute --wallet <KEY>")
        print("  python -m archimedes.scripts.verify_arc_e2e --execute  # uses .env key")


# ── Execute mode ──────────────────────────────────────────────────


async def execute_smoke_test(wallet_key: str | None = None) -> None:
    """Run the full E2E smoke test and record evidence."""
    from web3 import AsyncWeb3
    from web3.middleware import ExtraDataToPOAMiddleware

    evidence = EvidenceRecorder()
    print("=" * 60)
    print("  ARC TESTNET E2E SMOKE TEST — EXECUTE")
    print("=" * 60)
    print()

    # ── Step 1: Connect to Arc testnet ─────────────────────────────
    print("📋 Step 1: Connecting to Arc testnet...")
    rpc = os.getenv("RPC") or os.getenv("ARC_ARC_RPC_URL", "")
    if not rpc:
        evidence.record("Connect to RPC", "fail", error="RPC URL not configured")
        evidence.write()
        print("  ❌ RPC URL not configured. Aborting.")
        sys.exit(1)

    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    try:
        chain_id = await w3.eth.chain_id
        if chain_id != ARC_CHAIN_ID:
            evidence.record("Connect to RPC", "fail", error=f"Chain ID {chain_id} != expected {ARC_CHAIN_ID}")
            evidence.write()
            print(f"  ❌ Wrong chain: got {chain_id}, expected {ARC_CHAIN_ID}")
            sys.exit(1)
        block_number = await w3.eth.block_number
        evidence.record("Connect to RPC", "pass", chain_id=chain_id, block_number=block_number)
        print(f"  ✅ Connected. Chain ID: {chain_id}, Block: {block_number}")
    except Exception as e:
        evidence.record("Connect to RPC", "fail", error=str(e))
        evidence.write()
        print(f"  ❌ Connection failed: {e}")
        sys.exit(1)

    # ── Step 2: Setup wallet ───────────────────────────────────────
    print("📋 Step 2: Setting up wallet...")
    key = wallet_key or os.getenv("DEV_WALLET_PRIVATE_KEY") or os.getenv("ARC_AGENT_PRIVATE_KEY", "")
    if not key:
        evidence.record("Setup wallet", "fail", error="No wallet private key provided")
        evidence.write()
        print("  ❌ No wallet key. Use --wallet or set DEV_WALLET_PRIVATE_KEY.")
        sys.exit(1)

    from eth_account import Account

    account = Account.from_key(key)
    wallet_addr = account.address
    evidence.wallet_address = wallet_addr
    print(f"  ✅ Wallet: {wallet_addr}")

    # Check ETH balance for gas
    eth_balance = await w3.eth.get_balance(wallet_addr)
    eth_balance_arc = w3.from_wei(eth_balance, "ether")
    evidence.record("Setup wallet", "pass", address=wallet_addr, eth_balance=f"{eth_balance_arc:.4f} ARC ETH")
    print(f"  ✅ ETH balance: {eth_balance_arc:.4f}")

    # ── Step 3: Check USDC balance ─────────────────────────────────
    print("📋 Step 3: Checking USDC balance...")
    usdc_address = os.getenv("ARC_USDC_ADDRESS", "0x3600000000000000000000000000000000000000")

    # Load ERC20 ABI (just balanceOf + approve + transfer)
    erc20_abi = json.loads(
        '[{"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},{"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"to","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},{"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"}]'
    )

    usdc = w3.eth.contract(
        address=w3.to_checksum_address(usdc_address),
        abi=erc20_abi,
    )
    usdc_balance = await usdc.functions.balanceOf(w3.to_checksum_address(wallet_addr)).call()
    usdc_balance_human = usdc_balance / 1e6
    evidence.record("Check USDC balance", "pass", balance=f"{usdc_balance_human:.2f} USDC")
    print(f"  ✅ USDC balance: {usdc_balance_human:.2f}")

    if usdc_balance_human < 10:
        print("  ⚠️  USDC balance low. Get testnet USDC from faucet.circle.com")

    # ── Step 4: Load VaultFactory ABI + create vault ───────────────
    print("📋 Step 4: Creating vault via VaultFactory...")
    vf_address = os.getenv("VAULT_FACTORY_ADDRESS") or os.getenv("ARC_VAULT_FACTORY_ADDRESS", "")
    if not vf_address:
        evidence.record("Create vault", "fail", error="VAULT_FACTORY_ADDRESS not set")
        evidence.write()
        print("  ❌ VaultFactory address not configured.")
        sys.exit(1)

    abi_dir = Path(__file__).resolve().parents[3] / "contracts" / "abis"
    with open(abi_dir / "IVaultFactory.json") as f:
        vf_abi = json.load(f)

    vault_factory = w3.eth.contract(
        address=w3.to_checksum_address(vf_address),
        abi=vf_abi,
    )

    # Check existing vaults
    existing_vaults = await vault_factory.functions.getVaults().call()
    print(f"  ℹ️  Existing vaults: {len(existing_vaults)}")

    # Build createVault tx
    nonce = await w3.eth.get_transaction_count(wallet_addr)
    vault_name = f"E2E Test Vault {datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
    vault_symbol = "e2eV"

    try:
        tx = await vault_factory.functions.createVault(
            vault_name,
            vault_symbol,
            0,  # management_fee_bps
            0,  # performance_fee_bps
            True,  # agent_assisted
        ).build_transaction(
            {
                "from": wallet_addr,
                "nonce": nonce,
                "chainId": ARC_CHAIN_ID,
                "gas": 2_000_000,
                "gasPrice": await w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        tx_hash = signed.raw_transaction.hex()
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        print(f"  📤 TX sent: {tx_hash[:18]}…")

        receipt = await w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            evidence.record("Create vault", "fail", tx_hash=tx_hash, error="Transaction reverted")
            evidence.write()
            print(f"  ❌ TX reverted. Hash: {tx_hash}")
            sys.exit(1)

        # Find VaultCreated event
        new_vaults = await vault_factory.functions.getVaults().call()
        vault_address = None
        for v in new_vaults:
            if v not in existing_vaults:
                vault_address = v
                break
        if not vault_address and new_vaults:
            vault_address = new_vaults[-1]  # fallback: last vault

        evidence.vault_address = vault_address or ""
        evidence.record(
            "Create vault",
            "pass",
            tx_hash=tx_hash,
            vault_address=vault_address or "unknown",
            block_number=receipt["blockNumber"],
        )
        print(f"  ✅ Vault created: {vault_address}")
        print(f"     Arcscan: {ARCSCAN_BASE}/tx/{tx_hash}")
    except Exception as e:
        evidence.record("Create vault", "fail", error=str(e))
        evidence.write()
        print(f"  ❌ Failed: {e}")
        sys.exit(1)

    if not vault_address:
        evidence.record("Find vault address", "fail", error="Could not find new vault address")
        evidence.write()
        print("  ❌ Could not find new vault address.")
        sys.exit(1)

    # ── Step 5: Approve USDC for vault ─────────────────────────────
    print("📋 Step 5: Approving USDC for vault deposit...")
    deposit_amount = 10  # USDC
    deposit_amount_raw = int(deposit_amount * 1e6)

    try:
        nonce = await w3.eth.get_transaction_count(wallet_addr)
        tx = await usdc.functions.approve(
            w3.to_checksum_address(vault_address),
            deposit_amount_raw,
        ).build_transaction(
            {
                "from": wallet_addr,
                "nonce": nonce,
                "chainId": ARC_CHAIN_ID,
                "gas": 100_000,
                "gasPrice": await w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        approve_hash = signed.raw_transaction.hex()
        if not approve_hash.startswith("0x"):
            approve_hash = "0x" + approve_hash
        receipt = await w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)
        evidence.record("Approve USDC", "pass" if receipt["status"] == 1 else "fail", tx_hash=approve_hash)
        print(f"  ✅ Approved {deposit_amount} USDC. TX: {approve_hash[:18]}…")
    except Exception as e:
        evidence.record("Approve USDC", "fail", error=str(e))
        evidence.write()
        print(f"  ❌ Approve failed: {e}")
        sys.exit(1)

    # ── Step 6: Deposit into vault ─────────────────────────────────
    print("📋 Step 6: Depositing USDC into vault...")
    with open(abi_dir / "IVault.json") as f:
        vault_abi = json.load(f)
    vault_contract = w3.eth.contract(
        address=w3.to_checksum_address(vault_address),
        abi=vault_abi,
    )

    try:
        nonce = await w3.eth.get_transaction_count(wallet_addr)
        tx = await vault_contract.functions.deposit(
            deposit_amount_raw,
            w3.to_checksum_address(wallet_addr),
        ).build_transaction(
            {
                "from": wallet_addr,
                "nonce": nonce,
                "chainId": ARC_CHAIN_ID,
                "gas": 300_000,
                "gasPrice": await w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        deposit_hash = signed.raw_transaction.hex()
        if not deposit_hash.startswith("0x"):
            deposit_hash = "0x" + deposit_hash
        receipt = await w3.eth.wait_for_transaction_receipt(deposit_hash, timeout=120)
        evidence.record(
            "Deposit USDC",
            "pass" if receipt["status"] == 1 else "fail",
            tx_hash=deposit_hash,
            amount=f"{deposit_amount} USDC",
        )
        print(f"  ✅ Deposited {deposit_amount} USDC. TX: {deposit_hash[:18]}…")
    except Exception as e:
        evidence.record("Deposit USDC", "fail", error=str(e))
        evidence.write()
        print(f"  ❌ Deposit failed: {e}")
        sys.exit(1)

    # ── Step 7: Set target allocations ──────────────────────────────
    print("📋 Step 7: Setting target allocations...")
    sspy_address = os.getenv("ARC_SSPY_ADDRESS", "0x6fea38dedea0c6bb66ce93e5383c34385d8b889f")

    # Simple allocation: 60% sSPY, 40% USDC
    try:
        nonce = await w3.eth.get_transaction_count(wallet_addr)
        tx = await vault_contract.functions.setTargetAllocations(
            [w3.to_checksum_address(sspy_address), w3.to_checksum_address(usdc_address)],
            [6000, 4000],  # 60% sSPY, 40% USDC (in basis points)
        ).build_transaction(
            {
                "from": wallet_addr,
                "nonce": nonce,
                "chainId": ARC_CHAIN_ID,
                "gas": 200_000,
                "gasPrice": await w3.eth.gas_price,
            }
        )
        signed = account.sign_transaction(tx)
        alloc_hash = signed.raw_transaction.hex()
        if not alloc_hash.startswith("0x"):
            alloc_hash = "0x" + alloc_hash
        receipt = await w3.eth.wait_for_transaction_receipt(alloc_hash, timeout=60)
        evidence.record(
            "Set target allocations",
            "pass" if receipt["status"] == 1 else "fail",
            tx_hash=alloc_hash,
            allocation="60% sSPY / 40% USDC",
        )
        print(f"  ✅ Allocations set. TX: {alloc_hash[:18]}…")
    except Exception as e:
        evidence.record("Set target allocations", "fail", error=str(e))
        # Non-fatal — agent can still rebalance
        print(f"  ⚠️  Set allocations failed (non-fatal): {e}")

    # ── Step 8: Verify vault state ─────────────────────────────────
    print("📋 Step 8: Verifying vault state...")
    try:
        total_assets = await vault_contract.functions.totalAssets().call()
        holdings = await vault_contract.functions.getHoldings().call()
        evidence.record("Verify vault state", "pass", total_assets=total_assets, holdings=holdings)
        print(f"  ✅ Total assets: {total_assets / 1e6:.2f} USDC")
    except Exception as e:
        evidence.record("Verify vault state", "fail", error=str(e))
        print(f"  ⚠️  Could not read vault state: {e}")

    # ── Step 9: Check agent picks up vault ─────────────────────────
    print("📋 Step 9: Checking if agent picks up vault...")
    try:
        import urllib.request

        req = urllib.request.Request(f"{API_BASE}/api/agent/status")
        with urllib.request.urlopen(req, timeout=10) as resp:
            agent_data = json.loads(resp.read())
        agent_alive = agent_data.get("status") == "alive"
        evidence.record("Agent status", "pass" if agent_alive else "fail", status=agent_data.get("status", "unknown"))
        print(f"  ✅ Agent status: {agent_data.get('status', 'unknown')}")
    except Exception as e:
        evidence.record("Agent status", "fail", error=str(e))
        print(f"  ⚠️  Could not reach agent status endpoint: {e}")

    # ── Step 10: Wait for rebalance trace ──────────────────────────
    print("📋 Step 10: Waiting for rebalance trace (max 5 minutes)...")
    trace_id = None

    for poll in range(MAX_POLLS):
        try:
            import urllib.request

            req = urllib.request.Request(f"{API_BASE}/api/traces/?limit=5&vault={vault_address}")
            with urllib.request.urlopen(req, timeout=10) as resp:
                traces_data = json.loads(resp.read())
            traces = traces_data if isinstance(traces_data, list) else traces_data.get("traces", [])
            for t in traces:
                if t.get("vault_address", "").lower() == vault_address.lower():
                    trace_id = t.get("id") or t.get("trace_id")
                    break
            if trace_id:
                break
        except Exception:
            pass
        print(f"  ⏳ Poll {poll + 1}/{MAX_POLLS} — no trace yet...")
        await asyncio.sleep(POLL_INTERVAL)

    if trace_id:
        evidence.record("Rebalance trace found", "pass", trace_id=str(trace_id))
        print(f"  ✅ Trace found: {trace_id}")
    else:
        evidence.record(
            "Rebalance trace found", "fail", note="No trace found within timeout. Agent may not have ticked yet."
        )
        print("  ⚠️  No trace found within timeout.")

    # ── Step 11: Verify trace on-chain ─────────────────────────────
    if trace_id:
        print("📋 Step 11: Verifying trace on-chain...")
        try:
            import urllib.request

            req = urllib.request.Request(f"{API_BASE}/api/traces/{trace_id}/verify")
            with urllib.request.urlopen(req, timeout=15) as resp:
                verify_data = json.loads(resp.read())
            is_verified = verify_data.get("is_verified", False)
            anchor_tx = verify_data.get("anchor_tx", "")
            block_num = verify_data.get("block_number", "")
            evidence.record(
                "Verify trace on-chain",
                "pass" if is_verified else "fail",
                is_verified=is_verified,
                anchor_tx=anchor_tx,
                block_number=block_num,
                details=str(verify_data.get("details", "")),
            )
            print(f"  {'✅' if is_verified else '❌'} Trace verified: {is_verified}")
            if anchor_tx:
                print(f"     Anchor TX: {anchor_tx[:18]}…")
                print(f"     Arcscan: {ARCSCAN_BASE}/tx/{anchor_tx}")
        except Exception as e:
            evidence.record("Verify trace on-chain", "fail", error=str(e))
            print(f"  ⚠️  Verify endpoint error: {e}")
    else:
        evidence.record("Verify trace on-chain", "fail", note="Skipped — no trace found")

    # ── Write evidence ─────────────────────────────────────────────
    path = evidence.write()
    print()
    print("=" * 60)
    passed = sum(1 for e in evidence.entries if e["status"] == "pass")
    failed = sum(1 for e in evidence.entries if e["status"] == "fail")
    print(f"  RESULT: {passed} passed, {failed} failed ({len(evidence.entries)} total)")
    print(f"  Evidence: {path}")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Arc testnet E2E smoke test — vault lifecycle verification",
    )
    parser.add_argument("--dry-run", action="store_true", help="Check prerequisites only — no signing")
    parser.add_argument("--execute", action="store_true", help="Run the full happy path")
    parser.add_argument("--wallet", type=str, default=None, help="Wallet private key (or set DEV_WALLET_PRIVATE_KEY)")
    parser.add_argument("--api-base", type=str, default=None, help="Backend API base URL")

    args = parser.parse_args()

    if args.api_base:
        global API_BASE
        API_BASE = args.api_base

    if args.dry_run:
        issues = check_prerequisites()
        print_dry_run_report(issues)
        if issues:
            sys.exit(1)
        return

    if args.execute:
        asyncio.run(execute_smoke_test(wallet_key=args.wallet))
        return

    # Default: show help
    parser.print_help()
    print()
    print("Examples:")
    print("  python -m archimedes.scripts.verify_arc_e2e --dry-run")
    print("  python -m archimedes.scripts.verify_arc_e2e --execute")
    print("  python -m archimedes.scripts.verify_arc_e2e --execute --wallet 0xKEY")


if __name__ == "__main__":
    main()
