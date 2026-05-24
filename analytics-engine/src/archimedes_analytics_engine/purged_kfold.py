"""Purged k-fold cross-validation for serial-correlated financial returns.

Standard k-fold CV leaks information through time when labels overlap with
features across the train/test boundary.  Marcos López de Prado's
*Advances in Financial Machine Learning* (Ch. 7) addresses this with two
mechanisms:

1. **Purging** — drop training observations whose labels span the test
   window.  Without this, a label generated from a 5-day forward return
   may be "in training" while its underlying price path is "in test".
2. **Embargoing** — after each test fold, embargo the next ``embargo_pct``
   of observations from training, to prevent leakage through serially-
   correlated features (e.g. rolling-window technical indicators built
   on the test window).

Standard scikit-learn ``KFold`` does neither.  Use this when your labels
are computed over a forward horizon (typical for momentum / mean-reversion
backtests) — i.e. virtually any non-trivial financial signal.

References
----------
- López de Prado (2018), Advances in Financial Machine Learning, Ch. 7.
- Bailey, Borwein, López de Prado, Zhu (2014), Probability of Backtest
  Overfitting — complements purged k-fold by quantifying overfit risk
  across the trial set.

Owner: Önder (math/research lane).  Used by the post-hackathon paper-
replication workflow described in
``docs/specs/paper-replication-spec.md``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FoldSpec:
    """One purged-k-fold split."""

    fold_index: int  # 0-indexed
    train_idx: np.ndarray  # integer positions into the index
    test_idx: np.ndarray
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    n_purged: int  # training obs dropped due to label overlap
    n_embargoed: int  # training obs dropped due to embargo


def purged_kfold_splits(
    t1: pd.Series,
    n_splits: int = 5,
    embargo_pct: float = 0.01,
) -> Iterator[FoldSpec]:
    """Generate purged-k-fold splits for forward-horizon labels.

    Args:
        t1: Series indexed by the feature time ``t0``, with values equal
            to the corresponding label end time ``t1`` (when the label
            was actually observed).  For a 5-day forward return labeled
            at ``t0``, ``t1[t0] = t0 + 5 business days``.  Must be
            monotonic-increasing on its index.
        n_splits: Number of folds.  Default 5 — five contiguous time
            windows of equal size.
        embargo_pct: Fraction of the dataset to embargo *after* each
            test window.  Default 1% — for a 10y daily dataset that's
            ~25 days.

    Yields:
        FoldSpec(fold_index, train_idx, test_idx, test_start, test_end,
                 n_purged, n_embargoed) for each fold, in time order.

    Notes
    -----
    - Folds are **contiguous time windows** (not random), which is the
      only way to honor temporal causality.
    - Train sets may be discontinuous (a train fold at the start of
      time is *outside* the test window, then a train fold after the
      test window resumes after the embargo gap).
    - On very short embargo + dense labels, a fold may have zero usable
      training observations.  Caller should check ``len(train_idx) > 0``
      before fitting.
    """
    if not isinstance(t1, pd.Series):
        raise TypeError("t1 must be a pandas Series indexed by feature time t0")
    if not t1.index.is_monotonic_increasing:
        raise ValueError("t1.index must be monotonic-increasing")
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    if not (0.0 <= embargo_pct < 1.0):
        raise ValueError("embargo_pct must be in [0, 1)")

    n = len(t1)
    embargo_size = int(round(n * embargo_pct))
    fold_bounds = np.array_split(np.arange(n), n_splits)

    for fold_i, test_positions in enumerate(fold_bounds):
        if len(test_positions) == 0:
            continue

        test_start_pos = int(test_positions[0])
        test_end_pos = int(test_positions[-1])
        test_start_ts = t1.index[test_start_pos]
        test_end_ts = t1.index[test_end_pos]
        # The *latest* label-end time within the test window — anything
        # before this in train still has its label observed inside test.
        t1.iloc[test_positions].max()

        # Start with all-positions, then remove test + purge + embargo.
        all_positions = np.arange(n)
        candidate_train = np.setdiff1d(all_positions, test_positions, assume_unique=True)

        # PURGE — drop training observations whose label end falls inside
        # the test window (i.e. label was actually observed during test).
        train_t1 = t1.iloc[candidate_train]
        # A label "ends inside the test window" iff t1[t0] >= test_start
        # AND t0 <= test_end.  (If t0 > test_end the obs is post-test,
        # handled separately by embargo.)
        feature_in_or_before_test = candidate_train <= test_end_pos
        label_ends_in_or_after_test = train_t1.values >= test_start_ts
        purge_mask = feature_in_or_before_test & label_ends_in_or_after_test
        n_purged = int(purge_mask.sum())
        candidate_train = candidate_train[~purge_mask]

        # EMBARGO — drop the first ``embargo_size`` training observations
        # AFTER the test window, since their features are serially
        # correlated with the in-test window.
        if embargo_size > 0:
            embargo_start = test_end_pos + 1
            embargo_end = embargo_start + embargo_size
            embargo_positions = np.arange(embargo_start, min(embargo_end, n))
            before_embargo = len(candidate_train)
            candidate_train = np.setdiff1d(
                candidate_train,
                embargo_positions,
                assume_unique=True,
            )
            n_embargoed = before_embargo - len(candidate_train)
        else:
            n_embargoed = 0

        yield FoldSpec(
            fold_index=fold_i,
            train_idx=candidate_train,
            test_idx=test_positions,
            test_start=test_start_ts,
            test_end=test_end_ts,
            n_purged=n_purged,
            n_embargoed=n_embargoed,
        )


def cross_val_score(
    fit_fn,
    predict_fn,
    score_fn,
    X: pd.DataFrame,
    y: pd.Series,
    t1: pd.Series,
    n_splits: int = 5,
    embargo_pct: float = 0.01,
) -> dict:
    """Run a purged-k-fold loop and return aggregated scores.

    Args:
        fit_fn(X_train, y_train) -> model
        predict_fn(model, X_test) -> y_pred
        score_fn(y_true, y_pred) -> float
        X: Feature dataframe, indexed by feature time t0.
        y: Label series, indexed identically to X.
        t1: Label-end series, indexed identically to X.
        n_splits, embargo_pct: as ``purged_kfold_splits``.

    Returns:
        dict with keys:
            scores         — per-fold list of score_fn outputs
            mean, std      — aggregate scores
            n_folds_used   — folds with at least 1 training observation
            n_purged_total — sum of purged observations across folds
            n_embargoed_total — sum of embargoed observations across folds
    """
    if not X.index.equals(y.index) or not X.index.equals(t1.index):
        raise ValueError("X, y, t1 must share an index")

    scores: list[float] = []
    n_purged_total = 0
    n_embargoed_total = 0
    n_folds_used = 0

    for spec in purged_kfold_splits(t1, n_splits=n_splits, embargo_pct=embargo_pct):
        n_purged_total += spec.n_purged
        n_embargoed_total += spec.n_embargoed
        if len(spec.train_idx) == 0:
            continue
        X_train = X.iloc[spec.train_idx]
        y_train = y.iloc[spec.train_idx]
        X_test = X.iloc[spec.test_idx]
        y_test = y.iloc[spec.test_idx]
        model = fit_fn(X_train, y_train)
        y_pred = predict_fn(model, X_test)
        scores.append(float(score_fn(y_test, y_pred)))
        n_folds_used += 1

    arr = np.array(scores) if scores else np.array([])
    return {
        "scores": scores,
        "mean": float(arr.mean()) if scores else float("nan"),
        "std": float(arr.std(ddof=1)) if len(scores) > 1 else 0.0,
        "n_folds_used": n_folds_used,
        "n_purged_total": n_purged_total,
        "n_embargoed_total": n_embargoed_total,
    }


__all__ = ["FoldSpec", "cross_val_score", "purged_kfold_splits"]
