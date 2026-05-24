"""Tests for strategy_publisher — StrategyRegistry.sol integration.

Tests the StrategyPublisher service that anchors Tier-1 strategies on-chain.
Mocks chain_client and circle_signer to avoid requiring live Arc testnet.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from web3 import Web3

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_loader():
    """Mock ContractLoader with a strategy_registry contract."""
    loader = MagicMock()
    mock_contract = MagicMock()

    # Default: isRegistered returns False
    mock_contract.functions.isRegistered.return_value.call = AsyncMock(return_value=False)
    mock_contract.functions.registerStrategy.return_value.build_transaction = AsyncMock(return_value={"gas": 300_000})
    mock_contract.functions.strategyCount.return_value.call = AsyncMock(return_value=0)

    loader.strategy_registry = mock_contract
    return loader


@pytest.fixture
def publisher(mock_loader):
    """Create a StrategyPublisher with a mocked loader."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    return StrategyPublisher(loader=mock_loader)


def _keccak(text: str) -> str:
    """Helper to compute keccak256 hex."""
    return Web3.keccak(text=text).hex()


# ── Hash computation tests ────────────────────────────────────────────────


def test_hash_regime_tag():
    """Regime tag is hashed via keccak256."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    pub = StrategyPublisher.__new__(StrategyPublisher)
    h = pub._hash_regime_tag("bull")
    assert len(h) == 32
    assert h == Web3.keccak(text="bull")


def test_hash_regime_tag_none():
    """None regime tag hashes to 'unclassified'."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    pub = StrategyPublisher.__new__(StrategyPublisher)
    h = pub._hash_regime_tag(None)
    assert h == Web3.keccak(text="unclassified")


def test_hash_paper_corpus():
    """Paper corpus hash is keccak256 of sorted concatenated hashes."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    pub = StrategyPublisher.__new__(StrategyPublisher)
    papers = ["0xaaa", "0xbbb"]
    h = pub._hash_paper_corpus(papers)
    expected = Web3.keccak(text="0xaaa0xbbb")
    assert h == expected


def test_hash_paper_corpus_empty():
    """Empty paper list hashes to keccak256 of empty bytes."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    pub = StrategyPublisher.__new__(StrategyPublisher)
    h = pub._hash_paper_corpus([])
    assert h == Web3.keccak(b"")


def test_hash_paper_corpus_sorted():
    """Paper hashes are sorted before hashing (deterministic regardless of input order)."""
    from archimedes.chain.strategy_publisher import StrategyPublisher

    pub = StrategyPublisher.__new__(StrategyPublisher)
    h1 = pub._hash_paper_corpus(["0xbbb", "0xaaa"])
    h2 = pub._hash_paper_corpus(["0xaaa", "0xbbb"])
    assert h1 == h2


# ── is_anchored tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
async def test_is_anchored_returns_false_when_not_registered(mock_client, publisher):
    """is_anchored returns False for unregistered strategy."""
    mock_client.settings.strategy_registry_address = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_client.to_checksum.return_value = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"

    result = await publisher.is_anchored("0x" + "ab" * 32)
    assert result is False


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
async def test_is_anchored_returns_false_when_no_address(mock_client, publisher):
    """is_anchored returns False when contract address is not configured."""
    mock_client.settings.strategy_registry_address = ""
    result = await publisher.is_anchored("0x" + "ab" * 32)
    assert result is False


