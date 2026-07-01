"""Synthetic-universe SSOT loader (T1.5 — backtest==on-chain parity).

This module is the single source of truth for the **on-chain synthetic
universe**. The on-chain deploy path (``scripts/deploy_contracts.py``
``SYNTHETICS``) is derived from here, and the parity invariant in
``backend/tests/test_universe_parity.py`` asserts that this set equals the
**backtestable** universe (``services.strategy_signal_evaluator.GLOBAL_ASSETS``)
so the two can never silently diverge.

Why a JSON SSOT rather than a hardcoded literal: the on-chain universe was
previously a tuple literal in ``deploy_contracts.py`` while the backtest
universe lived in ``GLOBAL_ASSETS`` — nothing kept them in sync, so a synth
could be deployable but not backtestable (or vice-versa). Issue #682 introduced
the JSON-SSOT pattern for the analytics-engine instruments; this mirrors it for
the synthetic universe.

Public API:
  * ``SYNTHETIC_UNIVERSE`` — dict[str, SyntheticSpec] keyed by synth symbol.
  * ``ON_CHAIN_SYNTHS`` — sorted list of synth symbols on the live on-chain path.
  * ``synthetics_for_deploy()`` — ``[(name, symbol, price_6dp_int), ...]`` ready
    to drop into ``deploy_contracts.SYNTHETICS``.
  * ``COMPLIANCE_FLAGGED_SINGLE_STOCKS`` — single-name equity synths that are
    backtest-only and must NOT be added to the live on-chain path without a
    compliance sign-off.

Loading is ``Path(__file__)``-relative (the backend runs with
``PYTHONPATH=backend``, not as a pip-installed package), and falls back to an
empty mapping with a logged error only if the SSOT file is unreadable so an
import never hard-crashes the app — the parity test would catch an empty set.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SSOT_PATH = Path(__file__).resolve().parent / "data" / "synthetic_universe.json"


@dataclass(frozen=True)
class SyntheticSpec:
    """One synth's on-chain + backtest metadata, loaded from the SSOT.

    Oracle provenance is per-source and honest (T1.5b / #759): ``yfinance_ticker``
    is the required backtest source; ``pyth_feed_id`` / ``stork_asset_id`` /
    ``chainlink_feed`` are nullable per-source oracle configs (a REAL id or
    ``None`` — never a fabricated value). ``oracle_tier`` is a DERIVED hint from
    the count of configured feeds; the AUTHORITATIVE tier is on-chain via
    ``QuorumPriceOracle.priceWithProvenance()`` (#840).
    """

    symbol: str
    name: str
    price_usd: float
    decimals: int
    asset_class: str
    # ── T1.5b (#759) honest per-source oracle schema ──
    # `yfinance_ticker` is the backtest data source + the parity invariant key.
    # Older SSOT rows may omit it (loader falls back to the symbol minus the
    # leading `s`), but the generator always emits it.
    yfinance_ticker: str = ""
    pyth_feed_id: str | None = None
    stork_asset_id: str | None = None
    chainlink_feed: str | None = None
    oracle_tier: str = "admin"
    # Deprecated boolean retained for backward compat with any pre-#759 SSOT /
    # doc code that reads it; derived from `chainlink_feed is not None` at load.
    chainlink_covered: bool = False

    @property
    def price_int(self) -> int:
        """Oracle initial price as an integer in `decimals` fixed-point units.

        ``deploy_contracts`` and ``bootstrap_vaults`` push 6-decimal prices
        on-chain (e.g. $592.40 → 592_400_000). Derive that from the SSOT so the
        human-readable USD price stays the single editable value.
        """
        return int(round(self.price_usd * (10**self.decimals)))


def _load_universe() -> dict[str, SyntheticSpec]:
    try:
        payload = json.loads(_SSOT_PATH.read_text(encoding="utf-8"))
        raw = payload["synthetics"]
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        logger.error("could not load synthetic-universe SSOT %s (%s)", _SSOT_PATH, exc)
        return {}
    universe: dict[str, SyntheticSpec] = {}
    for symbol, spec in raw.items():
        pyth = spec.get("pyth_feed_id")
        stork = spec.get("stork_asset_id")
        chainlink = spec.get("chainlink_feed")
        # `chainlink_covered` is kept only for back-compat; the honest signal is
        # whether a chainlink_feed is configured (null everywhere while Arc has
        # no price Data Feeds). Fall back to the legacy bool if present.
        chainlink_covered = chainlink is not None or bool(spec.get("chainlink_covered", False))
        universe[symbol] = SyntheticSpec(
            symbol=symbol,
            name=spec["name"],
            price_usd=float(spec["price_usd"]),
            decimals=int(spec.get("decimals", 6)),
            asset_class=str(spec["asset_class"]),
            yfinance_ticker=str(spec.get("yfinance_ticker") or symbol.removeprefix("s")),
            pyth_feed_id=str(pyth) if pyth else None,
            stork_asset_id=str(stork) if stork else None,
            chainlink_feed=str(chainlink) if chainlink else None,
            oracle_tier=str(spec.get("oracle_tier") or _derive_oracle_tier(pyth, stork, chainlink)),
            chainlink_covered=chainlink_covered,
        )
    return universe


def _derive_oracle_tier(pyth: object, stork: object, chainlink: object) -> str:
    """Expected on-chain oracle tier from the count of configured feeds.

    "quorum" if >=2 of {pyth, stork, chainlink} are set, "single_checked" if
    exactly 1, "admin" if 0. This is only a HINT from configured feeds — the
    authoritative tier is on-chain via ``QuorumPriceOracle.priceWithProvenance()``
    (#840).
    """
    configured = sum(1 for v in (pyth, stork, chainlink) if v)
    if configured >= 2:
        return "quorum"
    if configured == 1:
        return "single_checked"
    return "admin"


def _load_compliance_flagged() -> list[str]:
    try:
        payload = json.loads(_SSOT_PATH.read_text(encoding="utf-8"))
        return list(payload["_compliance_review"]["single_stock_synths_backtest_only"])
    except (FileNotFoundError, KeyError, ValueError, OSError) as exc:
        logger.error("could not load compliance-flagged single-stock list (%s)", exc)
        return []


# ─── Public surface ──────────────────────────────────────────────────
SYNTHETIC_UNIVERSE: dict[str, SyntheticSpec] = _load_universe()

ON_CHAIN_SYNTHS: list[str] = sorted(SYNTHETIC_UNIVERSE.keys())

# Single-name equity synths that are intentionally NOT on the live on-chain
# path. They remain backtest-only (present in GLOBAL_ASSETS) pending a
# securities/derivatives compliance review — see synthetic_universe.json
# `_compliance_review`. Do not add any of these to the live universe without
# sign-off.
COMPLIANCE_FLAGGED_SINGLE_STOCKS: frozenset[str] = frozenset(_load_compliance_flagged())


def synthetics_for_deploy() -> list[tuple[str, str, int]]:
    """Return ``(name, symbol, price_int)`` tuples for the on-chain deploy path.

    Drop-in replacement for the old ``deploy_contracts.SYNTHETICS`` literal.
    Sorted by symbol for deterministic deploy ordering.
    """
    return [
        (spec.name, spec.symbol, spec.price_int) for spec in sorted(SYNTHETIC_UNIVERSE.values(), key=lambda s: s.symbol)
    ]


def chainlink_covered_synths() -> list[str]:
    """Synth symbols whose underlying has a Chainlink data feed."""
    return sorted(s for s, spec in SYNTHETIC_UNIVERSE.items() if spec.chainlink_covered)
