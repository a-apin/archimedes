"""Every money-path caller must settle on one Gateway chain (#1495).

Three call sites read ``GATEWAY_CHAIN`` from the environment and applied
``DEFAULT_GATEWAY_CHAIN`` as the fallback. A fourth — ``revenue_sweep._client``
— passed the constant through as a keyword argument and never consulted the
environment, so setting ``GATEWAY_CHAIN=arcMainnet`` moved the paywall, the
quote and settlement while leaving the revenue sweep on testnet.

The divergence had no visible symptom. A sweep pointed at an empty testnet
balance logs "below threshold — skip" and returns cleanly, which on the first
day of mainnet is indistinguishable from "no revenue yet".

Hermetic: circlekit is mocked at the module boundary. No Circle API, no chain.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from archimedes.marketplace import payments
from archimedes.marketplace.config import DEFAULT_GATEWAY_CHAIN, gateway_chain
from archimedes.services import generation_payment, revenue_sweep

RECIPIENT = "0xffa7abba5f17cb8471ebf150bf808bd6fb8856c1"
WALLET_ID = "af3e1cf6-76a3-55db-911a-b356860058e4"
MAINNET = "arcMainnet"


@pytest.fixture
def sweep_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The credentials `_client` refuses to run without."""
    monkeypatch.setenv("GENERATION_PAYMENT_RECIPIENT", RECIPIENT)
    monkeypatch.setenv("REVENUE_WALLET_ID", WALLET_ID)


def _sweep_chain() -> str:
    """The chain `revenue_sweep._client` hands to circlekit."""
    seen: dict[str, str] = {}

    class CapturingGatewayClient:
        def __init__(self, chain: str, **_kwargs: object) -> None:
            seen["chain"] = chain

    with (
        patch.object(revenue_sweep, "GatewayClient", CapturingGatewayClient),
        patch.object(revenue_sweep, "CircleWalletSigner", MagicMock()),
        patch.object(revenue_sweep, "CircleTxExecutor", MagicMock()),
    ):
        revenue_sweep._client()
    return seen["chain"]


def _charge_chain() -> str:
    """The chain `payments.get_gateway_middleware` builds against."""
    seen: dict[str, str] = {}

    def _capture(*, seller_address: str, chain: str, description: str) -> MagicMock:
        seen["chain"] = chain
        return MagicMock()

    payments._middleware_cache.clear()
    try:
        with patch.object(payments, "create_gateway_middleware", _capture):
            payments.get_gateway_middleware(RECIPIENT)
    finally:
        payments._middleware_cache.clear()
    return seen["chain"]


class TestRevenueSweepFollowsTheEnvironment:
    def test_sweep_follows_gateway_chain(self, monkeypatch: pytest.MonkeyPatch, sweep_env: None) -> None:
        """The regression. Fails on the pre-#1495 code, which returned arcTestnet."""
        monkeypatch.setenv("GATEWAY_CHAIN", MAINNET)
        assert _sweep_chain() == MAINNET

    def test_sweep_defaults_to_testnet_when_unset(self, monkeypatch: pytest.MonkeyPatch, sweep_env: None) -> None:
        monkeypatch.delenv("GATEWAY_CHAIN", raising=False)
        assert _sweep_chain() == DEFAULT_GATEWAY_CHAIN


class TestEveryCallSiteAgrees:
    """The property that actually matters: no site can diverge from the others.

    Asserted against each other rather than against a literal, so the test
    keeps holding if the default changes at the mainnet cutover.
    """

    def test_all_call_time_sites_report_one_chain(self, monkeypatch: pytest.MonkeyPatch, sweep_env: None) -> None:
        monkeypatch.setenv("GATEWAY_CHAIN", MAINNET)
        chains = {
            "revenue_sweep": _sweep_chain(),
            "payments": _charge_chain(),
            "quote": generation_payment.quote()["chain"],
            "helper": gateway_chain(),
        }
        assert len(set(chains.values())) == 1, f"money path split across chains: {chains}"
        assert chains["helper"] == MAINNET

    def test_they_agree_on_the_default_too(self, monkeypatch: pytest.MonkeyPatch, sweep_env: None) -> None:
        monkeypatch.delenv("GATEWAY_CHAIN", raising=False)
        chains = {_sweep_chain(), _charge_chain(), generation_payment.quote()["chain"]}
        assert chains == {DEFAULT_GATEWAY_CHAIN}


class TestHelperSemantics:
    def test_surrounding_whitespace_is_stripped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An env var set from a here-doc or a task-def JSON blob can carry a newline."""
        monkeypatch.setenv("GATEWAY_CHAIN", f"  {MAINNET}\n")
        assert gateway_chain() == MAINNET

    def test_settlement_constant_comes_from_the_helper(self) -> None:
        """`settlement.GATEWAY_CHAIN` is import-time by design — assert the source.

        It is a module-level constant that other modules import, so it is read
        once at import and a later `monkeypatch.setenv` cannot move it. Checking
        the value at runtime would therefore prove nothing about which code path
        produced it; checking the assignment does.
        """
        source = Path(revenue_sweep.__file__).parent.parent / "marketplace" / "settlement.py"
        assert "GATEWAY_CHAIN = gateway_chain()" in source.read_text()


class TestNoFifthCallSite:
    def test_default_constant_is_referenced_only_where_it_is_defined(self) -> None:
        """The structural half: a new caller cannot reintroduce the split.

        `revenue_sweep` diverged by importing the default and using it directly
        instead of reading the environment. Nothing stopped it. Confining the
        constant to `config.py` means the only way to obtain the chain elsewhere
        is `gateway_chain()`, which cannot forget the `getenv`.
        """
        package_root = Path(revenue_sweep.__file__).resolve().parents[1]
        offenders = {
            str(path.relative_to(package_root))
            for path in package_root.rglob("*.py")
            if path.name != "config.py" and re.search(r"\bDEFAULT_GATEWAY_CHAIN\b", path.read_text())
        }
        assert not offenders, (
            "DEFAULT_GATEWAY_CHAIN is referenced outside config.py by "
            f"{sorted(offenders)}. Call gateway_chain() instead — using the "
            "constant directly is how #1495 skipped the environment read."
        )

    def test_the_scan_actually_reaches_the_money_path_modules(self) -> None:
        """Guard on the guard: prove the rglob sees the files it is policing."""
        package_root = Path(revenue_sweep.__file__).resolve().parents[1]
        scanned = {str(p.relative_to(package_root)) for p in package_root.rglob("*.py")}
        for expected in (
            "services/revenue_sweep.py",
            "marketplace/payments.py",
            "marketplace/settlement.py",
            "services/generation_payment.py",
        ):
            assert expected in scanned, f"{expected} not scanned — the guard is blind"
