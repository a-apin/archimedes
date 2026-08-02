import json
from importlib import resources
from pathlib import Path

import archimedes_analytics_engine.instruments as instruments_mod
from archimedes_analytics_engine.instruments import (
    OPERATION_TO_SYMBOL,
    resolve_operations,
)
from archimedes_analytics_engine.strategy_loader import load_strategy

_STRATEGIES_DIR = Path(__file__).parent.parent / "strategies"


def test_operations_include_required_assets() -> None:
    # The original five operation assets must always be present.
    assert {"SPY", "NIKKEI", "GOLD", "TREASURY", "OIL"} <= set(OPERATION_TO_SYMBOL.keys())


def test_operations_include_pairs_legs() -> None:
    # ETF legs added for the Gatev et al. (2006) relative-value pairs strategy.
    assert {"GLD", "GDX", "IVV"} <= set(OPERATION_TO_SYMBOL.keys())


def test_operations_include_second_wave_pair_legs() -> None:
    # Phase 1.3 economic pairs: KO/PEP, EWA/EWC, GLD/SLV (GLD already present above).
    assert {"KO", "PEP", "EWA", "EWC", "SLV"} <= set(OPERATION_TO_SYMBOL.keys())


def test_operations_include_expanded_universe() -> None:
    # Expanded investable universe (2026-06): broad equity, fixed income / alts,
    # and the 9 SPDR sector ETFs.
    broad = {"QQQ", "IWM", "EFA", "EEM"}
    alts = {"IEF", "DBC", "VNQ"}
    sectors = {"XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"}
    assert (broad | alts | sectors) <= set(OPERATION_TO_SYMBOL.keys())


def test_resolve_operations_accepts_expanded_universe() -> None:
    # The expanded symbols resolve through the normal validation path.
    assert resolve_operations(["xlk", "vnq", "qqq"]) == ["XLK", "VNQ", "QQQ"]


def test_resolve_operations_rejects_unknown() -> None:
    try:
        resolve_operations(["SPY", "BAD"])
        raise AssertionError("Expected ValueError")
    except ValueError as exc:
        assert "Unsupported operation" in str(exc)


# ─── Single-source-of-truth loading (#463) ───────────────────────────


def test_operation_map_loaded_from_json_ssot() -> None:
    # OPERATION_TO_SYMBOL is loaded from the packaged data/instruments.json,
    # so it must equal that file's operation_to_symbol verbatim.
    raw = json.loads(
        resources.files("archimedes_analytics_engine").joinpath("data/instruments.json").read_text(encoding="utf-8")
    )
    assert raw["operation_to_symbol"] == OPERATION_TO_SYMBOL


def test_json_ssot_and_in_module_fallback_do_not_drift() -> None:
    # The in-module fallback must mirror the JSON SSOT exactly — drift between
    # the two is the failure mode the SSOT refactor exists to prevent (#463).
    assert OPERATION_TO_SYMBOL == instruments_mod._FALLBACK_OPERATION_TO_SYMBOL


def test_load_falls_back_when_resource_missing(monkeypatch) -> None:
    # If the packaged JSON cannot be read (e.g. a stripped install), the loader
    # must fall back to the in-module copy rather than raising on import.
    monkeypatch.setattr(instruments_mod, "_SSOT_RESOURCE", "data/does_not_exist.json")
    loaded = instruments_mod._load_operation_to_symbol()
    assert loaded == instruments_mod._FALLBACK_OPERATION_TO_SYMBOL


# ─── Declared-universe backfill (backtest-vol audit) ──────────────────────
#
# BIL (capital_preservation_tbill) and TLT (gatev_2006_portfolio_of_pairs)
# were the two ASSET_UNIVERSE tickers among the 34 curated strategies that
# were not yet operation keys — cli.run_command's declared-universe routing
# needs resolve_operations to succeed for every strategy's own universe.


def test_all_curated_strategies_declared_universe_resolves() -> None:
    strategy_files = sorted(_STRATEGIES_DIR.glob("*.py"))
    assert len(strategy_files) == 34, (
        f"expected 34 curated strategy files, found {len(strategy_files)} — "
        "update this test if the curated library size changed intentionally"
    )

    unresolved: dict[str, list[str]] = {}
    resolved_count = 0
    for path in strategy_files:
        bundle = load_strategy(path)
        universe = bundle.metadata.get("asset_universe") or []
        try:
            resolve_operations(universe)
            resolved_count += 1
        except ValueError:
            bad = [op for op in universe if op.upper() not in OPERATION_TO_SYMBOL]
            unresolved[path.name] = bad

    assert not unresolved, f"strategies with unresolved ASSET_UNIVERSE entries: {unresolved}"
    assert resolved_count == 34


def test_bil_and_tlt_are_operation_keys() -> None:
    # BIL: SPDR Bloomberg 1-3 Month T-Bill ETF (capital_preservation_tbill's cash leg).
    # TLT: iShares 20+ Year Treasury Bond ETF (gatev_2006_portfolio_of_pairs universe entry).
    assert OPERATION_TO_SYMBOL["BIL"] == "BIL"
    assert OPERATION_TO_SYMBOL["TLT"] == "TLT"
