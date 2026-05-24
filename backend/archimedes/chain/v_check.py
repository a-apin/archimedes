"""V_check — Reasoning I/O contract (Xia et al. 2026 § 5).

Deterministic validity checks that run before ANY rebalance transaction
submission.  The LLM cannot override these — they are pure Python.

Checks:
  - ``weights_sum_bps``: weights must sum to exactly 10000 BPS.
  - ``max_concentration``: no single weight exceeds a threshold.
  - ``min_cost_benefit_bps``: expected improvement must exceed minimum.

If any check fails, the action is rejected regardless of LLM confidence.

Reference: Xia et al. 2026 (arxiv 2605.19337), § 5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCENTRATION_BPS = 6000  # 60% max single position
DEFAULT_MIN_COST_BENEFIT_BPS = 5  # 0.05% min expected improvement


@dataclass
class VCheckResult:
    """Result of a V_check validation run."""

    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


class VCheck:
    """Deterministic pre-trade validity checker.

    Every rebalance action MUST pass through this before tx submission.
    The checks are mechanical (no LLM) — the agent cannot override them.

    Parameters
    ----------
    weights_bps : dict[str, int]
        Target weights in basis points, e.g. ``{"sSPY": 5000, "sQQQ": 3000}``.
    max_concentration_bps : int
        Maximum allowed single-position weight in BPS (default 6000 = 60%).
    cost_benefit_bps : int | None
        Expected cost-benefit of the rebalance in BPS (optional).
    min_cost_benefit_bps : int
        Minimum cost-benefit to justify trading (default 5 BPS = 0.05%).
    """

    def __init__(
        self,
        *,
        weights_bps: dict[str, int] | None = None,
        max_concentration_bps: int = DEFAULT_MAX_CONCENTRATION_BPS,
        cost_benefit_bps: int | None = None,
        min_cost_benefit_bps: int = DEFAULT_MIN_COST_BENEFIT_BPS,
    ) -> None:
        self.weights_bps = weights_bps or {}
        self.max_concentration_bps = max_concentration_bps
        self.cost_benefit_bps = cost_benefit_bps
        self.min_cost_benefit_bps = min_cost_benefit_bps

    def run(self) -> VCheckResult:
        """Execute all validity checks.

        Returns
        -------
        VCheckResult
            ``passed=True`` only if ALL checks pass.
        """
        checks: dict[str, bool] = {}
        failures: list[str] = []

        # 1. Weights sum to 10000 BPS
        total = sum(self.weights_bps.values())
        sum_ok = total == 10000
        checks["weights_sum_bps"] = sum_ok
        if not sum_ok:
            failures.append(f"weights sum to {total} BPS, expected 10000")

        # 2. Max concentration
        max_weight = max(self.weights_bps.values()) if self.weights_bps else 0
        conc_ok = max_weight <= self.max_concentration_bps
        checks["max_concentration"] = conc_ok
        if not conc_ok:
            failures.append(f"max concentration {max_weight} BPS exceeds limit {self.max_concentration_bps} BPS")

        # 3. Min cost-benefit (only if cost_benefit_bps is provided)
        if self.cost_benefit_bps is not None:
            cb_ok = self.cost_benefit_bps >= self.min_cost_benefit_bps
            checks["min_cost_benefit_bps"] = cb_ok
            if not cb_ok:
                failures.append(
                    f"cost-benefit {self.cost_benefit_bps} BPS below minimum {self.min_cost_benefit_bps} BPS"
                )

        passed = len(failures) == 0
        result = VCheckResult(passed=passed, checks=checks, failures=failures)

        if passed:
            logger.debug("v_check: PASSED (%d checks)", len(checks))
        else:
            logger.warning(
                "v_check: FAILED — %s",
                "; ".join(failures),
            )

        return result

    @staticmethod
    def from_weights_dict(
        weights: dict[str, float],
        **kwargs: Any,
    ) -> VCheck:
        """Construct from float weights (0.0–1.0).

        Converts to BPS internally.

        Parameters
        ----------
        weights : dict[str, float]
            Target weights as fractions (e.g. ``{"sSPY": 0.50}``).
        **kwargs
            Forwarded to ``VCheck.__init__``.
        """
        weights_bps = {k: int(round(v * 10000)) for k, v in weights.items()}
        return VCheck(weights_bps=weights_bps, **kwargs)
