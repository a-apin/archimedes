"""Strategy signal evaluator — paper-grounded strategies as live allocation signals.

Each strategy file in analytics-engine/strategies/ contains executable trading
logic encoded as a backtrader Strategy class. This module extracts the core
signal rule from each strategy and evaluates it against live market data,
producing a per-asset target weight.

No backtrader dependency — the signal rules are simple enough to evaluate
directly with numpy/pandas on a price DataFrame.

Design reference:
  - design.md § 4.3.2 (portfolio construction from strategy signals)
  - analytics-engine/strategies/*.py (source of truth for trading logic)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── Global asset universe ────────────────────────────────────────
# synth_symbol -> (yfinance_ticker, display_symbol, asset_class, exchange)
# Covers US equities (ETFs + individual stocks), European exchanges,
# Asian markets, Turkish exchange (BIST individual names),
# London-metal-aligned commodities, energy, fixed income, FX and crypto.
# Each entry is one tradable instrument; the agent ranks across all of them
# and the LLM portfolio agent can pick from any of them.
GLOBAL_ASSETS: dict[str, tuple[str, str, str, str]] = {
    # ── US equity ETFs ──
    "sSPY": ("SPY", "SPY", "us_equity_etf", "NYSE"),
    "sQQQ": ("QQQ", "QQQ", "us_equity_etf", "NASDAQ"),
    "sIWM": ("IWM", "IWM", "us_equity_etf", "NYSE"),
    "sDIA": ("DIA", "DIA", "us_equity_etf", "NYSE"),
    "sXLE": ("XLE", "XLE", "us_sector_etf", "NYSE"),
    "sXLF": ("XLF", "XLF", "us_sector_etf", "NYSE"),
    "sXLK": ("XLK", "XLK", "us_sector_etf", "NYSE"),
    "sXLV": ("XLV", "XLV", "us_sector_etf", "NYSE"),
    "sXLI": ("XLI", "XLI", "us_sector_etf", "NYSE"),
    "sXLU": ("XLU", "XLU", "us_sector_etf", "NYSE"),
    # ── US individual stocks (mega/large cap) ──
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
    # ── European individual stocks ──
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
    # ── European indices / equity ETFs ──
    "sEZU": ("EZU", "EZU", "eu_equity_etf", "NYSE"),
    "sEWG": ("EWG", "DAX_ETF", "eu_equity_etf", "NYSE"),
    "sEWU": ("EWU", "FTSE_ETF", "eu_equity_etf", "NYSE"),
    "sEWQ": ("EWQ", "CAC_ETF", "eu_equity_etf", "NYSE"),
    "sFTSE": ("^FTSE", "FTSE100", "eu_index", "LSE"),
    "sDAX": ("^GDAXI", "DAX40", "eu_index", "XETRA"),
    "sCAC": ("^FCHI", "CAC40", "eu_index", "Euronext"),
    # ── Asian individual stocks ──
    "sTSM": ("TSM", "TSM", "asia_stock", "TWSE/NYSE"),
    "sBABA": ("BABA", "BABA", "asia_stock", "NYSE"),
    "sTM": ("TM", "TM", "asia_stock", "TSE/NYSE"),
    "sSONY": ("SONY", "SONY", "asia_stock", "TSE/NYSE"),
    "sSE": ("SE", "SE", "asia_stock", "NYSE"),
    "sTCEHY": ("TCEHY", "TCEHY", "asia_stock", "OTC"),
    # ── Asian equity ETFs / indices ──
    "sEWJ": ("EWJ", "EWJ", "asia_equity_etf", "NYSE"),
    "sNKY": ("^N225", "NIKKEI", "asia_index", "TSE"),
    "sMCHI": ("MCHI", "MCHI", "asia_equity_etf", "NYSE"),
    "sINDA": ("INDA", "INDA", "asia_equity_etf", "NYSE"),
    "sEWY": ("EWY", "EWY", "asia_equity_etf", "NYSE"),
    "sEEM": ("EEM", "EEM", "em_equity_etf", "NYSE"),
    # ── Turkish individual stocks (BIST) ──
    "sTHYAO": ("THYAO.IS", "THYAO", "tr_stock", "BIST"),
    "sKCHOL": ("KCHOL.IS", "KCHOL", "tr_stock", "BIST"),
    "sGARAN": ("GARAN.IS", "GARAN", "tr_stock", "BIST"),
    "sASELS": ("ASELS.IS", "ASELS", "tr_stock", "BIST"),
    "sAKBNK": ("AKBNK.IS", "AKBNK", "tr_stock", "BIST"),
    "sSAHOL": ("SAHOL.IS", "SAHOL", "tr_stock", "BIST"),
    "sBIMAS": ("BIMAS.IS", "BIMAS", "tr_stock", "BIST"),
    "sEREGL": ("EREGL.IS", "EREGL", "tr_stock", "BIST"),
    # ── Turkish indices / ETFs ──
    "sTUR": ("TUR", "TUR_ETF", "tr_equity_etf", "NYSE"),
    "sBIST": ("XU100.IS", "BIST100", "tr_index", "BIST"),
    # ── Precious & base metals (London Metal Exchange aligned) ──
    "sGLD": ("GLD", "GLD", "metal_etf", "NYSE"),
    "sGOLD": ("GC=F", "GOLD_FUT", "metal_fut", "COMEX"),
    "sSLV": ("SLV", "SLV", "metal_etf", "NYSE"),
    "sSI": ("SI=F", "SILVER_FUT", "metal_fut", "COMEX"),
    "sPPLT": ("PPLT", "PLATINUM", "metal_etf", "NYSE"),
    "sPALL": ("PALL", "PALLADIUM", "metal_etf", "NYSE"),
    "sHG": ("HG=F", "COPPER_FUT", "metal_fut", "COMEX/LME"),
    "sGDX": ("GDX", "GDX", "metal_eq_etf", "NYSE"),
    "sGDXJ": ("GDXJ", "GDXJ", "metal_eq_etf", "NYSE"),
    # ── Energy ──
    "sUSO": ("USO", "USO", "energy_etf", "NYSE"),
    "sOIL": ("CL=F", "WTI_FUT", "energy_fut", "NYMEX"),
    "sBRENT": ("BZ=F", "BRENT_FUT", "energy_fut", "ICE"),
    "sUNG": ("UNG", "UNG", "energy_etf", "NYSE"),
    "sNG": ("NG=F", "NATGAS_FUT", "energy_fut", "NYMEX"),
    # ── Agricultural futures ──
    "sCORN": ("ZC=F", "CORN_FUT", "agri_fut", "CBOT"),
    "sWHEAT": ("ZW=F", "WHEAT_FUT", "agri_fut", "CBOT"),
    "sSOY": ("ZS=F", "SOY_FUT", "agri_fut", "CBOT"),
    # ── Fixed income (ETFs proxying individual maturities) ──
    "sTLT": ("TLT", "TLT", "us_bond_long", "NYSE"),  # 20+yr
    "sIEF": ("IEF", "IEF", "us_bond_mid", "NYSE"),  # 7-10yr
    "sSHY": ("SHY", "SHY", "us_bond_short", "NYSE"),  # 1-3yr
    "sBIL": ("BIL", "BIL", "us_bond_tbill", "NYSE"),  # 1-3mo T-Bills
    "sTIP": ("TIP", "TIP", "us_bond_tips", "NYSE"),  # TIPS
    "sAGG": ("AGG", "AGG", "us_bond_agg", "NYSE"),  # Aggregate
    "sHYG": ("HYG", "HYG", "credit_hy", "NYSE"),
    "sLQD": ("LQD", "LQD", "credit_ig", "NYSE"),
    "sEMB": ("EMB", "EMB", "em_bond", "NYSE"),
    "sMUB": ("MUB", "MUB", "us_muni", "NYSE"),
    # ── FX ──
    "sEURUSD": ("EURUSD=X", "EUR/USD", "fx", "OTC"),
    "sUSDTRY": ("USDTRY=X", "USD/TRY", "fx", "OTC"),
    "sGBPUSD": ("GBPUSD=X", "GBP/USD", "fx", "OTC"),
    "sUSDJPY": ("USDJPY=X", "USD/JPY", "fx", "OTC"),
    # ── Crypto ──
    "sBTC": ("BTC-USD", "BTC", "crypto", "Coinbase"),
    "sETH": ("ETH-USD", "ETH", "crypto", "Coinbase"),
    "sSOL": ("SOL-USD", "SOL", "crypto", "Coinbase"),
}


def synth_display(synth: str) -> str:
    """Return the human-facing label for a synth symbol."""
    entry = GLOBAL_ASSETS.get(synth)
    return entry[1] if entry else synth


def synth_asset_class(synth: str) -> str:
    entry = GLOBAL_ASSETS.get(synth)
    return entry[2] if entry else "unknown"


# Universe scanned by `rank_market()` to surface top opportunities.
# A focused subset (~70 names) covering all asset classes; the LLM
# portfolio agent is allowed to pick any asset in GLOBAL_ASSETS, not
# only what's in this scan list.
DEFAULT_SCAN_UNIVERSE: list[str] = [
    # US equity ETFs + sectors
    "sSPY",
    "sQQQ",
    "sIWM",
    "sXLE",
    "sXLF",
    "sXLK",
    "sXLV",
    "sXLI",
    # US individual mega/large caps (subset)
    "sAAPL",
    "sMSFT",
    "sGOOGL",
    "sAMZN",
    "sNVDA",
    "sMETA",
    "sTSLA",
    "sAMD",
    "sAVGO",
    "sJPM",
    "sV",
    "sLLY",
    "sUNH",
    "sXOM",
    "sCVX",
    "sWMT",
    "sCOST",
    "sCOIN",
    "sPLTR",
    # European stocks + indices
    "sASML",
    "sSAP",
    "sNOVO",
    "sAZN",
    "sSHEL",
    "sRHM",
    "sLVMH",
    "sSIE",
    "sFTSE",
    "sDAX",
    "sCAC",
    "sEZU",
    # Asian stocks + indices
    "sTSM",
    "sBABA",
    "sTM",
    "sSE",
    "sNKY",
    "sMCHI",
    "sINDA",
    "sEWY",
    "sEEM",
    # Turkish (BIST individual + index + FX)
    "sTHYAO",
    "sKCHOL",
    "sGARAN",
    "sASELS",
    "sBIST",
    "sTUR",
    "sUSDTRY",
    # Metals
    "sGLD",
    "sGOLD",
    "sSLV",
    "sSI",
    "sPPLT",
    "sPALL",
    "sHG",
    "sGDX",
    # Energy
    "sUSO",
    "sOIL",
    "sBRENT",
    "sNG",
    # Agri futures
    "sCORN",
    "sWHEAT",
    # Fixed income (maturity ladder)
    "sTLT",
    "sIEF",
    "sSHY",
    "sBIL",
    "sTIP",
    "sAGG",
    "sHYG",
    "sLQD",
    "sEMB",
    # FX
    "sEURUSD",
    "sGBPUSD",
    "sUSDJPY",
    # Crypto
    "sBTC",
    "sETH",
    "sSOL",
]


# ─── Price cache (module-level, TTL-bounded) ───────────────────────
# yfinance is the bottleneck; without caching the advisor endpoint
# would refetch the full universe on every request.  TTL is short
# enough that intraday signals stay fresh.
_PRICE_CACHE: dict[str, tuple[pd.Series, float]] = {}
_CACHE_TTL_SEC = 600  # 10 minutes


def _cache_get(synth: str) -> pd.Series | None:
    entry = _PRICE_CACHE.get(synth)
    if entry is None:
        return None
    series, ts = entry
    if (time.time() - ts) > _CACHE_TTL_SEC:
        return None
    return series


def _cache_put(synth: str, series: pd.Series) -> None:
    _PRICE_CACHE[synth] = (series, time.time())


class Signal(Enum):
    """Strategy signal for a single asset."""

    LONG = "long"  # Full exposure
    FLAT = "flat"  # No exposure (in cash/USDC)
    SCALED = "scaled"  # Partial exposure (vol-targeting)


@dataclass
class AssetSignal:
    """A single strategy's signal for one asset."""

    strategy_id: str
    strategy_name: str
    asset: str  # e.g. "sSPY"
    signal: Signal
    weight: float  # 0.0 to 1.0 — target portfolio fraction for this asset
    reason: str  # Human-readable explanation


