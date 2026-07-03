"""Reproducible generator for the cascade-aligned synthetic universe (T1.5b / #759).

This is the SSOT *builder*: it holds a CURATED asset list by class (the judgment
part — all yfinance-tickerable, NO single-name equities on the live path) and
resolves each entry's ``pyth_feed_id`` PROGRAMMATICALLY against the live Pyth
Hermes catalog (https://hermes.pyth.network/v2/price_feeds). It never invents a
feed id: a symbol with no real Pyth match gets ``pyth_feed_id: null``.

It emits, deterministically (sorted by synth symbol):

  * the SSOT ``synthetics`` block written into
    ``backend/archimedes/data/synthetic_universe.json`` (the ``--write`` path
    rewrites that file's ``synthetics`` + top-level ``_chainlink_note`` while
    preserving ``_comment`` / ``_compliance_review``), and
  * the ``GLOBAL_ASSETS`` block for
    ``services/strategy_signal_evaluator.py`` (printed with ``--print-global-assets``
    so a human can paste the generated live-synth rows).

Honest per-source oracle schema (replaces the misleading ``chainlink_covered``):
each entry carries ``yfinance_ticker`` (REQUIRED, backtest source + parity key),
``pyth_feed_id`` (REAL Pyth Hermes id or null), ``stork_asset_id`` (null for now —
no public Stork catalog to resolve yet), ``chainlink_feed`` (null EVERYWHERE — Arc
has no price Data Feeds yet, only CCIP as of 2026-06-30), and a DERIVED
``oracle_tier`` hint. The authoritative tier is on-chain via
``QuorumPriceOracle.priceWithProvenance()`` (#840).

Usage::

    # resolve against the live Pyth catalog and print a coverage report:
    PYTHONPATH=backend python -m archimedes.scripts.generate_universe --report
    # rewrite the SSOT synthetics block in-place (deterministic):
    PYTHONPATH=backend python -m archimedes.scripts.generate_universe --write
    # emit the GLOBAL_ASSETS live rows for the evaluator:
    PYTHONPATH=backend python -m archimedes.scripts.generate_universe --print-global-assets

    # offline / hermetic: point at a cached catalog file instead of the network:
    PYTHONPATH=backend python -m archimedes.scripts.generate_universe --report \
        --catalog /path/to/price_feeds.json

The ``--write`` path is gated on the #772 data-quality harness: every curated
yfinance ticker must pass ``verify_instrument`` over the 2y backtest window
(fetchable, coverage, gaps, survivorship) or the write refuses with per-ticker
reasons. ``--skip-data-quality`` bypasses the check (loudly) for offline runs.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

_PYTH_CATALOG_URL = "https://hermes.pyth.network/v2/price_feeds"
_SSOT_PATH = Path(__file__).resolve().parents[1] / "data" / "synthetic_universe.json"
_EVALUATOR_PATH = Path(__file__).resolve().parents[1] / "services" / "strategy_signal_evaluator.py"


@dataclass(frozen=True)
class Asset:
    """One curated instrument. ``pyth`` is the (asset_type, match_key) hint used
    to resolve a REAL Pyth feed id; None means "don't even try" (no Pyth product)."""

    synth: str  # on-chain synth symbol, e.g. "sSPY"
    name: str  # display name, e.g. "Synthetic SPY"
    yf: str  # yfinance ticker, e.g. "SPY"
    asset_class: str
    price: float  # reasonable seed price (USD)
    display: str  # GLOBAL_ASSETS display label
    exchange: str  # GLOBAL_ASSETS exchange label
    pyth: tuple[str, str] | None = None  # (asset_type, match_key) for Pyth resolution
    pyth_pair: str | None = field(default=None)  # FX full pair, e.g. "EURUSD"


# ─────────────────────────────────────────────────────────────────────────────
# CURATED UNIVERSE (the judgment part). No single-name equities on the live path.
# yf = yfinance ticker; pyth = (asset_type, base-ticker) hint for id resolution.
# For FX, pyth_pair carries the full "EURUSD"-style pair (Pyth keys FX by pair).
# ─────────────────────────────────────────────────────────────────────────────


def _eq(synth, name, yf, ac, price, disp, exch, pyth_base):
    """US-equity-ETF helper: Pyth resolves via ('Equity', base)."""
    return Asset(synth, name, yf, ac, price, disp, exch, pyth=("Equity", pyth_base) if pyth_base else None)


CURATED: list[Asset] = [
    # ── Broad-market US equity ETFs ──
    _eq("sSPY", "Synthetic SPY", "SPY", "us_equity_etf", 592.40, "SPY", "NYSE", "SPY"),
    _eq("sVOO", "Synthetic VOO", "VOO", "us_equity_etf", 545.10, "VOO", "NYSE", "VOO"),
    _eq("sIVV", "Synthetic IVV", "IVV", "us_equity_etf", 596.20, "IVV", "NYSE", "IVV"),
    _eq("sVTI", "Synthetic VTI", "VTI", "us_equity_etf", 293.40, "VTI", "NYSE", "VTI"),
    _eq("sITOT", "Synthetic ITOT", "ITOT", "us_equity_etf", 132.60, "ITOT", "NYSE", "ITOT"),
    _eq("sSCHB", "Synthetic SCHB", "SCHB", "us_equity_etf", 24.10, "SCHB", "NYSE", "SCHB"),
    _eq("sQQQ", "Synthetic QQQ", "QQQ", "us_equity_etf", 512.30, "QQQ", "NASDAQ", "QQQ"),
    _eq("sIWM", "Synthetic IWM", "IWM", "us_equity_etf", 228.70, "IWM", "NYSE", "IWM"),
    _eq("sDIA", "Synthetic DIA", "DIA", "us_equity_etf", 437.10, "DIA", "NYSE", "DIA"),
    _eq("sRSP", "Synthetic RSP", "RSP", "us_equity_etf", 182.30, "RSP", "NYSE", "RSP"),
    _eq("sMDY", "Synthetic MDY", "MDY", "us_equity_etf", 588.10, "MDY", "NYSE", None),
    _eq("sVO", "Synthetic VO", "VO", "us_equity_etf", 268.40, "VO", "NYSE", "VO"),
    _eq("sIJH", "Synthetic IJH", "IJH", "us_equity_etf", 64.80, "IJH", "NYSE", "IJH"),
    _eq("sIJR", "Synthetic IJR", "IJR", "us_equity_etf", 118.90, "IJR", "NYSE", "IJR"),
    _eq("sVB", "Synthetic VB", "VB", "us_equity_etf", 245.20, "VB", "NYSE", "VB"),
    # ── US sector ETFs (SPDR Select Sector) ──
    _eq("sXLE", "Synthetic XLE", "XLE", "us_sector_etf", 91.80, "XLE", "NYSE", "XLE"),
    _eq("sXLF", "Synthetic XLF", "XLF", "us_sector_etf", 49.60, "XLF", "NYSE", "XLF"),
    _eq("sXLK", "Synthetic XLK", "XLK", "us_sector_etf", 235.40, "XLK", "NYSE", "XLK"),
    _eq("sXLV", "Synthetic XLV", "XLV", "us_sector_etf", 146.20, "XLV", "NYSE", "XLV"),
    _eq("sXLI", "Synthetic XLI", "XLI", "us_sector_etf", 138.90, "XLI", "NYSE", "XLI"),
    _eq("sXLU", "Synthetic XLU", "XLU", "us_sector_etf", 78.40, "XLU", "NYSE", "XLU"),
    _eq("sXLP", "Synthetic XLP", "XLP", "us_sector_etf", 81.20, "XLP", "NYSE", "XLP"),
    _eq("sXLY", "Synthetic XLY", "XLY", "us_sector_etf", 218.60, "XLY", "NYSE", "XLY"),
    _eq("sXLB", "Synthetic XLB", "XLB", "us_sector_etf", 89.30, "XLB", "NYSE", "XLB"),
    _eq("sXLC", "Synthetic XLC", "XLC", "us_sector_etf", 104.70, "XLC", "NYSE", "XLC"),
    _eq("sXLRE", "Synthetic XLRE", "XLRE", "us_sector_etf", 42.10, "XLRE", "NYSE", None),
    # ── US thematic / industry ETFs ──
    _eq("sSMH", "Synthetic SMH", "SMH", "us_thematic_etf", 258.40, "SMH", "NASDAQ", "SMH"),
    _eq("sSOXX", "Synthetic SOXX", "SOXX", "us_thematic_etf", 228.90, "SOXX", "NASDAQ", "SOXX"),
    _eq("sXBI", "Synthetic XBI", "XBI", "us_thematic_etf", 92.60, "XBI", "NYSE", "XBI"),
    _eq("sKRE", "Synthetic KRE", "KRE", "us_thematic_etf", 62.30, "KRE", "NYSE", "KRE"),
    _eq("sXOP", "Synthetic XOP", "XOP", "us_thematic_etf", 138.20, "XOP", "NYSE", "XOP"),
    _eq("sITB", "Synthetic ITB", "ITB", "us_thematic_etf", 108.40, "ITB", "NYSE", "ITB"),
    _eq("sARKK", "Synthetic ARKK", "ARKK", "us_thematic_etf", 62.10, "ARKK", "NYSE", "ARKK"),
    _eq("sARKG", "Synthetic ARKG", "ARKG", "us_thematic_etf", 27.80, "ARKG", "NYSE", "ARKG"),
    _eq("sIGV", "Synthetic IGV", "IGV", "us_thematic_etf", 98.70, "IGV", "NASDAQ", "IGV"),
    _eq("sVGT", "Synthetic VGT", "VGT", "us_thematic_etf", 618.30, "VGT", "NYSE", "VGT"),
    _eq("sBOTZ", "Synthetic BOTZ", "BOTZ", "us_thematic_etf", 33.10, "BOTZ", "NASDAQ", "BOTZ"),
    _eq("sIYR", "Synthetic IYR", "IYR", "reit_etf", 98.40, "IYR", "NYSE", "IYR"),
    _eq("sVNQ", "Synthetic VNQ", "VNQ", "reit_etf", 92.10, "VNQ", "NYSE", None),
    _eq("sSCHH", "Synthetic SCHH", "SCHH", "reit_etf", 21.30, "SCHH", "NYSE", None),
    # ── Style / factor ETFs ──
    _eq("sMTUM", "Synthetic MTUM", "MTUM", "factor_etf", 224.60, "MTUM", "NYSE", "MTUM"),
    _eq("sQUAL", "Synthetic QUAL", "QUAL", "factor_etf", 178.20, "QUAL", "NYSE", "QUAL"),
    _eq("sUSMV", "Synthetic USMV", "USMV", "factor_etf", 88.40, "USMV", "NYSE", "USMV"),
    _eq("sVLUE", "Synthetic VLUE", "VLUE", "factor_etf", 108.90, "VLUE", "NYSE", "VLUE"),
    _eq("sVIG", "Synthetic VIG", "VIG", "factor_etf", 197.30, "VIG", "NYSE", "VIG"),
    _eq("sVYM", "Synthetic VYM", "VYM", "factor_etf", 128.60, "VYM", "NYSE", "VYM"),
    _eq("sSCHD", "Synthetic SCHD", "SCHD", "factor_etf", 27.80, "SCHD", "NYSE", "SCHD"),
    _eq("sNOBL", "Synthetic NOBL", "NOBL", "factor_etf", 102.40, "NOBL", "NYSE", "NOBL"),
    _eq("sDGRO", "Synthetic DGRO", "DGRO", "factor_etf", 63.10, "DGRO", "NYSE", "DGRO"),
    _eq("sIWF", "Synthetic IWF (Growth)", "IWF", "factor_etf", 418.20, "IWF", "NYSE", "IWF"),
    _eq("sIWD", "Synthetic IWD (Value)", "IWD", "factor_etf", 189.40, "IWD", "NYSE", "IWD"),
    _eq("sVUG", "Synthetic VUG", "VUG", "factor_etf", 428.10, "VUG", "NYSE", "VUG"),
    _eq("sVTV", "Synthetic VTV", "VTV", "factor_etf", 178.30, "VTV", "NYSE", "VTV"),
    _eq("sSPLV", "Synthetic SPLV", "SPLV", "factor_etf", 73.40, "SPLV", "NYSE", None),
    # ── International / regional / country equity ETFs ──
    _eq("sVEA", "Synthetic VEA", "VEA", "intl_equity_etf", 52.10, "VEA", "NYSE", "VEA"),
    _eq("sVWO", "Synthetic VWO", "VWO", "em_equity_etf", 46.30, "VWO", "NYSE", "VWO"),
    _eq("sEFA", "Synthetic EFA", "EFA", "intl_equity_etf", 84.20, "EFA", "NYSE", "EFA"),
    _eq("sEEM", "Synthetic EEM", "EEM", "em_equity_etf", 44.10, "EEM", "NYSE", "EEM"),
    _eq("sIEFA", "Synthetic IEFA", "IEFA", "intl_equity_etf", 75.60, "IEFA", "NYSE", "IEFA"),
    _eq("sIEMG", "Synthetic IEMG", "IEMG", "em_equity_etf", 56.30, "IEMG", "NYSE", "IEMG"),
    _eq("sVXUS", "Synthetic VXUS", "VXUS", "intl_equity_etf", 66.40, "VXUS", "NASDAQ", "VXUS"),
    _eq("sVT", "Synthetic VT", "VT", "intl_equity_etf", 122.30, "VT", "NYSE", "VT"),
    _eq("sACWI", "Synthetic ACWI", "ACWI", "intl_equity_etf", 118.70, "ACWI", "NASDAQ", None),
    _eq("sEWG", "Synthetic EWG (Germany)", "EWG", "eu_equity_etf", 35.40, "EWG", "NYSE", None),
    _eq("sEWU", "Synthetic EWU (UK)", "EWU", "eu_equity_etf", 38.20, "EWU", "NYSE", None),
    _eq("sEWQ", "Synthetic EWQ (France)", "EWQ", "eu_equity_etf", 43.90, "EWQ", "NYSE", None),
    _eq("sEWI", "Synthetic EWI (Italy)", "EWI", "eu_equity_etf", 41.30, "EWI", "NYSE", None),
    _eq("sEWP", "Synthetic EWP (Spain)", "EWP", "eu_equity_etf", 34.60, "EWP", "NYSE", None),
    _eq("sEWD", "Synthetic EWD (Sweden)", "EWD", "eu_equity_etf", 41.80, "EWD", "NYSE", None),
    _eq("sEWN", "Synthetic EWN (Netherlands)", "EWN", "eu_equity_etf", 62.40, "EWN", "NYSE", None),
    _eq("sEZU", "Synthetic EZU (Eurozone)", "EZU", "eu_equity_etf", 53.70, "EZU", "NYSE", None),
    _eq("sEWJ", "Synthetic EWJ (Japan)", "EWJ", "asia_equity_etf", 73.20, "EWJ", "NYSE", "EWJ"),
    _eq("sEWY", "Synthetic EWY (Korea)", "EWY", "asia_equity_etf", 58.30, "EWY", "NYSE", "EWY"),
    _eq("sEWT", "Synthetic EWT (Taiwan)", "EWT", "asia_equity_etf", 52.10, "EWT", "NYSE", None),
    _eq("sEWH", "Synthetic EWH (Hong Kong)", "EWH", "asia_equity_etf", 18.40, "EWH", "NYSE", "EWH"),
    _eq("sMCHI", "Synthetic MCHI (China)", "MCHI", "asia_equity_etf", 51.40, "MCHI", "NASDAQ", "MCHI"),
    _eq("sFXI", "Synthetic FXI (China large)", "FXI", "asia_equity_etf", 31.20, "FXI", "NASDAQ", None),
    _eq("sKWEB", "Synthetic KWEB (China tech)", "KWEB", "asia_equity_etf", 33.60, "KWEB", "NYSE", "KWEB"),
    _eq("sINDA", "Synthetic INDA (India)", "INDA", "asia_equity_etf", 55.80, "INDA", "NASDAQ", "INDA"),
    _eq("sEPI", "Synthetic EPI (India)", "EPI", "asia_equity_etf", 47.30, "EPI", "NYSE", None),
    _eq("sEWA", "Synthetic EWA (Australia)", "EWA", "asia_equity_etf", 25.10, "EWA", "NYSE", None),
    _eq("sEWZ", "Synthetic EWZ (Brazil)", "EWZ", "latam_equity_etf", 28.40, "EWZ", "NYSE", "EWZ"),
    _eq("sEWW", "Synthetic EWW (Mexico)", "EWW", "latam_equity_etf", 51.20, "EWW", "NYSE", None),
    _eq("sILF", "Synthetic ILF (LatAm)", "ILF", "latam_equity_etf", 22.30, "ILF", "NYSE", None),
    _eq("sECH", "Synthetic ECH (Chile)", "ECH", "latam_equity_etf", 26.10, "ECH", "NYSE", None),
    _eq("sEWC", "Synthetic EWC (Canada)", "EWC", "intl_equity_etf", 43.80, "EWC", "NYSE", None),
    _eq("sTUR", "Synthetic TUR (Turkey)", "TUR", "tr_equity_etf", 28.70, "TUR", "NYSE", None),
    _eq("sEZA", "Synthetic EZA (South Africa)", "EZA", "intl_equity_etf", 44.60, "EZA", "NYSE", None),
    _eq("sEPOL", "Synthetic EPOL (Poland)", "EPOL", "eu_equity_etf", 27.30, "EPOL", "NYSE", None),
    _eq("sEWL", "Synthetic EWL (Switzerland)", "EWL", "eu_equity_etf", 52.10, "EWL", "NYSE", None),
    _eq("sEWO", "Synthetic EWO (Austria)", "EWO", "eu_equity_etf", 23.40, "EWO", "NYSE", None),
    _eq("sEWK", "Synthetic EWK (Belgium)", "EWK", "eu_equity_etf", 21.80, "EWK", "NYSE", None),
    _eq("sEIRL", "Synthetic EIRL (Ireland)", "EIRL", "eu_equity_etf", 68.10, "EIRL", "NYSE", None),
    _eq("sEWS", "Synthetic EWS (Singapore)", "EWS", "asia_equity_etf", 22.60, "EWS", "NYSE", None),
    _eq("sEWM", "Synthetic EWM (Malaysia)", "EWM", "asia_equity_etf", 23.10, "EWM", "NYSE", None),
    _eq("sTHD", "Synthetic THD (Thailand)", "THD", "asia_equity_etf", 68.40, "THD", "NYSE", None),
    _eq("sEIDO", "Synthetic EIDO (Indonesia)", "EIDO", "asia_equity_etf", 19.30, "EIDO", "NYSE", None),
    _eq("sENZL", "Synthetic ENZL (New Zealand)", "ENZL", "asia_equity_etf", 47.20, "ENZL", "NASDAQ", None),
    _eq("sDXJ", "Synthetic DXJ (Japan hedged)", "DXJ", "asia_equity_etf", 108.40, "DXJ", "NYSE", None),
    _eq("sASHR", "Synthetic ASHR (China A-shares)", "ASHR", "asia_equity_etf", 27.80, "ASHR", "NYSE", None),
    _eq("sVNM", "Synthetic VNM (Vietnam)", "VNM", "asia_equity_etf", 12.40, "VNM", "NYSE", "VNM"),
    _eq("sARGT", "Synthetic ARGT (Argentina)", "ARGT", "latam_equity_etf", 78.20, "ARGT", "NYSE", None),
    _eq("sGXG", "Synthetic GXG (Colombia)", "GXG", "latam_equity_etf", 26.10, "GXG", "NYSE", None),
    # ── Bond + credit ETFs ──
    _eq("sTLT", "Synthetic TLT (20+yr)", "TLT", "us_bond_long", 92.30, "TLT", "NYSE", "TLT"),
    _eq("sIEF", "Synthetic IEF (7-10yr)", "IEF", "us_bond_mid", 95.10, "IEF", "NYSE", "IEF"),
    _eq("sSHY", "Synthetic SHY (1-3yr)", "SHY", "us_bond_short", 82.40, "SHY", "NYSE", "SHY"),
    _eq("sBIL", "Synthetic BIL (T-Bills)", "BIL", "us_bond_tbill", 91.60, "BIL", "NYSE", "BIL"),
    _eq("sSHV", "Synthetic SHV (short T)", "SHV", "us_bond_tbill", 110.30, "SHV", "NASDAQ", "SHV"),
    _eq("sGOVT", "Synthetic GOVT (Treasuries)", "GOVT", "us_bond_agg", 22.60, "GOVT", "NYSE", "GOVT"),
    _eq("sTIP", "Synthetic TIP (TIPS)", "TIP", "us_bond_tips", 108.70, "TIP", "NYSE", None),
    _eq("sVTIP", "Synthetic VTIP (short TIPS)", "VTIP", "us_bond_tips", 48.10, "VTIP", "NASDAQ", None),
    _eq("sAGG", "Synthetic AGG (Aggregate)", "AGG", "us_bond_agg", 98.20, "AGG", "NYSE", "AGG"),
    _eq("sBND", "Synthetic BND (Aggregate)", "BND", "us_bond_agg", 72.80, "BND", "NASDAQ", "BND"),
    _eq("sLQD", "Synthetic LQD (IG credit)", "LQD", "credit_ig", 110.30, "LQD", "NYSE", "LQD"),
    _eq("sVCIT", "Synthetic VCIT (IG credit)", "VCIT", "credit_ig", 80.40, "VCIT", "NASDAQ", "VCIT"),
    _eq("sVCSH", "Synthetic VCSH (short IG)", "VCSH", "credit_ig", 76.90, "VCSH", "NASDAQ", "VCSH"),
    _eq("sHYG", "Synthetic HYG (HY credit)", "HYG", "credit_hy", 79.40, "HYG", "NYSE", "HYG"),
    _eq("sJNK", "Synthetic JNK (HY credit)", "JNK", "credit_hy", 96.80, "JNK", "NYSE", None),
    _eq("sEMB", "Synthetic EMB (EM bond)", "EMB", "em_bond", 91.80, "EMB", "NASDAQ", "EMB"),
    _eq("sBNDX", "Synthetic BNDX (intl bond)", "BNDX", "intl_bond", 48.60, "BNDX", "NASDAQ", "BNDX"),
    _eq("sMUB", "Synthetic MUB (US muni)", "MUB", "us_muni", 106.50, "MUB", "NYSE", "MUB"),
    _eq("sVTEB", "Synthetic VTEB (US muni)", "VTEB", "us_muni", 50.20, "VTEB", "NYSE", "VTEB"),
    _eq("sFLOT", "Synthetic FLOT (floating rate)", "FLOT", "credit_ig", 50.70, "FLOT", "NYSE", "FLOT"),
    _eq("sMBB", "Synthetic MBB (MBS)", "MBB", "us_bond_agg", 93.10, "MBB", "NASDAQ", "MBB"),
    _eq("sMINT", "Synthetic MINT (ultrashort)", "MINT", "us_bond_tbill", 100.30, "MINT", "NYSE", "MINT"),
    _eq("sPFF", "Synthetic PFF (preferred)", "PFF", "credit_ig", 32.40, "PFF", "NASDAQ", None),
    _eq("sVGIT", "Synthetic VGIT (interm Treasury)", "VGIT", "us_bond_mid", 58.60, "VGIT", "NASDAQ", "VGIT"),
    _eq("sVGLT", "Synthetic VGLT (long Treasury)", "VGLT", "us_bond_long", 56.40, "VGLT", "NASDAQ", None),
    _eq("sVGSH", "Synthetic VGSH (short Treasury)", "VGSH", "us_bond_short", 58.20, "VGSH", "NASDAQ", None),
    _eq("sIUSB", "Synthetic IUSB (total bond)", "IUSB", "us_bond_agg", 44.30, "IUSB", "NASDAQ", "IUSB"),
    _eq("sBKLN", "Synthetic BKLN (leveraged loans)", "BKLN", "credit_hy", 21.10, "BKLN", "NYSE", None),
    _eq("sANGL", "Synthetic ANGL (fallen angels)", "ANGL", "credit_hy", 29.40, "ANGL", "NASDAQ", None),
    _eq("sEMLC", "Synthetic EMLC (EM local bond)", "EMLC", "em_bond", 23.60, "EMLC", "NYSE", None),
    _eq("sBWX", "Synthetic BWX (intl Treasury)", "BWX", "intl_bond", 22.10, "BWX", "NYSE", None),
    # ── Commodity / metals / energy / ag ETFs + spot metals ──
    _eq("sGLD", "Synthetic GLD (gold ETF)", "GLD", "metal_etf", 300.50, "GLD", "NYSE", "GLD"),
    _eq("sIAU", "Synthetic IAU (gold ETF)", "IAU", "metal_etf", 61.20, "IAU", "NYSE", "IAU"),
    _eq("sSLV", "Synthetic SLV (silver ETF)", "SLV", "metal_etf", 30.20, "SLV", "NYSE", "SLV"),
    _eq("sPPLT", "Synthetic PPLT (platinum ETF)", "PPLT", "metal_etf", 96.30, "PPLT", "NYSE", "PPLT"),
    _eq("sPALL", "Synthetic PALL (palladium ETF)", "PALL", "metal_etf", 92.10, "PALL", "NYSE", "PALL"),
    _eq("sGDX", "Synthetic GDX (gold miners)", "GDX", "metal_eq_etf", 40.80, "GDX", "NYSE", None),
    _eq("sGDXJ", "Synthetic GDXJ (jr gold miners)", "GDXJ", "metal_eq_etf", 51.60, "GDXJ", "NYSE", None),
    _eq("sDBC", "Synthetic DBC (broad commod)", "DBC", "commodity_etf", 22.40, "DBC", "NYSE", None),
    _eq("sGSG", "Synthetic GSG (broad commod)", "GSG", "commodity_etf", 21.30, "GSG", "NYSE", None),
    _eq("sPDBC", "Synthetic PDBC (broad commod)", "PDBC", "commodity_etf", 13.80, "PDBC", "NASDAQ", None),
    _eq("sDBA", "Synthetic DBA (agriculture)", "DBA", "commodity_etf", 25.60, "DBA", "NYSE", None),
    _eq("sDBB", "Synthetic DBB (base metals)", "DBB", "commodity_etf", 19.70, "DBB", "NYSE", None),
    _eq("sUSO", "Synthetic USO (oil ETF)", "USO", "energy_etf", 78.50, "USO", "NYSE", "USO"),
    _eq("sBNO", "Synthetic BNO (Brent oil ETF)", "BNO", "energy_etf", 28.40, "BNO", "NYSE", None),
    _eq("sUNG", "Synthetic UNG (natgas ETF)", "UNG", "energy_etf", 14.20, "UNG", "NYSE", None),
    _eq("sCORN", "Synthetic CORN (corn ETF)", "CORN", "agri_etf", 19.30, "CORN", "NYSE", None),
    _eq("sWEAT", "Synthetic WEAT (wheat ETF)", "WEAT", "agri_etf", 4.80, "WEAT", "NYSE", None),
    _eq("sSOYB", "Synthetic SOYB (soybean ETF)", "SOYB", "agri_etf", 22.10, "SOYB", "NYSE", None),
    _eq("sTAN", "Synthetic TAN (solar)", "TAN", "commodity_etf", 32.60, "TAN", "NYSE", None),
    _eq("sICLN", "Synthetic ICLN (clean energy)", "ICLN", "commodity_etf", 13.40, "ICLN", "NASDAQ", None),
    _eq("sXME", "Synthetic XME (metals & mining)", "XME", "metal_eq_etf", 68.40, "XME", "NYSE", None),
    _eq("sSIL", "Synthetic SIL (silver miners)", "SIL", "metal_eq_etf", 38.10, "SIL", "NYSE", None),
    _eq("sCOPX", "Synthetic COPX (copper miners)", "COPX", "metal_eq_etf", 41.30, "COPX", "NYSE", None),
    _eq("sURA", "Synthetic URA (uranium)", "URA", "commodity_etf", 31.20, "URA", "NYSE", None),
    _eq("sLIT", "Synthetic LIT (lithium/battery)", "LIT", "commodity_etf", 42.80, "LIT", "NYSE", None),
    _eq("sMOO", "Synthetic MOO (agribusiness)", "MOO", "agri_etf", 68.10, "MOO", "NYSE", None),
    _eq("sWOOD", "Synthetic WOOD (timber)", "WOOD", "commodity_etf", 82.40, "WOOD", "NASDAQ", None),
    # ── More thematic / industry ETFs ──
    _eq("sCIBR", "Synthetic CIBR (cybersecurity)", "CIBR", "us_thematic_etf", 68.30, "CIBR", "NASDAQ", None),
    _eq("sHACK", "Synthetic HACK (cybersecurity)", "HACK", "us_thematic_etf", 78.10, "HACK", "NYSE", None),
    _eq("sSKYY", "Synthetic SKYY (cloud)", "SKYY", "us_thematic_etf", 138.20, "SKYY", "NASDAQ", None),
    _eq("sFDN", "Synthetic FDN (internet)", "FDN", "us_thematic_etf", 268.40, "FDN", "NYSE", None),
    _eq("sIBB", "Synthetic IBB (biotech)", "IBB", "us_thematic_etf", 132.10, "IBB", "NASDAQ", None),
    _eq("sOIH", "Synthetic OIH (oil services)", "OIH", "us_thematic_etf", 288.60, "OIH", "NYSE", None),
    _eq("sJETS", "Synthetic JETS (airlines)", "JETS", "us_thematic_etf", 24.30, "JETS", "NYSE", None),
    _eq("sROBO", "Synthetic ROBO (robotics)", "ROBO", "us_thematic_etf", 58.40, "ROBO", "NYSE", None),
    _eq("sPAVE", "Synthetic PAVE (infrastructure)", "PAVE", "us_thematic_etf", 42.10, "PAVE", "NYSE", "PAVE"),
    _eq("sIFRA", "Synthetic IFRA (infrastructure)", "IFRA", "us_thematic_etf", 51.30, "IFRA", "NYSE", "IFRA"),
    _eq("sVNQI", "Synthetic VNQI (intl REIT)", "VNQI", "reit_etf", 43.60, "VNQI", "NASDAQ", None),
    # spot metals via Pyth Metal.* — yfinance backtest source is the COMEX/NYMEX
    # future (real, liquid tickers). Only metals with a genuine yfinance future are
    # included; illiquid base metals (Ni/Al/Li) lack a clean yfinance source and are
    # deliberately omitted rather than faked (yfinance_ticker must be real).
    Asset("sXAU", "Synthetic Gold (spot)", "GC=F", "metal_spot", 3250.00, "GOLD_SPOT", "COMEX", pyth=("Metal", "XAU")),
    Asset(
        "sXAG", "Synthetic Silver (spot)", "SI=F", "metal_spot", 32.40, "SILVER_SPOT", "COMEX", pyth=("Metal", "XAG")
    ),
    Asset(
        "sXPT", "Synthetic Platinum (spot)", "PL=F", "metal_spot", 980.00, "PLAT_SPOT", "NYMEX", pyth=("Metal", "XPT")
    ),
    Asset(
        "sXPD", "Synthetic Palladium (spot)", "PA=F", "metal_spot", 1010.00, "PALL_SPOT", "NYMEX", pyth=("Metal", "XPD")
    ),
    Asset("sXCU", "Synthetic Copper (spot)", "HG=F", "metal_spot", 4.30, "COPPER_SPOT", "COMEX", pyth=("Metal", "XCU")),
    # ── Volatility / hedge ETFs ──
    _eq("sVXX", "Synthetic VXX (VIX futures)", "VXX", "volatility_etf", 48.30, "VXX", "NYSE", "VXX"),
    _eq("sUVXY", "Synthetic UVXY (2x VIX)", "UVXY", "volatility_etf", 22.10, "UVXY", "NYSE", "UVXY"),
    _eq("sSVXY", "Synthetic SVXY (short VIX)", "SVXY", "volatility_etf", 44.60, "SVXY", "NYSE", "SVXY"),
    # ── Crypto majors (Pyth Crypto.<T>/USD) ──
    *[
        Asset(f"s{t}", f"Synthetic {t}", f"{t}-USD", "crypto", px, t, "Coinbase", pyth=("Crypto", t))
        for t, px in [
            ("BTC", 104500.00),
            ("ETH", 3850.00),
            ("SOL", 215.00),
            ("BNB", 685.00),
            ("XRP", 2.35),
            ("ADA", 1.05),
            ("AVAX", 42.30),
            ("DOGE", 0.38),
            ("DOT", 8.20),
            ("LINK", 24.60),
            ("POL", 0.52),
            ("LTC", 108.40),
            ("BCH", 512.30),
            ("ATOM", 8.90),
            ("UNI", 14.20),
            ("NEAR", 6.30),
            ("APT", 11.40),
            ("ARB", 0.92),
            ("OP", 2.10),
            ("SUI", 4.30),
            ("SEI", 0.48),
            ("INJ", 26.80),
            ("TIA", 6.70),
            ("FIL", 6.20),
            ("ICP", 12.30),
            ("ETC", 32.10),
            ("XLM", 0.44),
            ("HBAR", 0.31),
            ("VET", 0.058),
            ("ALGO", 0.42),
            ("AAVE", 340.20),
            ("RENDER", 8.60),
            ("IMX", 1.85),
            ("STX", 2.30),
            ("GRT", 0.28),
            ("SAND", 0.68),
            ("AXS", 8.40),
            ("RUNE", 6.10),
            ("THETA", 2.40),
            ("FLOW", 1.20),
            ("CRV", 1.10),
            ("LDO", 2.60),
            ("SNX", 3.20),
            ("KSM", 42.10),
            ("TON", 5.40),
            ("TRX", 0.26),
            ("KAS", 0.12),
            ("FET", 1.30),
            ("AR", 12.80),
            ("WLD", 2.60),
            ("ENA", 0.98),
            ("ONDO", 1.40),
            ("JUP", 0.88),
            ("PYTH", 0.38),
            ("JTO", 3.10),
            ("GALA", 0.036),
            ("CHZ", 0.088),
            ("ENJ", 0.24),
            ("ZEC", 52.30),
            ("DASH", 38.10),
            ("XTZ", 1.05),
            ("IOTA", 0.28),
            ("EGLD", 32.40),
            ("MINA", 0.62),
            ("ROSE", 0.098),
            ("KAVA", 0.46),
            ("QNT", 108.20),
            ("GMX", 24.10),
            ("DYDX", 1.40),
            ("APE", 1.20),
            ("YFI", 8600.00),
        ]
    ],
    # ── FX majors + crosses (Pyth FX.<pair>) ──
    *[
        Asset(
            f"s{pair}",
            f"Synthetic {pair[:3]}/{pair[3:]}",
            f"{pair}=X",
            "fx",
            px,
            f"{pair[:3]}/{pair[3:]}",
            "OTC",
            pyth=("FX", pair),
            pyth_pair=pair,
        )
        for pair, px in [
            ("EURUSD", 1.0850),
            ("GBPUSD", 1.2700),
            ("USDJPY", 157.20),
            ("USDCHF", 0.9050),
            ("USDCAD", 1.4350),
            ("AUDUSD", 0.6250),
            ("NZDUSD", 0.5650),
            ("USDCNH", 7.3100),
            ("USDHKD", 7.7800),
            ("USDSGD", 1.3600),
            ("USDMXN", 20.4000),
            ("USDZAR", 18.7000),
            ("USDTRY", 39.1000),
            ("USDINR", 85.6000),
            ("USDBRL", 6.1500),
            ("USDSEK", 11.0500),
            ("USDNOK", 11.3800),
            ("USDPLN", 4.1200),
            ("USDHUF", 397.0000),
            ("USDCZK", 24.3000),
            ("EURGBP", 0.8540),
            ("EURJPY", 170.6000),
            ("EURCHF", 0.9820),
            ("GBPJPY", 199.6000),
            ("AUDJPY", 98.3000),
            ("EURSEK", 11.9900),
            ("EURNOK", 12.3500),
            ("USDKRW", 1445.0000),
            ("USDTHB", 34.5000),
            ("USDILS", 3.6400),
        ]
    ],
]


# ── Compliance-flagged single-name equities: stay backtest-only in GLOBAL_ASSETS ──
# (NOT in the live SSOT `synthetics`; preserved from the pre-#759 universe so the
# compliance invariant + backtest-only exception continue to hold). yfinance ticker,
# display, asset_class, exchange.
COMPLIANCE_SINGLE_STOCKS: dict[str, tuple[str, str, str, str]] = {
    "sAAPL": ("AAPL", "AAPL", "us_stock", "NASDAQ"),
    "sMSFT": ("MSFT", "MSFT", "us_stock", "NASDAQ"),
    "sGOOGL": ("GOOGL", "GOOGL", "us_stock", "NASDAQ"),
    "sAMZN": ("AMZN", "AMZN", "us_stock", "NASDAQ"),
    "sNVDA": ("NVDA", "NVDA", "us_stock", "NASDAQ"),
    "sMETA": ("META", "META", "us_stock", "NASDAQ"),
    "sTSLA": ("TSLA", "TSLA", "us_stock", "NASDAQ"),
    "sAMD": ("AMD", "AMD", "us_stock", "NASDAQ"),
    "sAVGO": ("AVGO", "AVGO", "us_stock", "NASDAQ"),
    "sORCL": ("ORCL", "ORCL", "us_stock", "NYSE"),
    "sCRM": ("CRM", "CRM", "us_stock", "NYSE"),
    "sNFLX": ("NFLX", "NFLX", "us_stock", "NASDAQ"),
    "sJPM": ("JPM", "JPM", "us_stock", "NYSE"),
    "sBAC": ("BAC", "BAC", "us_stock", "NYSE"),
    "sGS": ("GS", "GS", "us_stock", "NYSE"),
    "sV": ("V", "V", "us_stock", "NYSE"),
    "sMA": ("MA", "MA", "us_stock", "NYSE"),
    "sBRK-B": ("BRK-B", "BRK.B", "us_stock", "NYSE"),
    "sLLY": ("LLY", "LLY", "us_stock", "NYSE"),
    "sUNH": ("UNH", "UNH", "us_stock", "NYSE"),
    "sJNJ": ("JNJ", "JNJ", "us_stock", "NYSE"),
    "sMRK": ("MRK", "MRK", "us_stock", "NYSE"),
    "sPFE": ("PFE", "PFE", "us_stock", "NYSE"),
    "sXOM": ("XOM", "XOM", "us_stock", "NYSE"),
    "sCVX": ("CVX", "CVX", "us_stock", "NYSE"),
    "sCOP": ("COP", "COP", "us_stock", "NYSE"),
    "sWMT": ("WMT", "WMT", "us_stock", "NYSE"),
    "sCOST": ("COST", "COST", "us_stock", "NASDAQ"),
    "sHD": ("HD", "HD", "us_stock", "NYSE"),
    "sPG": ("PG", "PG", "us_stock", "NYSE"),
    "sCOIN": ("COIN", "COIN", "us_stock", "NASDAQ"),
    "sMSTR": ("MSTR", "MSTR", "us_stock", "NASDAQ"),
    "sPLTR": ("PLTR", "PLTR", "us_stock", "NASDAQ"),
    "sASML": ("ASML", "ASML", "eu_stock", "AMS/NASDAQ"),
    "sSAP": ("SAP", "SAP", "eu_stock", "XETRA/NYSE"),
    "sNESN": ("NESN.SW", "NESN", "eu_stock", "SIX"),
    "sNOVO": ("NVO", "NVO", "eu_stock", "NYSE/Copenhagen"),
    "sAZN": ("AZN", "AZN", "eu_stock", "LSE/NASDAQ"),
    "sSHEL": ("SHEL", "SHEL", "eu_stock", "LSE/NYSE"),
    "sBP": ("BP", "BP", "eu_stock", "LSE/NYSE"),
    "sHSBC": ("HSBC", "HSBC", "eu_stock", "LSE/NYSE"),
    "sTTE": ("TTE", "TTE", "eu_stock", "Euronext/NYSE"),
    "sRHM": ("RHM.DE", "RHM", "eu_stock", "XETRA"),
    "sLVMH": ("MC.PA", "LVMH", "eu_stock", "Euronext"),
    "sSIE": ("SIE.DE", "SIE", "eu_stock", "XETRA"),
    "sTSM": ("TSM", "TSM", "asia_stock", "TWSE/NYSE"),
    "sBABA": ("BABA", "BABA", "asia_stock", "NYSE"),
    "sTM": ("TM", "TM", "asia_stock", "TSE/NYSE"),
    "sSONY": ("SONY", "SONY", "asia_stock", "TSE/NYSE"),
    "sSE": ("SE", "SE", "asia_stock", "NYSE"),
    "sTCEHY": ("TCEHY", "TCEHY", "asia_stock", "OTC"),
    "sTHYAO": ("THYAO.IS", "THYAO", "tr_stock", "BIST"),
    "sKCHOL": ("KCHOL.IS", "KCHOL", "tr_stock", "BIST"),
    "sGARAN": ("GARAN.IS", "GARAN", "tr_stock", "BIST"),
    "sASELS": ("ASELS.IS", "ASELS", "tr_stock", "BIST"),
    "sAKBNK": ("AKBNK.IS", "AKBNK", "tr_stock", "BIST"),
    "sSAHOL": ("SAHOL.IS", "SAHOL", "tr_stock", "BIST"),
    "sBIMAS": ("BIMAS.IS", "BIMAS", "tr_stock", "BIST"),
    "sEREGL": ("EREGL.IS", "EREGL", "tr_stock", "BIST"),
}


_COMMENT = (
    "Single source of truth (SSOT) for the on-chain synthetic universe. T1.5 — backtest==on-chain "
    "parity invariant. GENERATED by backend/archimedes/scripts/generate_universe.py (T1.5b / #759) — "
    "curated asset list resolved against the live Pyth Hermes catalog; regenerate with "
    "`PYTHONPATH=backend python -m archimedes.scripts.generate_universe --write`. Each entry under "
    "`synthetics` is one tradable synth that lives on BOTH the on-chain path (deploy_contracts.py "
    "SYNTHETICS is derived from here) AND the backtest path (every symbol here is present in "
    "strategy_signal_evaluator.GLOBAL_ASSETS via its `yfinance_ticker` and so is backtestable). The "
    "parity invariant in backend/tests/test_universe_parity.py fails CI if the two ever diverge. "
    "HONEST oracle schema (Chainlink-only, #724): `yfinance_ticker` is REQUIRED (backtest source + "
    "parity key); `chainlink_feed` is the live on-chain price source when configured (null for now — "
    "Arc has no Chainlink Data Feeds yet, only CCIP as of 2026-06-30); `pyth_feed_id` is a REAL Pyth "
    "Hermes id or null (never fabricated), retained as REFERENCE metadata only — NOT on the live "
    "oracle path; `stork_asset_id` is null (multi-source quorum dropped 2026-07-01). `oracle_tier` is "
    "the live PriceOracle tier: `chainlink` when a Data Feed is configured for the synth, else `admin`. "
    "Add-only by convention; do not rename or repurpose existing symbols. Single-stock synths are NOT "
    "live here — see `_compliance_review` below."
)

_CHAINLINK_NOTE = (
    "Oracle: Chainlink-only via the on-chain PriceOracle (#724) — one Chainlink AggregatorV3Interface "
    "Data Feed per synth, with a bounded admin-push fallback (yfinance-derived) when no feed is "
    "configured or it reads stale/out-of-band. Chainlink price Data Feeds are NOT yet deployed on Arc "
    "testnet (only CCIP as of 2026-06-30) — `chainlink_feed` is null pending Arc publishing feed "
    "addresses; wiring is one `setPriceFeed` call away. Until then the live source is the bounded "
    "admin push, so `oracle_tier` is `admin` everywhere (it becomes `chainlink` per-synth once its "
    "feed is configured). The multi-source quorum (Pyth/Stork median) was dropped 2026-07-01; "
    "`pyth_feed_id` is retained as reference metadata only and is NOT on the live oracle path."
)


# ─────────────────────────────────────────────────────────────────────────────
# Pyth catalog resolution — REAL ids only; no match → None (never invent).
# ─────────────────────────────────────────────────────────────────────────────


def _catalog_is_bad(a: dict) -> bool:
    """Skip DEPRECATED and OVERNIGHT-hours feeds — they are not the canonical price."""
    desc = (a.get("description", "") or "").upper()
    disp = (a.get("display_symbol", "") or "").upper()
    return "DEPRECATED" in desc or "OVERNIGHT" in disp or "OVERNIGHT" in desc


def load_pyth_catalog(catalog_path: str | None = None) -> list[dict]:
    if catalog_path:
        return json.loads(Path(catalog_path).read_text(encoding="utf-8"))
    with urllib.request.urlopen(_PYTH_CATALOG_URL, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_pyth_index(feeds: list[dict]) -> dict[tuple[str, str], str]:
    """Map (asset_type, match_key) -> feed id (no 0x). Equity/Crypto/Metal key on
    the uppercased `base` ticker; FX keys on the full pair (base+quote). USD-quoted
    only for Crypto/Metal (avoids e.g. SOL/ETH). First non-bad match wins."""
    idx: dict[tuple[str, str], str] = {}
    fx_seen: dict[str, str] = {}
    for f in feeds:
        a = f["attributes"]
        if _catalog_is_bad(a):
            continue
        at = a.get("asset_type")
        base = (a.get("base") or "").upper()
        quote = (a.get("quote_currency") or "").upper()
        fid = f["id"]
        if at == "FX":
            sym = a.get("symbol", "")  # FX.EUR/USD
            if sym.startswith("FX."):
                pair = sym[3:].replace("/", "").upper()
                fx_seen.setdefault(pair, fid)
            continue
        if at in ("Crypto", "Metal") and quote != "USD":
            continue
        idx.setdefault((at, base), fid)
    for pair, fid in fx_seen.items():
        idx.setdefault(("FX", pair), fid)
    return idx


def resolve_pyth_id(asset: Asset, idx: dict[tuple[str, str], str]) -> str | None:
    """Return the REAL Pyth feed id (0x-prefixed) for this asset, or None."""
    if asset.pyth is None:
        return None
    at, key = asset.pyth
    if at == "FX":
        key = (asset.pyth_pair or key).upper()
    fid = idx.get((at, key.upper()))
    if fid is None:
        return None
    return fid if fid.startswith("0x") else f"0x{fid}"


# ─────────────────────────────────────────────────────────────────────────────
# Build the SSOT synthetics block + GLOBAL_ASSETS rows.
# ─────────────────────────────────────────────────────────────────────────────


def _oracle_tier(chainlink: str | None) -> str:
    # Chainlink-only oracle (#724 PriceOracle): the live per-synth tier is `chainlink`
    # when a Chainlink Data Feed is configured, else the bounded admin fallback. The
    # multi-source quorum (pyth/stork median) was dropped 2026-07-01.
    return "chainlink" if chainlink else "admin"


def build_synthetics(idx: dict[tuple[str, str], str]) -> dict[str, dict]:
    """Return the SSOT `synthetics` block, sorted by symbol, honest schema."""
    _assert_no_dupes()
    _assert_no_single_names()
    out: dict[str, dict] = {}
    for a in sorted(CURATED, key=lambda x: x.synth):
        pyth = resolve_pyth_id(a, idx)
        stork = None  # no public Stork catalog to resolve yet (#828 follow-up)
        chainlink = None  # Arc has no price Data Feeds yet — null everywhere
        out[a.synth] = {
            "name": a.name,
            "price_usd": a.price,
            "decimals": 6,
            "asset_class": a.asset_class,
            "yfinance_ticker": a.yf,
            "pyth_feed_id": pyth,
            "stork_asset_id": stork,
            "chainlink_feed": chainlink,
            "oracle_tier": _oracle_tier(chainlink),
        }
    return out


def build_global_assets_rows() -> list[tuple[str, tuple[str, str, str, str]]]:
    """GLOBAL_ASSETS live rows: synth -> (yfinance_ticker, display, asset_class, exchange)."""
    return [(a.synth, (a.yf, a.display, a.asset_class, a.exchange)) for a in sorted(CURATED, key=lambda x: x.synth)]


def vet_data_quality(window_years: int = 2, _downloader=None):
    """Run the #772 data-quality harness over every CURATED yfinance ticker.

    The window matches the evaluator's backtest fetch (period="2y" in
    ``_fetch_price_history``), so a ticker is admitted only if the history the
    backtests will actually consume is fetchable, covers the window, has no
    large gaps, and still trades near the window end. ``_downloader`` is the
    test seam — injected so tests never touch the network.
    """
    import pandas as pd

    from archimedes.services.data_quality import verify_universe

    end = pd.Timestamp.today().normalize()
    start = end - pd.DateOffset(years=window_years)
    tickers = sorted({a.yf for a in CURATED})
    return verify_universe(tickers, start, end, _downloader=_downloader)


def _dq_gate(report) -> None:
    """Refuse admission when any ticker fails the harness. Bad data gets
    excluded at the source (the curator fixes or drops the entry), never
    padded into the SSOT (#772 anti-goal)."""
    if report.rejected:
        lines = [f"  {t}: {'; '.join(reasons)}" for t, reasons in sorted(report.rejected.items())]
        raise SystemExit(
            f"data-quality gate: {len(report.rejected)} ticker(s) failed verification "
            f"({len(report.admitted)} passed) — refusing to write the SSOT.\n"
            + "\n".join(lines)
            + "\nFix or drop the entries in CURATED, or rerun with --skip-data-quality (offline only)."
        )


def _assert_no_dupes() -> None:
    seen = set()
    for a in CURATED:
        if a.synth in seen:
            raise SystemExit(f"duplicate synth in CURATED: {a.synth}")
        seen.add(a.synth)


def _assert_no_single_names() -> None:
    banned = {"us_stock", "eu_stock", "asia_stock", "tr_stock", "latam_stock"}
    bad = [a.synth for a in CURATED if a.asset_class in banned]
    if bad:
        raise SystemExit(f"single-name equities on the live path (forbidden): {bad}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _write_ssot(idx: dict[tuple[str, str], str]) -> None:
    payload = json.loads(_SSOT_PATH.read_text(encoding="utf-8"))
    payload["_comment"] = _COMMENT
    payload["_chainlink_note"] = _CHAINLINK_NOTE
    payload["synthetics"] = build_synthetics(idx)
    # 2-space indent, sorted synthetics already; keep a trailing newline.
    _SSOT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {_SSOT_PATH} ({len(payload['synthetics'])} synths)")


def _print_global_assets() -> None:
    for synth, (yf, disp, ac, exch) in build_global_assets_rows():
        print(f'    "{synth}": ("{yf}", "{disp}", "{ac}", "{exch}"),')


def _report(idx: dict[tuple[str, str], str]) -> None:
    synths = build_synthetics(idx)
    by_class: dict[str, int] = defaultdict(int)
    n_pyth = 0
    ambiguous: list[str] = []
    for s in synths.values():
        by_class[s["asset_class"]] += 1
        if s["pyth_feed_id"]:
            n_pyth += 1
    print(f"TOTAL live synths: {len(synths)}")
    print(f"WITH real pyth_feed_id: {n_pyth} · NULL pyth: {len(synths) - n_pyth}")
    print("\nBy asset_class:")
    for ac in sorted(by_class):
        print(f"  {ac:20} {by_class[ac]}")
    # Any curated asset that DECLARED a pyth hint but got no match is worth flagging.
    for a in sorted(CURATED, key=lambda x: x.synth):
        if a.pyth is not None and resolve_pyth_id(a, idx) is None:
            ambiguous.append(f"{a.synth} ({a.pyth[0]}:{a.pyth_pair or a.pyth[1]})")
    print(f"\nDeclared-a-Pyth-hint-but-no-match ({len(ambiguous)}):")
    for x in ambiguous:
        print(f"  {x}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    catalog_path = None
    if "--catalog" in argv:
        i = argv.index("--catalog")
        catalog_path = argv[i + 1]
    feeds = load_pyth_catalog(catalog_path)
    idx = build_pyth_index(feeds)
    if "--write" in argv:
        if "--skip-data-quality" in argv:
            print("WARNING: --skip-data-quality set — writing the SSOT without the #772 harness check")
        else:
            report = vet_data_quality()
            _dq_gate(report)
            print(f"data-quality gate: all {len(report.admitted)} tickers passed")
        _write_ssot(idx)
    elif "--print-global-assets" in argv:
        # Same #772 gate as --write: GLOBAL_ASSETS is the runtime universe the
        # backtests actually fetch against (these printed rows get hand-pasted
        # into strategy_signal_evaluator.py), so an unvetted ticker here
        # reaches production more directly than one in the SSOT JSON.
        if "--skip-data-quality" in argv:
            print("WARNING: --skip-data-quality set — printing GLOBAL_ASSETS without the #772 harness check")
        else:
            report = vet_data_quality()
            _dq_gate(report)
            print(f"data-quality gate: all {len(report.admitted)} tickers passed")
        _print_global_assets()
    else:  # default = report
        _report(idx)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
