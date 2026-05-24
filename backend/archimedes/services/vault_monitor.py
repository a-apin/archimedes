"""Vault monitoring service — tracks metrics over time.

Provides:
- Periodic snapshot collection (called from agent_runner tick)
- Vault health assessment (AUM trends, rebalance staleness, oracle freshness)
- API helpers for the monitoring dashboard and SSE stream
"""

from __future__ import annotations

import logging
import math as _math
from datetime import UTC, datetime

from archimedes.chain.executor import chain_executor
from archimedes.services.redis_state import AgentStateStore

logger = logging.getLogger(__name__)

_MIN_SNAPSHOTS_FOR_SHARPE = 5
_ANNUALIZATION_FACTOR = 252  # trading days per year
_MCLEAN_PONTIFF_DECAY = 0.42  # expected Sharpe retention post-publication


def compute_sharpe_drift(
    aum_snapshots: list[dict],
    backtest_sharpe: float,
    snapshot_interval_minutes: float = 5.0,
) -> dict:
    """Compare rolling live Sharpe against backtested baseline.

    Uses AUM history to compute live returns and Sharpe, then checks for
    significant divergence from the backtest baseline.

    Returns a dict with keys: live_sharpe, backtest_sharpe, decay_floor,
    drift_sigma, status ("NORMAL"|"WARNING"|"CRITICAL"|"INSUFFICIENT_DATA").
    """
    # Need at least a few snapshots to compute returns
    if len(aum_snapshots) < _MIN_SNAPSHOTS_FOR_SHARPE:
        return {
            "live_sharpe": None,
            "backtest_sharpe": backtest_sharpe,
            "decay_floor": round(backtest_sharpe * _MCLEAN_PONTIFF_DECAY, 4),
            "drift_sigma": None,
            "status": "INSUFFICIENT_DATA",
        }

    # Compute period returns from AUM history (most-recent first in snapshots)
    navs = [s.get("aum_usdc", 0) or s.get("share_price", 0) for s in reversed(aum_snapshots)]
    returns = [(navs[i] - navs[i - 1]) / navs[i - 1] for i in range(1, len(navs)) if navs[i - 1] > 0]

    if len(returns) < _MIN_SNAPSHOTS_FOR_SHARPE - 1:
        return {
            "live_sharpe": None,
            "backtest_sharpe": backtest_sharpe,
            "decay_floor": round(backtest_sharpe * _MCLEAN_PONTIFF_DECAY, 4),
            "drift_sigma": None,
            "status": "INSUFFICIENT_DATA",
        }

    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / max(len(returns) - 1, 1)
    std_r = _math.sqrt(var_r) if var_r > 0 else 0.0

    if std_r <= 0:
        # Zero variance — flat AUM series; Sharpe is undefined, not zero.
        return {
            "live_sharpe": None,
            "backtest_sharpe": backtest_sharpe,
            "decay_floor": round(backtest_sharpe * _MCLEAN_PONTIFF_DECAY, 4),
            "drift_sigma": None,
            "status": "INSUFFICIENT_DATA",
        }

    # Annualize from snapshot-period returns using periods per year
    periods_per_year = (24 * 60 / snapshot_interval_minutes) * _ANNUALIZATION_FACTOR
    live_sharpe = (mean_r / std_r) * _math.sqrt(periods_per_year)

    decay_floor = backtest_sharpe * _MCLEAN_PONTIFF_DECAY

    # Drift in units of backtest SE (Lo 2002 approximation), using
    # snapshot-period units consistently with the observed returns series.
    sr_period = backtest_sharpe / _math.sqrt(periods_per_year)
    n_obs_period = len(returns)
    se_backtest = _math.sqrt((1 + 0.5 * sr_period**2) * periods_per_year / max(n_obs_period, 1))
    drift_sigma = (live_sharpe - backtest_sharpe) / se_backtest if se_backtest > 0 else 0.0

    if live_sharpe >= decay_floor:
        status = "NORMAL"
    elif live_sharpe >= decay_floor * 0.5:
        status = "WARNING"
    else:
        status = "CRITICAL"

    return {
        "live_sharpe": round(live_sharpe, 4),
        "backtest_sharpe": round(backtest_sharpe, 4),
        "decay_floor": round(decay_floor, 4),
        "drift_sigma": round(drift_sigma, 4),
        "status": status,
    }