@dataclass
class StrategySignals:
    """All signals from one strategy across all assets."""

    strategy_id: str
    strategy_name: str
    paper_title: str
    signals: list[AssetSignal]

    @property
    def total_weight(self) -> float:
        return sum(s.weight for s in self.signals)


# ─── Price data helper ────────────────────────────────────────────


def _fetch_price_history(symbol: str, period: str = "2y", interval: str = "1d") -> pd.Series:
    """Fetch daily closing prices for a single synth symbol (with cache).

    Used as a fallback; the batched variant below is preferred for
    multi-asset scans.
    """
    cached = _cache_get(symbol)
    if cached is not None:
        return cached

    entry = GLOBAL_ASSETS.get(symbol)
    if not entry:
        return pd.Series(dtype=float)
    yf_ticker = entry[0]

    try:
        import yfinance as yf

        data = yf.download(
            yf_ticker,
            period=period,
            interval=interval,
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if data.empty:
            return pd.Series(dtype=float)
        close = data["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.name = symbol
        close = close.dropna()
        _cache_put(symbol, close)
        return close
    except Exception as e:
        logger.warning("Failed to fetch price history for %s (%s): %s", symbol, yf_ticker, e)
        return pd.Series(dtype=float)


def _fetch_price_histories(
    symbols: list[str],
    period: str = "2y",
) -> dict[str, pd.Series]:
    """Batch-fetch price histories for many synth symbols.

    Uses a single yfinance.download() call for everything that is not
    already in cache.  Failed/empty tickers are simply omitted from
    the result.
    """
    result: dict[str, pd.Series] = {}
    # First pass: serve from cache
    to_fetch_synths: list[str] = []
    for sym in symbols:
        cached = _cache_get(sym)
        if cached is not None and not cached.empty:
            result[sym] = cached
        else:
            to_fetch_synths.append(sym)

    if not to_fetch_synths:
        return result

    # Build the yfinance ticker list (skip unknown synths)
    ticker_for_synth: dict[str, str] = {}
    for sym in to_fetch_synths:
        entry = GLOBAL_ASSETS.get(sym)
        if entry:
            ticker_for_synth[sym] = entry[0]
    if not ticker_for_synth:
        return result

    yf_tickers = list(ticker_for_synth.values())
    try:
        import yfinance as yf

        data = yf.download(
            tickers=" ".join(yf_tickers),
            period=period,
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
    except Exception as e:
        logger.warning("Batched yfinance fetch failed: %s", e)
        # Fall back to per-symbol fetch for the ones we still need
        for sym in to_fetch_synths:
            series = _fetch_price_history(sym, period=period)
            if not series.empty:
                result[sym] = series
        return result

    # Unpack the batched response (yfinance returns a MultiIndex
    # frame when given >1 ticker; a flat frame when given 1).
    if data is None or len(data) == 0:
        return result

    if len(yf_tickers) == 1:
        sole_synth = next(iter(ticker_for_synth))
        try:
            close = data["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            close.name = sole_synth
            if not close.empty:
                _cache_put(sole_synth, close)
                result[sole_synth] = close
        except Exception as e:
            logger.warning("Failed to extract Close for %s: %s", sole_synth, e)
        return result

    for synth, yf_ticker in ticker_for_synth.items():
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if yf_ticker not in data.columns.get_level_values(0):
                    continue
                close = data[yf_ticker]["Close"]
            else:
                close = data["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = close.dropna()
            if close.empty:
                continue
            close.name = synth
            _cache_put(synth, close)
            result[synth] = close
        except Exception as e:
            logger.warning("Failed to extract %s (%s): %s", synth, yf_ticker, e)

    return result


# ─── Signal evaluators — one per paper strategy ────────────────────
#
# Each evaluator is a pure function:
#   (asset_symbol, price_history) → AssetSignal
#
# The logic mirrors the backtrader Strategy class in the corresponding
# analytics-engine/strategies/ file.


def _faber_sma200_signal(
    strategy_id: str,
    asset: str,
    prices: pd.Series,
) -> AssetSignal:
    """Faber 2007 — long when close > SMA200, flat otherwise.

    Mirrors: FaberSMA200.next() in faber_2007_sma200_timing.py
    Rule: price > 200-day SMA → long; else → flat
    """
    sma_period = 200
    if len(prices) < sma_period + 1:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Faber SMA200",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"Insufficient data ({len(prices)} bars, need {sma_period + 1})",
        )

    sma = prices.rolling(sma_period).mean()
    current_price = float(prices.iloc[-1])
    current_sma = float(sma.iloc[-1])

    if current_price > current_sma:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Faber SMA200",
            asset=asset,
            signal=Signal.LONG,
            weight=1.0,
            reason=f"Price {current_price:.2f} > SMA200 {current_sma:.2f} → long",
        )
    else:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Faber SMA200",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"Price {current_price:.2f} ≤ SMA200 {current_sma:.2f} → flat",
        )


def _vol_managed_signal(
    strategy_id: str,
    asset: str,
    prices: pd.Series,
    target_vol_annual: float = 0.15,
) -> AssetSignal:
    """Moreira & Muir 2017 — scale exposure by target_vol / realized_vol.

    Mirrors: VolatilityManagedLong.next() in moreira_muir_2017_volatility_managed.py
    Rule: exposure = min(target_vol / realized_vol_annual, 1.0)
    """
    vol_window = 22
    annualization = 252

    if len(prices) < vol_window + 1:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Vol-Managed",
            asset=asset,
            signal=Signal.SCALED,
            weight=0.5,
            reason=f"Insufficient data ({len(prices)} bars), defaulting to 50%",
        )

    # Compute realized volatility from daily returns
    returns = prices.pct_change().dropna().tail(vol_window)
    if len(returns) < 2:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Vol-Managed",
            asset=asset,
            signal=Signal.SCALED,
            weight=0.5,
            reason="Too few returns, defaulting to 50%",
        )

    realized_vol = float(returns.std() * np.sqrt(annualization))
    if realized_vol <= 0:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="Vol-Managed",
            asset=asset,
            signal=Signal.LONG,
            weight=1.0,
            reason="Zero realized vol → full exposure",
        )

    exposure = min(target_vol_annual / realized_vol, 1.0)
    current_price = float(prices.iloc[-1])

    signal_type = Signal.LONG if exposure >= 0.95 else Signal.SCALED
    return AssetSignal(
        strategy_id=strategy_id,
        strategy_name="Vol-Managed",
        asset=asset,
        signal=signal_type,
        weight=exposure,
        reason=f"Realized vol {realized_vol:.1%}, target {target_vol_annual:.0%} → exposure {exposure:.0%} (price {current_price:.2f})",
    )


