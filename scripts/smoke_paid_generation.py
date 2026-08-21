"""End-to-end PAID generation smoke test against a live deployment.

First-execution-is-post-merge guard for the x402 settle path (#834 flip):
the generation paywall's verify+settle branch has never run outside
PAYMENTS_DRY_RUN. Run this BEFORE flipping dry-run off (expect exit 2:
header accepted, nothing settled) and AGAIN after the flip (expect exit 0:
settled, receipt returned). Any exit 1 blocks the flip.

Usage (secrets via env only — never on the command line):

    export ARCHIMEDES_EMAIL=...  ARCHIMEDES_PASSWORD=...   # smoke account
    export AGENT_WALLET_KEY=0x...   # DEV wallet key, faucet-funded, never a real-funds wallet
    python scripts/smoke_paid_generation.py --base-url https://archimedes-arc.com

Flow: sign in -> link the dev wallet (agent_journey's step_auth /
step_wallet_link, #1293's headless path) -> check the payer's Circle
Gateway balance, `deposit` if short (approve+deposit, gas in native USDC)
-> POST /api/generate/start expecting 402 -> parse PAYMENT-REQUIRED ->
sign the GatewayWalletBatched TransferWithAuthorization with the dev key
(circlekit PrivateKeySigner — the exact buyer flow marketplace
`charge()` uses) -> retry with Payment-Signature -> classify the result.

Exit codes:  0 settled (PAYMENT-RESPONSE receipt present — live mode works)
             2 accepted WITHOUT receipt (server is in PAYMENTS_DRY_RUN)
             1 any failure (auth, link, deposit, 402 parse, sign, retry)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from agent_journey import step_auth, step_wallet_link

_KEY_ENV = "AGENT_WALLET_KEY"
_BODY = {
    "brief": {"intent": "smoke: paid generation settle path", "risk_appetite": "moderate"},
    "n_candidates": 1,
}


def _fail(msg: str) -> int:
    print(f"  ✗ {msg}")
    return 1


async def _ensure_gateway_balance(key: str, need_units: int, deposit_usd: str) -> bool:
    """True iff the payer's Gateway balance covers `need_units` (base units,
    6 decimals — GatewayBalance.available is an int in base units), depositing
    if short."""
    from circlekit import GatewayClient

    client = GatewayClient(chain="arcTestnet", private_key=key)
    bal = await client.get_gateway_balance()
    print(f"  · gateway balance: {bal.formatted_available} available (need {need_units / 10**6:.2f})")
    if bal.available >= need_units:
        return True
    print(f"  · depositing {deposit_usd} USDC into the Gateway (approve + deposit, on-chain)…")
    result = await client.deposit(deposit_usd)
    print(f"  · deposit tx: {getattr(result, 'tx_hash', result)}")
    bal = await client.get_gateway_balance()
    print(f"  · gateway balance after deposit: {bal.formatted_available}")
    return bal.available >= need_units


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="https://archimedes-arc.com")
    ap.add_argument(
        "--deposit",
        default="4.00",
        help="USDC to deposit when the Gateway balance is short (default 4.00 = two $2 generations)",
    )
    args = ap.parse_args()

    key = os.getenv(_KEY_ENV, "").strip()
    if not key:
        return _fail(f"${_KEY_ENV} is not set (dev wallet key — faucet-funded, never real funds)")

    with httpx.Client(base_url=args.base_url, timeout=60.0, follow_redirects=True) as client:
        print(f"— smoke: paid generation against {args.base_url}")
        # Sign-in-else-sign-up: a fresh smoke account (env creds) is created on
        # first use, then signed into on every later run — the ephemeral mode
        # would mint a NEW account each run and orphan the funded wallet's link.
        if step_auth(client, args.base_url, ephemeral=False) is None:
            signup = client.post(
                "/api/auth/sign-up/email",
                json={
                    "name": "x402 smoke",
                    "email": os.environ.get("ARCHIMEDES_EMAIL", ""),
                    "password": os.environ.get("ARCHIMEDES_PASSWORD", ""),
                },
            )
            if not signup.is_success:
                return _fail(f"sign-in and sign-up both failed (HTTP {signup.status_code})")
            if step_auth(client, args.base_url, ephemeral=False) is None:
                return _fail("sign-in failed after successful sign-up")
        wallet = step_wallet_link(client, ephemeral=False)
        if wallet is None:
            return _fail("wallet link failed")

        # 1. Expect the paywall. A 202 here means the flag is OFF — that is a
        # smoke FAILURE for this script's purpose (it validates the paid path).
        r = client.post("/api/generate/start", json=_BODY)
        if r.status_code == 202:
            return _fail("got 202 with no payment challenge — GENERATION_PAYMENT_REQUIRED is off; nothing to smoke")
        if r.status_code != 402:
            return _fail(f"expected 402, got HTTP {r.status_code}: {r.text[:200]}")
        quote = (r.json().get("detail") or {}).get("quote") or {}
        print(f"  · 402 received; quote price: {quote.get('price', '?')}")

        # 2. Parse requirements exactly the way marketplace charge() does.
        from circlekit.signer import PrivateKeySigner
        from circlekit.x402 import create_payment_header, get_payment_required

        x402 = get_payment_required(
            r.headers.get("PAYMENT-REQUIRED"),
            r.text,
        )
        requirements = x402.get_gateway_option()
        resource = getattr(x402, "resource", None)  # REQUIRED by the facilitator schema (2026-08-20)
        if requirements is None:
            return _fail("402 carried no GatewayWalletBatched payment option")
        need_units = int(requirements.amount)

        # 3. Payer-side funding (the one step with on-chain writes).
        try:
            funded = asyncio.run(_ensure_gateway_balance(key, need_units, args.deposit))
        except Exception as exc:
            return _fail(f"gateway balance/deposit failed: {exc}")
        if not funded:
            return _fail("gateway balance still short after deposit")

        # 4. Sign and retry.
        signer = PrivateKeySigner(key)
        if signer.address.lower() != wallet.lower():
            print(f"  · note: payer {signer.address} != linked wallet {wallet} — payer-binding may refuse")
        header = create_payment_header(signer=signer, requirements=requirements, resource=resource)
        r2 = client.post("/api/generate/start", json=_BODY, headers={"Payment-Signature": header})
        if r2.status_code != 202:
            return _fail(f"paid retry expected 202, got HTTP {r2.status_code}: {r2.text[:300]}")

        receipt = r2.headers.get("PAYMENT-RESPONSE") or r2.headers.get("payment-response")
        job_id = r2.json().get("job_id")
        if receipt:
            print(f"  ✓ SETTLED — receipt present, job {job_id} started. Live paid path works.")
            return 0
        print(
            f"  ⚠ accepted WITHOUT settlement receipt (server PAYMENTS_DRY_RUN) — job {job_id} started, no funds moved."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