class VaultMonitor:
    """Collects and serves vault monitoring data."""

    def __init__(self) -> None:
        self.state = AgentStateStore()

    async def snapshot_all_vaults(self) -> list[dict]:
        """Snapshot metrics for every vault. Called once per agent tick."""
        snapshots: list[dict] = []
        try:
            vaults = await chain_executor.get_all_vaults()
        except Exception as e:
            logger.warning("Cannot fetch vaults for monitoring: %s", e)
            return snapshots

        for vault_addr in vaults:
            try:
                metrics = await chain_executor.get_vault_metrics(vault_addr)
                snapshot = {
                    "vault_address": vault_addr,
                    "aum_usdc": metrics["total_aum_usdc"],
                    "share_price": metrics["share_price_usdc"],
                    "tier": metrics["tier"],
                    "paused": metrics["paused"],
                    "is_agent_assisted": metrics["is_agent_assisted"],
                }
                await self.state.save_vault_snapshot(vault_addr, snapshot)
                snapshots.append(snapshot)
            except Exception as e:
                logger.debug("Snapshot failed for %s: %s", vault_addr[:10], e)

        return snapshots

    async def get_vault_health(self, vault_address: str) -> dict:
        """Compute health indicators for a single vault."""
        snapshots = await self.state.get_vault_snapshots(vault_address, count=50)
        last_rebalance = await self.state.get_last_rebalance(vault_address)
        heartbeat = await self.state.get_heartbeat()
        events = await self.state.get_events(count=10)

        # AUM trend (latest vs 1h ago — ~12 snapshots at 5min interval)
        aum_trend = 0.0
        if len(snapshots) >= 2:
            latest_aum = snapshots[0].get("aum_usdc", 0)
            old_aum = snapshots[min(11, len(snapshots) - 1)].get("aum_usdc", 0)
            if old_aum > 0:
                aum_trend = ((latest_aum - old_aum) / old_aum) * 100

        # Rebalance staleness
        rebalance_age_seconds = None
        if last_rebalance:
            rebalance_age_seconds = (datetime.now(UTC) - last_rebalance).total_seconds()

        # Agent alive?
        agent_alive = False
        if heartbeat:
            try:
                hb_time = datetime.fromisoformat(heartbeat)
                age = (datetime.now(UTC) - hb_time).total_seconds()
                agent_alive = age < 600  # alive if heartbeat < 10min old
            except Exception:
                pass

        # Sharpe drift requires a strategy-specific backtest baseline. Until that
        # baseline is wired in, do not compute drift from a hard-coded value.
        sharpe_drift = {
            "available": False,
            "reason": "baseline_backtest_sharpe_unavailable",
        }

        return {
            "vault_address": vault_address,
            "agent_alive": agent_alive,
            "last_heartbeat": heartbeat,
            "last_rebalance": last_rebalance.isoformat() if last_rebalance else None,
            "rebalance_age_seconds": rebalance_age_seconds,
            "aum_trend_pct": round(aum_trend, 4),
            "snapshot_count": len(snapshots),
            "latest_snapshot": snapshots[0] if snapshots else None,
            "sharpe_drift": sharpe_drift,
            "recent_events": [
                e
                for e in events
                if e.get("data", {}).get("address", "").lower() == vault_address.lower()
                or e.get("type") in ("regime_change", "agent_error")
            ][:5],
        }

    async def get_all_vault_status(self) -> list[dict]:
        """Health summary across all vaults — for the monitoring dashboard."""
        try:
            vaults = await chain_executor.get_all_vaults()
        except Exception:
            return []

        results = []
        for v in vaults:
            try:
                health = await self.get_vault_health(v)
                results.append(health)
            except Exception:
                results.append({"vault_address": v, "error": True})
        return results

    async def close(self) -> None:
        await self.state.close()


# Module-level singleton
vault_monitor = VaultMonitor()