def _tsmom_signal(
    strategy_id: str,
    asset: str,
    prices: pd.Series,
) -> AssetSignal:
    """Moskowitz, Ooi, Pedersen 2012 — long when 12-month return positive.

    Mirrors: TimeSeriesMomentum.next() in moskowitz_ooi_pedersen_2012_tsmom.py
    Rule: trailing 252-day return > 0 → long; else → flat
    """
    lookback = 252

    if len(prices) < lookback + 1:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="TSMOM",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"Insufficient data ({len(prices)} bars, need {lookback + 1})",
        )

    current_price = float(prices.iloc[-1])
    past_price = float(prices.iloc[-lookback - 1])
    trailing_return = (current_price / past_price) - 1.0

    if trailing_return > 0:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="TSMOM",
            asset=asset,
            signal=Signal.LONG,
            weight=1.0,
            reason=f"12m return {trailing_return:+.1%} ({past_price:.2f} → {current_price:.2f}) → long",
        )
    else:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="TSMOM",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"12m return {trailing_return:+.1%} ({past_price:.2f} → {current_price:.2f}) → flat",
        )


def _52w_high_signal(
    strategy_id: str,
    asset: str,
    prices: pd.Series,
) -> AssetSignal:
    """George & Hwang 2004 — long when price is within 5% of 52-week high.

    Mirrors: FiftyTwoWeekHigh in george_hwang_2004_52w_high.py
    Rule: proximity = price / 52w_high; long if proximity >= 0.95
    """
    lookback = 252
    if len(prices) < lookback:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="52W High",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"Insufficient data ({len(prices)} bars, need {lookback})",
        )

    high_52w = float(prices.rolling(lookback).max().iloc[-1])
    current = float(prices.iloc[-1])
    proximity = current / high_52w if high_52w > 0 else 0.0

    if proximity >= 0.95:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="52W High",
            asset=asset,
            signal=Signal.LONG,
            weight=proximity,
            reason=f"Price {current:.2f} is {proximity:.0%} of 52w high {high_52w:.2f} → long",
        )
    else:
        return AssetSignal(
            strategy_id=strategy_id,
            strategy_name="52W High",
            asset=asset,
            signal=Signal.FLAT,
            weight=0.0,
            reason=f"Price {current:.2f} is only {proximity:.0%} of 52w high {high_52w:.2f} → flat",
        )


