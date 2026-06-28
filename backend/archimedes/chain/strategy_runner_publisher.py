"""Publisher agent — single-strategy, single-vault publisher for the
subscription marketplace.

Runs a FastAPI server alongside an agent loop. The agent loop:
1. Evaluates its single strategy against live market data.
2. Pushes evaluation_step events to all registered subscriber webhooks.
3. Before rebalancing, charges each subscriber on-chain via
   SubscriptionManager.chargeActions().
4. Sends rebalance payload only to successfully charged subscribers.
5. If halted (no subscribers, strategy error, etc.), broadcasts halt
   notification (free, no charge).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import aiohttp
import httpx
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

INTERVAL = int(os.getenv("AGENT_INTERVAL_SECONDS", "300"))
DRY_RUN = os.getenv("AGENT_DRY_RUN", "false").lower() == "true"
FORCE_HALT = os.getenv("FORCE_HALT", "false").lower() == "true"
PUBLISHER_HOST = os.getenv("PUBLISHER_HOST", "0.0.0.0")
PUBLISHER_PORT = int(os.getenv("PUBLISHER_PORT", "8080"))
PUBLISHER_STRATEGY_ID = os.getenv("PUBLISHER_STRATEGY_ID", "")
PUBLISHER_VAULT_ADDRESS = os.getenv("PUBLISHER_VAULT_ADDRESS", "")
PUBLISHER_POOL_ID = os.getenv("PUBLISHER_POOL_ID", "")
CREATOR_ADDRESS = os.getenv("CREATOR_ADDRESS", "")
PLATFORM_WALLET = os.getenv("PLATFORM_WALLET", "")
FLAT_FEE_PER_ACTION = int(os.getenv("FLAT_FEE_PER_ACTION", "100"))
PAYMENT_SPLITTER_ADDRESS = os.getenv("PAYMENT_SPLITTER_ADDRESS", "")
SUBSCRIPTION_MANAGER_ADDRESS = os.getenv("SUBSCRIPTION_MANAGER_ADDRESS", "")
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY", "")

MAX_WEBHOOK_RETRIES = 3
WEBHOOK_BACKOFF = 1.0  # seconds, doubles each retry

# ─── Data Models ───────────────────────────────────────────────────────

TICK_ID_PREFIX = "pub"


@dataclass
class SubscriberInfo:
    sub_id: str
    webhook_url: str
    ephemeral_wallet: str
    active: bool = True


class SubscribeRequest(BaseModel):
    sub_id: str = Field(..., description="Hex-encoded bytes32 sub_id")
    webhook_url: str = Field(..., description="Subscriber's /events endpoint")
    ephemeral_wallet: str = Field(..., description="Ephemeral wallet address")


class UpdateEphemeralRequest(BaseModel):
    sub_id: str = Field(...)
    new_ephemeral_wallet: str = Field(...)


# ─── HTTP Payload Builders ─────────────────────────────────────────────


def _eval_step_payload(step: str, tick_id: str, signals: dict[str, Any]) -> dict:
    return {
        "type": "evaluation_step",
        "step": step,
        "tick_id": tick_id,
        "halted": False,
        "signal_summary": signals,
    }


def _rebalance_payload(tick_id: str, action_count: int, trades: list,
                       target_weights: dict) -> dict:
    return {
        "type": "rebalance",
        "tick_id": tick_id,
        "action_count": action_count,
        "trades": trades,
        "target_weights": target_weights,
    }


def _halt_payload(tick_id: str, step: str, reason: str, message: str) -> dict:
    return {
        "type": "halt",
        "tick_id": tick_id,
        "step": step,
        "reason": reason,
        "message": message,
    }


# ─── Publisher Agent ───────────────────────────────────────────────────


class PublisherAgent:
    """Runs a single-strategy evaluation loop and pushes events to
    registered subscriber webhooks."""

    def __init__(self):
        self.settings = ChainSettings()
        self.loader = ContractLoader()
        self.executor = ChainExecutor(loader=self.loader)
        self.circle_signer = CircleSigner()
        self.redis = AgentStateStore()

        # Override contract addresses from env vars (not in ChainSettings)
        self.payment_splitter_address = (
            PAYMENT_SPLITTER_ADDRESS or self.settings.payment_splitter_address
        )
        self.subscription_manager_address = (
            SUBSCRIPTION_MANAGER_ADDRESS
            or self.settings.subscription_manager_address
        )

        # Strategy identity
        self.strategy_id = PUBLISHER_STRATEGY_ID
        self.vault_address = PUBLISHER_VAULT_ADDRESS
        self.pool_id = PUBLISHER_POOL_ID

        # Webhook subscriber registry: sub_id -> SubscriberInfo
        self.subscribers: dict[str, SubscriberInfo] = {}

        # Tick tracking
        self._tick_counter = 0
        self._halted = False
        self._initialized = False

        # Session for webhook delivery
        self._http_session: aiohttp.ClientSession | None = None

    # ─── Initialization ────────────────────────────────────────────

    async def initialize(self):
        """One-time startup: create vault, create pool, load subscribers."""
        if self._initialized:
            return

        # Create vault if not set
        if not self.vault_address:
            vault_addr = await self._load_or_create_vault()
            self.vault_address = vault_addr
            logger.info("Created vault at %s", self.vault_address)

        # Create PaymentSplitter pool if not yet active
        if not self.pool_id:
            pool_id = await self._ensure_pool()
            self.pool_id = pool_id
            logger.info("Ensured pool %s", self.pool_id)

        # Restore subscriber registry from Redis
        await self._restore_subscribers()

        self._initialized = True
        logger.info(
            "Publisher initialized: strategy=%s vault=%s pool=%s",
            self.strategy_id, self.vault_address, self.pool_id,
        )

    async def _load_or_create_vault(self) -> str:
        """Load vault address from Redis or create one."""
        redis_key = f"publisher:vault_address:{self.strategy_id}"
        cached = await self.redis.redis.get(redis_key)
        if cached:
            return cached.decode()

        if DRY_RUN:
            vault_addr = "0x0000000000000000000000000000000000000001"
        else:
            vault_addr = await self.executor.create_vault(
                name=f"Publisher-{self.strategy_id}",
                symbol=f"PV{self.strategy_id[:4]}",
                management_fee_bps=50,
                performance_fee_bps=500,
                agent_assisted=True,
            )

        await self.redis.redis.set(redis_key, vault_addr)
        return vault_addr

    async def _ensure_pool(self) -> str:
        """Create PaymentSplitter pool if it doesn't exist."""
        if not self.payment_splitter_address or not CREATOR_ADDRESS or not PLATFORM_WALLET:
            logger.warning("PaymentSplitter not configured — skipping pool creation")
            return ""

        pool_id = f"{self.strategy_id}_{CREATOR_ADDRESS}"

        if DRY_RUN:
            logger.info("DRY RUN: would create pool %s", pool_id)
            return pool_id

        contract = self.loader._contract(
            self.payment_splitter_address, "PaymentSplitter"
        )
        try:
            pool_data = await contract.functions.pools(pool_id).call()
            if pool_data[4]:  # active
                logger.info("Pool %s already active", pool_id)
                return pool_id
        except Exception:
            pass

        if self.circle_signer.is_configured:
            await self.circle_signer.execute_contract(
                self.payment_splitter_address,
                "createPool",
                [pool_id, CREATOR_ADDRESS, PLATFORM_WALLET],
            )
        else:
            tx = await contract.functions.createPool(
                pool_id, CREATOR_ADDRESS, PLATFORM_WALLET
            ).build_transaction({
                "from": self.settings.agent_account.address,
                "nonce": await self._get_nonce(),
                "gas": 200_000,
                "gasPrice": await self._get_gas_price(),
            })
            signed = self.settings.agent_account.sign_transaction(tx)
            await self._send_raw(signed.raw_transaction)

        return pool_id

    async def _restore_subscribers(self):
        """Load subscriber registry from Redis."""
        redis_key = f"publisher:subscribers:{self.strategy_id}"
        data = await self.redis.redis.get(redis_key)
        if data:
            raw = json.loads(data.decode())
            self.subscribers = {
                sid: SubscriberInfo(**info) for sid, info in raw.items()
            }
            logger.info("Restored %d subscribers from Redis", len(self.subscribers))

    async def _persist_subscribers(self):
        """Save subscriber registry to Redis."""
        redis_key = f"publisher:subscribers:{self.strategy_id}"
        raw = {
            sid: {
                "sub_id": info.sub_id,
                "webhook_url": info.webhook_url,
                "ephemeral_wallet": info.ephemeral_wallet,
                "active": info.active,
            }
            for sid, info in self.subscribers.items()
        }
        await self.redis.redis.set(redis_key, json.dumps(raw))

    # ─── Webhook Delivery ──────────────────────────────────────────

    async def _ensure_session(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()

    async def _deliver_webhook(self, url: str, payload: dict) -> bool:
        """Deliver a webhook payload with exponential backoff."""
        await self._ensure_session()
        for attempt in range(1, MAX_WEBHOOK_RETRIES + 1):
            try:
                async with self._http_session.post(
                    url, json=payload, timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning(
                        "Webhook %s returned %d (attempt %d/%d)",
                        url, resp.status, attempt, MAX_WEBHOOK_RETRIES,
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "Webhook %s failed: %s (attempt %d/%d)",
                    url, exc, attempt, MAX_WEBHOOK_RETRIES,
                )
            if attempt < MAX_WEBHOOK_RETRIES:
                await asyncio.sleep(WEBHOOK_BACKOFF * (2 ** (attempt - 1)))
        return False

    async def _notify_all(self, payload: dict, only_active: bool = True):
        """Send a payload to all (active) subscribers."""
        tasks = []
        for sub_id, info in list(self.subscribers.items()):
            if only_active and not info.active:
                continue
            tasks.append(self._deliver_webhook(info.webhook_url, payload))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for (sub_id, info), ok in zip(
                [(s, i) for s, i in self.subscribers.items()
                 if not only_active or i.active],
                results,
            ):
                if isinstance(ok, Exception) or not ok:
                    logger.warning("Delivery to %s failed, marking inactive", sub_id)
                    info.active = False

    async def _notify_one(self, sub_id: str, payload: dict):
        """Send a payload to a single subscriber."""
        info = self.subscribers.get(sub_id)
        if not info:
            return
        ok = await self._deliver_webhook(info.webhook_url, payload)
        if not ok:
            info.active = False

    # ─── On-chain Helpers ──────────────────────────────────────────

    def _get_nonce(self):
        from web3 import Web3
        return (
            self.settings.web3_provider or Web3(Web3.HTTPProvider(self.settings.arc_rpc_url))
        ).eth.get_transaction_count(self.settings.agent_account.address)

    async def _get_gas_price(self):
        w3 = self.loader.client.w3
        return await w3.eth.gas_price

    async def _send_raw(self, raw_tx: bytes):
        w3 = self.loader.client.w3
        tx_hash = await w3.eth.send_raw_transaction(raw_tx)
        return await w3.eth.wait_for_transaction_receipt(tx_hash)

    async def _charge_subscriber(self, sub_id: str, action_count: int) -> bool:
        """Charge a subscriber for N actions. Returns True if successful."""
        if not self.subscription_manager_address:
            logger.warning("SubscriptionManager not configured — skipping charge")
            return False

        if DRY_RUN:
            logger.info("DRY RUN: charge %s for %d actions", sub_id, action_count)
            return True

        contract = self.loader._contract(
            self.subscription_manager_address, "SubscriptionManager"
        )
        try:
            if self.circle_signer.is_configured:
                await self.circle_signer.execute_contract(
                    self.subscription_manager_address,
                    "chargeActions",
                    [sub_id, action_count],
                )
            else:
                tx = await contract.functions.chargeActions(
                    sub_id, action_count
                ).build_transaction({
                    "from": self.settings.agent_account.address,
                    "nonce": await self._get_nonce(),
                    "gas": 200_000,
                    "gasPrice": await self._get_gas_price(),
                })
                signed = self.settings.agent_account.sign_transaction(tx)
                await self._send_raw(signed.raw_transaction)
            return True
        except Exception as exc:
            logger.warning("chargeActions failed for %s: %s", sub_id, exc)
            return False

    # ─── Halt Check ────────────────────────────────────────────────

    def _halt_check(self) -> tuple[bool, str, str]:
        """Check if the agent should halt.

        Returns (halted, reason, message).
        """
        if FORCE_HALT:
            return True, "forced", "Halted via FORCE_HALT env var"

        active_count = sum(1 for info in self.subscribers.values() if info.active)
        if active_count == 0:
            return True, "no_active_subscribers", "No active subscribers remain"

        return False, "", ""

    # ─── Tick ──────────────────────────────────────────────────────

    async def tick(self):
        """Run one evaluation + notification cycle."""
        self._tick_counter += 1
        tick_id = f"{TICK_ID_PREFIX}_{int(time.time())}_{self._tick_counter}"

        logger.info("Tick %d (%s) — %d subscribers",
                     self._tick_counter, tick_id, len(self.subscribers))

        # 1. Load & evaluate strategy (simplified — single strategy)
        signals = await self._evaluate_strategy()
        if not signals:
            halted, reason, msg = True, "strategy_error", "Strategy evaluation returned no signals"
            halt_pl = _halt_payload(tick_id, "strategy_evaluation", reason, msg)
            await self._notify_all(halt_pl, only_active=False)
            logger.warning("Halted: %s", msg)
            return

        # 2. Halt check after evaluation
        halted, reason, msg = self._halt_check()
        if halted:
            halt_pl = _halt_payload(tick_id, "post_evaluation", reason, msg)
            await self._notify_all(halt_pl, only_active=False)
            logger.warning("Halted: %s", msg)
            return

        # 3. Notify evaluation step (no charge)
        for step_name in ["signal_collection", "signal_analysis", "weight_computation"]:
            step_halted, step_reason, step_msg = self._halt_check()
            if step_halted:
                halt_pl = _halt_payload(tick_id, step_name, step_reason, step_msg)
                await self._notify_all(halt_pl, only_active=False)
                logger.warning("Halted at %s: %s", step_name, step_msg)
                return

            pl = _eval_step_payload(step_name, tick_id,
                                    {"status": "ok", "signals_count": len(signals)})
            await self._notify_all(pl)

        # 4. Determine if rebalance needed
        trades, target_weights = await self._compute_rebalance(signals)
        if not trades:
            logger.info("No rebalance needed this tick")
            return

        action_count = len(trades)

        # 5. Pre-rebalance: charge each active subscriber
        for sub_id, info in list(self.subscribers.items()):
            if not info.active:
                continue

            charged = await self._charge_subscriber(sub_id, action_count)
            if not charged:
                info.active = False
                halt_pl = _halt_payload(
                    tick_id, "pre_rebalance",
                    "insufficient_balance",
                    f"Subscriber {sub_id} has insufficient balance",
                )
                await self._notify_one(sub_id, halt_pl)
            else:
                # 6. Send rebalance payload to charged subscribers only
                pl = _rebalance_payload(tick_id, action_count, trades, target_weights)
                await self._notify_one(sub_id, pl)

        # 7. Execute rebalance on publisher's own vault (non-dry-run)
        if not DRY_RUN:
            try:
                await self.executor.execute_trades(self.vault_address, trades)
            except Exception as exc:
                logger.error("Rebalance execution failed: %s", exc)

        # Persist subscriber state
        await self._persist_subscribers()

    async def _evaluate_strategy(self) -> dict[str, Any] | None:
        """Evaluate a single strategy. Simplified for publisher context."""
        try:
            from archimedes.strategies.registry import StrategyRegistry
            registry = StrategyRegistry()
            strategy = registry.get(self.strategy_id)
            if not strategy:
                logger.warning("Strategy %s not found", self.strategy_id)
                return None

            # Evaluate signals (simplified)
            from archimedes.chain.strategy_signal_evaluator import evaluate_strategy_signals
            signals = await evaluate_strategy_signals(
                self.strategy_id,
                strategy.parameters,
            )
            return signals or {}
        except Exception as exc:
            logger.error("Strategy evaluation error: %s", exc)
            return None

    async def _compute_rebalance(self, signals: dict) -> tuple[list, dict]:
        """Compute rebalance trades from signals.

        Returns (trades, target_weights).
        """
        # Simplified: in production this would compute target allocations,
        # compare with current portfolio, and generate trades.
        # For the publisher, we rely on the strategy's signal to determine
        # whether a rebalance is needed.
        if not signals:
            return [], {}
        return [], {}

    # ─── HTTP API (FastAPI routes) ─────────────────────────────────

    async def handle_subscribe(self, req: SubscribeRequest) -> dict:
        """POST /subscribe — register a subscriber."""
        # Validate sub_id on-chain
        if not self.subscription_manager_address:
            raise HTTPException(status_code=500, detail="SubscriptionManager not configured")

        if DRY_RUN:
            logger.info("DRY RUN: would validate sub_id %s on-chain", req.sub_id)
        else:
            contract = self.loader._contract(
                self.subscription_manager_address, "SubscriptionManager"
            )
            try:
                sub_data = await contract.functions.subscriptions(req.sub_id).call()
                if not sub_data[5]:  # active field
                    raise HTTPException(status_code=400, detail="Subscription is not active")
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid sub_id: {exc}")

        # Validate webhook_url is reachable
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    req.webhook_url.rstrip("/") + "/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=400, detail="Webhook URL not reachable")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise HTTPException(status_code=400, detail="Webhook URL not reachable")

        # Register
        self.subscribers[req.sub_id] = SubscriberInfo(
            sub_id=req.sub_id,
            webhook_url=req.webhook_url,
            ephemeral_wallet=req.ephemeral_wallet,
            active=True,
        )
        await self._persist_subscribers()
        logger.info("Registered subscriber %s (webhook: %s)", req.sub_id, req.webhook_url)
        return {"status": "registered", "sub_id": req.sub_id}

    async def handle_update_ephemeral(self, req: UpdateEphemeralRequest) -> dict:
        """POST /update-ephemeral — update subscriber's ephemeral wallet."""
        if req.sub_id not in self.subscribers:
            raise HTTPException(status_code=404, detail="Subscriber not found")

        self.subscribers[req.sub_id].ephemeral_wallet = req.new_ephemeral_wallet
        await self._persist_subscribers()
        logger.info("Updated ephemeral wallet for %s", req.sub_id)
        return {"status": "updated"}

    async def handle_health(self) -> dict:
        """GET /health."""
        return {
            "status": "ok",
            "strategy_id": self.strategy_id,
            "vault": self.vault_address,
            "subscribers": len(self.subscribers),
            "active_subscribers": sum(1 for i in self.subscribers.values() if i.active),
        }

    async def handle_subscribers_list(self) -> list[dict]:
        """GET /subscribers — return active sub_ids (no webhook URLs)."""
        return [
            {"sub_id": sid, "active": info.active}
            for sid, info in self.subscribers.items()
        ]

    # ─── Lifecycle ─────────────────────────────────────────────────

    async def shutdown(self):
        if self._http_session and not self._http_session.closed:
            await self._http_session.close()


# ─── FastAPI App ───────────────────────────────────────────────────────

agent = PublisherAgent()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.initialize()
    task = asyncio.create_task(_run_loop())
    yield
    task.cancel()
    await agent.shutdown()


app = FastAPI(title="Strategy Publisher", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/subscribe")
async def subscribe(req: SubscribeRequest):
    return await agent.handle_subscribe(req)


@app.post("/update-ephemeral")
async def update_ephemeral(req: UpdateEphemeralRequest):
    return await agent.handle_update_ephemeral(req)


@app.get("/health")
async def health():
    return await agent.handle_health()


@app.get("/subscribers")
async def subscribers():
    return await agent.handle_subscribers_list()


async def _run_loop():
    """Background task: run the agent tick loop."""
    while True:
        try:
            await agent.tick()
        except Exception as exc:
            logger.error("Tick error: %s", exc, exc_info=True)
        await asyncio.sleep(INTERVAL)


# ─── Main ──────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    import uvicorn
    uvicorn.run(app, host=PUBLISHER_HOST, port=PUBLISHER_PORT)


if __name__ == "__main__":
    main()
