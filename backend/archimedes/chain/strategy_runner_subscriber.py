"""Subscriber agent — receives push events from a publisher and executes
rebalances on its own vault.

The subscriber is a FastAPI backend service (not a full StrategyRunner) that:
- Receives evaluation_step, rebalance, and halt events via POST /events.
- Maintains its own vault (created at subscription time).
- Executes on-chain rebalance when a rebalance payload arrives.
- Manages its ephemeral wallet top-up lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from archimedes.chain.circle_signer import CircleSigner
from archimedes.chain.client import ChainSettings
from archimedes.chain.contracts import ContractLoader
from archimedes.chain.executor import ChainExecutor
from archimedes.services.redis_state import AgentStateStore

logger = logging.getLogger(__name__)

# ─── Env / Constants ───────────────────────────────────────────────────

SUBSCRIBER_HOST = os.getenv("SUBSCRIBER_HOST", "0.0.0.0")
SUBSCRIBER_PORT = int(os.getenv("SUBSCRIBER_PORT", "8081"))
SUBSCRIBER_WALLET_ADDRESS = os.getenv("SUBSCRIBER_WALLET_ADDRESS", "")
SUBSCRIBER_SUB_ID = os.getenv("SUBSCRIBER_SUB_ID", "")
SUBSCRIBER_VAULT_ADDRESS = os.getenv("SUBSCRIBER_VAULT_ADDRESS", "")
SUBSCRIBER_POOL_ID = os.getenv("SUBSCRIBER_POOL_ID", "")
PUBLISHER_ENDPOINT = os.getenv("PUBLISHER_ENDPOINT", "")
SUBSCRIPTION_MANAGER_ADDRESS = os.getenv("SUBSCRIPTION_MANAGER_ADDRESS", "")
INITIAL_DEPOSIT_USDC = int(os.getenv("INITIAL_DEPOSIT_USDC", "10000000"))
LOW_BALANCE_THRESHOLD = int(os.getenv("LOW_BALANCE_THRESHOLD", "1000000"))
DRY_RUN = os.getenv("AGENT_DRY_RUN", "false").lower() == "true"

PUBLISHER_REGISTRATION_RETRIES = 5
PUBLISHER_REGISTRATION_BACKOFF = 2.0  # seconds

TRACE_STUB_VERSION = "1.0.0"


# ─── Data Models ───────────────────────────────────────────────────────

class PublisherEvent(BaseModel):
    type: str = Field(..., description="evaluation_step | rebalance | halt")
    tick_id: str = Field(default="")
    step: str = Field(default="")
    reason: str = Field(default="")
    message: str = Field(default="")
    action_count: int = Field(default=0)
    trades: list[dict] = Field(default_factory=list)
    target_weights: dict[str, float] = Field(default_factory=dict)
    signal_summary: dict[str, Any] = Field(default_factory=dict)
    halted: bool = Field(default=False)


class TopUpRequest(BaseModel):
    amount_usdc_raw: int = Field(..., description="Amount in USDC raw (6 decimals)")


# ─── Subscriber Agent ──────────────────────────────────────────────────


class SubscriberAgent:
    """Receives publisher events and mirrors actions on its own vault."""

    def __init__(self):
        self.settings = ChainSettings()
        self.loader = ContractLoader()
        self.executor = ChainExecutor(loader=self.loader)
        self.circle_signer = CircleSigner()
        self.redis = AgentStateStore()

        self.subscription_manager_address = (
            SUBSCRIPTION_MANAGER_ADDRESS or self.settings.subscription_manager_address
        )

        # Identity
        self.wallet_address = SUBSCRIBER_WALLET_ADDRESS
        self.sub_id = SUBSCRIBER_SUB_ID
        self.vault_address = SUBSCRIBER_VAULT_ADDRESS
        self.pool_id = SUBSCRIBER_POOL_ID
        self.webhook_url = f"http://{SUBSCRIBER_HOST}:{SUBSCRIBER_PORT}/events"
        self.ephemeral_wallet_address = ""

        # State
        self._initialized = False

        # HTTP session
        self._http_session: aiohttp.ClientSession | None = None

    # ─── Initialization ────────────────────────────────────────────

    async def initialize(self):
        """Startup sequence: create vault, ephemeral wallet, register."""
        if self._initialized:
            return

        # 1. Create or load vault
        vault_addr = await self._load_or_create_vault()
        self.vault_address = vault_addr
        logger.info("Vault: %s", self.vault_address)

        # 2. Create ephemeral wallet via SubscriptionManager.subscribe()
        sub_id, wallet_addr = await self._create_ephemeral_wallet()
        self.sub_id = sub_id
        self.ephemeral_wallet_address = wallet_addr
        logger.info("Ephemeral wallet: %s (sub_id: %s)", wallet_addr, sub_id)

        # 3. Register with publisher
        if PUBLISHER_ENDPOINT:
            await self._register_with_publisher()

        self._initialized = True
        logger.info("Subscriber initialized")

    async def _load_or_create_vault(self) -> str:
        """Load vault address from Redis or create one."""
        redis_key = "subscriber:vault_address"
        cached = await self.redis.redis.get(redis_key)
        if cached:
            return cached.decode()

        if DRY_RUN or not self.vault_address:
            vault_addr = "0x0000000000000000000000000000000000000002"
        else:
            vault_addr = await self.executor.create_vault(
                name=f"Subscriber-{uuid.uuid4().hex[:8]}",
                symbol="SUBV",
                management_fee_bps=50,
                performance_fee_bps=500,
                agent_assisted=True,
            )

        await self.redis.redis.set(redis_key, vault_addr)
        return vault_addr

    async def _create_ephemeral_wallet(self) -> tuple[str, str]:
        """Call SubscriptionManager.subscribe() to create ephemeral wallet.

        Returns (sub_id, wallet_address).
        """
        if not self.subscription_manager_address:
            logger.warning("SubscriptionManager not configured — using placeholder")
            return self.sub_id or f"sub_{uuid.uuid4().hex}", "0x0000000000000000000000000000000000000000"

        sub_id = self.sub_id
        wallet_addr = ""

        if DRY_RUN:
            sub_id = sub_id or f"dry_sub_{uuid.uuid4().hex}"
            logger.info("DRY RUN: subscribe pool=%s deposit=%d", self.pool_id, INITIAL_DEPOSIT_USDC)
            return sub_id, "0x0000000000000000000000000000000000000000"

        contract = self.loader._contract(
            self.subscription_manager_address, "SubscriptionManager"
        )

        try:
            if self.circle_signer.is_configured:
                result = await self.circle_signer.execute_contract(
                    self.subscription_manager_address,
                    "subscribe",
                    [self.pool_id, self.webhook_url, INITIAL_DEPOSIT_USDC],
                )
                sub_id = result.get("sub_id", f"sub_{uuid.uuid4().hex}")
            else:
                tx = await contract.functions.subscribe(
                    self.pool_id, self.webhook_url, INITIAL_DEPOSIT_USDC
                ).build_transaction({
                    "from": self.settings.agent_account.address,
                    "nonce": await self._get_nonce(),
                    "gas": 300_000,
                    "gasPrice": await self._get_gas_price(),
                })
                signed = self.settings.agent_account.sign_transaction(tx)
                receipt = await self._send_raw(signed.raw_transaction)
                sub_id = f"sub_{uuid.uuid4().hex}"

            sub_data = await contract.functions.subscriptions(sub_id).call()
            wallet_addr = sub_data[2]  # ephemeral_wallet field

        except Exception as exc:
            logger.error("Failed to create subscription: %s", exc)
            sub_id = self.sub_id or f"sub_{uuid.uuid4().hex}"
            wallet_addr = "0x0000000000000000000000000000000000000000"

        return sub_id, wallet_addr

    async def _register_with_publisher(self):
        """Register this subscriber with the publisher's /subscribe endpoint."""
        if not self.sub_id:
            logger.warning("No sub_id to register")
            return

        url = f"{PUBLISHER_ENDPOINT.rstrip('/')}/subscribe"
        payload = {
            "sub_id": self.sub_id,
            "webhook_url": self.webhook_url,
            "ephemeral_wallet": self.ephemeral_wallet_address,
        }

        await self._ensure_session()
        for attempt in range(1, PUBLISHER_REGISTRATION_RETRIES + 1):
            try:
                async with self._http_session.post(
                    url, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        logger.info("Registered with publisher at %s", PUBLISHER_ENDPOINT)
                        return
                    body = await resp.text()
                    logger.warning(
                        "Publisher registration attempt %d/%d: %d %s",
                        attempt, PUBLISHER_REGISTRATION_RETRIES,
                        resp.status, body,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Publisher registration attempt %d/%d failed: %s",
                    attempt, PUBLISHER_REGISTRATION_RETRIES, exc,
                )
            if attempt < PUBLISHER_REGISTRATION_RETRIES:
                await asyncio.sleep(PUBLISHER_REGISTRATION_BACKOFF * (2 ** (attempt - 1)))

        logger.error("Failed to register with publisher after %d attempts",
                      PUBLISHER_REGISTRATION_RETRIES)

    # ─── On-chain Helpers ──────────────────────────────────────────

    async def _get_nonce(self):
        w3 = self.loader.client.w3
        return await w3.eth.get_transaction_count(self.settings.agent_account.address)

    async def _get_gas_price(self):
        w3 = self.loader.client.w3
        return await w3.eth.gas_price

    async def _send_raw(self, raw_tx: bytes):
        w3 = self.loader.client.w3
        tx_hash = await w3.eth.send_raw_transaction(raw_tx)
        return await w3.eth.wait_for_transaction_receipt(tx_hash)

    async def _ephemeral_balance(self) -> int:
        """Read ephemeral wallet balance from SubscriptionManager."""
        if not self.subscription_manager_address or not self.sub_id:
            return 0

        contract = self.loader._contract(
            self.subscription_manager_address, "SubscriptionManager"
        )
        try:
            sub_data = await contract.functions.subscriptions(self.sub_id).call()
            return sub_data[3]  # reserved_usdc field
        except Exception:
            return 0

    async def _publish_trace_stub(self, event: PublisherEvent):
        """Publish a reasoning trace stub to ReasoningTraceRegistry."""
        trace_registry = self.loader.trace_registry
        trace_id = uuid.uuid4().hex
        trace_data = json.dumps({
            "version": TRACE_STUB_VERSION,
            "type": "subscriber_mirror",
            "publisher_tick_id": event.tick_id,
            "vault": self.vault_address,
            "event_type": event.type,
        })

        try:
            if self.circle_signer.is_configured:
                await self.circle_signer.execute_contract(
                    self.settings.reasoning_trace_registry_address,
                    "publishTrace",
                    [trace_id, trace_data],
                )
            else:
                commit_hash = self.loader.client.w3.keccak(text=trace_data)
                tx = await trace_registry.functions.commitTrace(
                    commit_hash
                ).build_transaction({
                    "from": self.settings.agent_account.address,
                    "nonce": await self._get_nonce(),
                    "gas": 150_000,
                    "gasPrice": await self._get_gas_price(),
                })
                signed = self.settings.agent_account.sign_transaction(tx)
                await self._send_raw(signed.raw_transaction)
        except Exception as exc:
            logger.warning("Failed to publish trace stub: %s", exc)

    # ─── Event Handlers ────────────────────────────────────────────

    async def handle_event(self, event: PublisherEvent) -> dict:
        """Process an incoming publisher event."""
        logger.info("Received event: type=%s tick_id=%s step=%s",
                     event.type, event.tick_id, event.step)

        if event.type == "evaluation_step":
            await self._handle_evaluation_step(event)
        elif event.type == "rebalance":
            await self._handle_rebalance(event)
        elif event.type == "halt":
            await self._handle_halt(event)
        else:
            logger.warning("Unknown event type: %s", event.type)

        return {"status": "received", "type": event.type}

    async def _handle_evaluation_step(self, event: PublisherEvent):
        """Log the step and check ephemeral wallet balance."""
        logger.info("Evaluation step: %s (tick: %s)", event.step, event.tick_id)

        # Check balance
        balance = await self._ephemeral_balance()
        if balance < LOW_BALANCE_THRESHOLD:
            logger.warning(
                "Low ephemeral wallet balance: %d USDC raw (threshold: %d)",
                balance, LOW_BALANCE_THRESHOLD,
            )

    async def _handle_rebalance(self, event: PublisherEvent):
        """Execute rebalance on subscriber vault."""
        if not event.trades:
            logger.warning("Rebalance event has no trades — skipping")
            return

        if DRY_RUN:
            logger.info(
                "DRY RUN: would execute %d trades on vault %s",
                len(event.trades), self.vault_address,
            )
            return

        try:
            from archimedes.chain.executor import TradeOrder

            trades = [
                TradeOrder(
                    symbol=t.get("symbol", ""),
                    token_address=t.get("token_address", t.get("symbol", "")),
                    amount=float(t.get("amount", 0)),
                    direction=t.get("direction", "BUY"),
                    estimated_usdc_value=float(t.get("amount", 0)),
                )
                for t in event.trades
            ]

            tx_hashes = await self.executor.execute_trades(self.vault_address, trades)
            logger.info("Executed %d trades on vault %s: %s",
                         len(trades), self.vault_address, tx_hashes)

            # Publish trace stub
            await self._publish_trace_stub(event)

        except Exception as exc:
            logger.error("Rebalance execution failed: %s", exc)

    async def _handle_halt(self, event: PublisherEvent):
        """Log the halt. No on-chain action."""
        logger.info(
            "Halt received: step=%s reason=%s message=%s",
            event.step, event.reason, event.message,
        )

        if event.reason == "insufficient_balance":
            logger.warning(
                "Ephemeral wallet needs top-up. Use POST /top-up or call "
                "SubscriptionManager.renewEphemeralWallet()"
            )

    # ─── Top-up ────────────────────────────────────────────────────

    async def handle_top_up(self, req: TopUpRequest) -> dict:
        """Top up the ephemeral wallet."""
        if not self.subscription_manager_address:
            raise HTTPException(status_code=500, detail="SubscriptionManager not configured")

        if DRY_RUN:
            logger.info("DRY RUN: would top up %d USDC raw", req.amount_usdc_raw)
            return {"status": "topped_up", "new_balance": 0}

        contract = self.loader._contract(
            self.subscription_manager_address, "SubscriptionManager"
        )

        try:
            if self.circle_signer.is_configured:
                await self.circle_signer.execute_contract(
                    self.subscription_manager_address,
                    "renewEphemeralWallet",
                    [self.sub_id, req.amount_usdc_raw],
                )
            else:
                tx = await contract.functions.renewEphemeralWallet(
                    self.sub_id, req.amount_usdc_raw
                ).build_transaction({
                    "from": self.settings.agent_account.address,
                    "nonce": await self._get_nonce(),
                    "gas": 200_000,
                    "gasPrice": await self._get_gas_price(),
                })
                signed = self.settings.agent_account.sign_transaction(tx)
                await self._send_raw(signed.raw_transaction)

            new_balance = await self._ephemeral_balance()
            return {"status": "topped_up", "new_balance": new_balance}

        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Top-up failed: {exc}")

    # ─── Health ────────────────────────────────────────────────────

    async def handle_health(self) -> dict:
        balance = await self._ephemeral_balance()
        return {
            "status": "ok",
            "sub_id": self.sub_id,
            "vault": self.vault_address,
            "ephemeral_balance": balance,
        }

    # ─── HTTP Helpers ──────────────────────────────────────────────

    async def _ensure_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()

    async def shutdown(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()


# ─── FastAPI App ───────────────────────────────────────────────────────

agent = SubscriberAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.initialize()
    yield
    await agent.shutdown()


app = FastAPI(title="Strategy Subscriber", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/events")
async def events(event: PublisherEvent):
    return await agent.handle_event(event)


@app.get("/health")
async def health():
    return await agent.handle_health()


@app.post("/top-up")
async def top_up(req: TopUpRequest):
    return await agent.handle_top_up(req)


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    import uvicorn
    uvicorn.run(app, host=SUBSCRIBER_HOST, port=SUBSCRIBER_PORT)


if __name__ == "__main__":
    main()
