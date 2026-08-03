"""Investable-universe instrument definitions for the analytics engine.

The operation→symbol mapping is loaded from a single source of truth — the
packaged data file ``data/instruments.json`` — rather than from hardcoded
literals in this module (issue #463). ``OPERATION_TO_SYMBOL`` and
``resolve_operations`` remain the stable public API; only their backing store
moved to the JSON SSOT.

Universe notes (kept here for readers; the authoritative values live in
``data/instruments.json``):

* The original five operations (``SPY``, ``NIKKEI`` → ``^N225``, ``GOLD`` →
  ``GC=F``, ``TREASURY`` → ``TLT``, ``OIL`` → ``CL=F``).
* ETF legs for relative-value / pairs strategies (Gatev et al. 2006):
  ``GLD``/``GDX``/``IVV`` plus the second-wave economic pairs
  ``KO``/``PEP``, ``EWA``/``EWC``, ``GLD``/``SLV``.
* The expanded investable universe (2026-06): liquid, US-listed
  (synchronous-close) ETFs — broad equity building blocks (``QQQ``, ``IWM``,
  ``EFA``, ``EEM``), fixed-income / alternative diversifiers (``IEF``, ``DBC``,
  ``VNQ``), and the nine SPDR sector ETFs (``XLB``…``XLY``). See
  ``docs/specs/second-wave-universe-experiment.md`` for which composition suits
  which strategy.
* Declared-universe backfill (backtest-vol audit): ``BIL`` (SPDR 1-3 Month
  T-Bill ETF) and ``TLT`` (iShares 20+ Year Treasury Bond ETF) as direct
  operation keys, so every curated strategy's ``ASSET_UNIVERSE`` resolves
  through ``resolve_operations`` — see ``cli.run_command``.

If the JSON SSOT cannot be read (e.g. a stripped install that dropped package
data), the module falls back to an in-module copy so the package never fails to
import — the JSON file remains canonical when present.
"""

from __future__ import annotations

import json
import logging
from importlib import resources

logger = logging.getLogger(__name__)

_SSOT_PACKAGE = "archimedes_analytics_engine"
_SSOT_RESOURCE = "data/instruments.json"

# In-module fallback, used only if the packaged JSON SSOT cannot be read. Kept
# in sync with data/instruments.json (issue #463 keeps the JSON authoritative).
_FALLBACK_OPERATION_TO_SYMBOL: dict[str, str] = {
    "SPY": "SPY",
    "NIKKEI": "^N225",
    "GOLD": "GC=F",
    "TREASURY": "TLT",
    "OIL": "CL=F",
    "GLD": "GLD",
    "GDX": "GDX",
    "IVV": "IVV",
    "KO": "KO",
    "PEP": "PEP",
    "EWA": "EWA",
    "EWC": "EWC",
    "SLV": "SLV",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "EFA": "EFA",
    "EEM": "EEM",
    "IEF": "IEF",
    "DBC": "DBC",
    "VNQ": "VNQ",
    "XLB": "XLB",
    "XLE": "XLE",
    "XLF": "XLF",
    "XLI": "XLI",
    "XLK": "XLK",
    "XLP": "XLP",
    "XLU": "XLU",
    "XLV": "XLV",
    "XLY": "XLY",
    # Declared-universe backfill (backtest-vol audit): the two ASSET_UNIVERSE
    # tickers among the 34 curated strategies that were not yet operation keys
    # — capital_preservation_tbill declares BIL (SPDR 1-3 Month T-Bill ETF),
    # gatev_2006_portfolio_of_pairs declares TLT (iShares 20+ Year Treasury
    # Bond ETF) alongside its other direct-ticker entries. Added so
    # cli.run_command can resolve every declared universe; TREASURY -> TLT
    # (above) is a separate legacy alias and is kept unchanged.
    "BIL": "BIL",
    "TLT": "TLT",
}


def _load_operation_to_symbol() -> dict[str, str]:
    """Load the operation→symbol map from the packaged JSON SSOT.

    Falls back to the in-module copy if the resource is missing or malformed so
    that importing this module never raises.
    """
    try:
        raw = resources.files(_SSOT_PACKAGE).joinpath(_SSOT_RESOURCE).read_text(encoding="utf-8")
        payload = json.loads(raw)
        mapping = payload["operation_to_symbol"]
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("operation_to_symbol must be a non-empty object")
        return {str(k): str(v) for k, v in mapping.items()}
    except (FileNotFoundError, ModuleNotFoundError, KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "could not load instrument SSOT %s/%s (%s) — using in-module fallback",
            _SSOT_PACKAGE,
            _SSOT_RESOURCE,
            exc,
        )
        return dict(_FALLBACK_OPERATION_TO_SYMBOL)


OPERATION_TO_SYMBOL: dict[str, str] = _load_operation_to_symbol()


def resolve_operations(operations: list[str]) -> list[str]:
    normalized = [op.upper() for op in operations]
    unsupported = [op for op in normalized if op not in OPERATION_TO_SYMBOL]
    if unsupported:
        raise ValueError(f"Unsupported operation(s): {', '.join(unsupported)}")
    return normalized