def _buy_hold_signal(
    strategy_id: str,
    asset: str,
    prices: pd.Series,
) -> AssetSignal:
    """Buy-and-hold baseline — always fully invested."""
    return AssetSignal(
        strategy_id=strategy_id,
        strategy_name="Buy & Hold",
        asset=asset,
        signal=Signal.LONG,
        weight=1.0,
        reason="Buy-and-hold: always fully invested",
    )


# ─── Strategy → evaluator mapping ─────────────────────────────────

# Maps strategy code_hash prefix to its evaluator function.
# In production, this would be auto-discovered from strategy metadata.
_STRATEGY_EVALUATORS: dict[str, callable] = {
    # faber_2007_sma200_timing.py
    "Faber": _faber_sma200_signal,
    "SMA200": _faber_sma200_signal,
    # moreira_muir_2017_volatility_managed.py
    "Volatility": _vol_managed_signal,
    "Vol-Managed": _vol_managed_signal,
    # moskowitz_ooi_pedersen_2012_tsmom.py
    "Time Series Momentum": _tsmom_signal,
    "TSMOM": _tsmom_signal,
    # george_hwang_2004_52w_high.py
    "52-Week": _52w_high_signal,
    "George": _52w_high_signal,
    "Hwang": _52w_high_signal,
    # pipeline_buy_hold.py
    "Buy-and-Hold": _buy_hold_signal,
}


