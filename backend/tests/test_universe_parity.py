"""Backtest == on-chain universe parity invariant (T1.5).

The on-chain synthetic universe and the backtestable universe MUST stay in
lock-step. Historically they drifted: the on-chain ``SYNTHETICS`` list was a
tuple literal in ``scripts/deploy_contracts.py`` (7 names) while the
backtestable universe lived in ``services.strategy_signal_evaluator.GLOBAL_ASSETS``
(>100 names), with nothing keeping them consistent — a synth could be
deployable but not backtestable, or backtestable but never deployed, and no
test caught it.

This module makes the invariant CI-enforced. Both sides now derive from the
JSON SSOT (``backend/archimedes/data/synthetic_universe.json``); these tests
assert the SSOT-driven on-chain set is consistent with the backtest universe in
both directions, with the single documented exception that single-name equity
synths are intentionally backtest-only pending compliance review.

The invariants:

1. Every on-chain synth is backtestable (on-chain ⊆ GLOBAL_ASSETS).
2. No compliance-flagged single-stock synth is on the live on-chain path.
3. Every backtestable-but-not-on-chain synth is an explained
   compliance-flagged single-stock (the "vice-versa" direction, with its
   audited exception) — so a newly added backtest symbol cannot silently fail
   to reach on-chain unless it was deliberately flagged.
4. The live ``deploy_contracts.SYNTHETICS`` list is exactly the SSOT-derived
   set — i.e. the on-chain deploy path has no hand-edited drift from the SSOT.

These are pure in-memory set comparisons: hermetic, no DB / Redis / RPC / .env.
"""

from __future__ import annotations

import re
from pathlib import Path

from archimedes.services.strategy_signal_evaluator import GLOBAL_ASSETS
from archimedes.universe import (
    COMPLIANCE_FLAGGED_SINGLE_STOCKS,
    ON_CHAIN_SYNTHS,
    SYNTHETIC_UNIVERSE,
    synthetics_for_deploy,
)

# The backtestable universe = the synths the strategy/portfolio agents can rank
# and allocate over. GLOBAL_ASSETS is the canonical backtest universe.
BACKTEST_SYNTHS: frozenset[str] = frozenset(GLOBAL_ASSETS.keys())
ON_CHAIN: frozenset[str] = frozenset(ON_CHAIN_SYNTHS)


def test_global_assets_gated_on_data_quality_harness() -> None:
    """Every admitted universe ticker clears verify_instrument over the backtest
    window on a clean feed — proving GLOBAL_ASSETS is gated on the #772
    data-quality harness (the gate excludes poisoned data; it does not lower the
    DSR/PBO bar). Mocked at the yfinance boundary — no live network."""
    import numpy as np
    import pandas as pd
    from archimedes.services.strategy_signal_evaluator import verify_universe_data_quality

    start, end = "2022-01-03", "2023-12-29"

    def _clean_feed(ticker, s, e):
        idx = pd.bdate_range(s, e)
        return pd.Series(100.0 + np.arange(len(idx), dtype=float), index=idx)

    report = verify_universe_data_quality(start, end, _downloader=_clean_feed)
    # On a clean feed nothing is rejected — the harness admits the full universe
    # and (by construction) every admitted ticker passed verify_instrument.
    assert report.rejected == {}, f"clean feed must admit all tickers; rejected={report.rejected}"
    assert len(report.admitted) > 0
    assert all(report.verdicts[t].ok for t in report.admitted)


def test_data_quality_harness_excludes_bad_ticker() -> None:
    """The parity gate rejects an un-fetchable ticker rather than admitting it —
    the universe is not greened on data that cannot be fetched (#772)."""
    import pandas as pd
    from archimedes.services.strategy_signal_evaluator import verify_universe_data_quality

    start, end = "2022-01-03", "2023-12-29"

    def _all_empty(ticker, s, e):
        return pd.Series(dtype=float)

    report = verify_universe_data_quality(start, end, _downloader=_all_empty)
    assert report.admitted == ()
    assert len(report.rejected) > 0


