#!/usr/bin/env python3
"""Plan (and, for the owner only, execute) the ERC-8004 identity registration on Arc (#1527).

WHAT THIS DOES
    Builds the ``register(string agentURI)`` call against the ERC-8004 IdentityRegistry
    named in ``ui/public/.well-known/agent.json``. ``--plan`` (the default) is READ-ONLY:
    it prints the calldata, decodes it back, checks the registry and chain over JSON-RPC,
    and estimates gas. Nothing is signed and nothing is sent.

WHAT THIS DOES NOT DO
    It does not make Archimedes an ERC-8004 agent. Registration mints an ERC-721 to the
    sender, so it needs the OWNER's key — which this repo does not have and must not have.
    Until a human runs ``--execute`` and a ``Registered`` event exists, every discovery
    surface keeps saying ``status: registration_pending`` and that is the truth.

THE STANDARD (https://eips.ethereum.org/EIPS/eip-8004)
    struct MetadataEntry { string metadataKey; bytes metadataValue; }
    function register(string agentURI, MetadataEntry[] calldata metadata) returns (uint256 agentId)
    function register(string agentURI)                                    returns (uint256 agentId)
    function register()                                                   returns (uint256 agentId)
    event    Registered(uint256 indexed agentId, string agentURI, address indexed owner)

    ``agentURI`` must resolve to the agent registration file. Ours is published at
    ``ui/public/.well-known/agent-registration.json`` and typed
    ``https://eips.ethereum.org/EIPS/eip-8004#registration-v1``. Because the endpoint
    domain serves it, it doubles as the EIP's optional domain-control proof.

NO RESTATED CONSTANTS
    The registry address, chain and agentURI are READ from the shipped agent card, which
    is the same block ``GET /api/agent/manifest`` serves. A value changed in one place and
    not the other fails ``backend/tests/test_erc8004_identity.py``, not here at 3am.

USAGE
    # read-only: calldata + registry/chain checks + gas estimate
    python scripts/register_erc8004_identity.py --plan
    python scripts/register_erc8004_identity.py --plan --from 0xOwner...
    python scripts/register_erc8004_identity.py --plan --offline      # no RPC at all

    # owner only — NOT run by CI, agents, or the backend:
    OWNER_PRIVATE_KEY=0x... python scripts/register_erc8004_identity.py --execute
    python scripts/register_erc8004_identity.py --execute \
        --circle-wallet-id <id> --circle-entity-secret-ciphertext <ct>   # CIRCLE_API_KEY in env

AFTER A SUCCESSFUL --execute
    The script prints the minted agentId and the exact JSON edits to land in the same
    commit as the transaction hash: the ``registrations`` entry for both registration
    files, and ``_ERC8004_AGENT_ID`` / ``_ERC8004_TOKEN_URI`` in
    ``backend/archimedes/api/agent_manifest_routes.py``. Only then may any surface say
    "registered" — the tests enforce that ordering.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import keccak

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_CARD = REPO_ROOT / "ui" / "public" / ".well-known" / "agent.json"
REGISTRATION_FILE = REPO_ROOT / "ui" / "public" / ".well-known" / "agent-registration.json"

# ERC-8004 IdentityRegistry, from the EIP's interface. Kept as signature strings and
# hashed at runtime rather than pasted selectors, so a typo cannot silently produce
# well-formed calldata for a function that does not exist.
REGISTER_STRING_SIG = "register(string)"
REGISTERED_EVENT_SIG = "Registered(uint256,string,address)"

DEFAULT_RPC = "https://rpc.testnet.arc.network"
CIRCLE_API_BASE = "https://api.circle.com/v1/w3s"

# Gas headroom over the estimate. An ERC-721 mint plus a string SSTORE is not variable
# enough to justify more, and a fat multiplier on a chain where USDC IS gas is real money.
GAS_MULTIPLIER = 1.25


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def topic(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


# ── inputs, read from the shipped discovery surface ───────────────────────────────────


def load_identity_block() -> dict:
    """The ``erc8004`` block from the static agent card — the SSOT for this script."""
    if not AGENT_CARD.exists():
        raise SystemExit(f"✗ agent card not found: {AGENT_CARD}")
    card = json.loads(AGENT_CARD.read_text(encoding="utf-8"))
    block = card.get("erc8004")
    if not isinstance(block, dict):
        raise SystemExit(f"✗ {AGENT_CARD} has no erc8004 block — nothing to register against.")
    for key in ("chain", "identityRegistry", "registrationUri", "status"):
        if not block.get(key):
            raise SystemExit(f"✗ erc8004 block is missing {key!r}.")
    return block


def chain_id_from_caip2(chain: str) -> int:
    """``eip155:5042002`` → ``5042002``. Anything else is refused, not guessed."""
    namespace, _, reference = chain.partition(":")
    if namespace != "eip155" or not reference.isdigit():
        raise SystemExit(f"✗ erc8004.chain must be an eip155 CAIP-2 id, got {chain!r}.")
    return int(reference)


def already_registered(block: dict) -> bool:
    return block.get("agentId") is not None


# ── calldata ──────────────────────────────────────────────────────────────────────────


def build_register_calldata(agent_uri: str) -> bytes:
    """ABI-encoded ``register(string agentURI)``.

    The no-metadata overload on purpose: every field an ERC-8004 consumer needs is in
    the registration file the URI resolves to, so on-chain MetadataEntry rows would be a
    second copy of the same facts with its own way to go stale.
    """
    return selector(REGISTER_STRING_SIG) + abi_encode(["string"], [agent_uri])


def decode_register_calldata(calldata: bytes) -> str:
    """Decode our own calldata back. A plan you cannot read back is not a plan."""
    if calldata[:4] != selector(REGISTER_STRING_SIG):
        raise ValueError("calldata does not start with the register(string) selector")
    (agent_uri,) = abi_decode(["string"], calldata[4:])
    return agent_uri


# ── JSON-RPC (read-only helpers) ──────────────────────────────────────────────────────


def rpc(client: httpx.Client, url: str, method: str, params: list) -> object:
    resp = client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    resp.raise_for_status()
    body = resp.json()
    if "error" in body:
        raise RuntimeError(f"{method} → {body['error']}")
    return body.get("result")


def owner_address_from_env() -> str | None:
    """Address for ``OWNER_PRIVATE_KEY`` if it is set — the key itself is never printed."""
    key = os.environ.get("OWNER_PRIVATE_KEY", "").strip()
    if not key:
        return None
    from eth_account import Account

    try:
        return Account.from_key(key).address
    except (ValueError, TypeError):
        raise SystemExit("✗ OWNER_PRIVATE_KEY is not a valid private key.") from None


def preflight(client: httpx.Client, rpc_url: str, registry: str, want_chain_id: int) -> list[str]:
    """Chain-id and code-at-address checks. Returns human problem lines (empty = clean).

    Both are read-only and both are worth a round-trip: registering against an address
    with no code, or on the wrong chain, burns real gas to accomplish nothing.
    """
    problems: list[str] = []
    chain_id = int(str(rpc(client, rpc_url, "eth_chainId", [])), 16)
    print(f"  rpc chain id:      {chain_id}")
    if chain_id != want_chain_id:
        problems.append(f"RPC reports chain {chain_id}, the agent card says {want_chain_id}")

    code = str(rpc(client, rpc_url, "eth_getCode", [registry, "latest"]) or "0x")
    size = max(len(code) - 2, 0) // 2
    print(f"  registry bytecode: {size} bytes")
    if size == 0:
        problems.append(
            f"no contract code at {registry} on chain {chain_id} — the IdentityRegistry is "
            "either not deployed here or the address is wrong; registering would send USDC "
            "gas to an EOA and mint nothing"
        )
    return problems


def estimate_gas(client: httpx.Client, rpc_url: str, sender: str, registry: str, calldata: bytes) -> int:
    return int(
        str(rpc(client, rpc_url, "eth_estimateGas", [{"from": sender, "to": registry, "data": "0x" + calldata.hex()}])),
        16,
    )


# ── plan ──────────────────────────────────────────────────────────────────────────────


def plan(args: argparse.Namespace) -> int:
    block = load_identity_block()
    registry = block["identityRegistry"]
    chain_id = chain_id_from_caip2(block["chain"])
    agent_uri = args.agent_uri or block["registrationUri"]
    calldata = build_register_calldata(agent_uri)

    print("ERC-8004 identity registration — PLAN (read-only, nothing signed, nothing sent)")
    print(f"  status now:        {block['status']}  (agentId={block['agentId']!r}, tokenURI={block['tokenURI']!r})")
    print(f"  chain:             {block['chain']}  (chain id {chain_id})")
    print(f"  identityRegistry:  {registry}")
    print(f"  function:          {REGISTER_STRING_SIG}   selector 0x{selector(REGISTER_STRING_SIG).hex()}")
    print(f"  agentURI:          {agent_uri}")
    print(f"  calldata:          0x{calldata.hex()}")
    print(f"  decodes back to:   {decode_register_calldata(calldata)!r}")

    if not REGISTRATION_FILE.exists():
        print(f"  ! registration file missing at {REGISTRATION_FILE} — agentURI would resolve to nothing.")
    else:
        doc = json.loads(REGISTRATION_FILE.read_text(encoding="utf-8"))
        print(f"  registration file: {REGISTRATION_FILE.relative_to(REPO_ROOT)}  type={doc.get('type')!r}")

    if already_registered(block):
        print(
            f"  ! the agent card already carries agentId {block['agentId']} — registering again mints a SECOND identity."
        )

    if args.offline:
        print("  gas estimate:      skipped (--offline)")
        return 0

    sender = args.sender or owner_address_from_env()
    problems: list[str] = []
    try:
        with httpx.Client(timeout=30.0) as client:
            print(f"  rpc:               {args.rpc}")
            problems = preflight(client, args.rpc, registry, chain_id)
            if sender is None:
                print("  gas estimate:      unavailable — no sender (pass --from 0x..., or set OWNER_PRIVATE_KEY)")
            elif problems:
                print(f"  gas estimate:      not attempted — preflight failed (sender would be {sender})")
            else:
                gas = estimate_gas(client, args.rpc, sender, registry, calldata)
                gas_price = int(str(rpc(client, args.rpc, "eth_gasPrice", [])), 16)
                # USDC is the native gas token on Arc, 18-decimal at the RPC level.
                cost = gas * gas_price / 10**18
                print(f"  sender:            {sender}")
                print(f"  gas estimate:      {gas}  @ gasPrice {gas_price}  ≈ {cost:.6f} USDC")
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        # An unreachable or unhealthy RPC is a real, reportable condition — it must not
        # masquerade as a clean plan.
        print(f"  ! RPC checks could not complete: {exc}")
        print("  ! calldata above is still correct — it is built locally — but the registry, chain")
        print("    and gas were NOT verified. Re-run with a reachable --rpc before executing.")
        return 1

    for problem in problems:
        print(f"  ✗ {problem}")
    if problems:
        print("Plan is NOT safe to execute. Fix the above first.")
        return 1
    print("Plan looks consistent. Execution is a separate, owner-only act: --execute")
    return 0


# ── execute (owner only) ──────────────────────────────────────────────────────────────


def execute_with_private_key(args: argparse.Namespace, registry: str, chain_id: int, calldata: bytes) -> int:
    from eth_account import Account

    acct = Account.from_key(os.environ["OWNER_PRIVATE_KEY"].strip())
    with httpx.Client(timeout=60.0) as client:
        problems = preflight(client, args.rpc, registry, chain_id)
        for problem in problems:
            print(f"✗ {problem}")
        if problems:
            return 2
        gas = estimate_gas(client, args.rpc, acct.address, registry, calldata)
        gas_price = int(str(rpc(client, args.rpc, "eth_gasPrice", [])), 16)
        nonce = int(str(rpc(client, args.rpc, "eth_getTransactionCount", [acct.address, "pending"])), 16)
        tx = {
            "to": registry,
            "value": 0,
            "data": "0x" + calldata.hex(),
            "gas": int(gas * GAS_MULTIPLIER),
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": chain_id,
        }
        print(f"sending register() from {acct.address} (gas {tx['gas']}, nonce {nonce})…")
        signed = acct.sign_transaction(tx)
        tx_hash = str(rpc(client, args.rpc, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()]))
        print(f"tx: {tx_hash}")
        print("Poll eth_getTransactionReceipt for the Registered log; then run --print-followup with the agentId.")
    return 0


def execute_with_circle(args: argparse.Namespace, registry: str, calldata: bytes) -> int:
    """Circle dev-controlled wallet path — the same contract-execution API the marketplace uses."""
    api_key = os.environ.get("CIRCLE_API_KEY", "").strip()
    if not api_key:
        print("✗ CIRCLE_API_KEY is not set.")
        return 2
    if not args.circle_entity_secret_ciphertext:
        print("✗ --circle-entity-secret-ciphertext is required for the Circle path.")
        return 2
    body = {
        "walletId": args.circle_wallet_id,
        "contractAddress": registry,
        "callData": "0x" + calldata.hex(),
        "entitySecretCiphertext": args.circle_entity_secret_ciphertext,
        "idempotencyKey": args.idempotency_key,
        "feeLevel": "MEDIUM",
    }
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            f"{CIRCLE_API_BASE}/developer/transactions/contractExecution",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
    # Print the raw response either way: a 4xx naming the offending field is the useful
    # output here, not an exception trace that hides it.
    print(f"Circle API {resp.status_code}: {resp.text}")
    return 0 if resp.is_success else 2


def execute(args: argparse.Namespace) -> int:
    block = load_identity_block()
    registry = block["identityRegistry"]
    chain_id = chain_id_from_caip2(block["chain"])
    agent_uri = args.agent_uri or block["registrationUri"]
    calldata = build_register_calldata(agent_uri)

    if already_registered(block) and not args.allow_second_identity:
        print(f"✗ agent card already carries agentId {block['agentId']} — refusing to mint a second")
        print("  identity. Pass --allow-second-identity only if that is genuinely what you want.")
        return 2

    have_key = bool(os.environ.get("OWNER_PRIVATE_KEY", "").strip())
    have_circle = bool(args.circle_wallet_id)
    if have_key == have_circle:
        print("✗ --execute needs exactly ONE owner credential:")
        print("    OWNER_PRIVATE_KEY=0x…                      (env var, never a CLI argument)")
        print("    --circle-wallet-id <id> --circle-entity-secret-ciphertext <ct>   (CIRCLE_API_KEY in env)")
        print("  Both present, or neither, is ambiguous about who signs — refusing.")
        return 2

    print(f"EXECUTE: {REGISTER_STRING_SIG} → {registry} on chain {chain_id}")
    print(f"  agentURI: {agent_uri}")
    if have_key:
        return execute_with_private_key(args, registry, chain_id, calldata)
    return execute_with_circle(args, registry, calldata)


# ── follow-up ─────────────────────────────────────────────────────────────────────────


def print_followup(agent_id: int) -> int:
    """The exact edits that turn a real receipt into an honest 'registered' claim."""
    block = load_identity_block()
    entry = {"agentId": agent_id, "agentRegistry": f"{block['chain']}:{block['identityRegistry']}"}
    print("Land these in ONE commit, with the transaction hash in the message:")
    print()
    print("1. ui/public/.well-known/agent-registration.json AND agent-registration.domain.json —")
    print('   replace "registrations": [] with:')
    print("   " + json.dumps([entry], indent=2).replace("\n", "\n   "))
    print()
    print("2. backend/archimedes/api/agent_manifest_routes.py —")
    print(f"   _ERC8004_AGENT_ID = {agent_id}")
    print(f"   _ERC8004_TOKEN_URI = {block['registrationUri']!r}")
    print()
    print("3. ui/public/.well-known/agent.json — regenerate the erc8004 block from")
    print("   erc8004_identity() so the card and the served manifest stay identical.")
    print()
    print(f"   Registered event topic0: {topic(REGISTERED_EVENT_SIG)}")
    print("   backend/tests/test_erc8004_identity.py enforces that all of these move together.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true", help="read-only: calldata, registry/chain checks, gas (default)")
    mode.add_argument("--execute", action="store_true", help="OWNER ONLY: sign and send register()")
    mode.add_argument("--print-followup", type=int, metavar="AGENT_ID", help="print the post-registration edits")
    ap.add_argument("--rpc", default=os.environ.get("RPC") or os.environ.get("ARC_ARC_RPC_URL") or DEFAULT_RPC)
    ap.add_argument("--from", dest="sender", default=None, help="address to estimate gas from (plan only)")
    ap.add_argument(
        "--agent-uri", default=None, help="override the agentURI (default: the agent card's registrationUri)"
    )
    ap.add_argument("--offline", action="store_true", help="plan only: build calldata, make no RPC calls")
    ap.add_argument("--circle-wallet-id", default=None)
    ap.add_argument("--circle-entity-secret-ciphertext", default=None)
    ap.add_argument("--idempotency-key", default=None)
    ap.add_argument(
        "--allow-second-identity", action="store_true", help="permit --execute when an agentId already exists"
    )
    args = ap.parse_args()

    if args.print_followup is not None:
        return print_followup(args.print_followup)
    if args.execute:
        return execute(args)
    return plan(args)


if __name__ == "__main__":
    sys.exit(main())