def _get_evaluator(strategy_name: str, strategy_code_path: str | None = None) -> callable:
    """Find the evaluator function for a strategy by title or filename keyword match."""
    name_lower = strategy_name.lower()
    path_lower = (strategy_code_path or "").lower()
    combined = name_lower + " " + path_lower

    for keyword, evaluator in _STRATEGY_EVALUATORS.items():
        if keyword.lower() in combined:
            return evaluator
    # Default: buy-and-hold (always long)
    return _buy_hold_signal


# ─── Public API ────────────────────────────────────────────────────


class StrategySignalEvaluator:
    """Evaluates paper-grounded strategies against live market data.

    This IS the intelligence layer. No heuristics, no regime detector —
    the strategies themselves determine allocations through their
    published signal rules.
    """

    def evaluate_strategies(
        self,
        strategies: list,
        synth_assets: list[str],
        price_histories: dict[str, pd.Series] | None = None,
        scan_full_universe: bool = False,
    ) -> list[StrategySignals]:
        """Evaluate all strategies against live data.

        Args:
            strategies: List of Strategy dataclasses from LocalStrategyProvider
            synth_assets: List of synth symbols to evaluate (e.g. ["sSPY", "sTSLA"])
            price_histories: Optional pre-fetched price data. If None, fetches from yfinance.
            scan_full_universe: If True, every strategy evaluates against every
                asset in ``synth_assets`` regardless of its declared
                ``asset_universe``.  This is how the advisor "scans the market".

        Returns:
            List of StrategySignals, one per strategy.
        """
        # Fetch price histories if not provided
        if price_histories is None:
            price_histories = _fetch_price_histories(synth_assets, period="2y")

        if not price_histories:
            logger.warning("No price histories available — returning empty signals")
            return []

        # Map a strategy's declared asset_universe → synth symbols.
        synth_map = {
            "SPY": "sSPY",
            "QQQ": "sQQQ",
            "IWM": "sIWM",
            "TSLA": "sTSLA",
            "NVDA": "sNVDA",
            "BTC": "sBTC",
            "ETH": "sETH",
            "GOLD": "sGOLD",
            "SILVER": "sSI",
            "COPPER": "sHG",
            "OIL": "sOIL",
            "BRENT": "sBRENT",
            "NATGAS": "sNG",
            "NIKKEI": "sNKY",
            "TREASURY": "sBIL",
            "BIL": "sBIL",
            "DAX": "sDAX",
            "FTSE": "sFTSE",
            "CAC": "sCAC",
            "BIST": "sBIST",
            "TUR": "sTUR",
        }

        results: list[StrategySignals] = []

        for strategy in strategies:
            if scan_full_universe:
                # Scan every asset in the supplied universe (the agent
                # is searching the market, not just rerunning paper-listed tickers).
                strategy_synths = [s for s in synth_assets if s in price_histories]
            else:
                strategy_synths = []
                seen_synths: set[str] = set()
                for ticker in strategy.asset_universe:
                    sym = synth_map.get(ticker)
                    if sym and sym in price_histories and sym not in seen_synths:
                        strategy_synths.append(sym)
                        seen_synths.add(sym)
                if not strategy_synths:
                    strategy_synths = list(price_histories.keys())

            evaluator = _get_evaluator(strategy.paper_title, strategy.strategy_code_path)
            signals: list[AssetSignal] = []

            for asset in strategy_synths:
                prices = price_histories.get(asset)
                if prices is None or prices.empty:
                    continue
                signal = evaluator(strategy.id, asset, prices)
                signals.append(signal)

            if signals:
                results.append(
                    StrategySignals(
                        strategy_id=strategy.id,
                        strategy_name=strategy.paper_title,
                        paper_title=strategy.paper_title,
                        signals=signals,
                    )
                )

                logger.info(
                    "Strategy '%s' scanned %d assets (long on %d)",
                    strategy.paper_title,
                    len(signals),
                    sum(1 for s in signals if s.signal != Signal.FLAT and s.weight > 0),
                )

        return results

    def rank_market(
        self,
        price_histories: dict[str, pd.Series],
        lookback_days: int = 90,
        top_n: int = 12,
    ) -> list[dict]:
        """Rank assets by recent risk-adjusted return (Sharpe-like).

        Returns a list sorted desc by score, with a dict per asset:
          { synth, display, asset_class, score, momentum, vol_ann }
        Used by the advisor to give strategies a focused "scan list"
        of the most promising opportunities globally.
        """
        ranked: list[dict] = []
        for synth, series in price_histories.items():
            if series.empty or len(series) < lookback_days + 1:
                continue
            window = series.tail(lookback_days + 1)
            returns = window.pct_change().dropna()
            if len(returns) < 5:
                continue
            mean_d = float(returns.mean())
            std_d = float(returns.std())
            if std_d <= 0 or not np.isfinite(std_d):
                continue
            momentum = float(window.iloc[-1] / window.iloc[0] - 1.0)
            sharpe_like = (mean_d / std_d) * float(np.sqrt(252))
            vol_ann = std_d * float(np.sqrt(252))
            entry = GLOBAL_ASSETS.get(synth)
            ranked.append(
                {
                    "synth": synth,
                    "display": entry[1] if entry else synth,
                    "asset_class": entry[2] if entry else "unknown",
                    "exchange": entry[3] if entry else "?",
                    "score": round(sharpe_like, 4),
                    "momentum_90d": round(momentum, 4),
                    "vol_ann": round(vol_ann, 4),
                }
            )
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return ranked[:top_n]

    def aggregate_signals(
        self,
        all_signals: list[StrategySignals],
        usdc_floor: float = 0.20,
    ) -> dict[str, float]:
        """Aggregate strategy signals into target portfolio weights.

        Each strategy votes on each asset. Votes are averaged across
        strategies, then normalized to (1 - usdc_floor).

        Args:
            all_signals: Output of evaluate_strategies()
            usdc_floor: Minimum USDC allocation (0.0 to 1.0)

        Returns:
            Dict of symbol → target weight (all positive, sum to 1.0)
        """
        if not all_signals:
            return {"USDC": 1.0}

        # Collect votes per asset
        asset_votes: dict[str, list[float]] = {}
        for strat_signals in all_signals:
            for sig in strat_signals.signals:
                asset_votes.setdefault(sig.asset, []).append(sig.weight)

        # Average votes
        raw_weights: dict[str, float] = {}
        for asset, votes in asset_votes.items():
            raw_weights[asset] = sum(votes) / len(votes)

        # Scale to synth budget (1 - usdc_floor)
        synth_budget = 1.0 - usdc_floor
        total_raw = sum(raw_weights.values())

        normalized = {k: v / total_raw * synth_budget for k, v in raw_weights.items()} if total_raw > 0 else {}

        # Add USDC floor
        normalized["USDC"] = usdc_floor

        # Round and return
        return {k: round(v, 4) for k, v in sorted(normalized.items())}


# Singleton
strategy_evaluator = StrategySignalEvaluator()