def test_ssot_loaded_non_empty() -> None:
    # A silent SSOT-load failure (missing/garbled JSON) would make every other
    # parity check pass vacuously on an empty set — guard against it.
    assert len(SYNTHETIC_UNIVERSE) >= 200, (
        f"SSOT loaded only {len(SYNTHETIC_UNIVERSE)} synths — expected the cascade-aligned "
        "expanded universe (~280, #759). Did synthetic_universe.json fail to load?"
    )
    assert frozenset(SYNTHETIC_UNIVERSE.keys()) == ON_CHAIN


def test_every_on_chain_synth_is_backtestable() -> None:
    # Invariant 1: on-chain ⊆ backtest. Anything we can deploy, we can backtest.
    not_backtestable = ON_CHAIN - BACKTEST_SYNTHS
    assert not not_backtestable, (
        "On-chain synths are not present in the backtest universe (GLOBAL_ASSETS): "
        f"{sorted(not_backtestable)}. Add them to GLOBAL_ASSETS or remove from the SSOT."
    )


def test_no_compliance_flagged_single_stock_on_live_path() -> None:
    # Invariant 2: single-name equity synths are backtest-only pending review.
    flagged_on_live = ON_CHAIN & COMPLIANCE_FLAGGED_SINGLE_STOCKS
    assert not flagged_on_live, (
        "Compliance-flagged single-stock synths are on the live on-chain path: "
        f"{sorted(flagged_on_live)}. These need compliance sign-off before going live."
    )


def test_backtest_only_synths_are_all_compliance_flagged() -> None:
    # Invariant 3 (the "vice-versa"): every backtestable symbol that is NOT
    # on-chain must be explained by the compliance flag. An unexplained gap
    # means a backtest symbol was added without either deploying it on-chain or
    # flagging it — exactly the silent divergence this invariant exists to stop.
    backtest_only = BACKTEST_SYNTHS - ON_CHAIN
    unexplained = backtest_only - COMPLIANCE_FLAGGED_SINGLE_STOCKS
    assert not unexplained, (
        "Backtestable synths are neither on-chain nor compliance-flagged: "
        f"{sorted(unexplained)}. Either add them to the SSOT (live on-chain) or "
        "flag them in synthetic_universe.json `_compliance_review`."
    )


def test_compliance_flags_are_not_stale() -> None:
    # Every flagged single-stock should actually exist in the backtest universe;
    # a flag for a symbol no longer in GLOBAL_ASSETS is dead config.
    stale = COMPLIANCE_FLAGGED_SINGLE_STOCKS - BACKTEST_SYNTHS
    assert not stale, (
        f"Compliance-flag entries reference symbols absent from GLOBAL_ASSETS: {sorted(stale)}. Remove the stale flags."
    )


def test_deploy_synthetics_match_ssot_exactly() -> None:
    # Invariant 4: the live on-chain deploy list is exactly the SSOT-derived set,
    # with no hand-edited drift. Import the actual production module so a future
    # edit that re-hardcodes SYNTHETICS would fail here.
    from archimedes.scripts import deploy_contracts

    deploy_symbols = {sym for (_name, sym, _price) in deploy_contracts.SYNTHETICS}
    assert deploy_symbols == ON_CHAIN, (
        "deploy_contracts.SYNTHETICS drifted from the SSOT. "
        f"only-in-deploy={sorted(deploy_symbols - ON_CHAIN)} "
        f"only-in-ssot={sorted(ON_CHAIN - deploy_symbols)}"
    )
    # Also verify the deploy tuples carry positive integer prices (the contract
    # constructor takes a uint256 oracle price).
    for name, sym, price in deploy_contracts.SYNTHETICS:
        assert isinstance(price, int) and price > 0, f"{sym} has a non-positive integer price: {price!r}"
        assert name and sym.startswith("s"), f"malformed deploy entry: {(name, sym, price)}"


def test_synthetics_for_deploy_is_deterministic_and_sorted() -> None:
    # Deterministic deploy ordering avoids spurious diffs / nonce churn.
    tuples = synthetics_for_deploy()
    symbols = [sym for (_n, sym, _p) in tuples]
    assert symbols == sorted(symbols)
    assert len(symbols) == len(set(symbols)), "duplicate symbol in deploy list"


