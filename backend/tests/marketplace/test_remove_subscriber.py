"""A1: Test real MarketService.remove_subscriber (not mocked).

Verifies the _deactivate_subscriber_db helper is correctly wired so
unsubscribe does not raise AttributeError and the DB row is flipped to
"stopped".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from archimedes.db import get_session
from archimedes.marketplace.service import MarketService, Publisher, Subscriber
from archimedes.models.marketplace import MarketplaceAgent

# DB isolation for this file's tests comes from the autouse fixture in
# backend/tests/marketplace/conftest.py (see tests/db_isolation.py) — it
# replaces the ad hoc redirect-without-restore fixture that used to live
# here, which left archimedes.db.engine/SessionLocal permanently pointed at
# this file's last tmp DB for every test that ran later in the same pytest
# process (issue #1100).


@pytest.fixture
def market():
    """MarketService with mocked state/executor and no running publishers."""
    svc = MarketService(interval_seconds=9999, payments_dry_run=True, paper_trading=True)
    svc.state = MagicMock()
    svc.state.save_subscribers = AsyncMock()
    svc.publishers = {}
    return svc


def _seed_subscriber_db(strategy_id: str, sub_id: str, **overrides):
    """Insert a MarketplaceAgent row with role='subscriber', status='running'."""
    with get_session() as session:
        row = MarketplaceAgent(
            role="subscriber",
            strategy_id=strategy_id,
            sub_id=sub_id,
            status="running",
            pool_id="0x" + "dd" * 32,
            vault_address="0xvault",
            ephemeral_wallet="0xeph",
            subscriber_wallet="0xsub",
            creator_wallet="0xcreator",
            **overrides,
        )
        session.add(row)
        session.commit()
        return row.id


@pytest.mark.asyncio
async def test_remove_subscriber_flips_db_status_to_stopped(market: MarketService):
    """Calling remove_subscriber against a real running subscriber deactivates it."""
    strategy_id = "strat_a1"
    sub_id = "0x" + "aa" * 32

    # Seed the DB with a running subscriber row
    _seed_subscriber_db(strategy_id, sub_id)

    # Create publisher with this subscriber in memory
    sub = Subscriber(
        sub_id=sub_id,
        pool_id="0x" + "dd" * 32,
        vault_address="0xvault",
        ephemeral_wallet="0xeph",
        subscriber_wallet="0xsub",
        active=True,
    )
    market.publishers[strategy_id] = Publisher(
        strategy_id=strategy_id,
        pool_id="0x" + "dd" * 32,
        vault_address="0xvault",
        creator_wallet="0xcreator",
        subscribers={sub_id: sub},
    )

    # Act — call the real method (not mocked)
    await market.remove_subscriber(strategy_id, sub_id)

    # Assert — DB row flipped to "stopped"
    with get_session() as session:
        row = (
            session.query(MarketplaceAgent)
            .filter(
                MarketplaceAgent.strategy_id == strategy_id,
                MarketplaceAgent.sub_id == sub_id,
            )
            .first()
        )
        assert row is not None, "DB row should still exist"
        assert row.status == "stopped", f"Expected 'stopped', got '{row.status}'"

    # Assert — subscriber removed from in-memory publisher
    assert sub_id not in market.publishers[strategy_id].subscribers


@pytest.mark.asyncio
async def test_remove_subscriber_no_db_row_does_not_raise(market: MarketService):
    """If there is no DB row, remove_subscriber should still succeed gracefully."""
    strategy_id = "strat_a1_nodb"
    sub_id = "0x" + "bb" * 32

    sub = Subscriber(
        sub_id=sub_id,
        pool_id="0x" + "dd" * 32,
        vault_address="0xvault",
        ephemeral_wallet="0xeph",
        subscriber_wallet="0xsub",
        active=True,
    )
    market.publishers[strategy_id] = Publisher(
        strategy_id=strategy_id,
        pool_id="0x" + "dd" * 32,
        vault_address="0xvault",
        creator_wallet="0xcreator",
        subscribers={sub_id: sub},
    )

    # Should not raise even though no DB row exists
    await market.remove_subscriber(strategy_id, sub_id)

    # Subscriber still removed from in-memory publisher
    assert sub_id not in market.publishers[strategy_id].subscribers