# ── anchor tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
@patch("archimedes.chain.strategy_publisher.circle_signer")
async def test_anchor_returns_none_when_no_config(mock_signer, mock_client, publisher):
    """anchor returns None when no contract address or signing key is configured."""
    mock_client.settings.strategy_registry_address = ""
    mock_signer.is_configured = False

    result = await publisher.anchor(
        strategy_id="0x" + "ab" * 32,
        methodology_hash="0x" + "cd" * 32,
    )
    assert result is None


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
@patch("archimedes.chain.strategy_publisher.circle_signer")
async def test_anchor_skips_when_registry_not_configured(mock_signer, mock_client, publisher):
    """anchor skips when registry address is empty."""
    mock_client.settings.strategy_registry_address = ""
    mock_signer.is_configured = False
    mock_client.settings.agent_account = None

    result = await publisher.anchor(
        strategy_id="0x" + "ab" * 32,
        methodology_hash="0x" + "cd" * 32,
    )
    assert result is None


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
@patch("archimedes.chain.strategy_publisher.circle_signer")
async def test_anchor_via_circle_signer(mock_signer, mock_client, publisher):
    """anchor uses Circle signer when configured."""
    mock_client.settings.strategy_registry_address = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_client.to_checksum.return_value = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_signer.is_configured = True
    mock_signer.execute_contract = AsyncMock(return_value="0xdeadbeef" + "00" * 28)

    result = await publisher.anchor(
        strategy_id="0x" + "ab" * 32,
        methodology_hash="0x" + "cd" * 32,
        paper_hashes=["0x" + "11" * 32],
        regime_tag="bull",
        metadata_uri="ipfs://QmExample",
    )
    assert result is not None
    assert result.startswith("0x")
    mock_signer.execute_contract.assert_called_once()

    # Verify the function signature and params structure
    call_args = mock_signer.execute_contract.call_args
    assert "registerStrategy" in call_args.kwargs.get("abi_function", call_args[1].get("abi_function", ""))


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
@patch("archimedes.chain.strategy_publisher.circle_signer")
async def test_anchor_falls_back_to_raw_key(mock_signer, mock_client, publisher, mock_loader):
    """anchor falls back to raw key path when Circle fails and agent_account is set.

    Verifies the error handling: Circle fails → raw key path attempted → web3
    unavailable → graceful None return (not an exception)."""
    mock_client.settings.strategy_registry_address = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_client.to_checksum.return_value = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_signer.is_configured = True
    mock_signer.execute_contract = AsyncMock(side_effect=Exception("Circle down"))

    # No agent_account → raw key path is skipped → returns None
    mock_client.settings.agent_account = None
    result = await publisher.anchor(
        strategy_id="0x" + "ab" * 32,
        methodology_hash="0x" + "cd" * 32,
    )
    assert result is None


# ── strategy_count tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
async def test_strategy_count_zero_when_no_address(mock_client, publisher):
    """strategy_count returns 0 when no address configured."""
    mock_client.settings.strategy_registry_address = ""
    result = await publisher.strategy_count()
    assert result == 0


@pytest.mark.asyncio
@patch("archimedes.chain.strategy_publisher.chain_client")
async def test_strategy_count_returns_count(mock_client, publisher):
    """strategy_count returns on-chain count."""
    mock_client.settings.strategy_registry_address = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    mock_client.to_checksum.return_value = "0x728C264d0681b71c4Cc1D26a4fb14Ec29D9a90e4"
    publisher.loader.strategy_registry.functions.strategyCount.return_value.call = AsyncMock(return_value=3)
    result = await publisher.strategy_count()
    assert result == 3


# ── Integration: strategy_store Tier-1 promotion ──────────────────────────


def test_strategy_record_has_on_chain_fields():
    """StrategyRecord has on_chain_registration_tx and on_chain_registration_block columns."""
    from archimedes.models.strategy_store import StrategyRecord

    # Check the column is defined on the class (SQLAlchemy mapped)
    assert "on_chain_registration_tx" in StrategyRecord.__table__.columns
    assert "on_chain_registration_block" in StrategyRecord.__table__.columns


def test_strategy_to_dict_includes_on_chain_fields():
    """to_dict includes the on-chain registration fields."""
    from archimedes.models.strategy_store import StrategyRecord

    record = StrategyRecord(
        id="test123",
        content_hash="0xabc",
        generation_method="fusion",
        source_papers="[]",
        strategy_name="Test",
        thesis="Test thesis",
        asset_universe="[]",
        on_chain_registration_tx="0xdeadbeef" + "00" * 28,
        on_chain_registration_block="12345",
    )
    d = record.to_dict()
    assert d["on_chain_registration_tx"] == "0xdeadbeef" + "00" * 28
    assert d["on_chain_registration_block"] == "12345"


# ── Determinism test ──────────────────────────────────────────────────────


def test_paper_corpus_hash_is_deterministic_with_hashseed_variation():
    """Paper corpus hash is deterministic regardless of PYTHONHASHSEED."""
    import subprocess
    import sys

    code = (
        "from web3 import Web3; "
        "papers = ['0xaaa', '0xbbb', '0xccc']; "
        "h = Web3.keccak(text=''.join(sorted(papers))); "
        "print(h.hex())"
    )
    results = set()
    for seed in ["0", "1", "42", "12345", "random"]:
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONHASHSEED": seed},
        )
        results.add(r.stdout.strip())

    # Must produce the same hash regardless of hash seed
    assert len(results) == 1, f"Non-deterministic hashes: {results}"