def test_universe_expanded_at_least_5x() -> None:
    # The T1.5 goal was to expand the on-chain universe 5–10X from the legacy 7 synths;
    # T1.5b (#759) grows it further to a cascade-aligned ~300-instrument universe. Floor at
    # 250 (the low end of the #759 accept range) — comfortably >5X the legacy 7.
    legacy_size = 7
    assert len(ON_CHAIN) >= 250, (
        f"On-chain universe is {len(ON_CHAIN)}; expected at least 250 "
        f"(cascade-aligned #759 target; >{5 * legacy_size} = 5X the legacy {legacy_size}-synth universe)."
    )


def test_explore_universe_matches_generate_picker_source() -> None:
    # Invariant 6 (#759 follow-up to #842): the Explore page's backend listing
    # (asset_market_service._explore_universe) iterates EXACTLY the deploy-eligible
    # SSOT set — the same source the Generate picker is generated from
    # (gen_ui_asset_universe renders ON_CHAIN_SYNTHS). Historically Explore
    # iterated the ~74-name DEFAULT_SCAN_UNIVERSE scan subset and silently
    # showed a stale fraction of the universe.
    from archimedes.scripts.gen_ui_asset_universe import expected_tickers
    from archimedes.services.asset_market_service import _explore_universe

    explore = _explore_universe()
    assert len(explore) == len(set(explore)), "duplicate symbol in Explore universe"
    assert frozenset(explore) == ON_CHAIN, (
        "Explore's universe drifted from the deploy-eligible SSOT set. "
        f"only-in-explore={sorted(frozenset(explore) - ON_CHAIN)} "
        f"only-in-ssot={sorted(ON_CHAIN - frozenset(explore))}"
    )
    # And the display-symbol projection is exactly the Generate picker's ticker set,
    # so both pages provably show the same universe.
    assert {GLOBAL_ASSETS[s][1] for s in explore} == expected_tickers()


def _generated_solidity_path() -> Path:
    import archimedes  # backend/archimedes/__init__.py → parents: archimedes, backend, <repo>

    return Path(archimedes.__file__).resolve().parents[2] / "contracts" / "src" / "generated" / "SyntheticUniverse.sol"


def test_generated_solidity_universe_matches_ssot() -> None:
    # Invariant 5 (#756): the Foundry deploy path reads the GENERATED Solidity library
    # contracts/src/generated/SyntheticUniverse.sol — assert it carries exactly the SSOT
    # symbol set with positive prices, so a `forge` redeploy can't silently ship the wrong
    # universe (previously Deploy.s.sol hardcoded 5 synths, incl. 2 compliance-flagged ones).
    text = _generated_solidity_path().read_text(encoding="utf-8")
    symbols = set(re.findall(r'symbols\[\d+\] = "(s[A-Za-z0-9-]+)"', text))
    prices = [int(p) for p in re.findall(r"prices\[\d+\] = (\d+);", text)]
    assert symbols == ON_CHAIN, (
        "generated SyntheticUniverse.sol drifted from the SSOT. "
        f"only-in-sol={sorted(symbols - ON_CHAIN)} only-in-ssot={sorted(ON_CHAIN - symbols)}"
    )
    assert len(prices) == len(ON_CHAIN), f"price count {len(prices)} != synth count {len(ON_CHAIN)}"
    assert all(p > 0 for p in prices), "generated SyntheticUniverse.sol has a non-positive price"


def test_generated_solidity_is_in_sync() -> None:
    # The committed generated file must be byte-identical to a fresh render — a SSOT edit
    # that forgets to regenerate fails CI here. Fix: regenerate with
    # `python -m archimedes.scripts.gen_solidity_universe`.
    from archimedes.scripts.gen_solidity_universe import render_solidity

    committed = _generated_solidity_path().read_text(encoding="utf-8")
    assert committed == render_solidity(), (
        "contracts/src/generated/SyntheticUniverse.sol is stale vs the SSOT — regenerate with "
        "`python -m archimedes.scripts.gen_solidity_universe`."
    )
