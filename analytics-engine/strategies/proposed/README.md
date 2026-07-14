# Proposed consolidated strategies — NOT part of the live curated library

Files in this directory are **proposals only**. They are intentionally NOT
`.py` `bt.Strategy` files and are NOT picked up by
`strategy_provider.LocalStrategyProvider` (which globs
`analytics-engine/strategies/*.py` — non-recursive, so this subdirectory is
invisible to it) or by any other production strategy-discovery path.

Each `*.json` here documents one candidate multi-paper "fusion" composed from
existing curated single-paper strategies, per
`docs/CURATED-STRATEGY-DECOUPLE-AND-CONSOLIDATE-2026-07-08.md` Part B, with
its **real, honestly-computed** verification numbers (never synthetic data,
never tuned to pass). A candidate here may be a REAL FAIL — that is a valid,
reported outcome, not a reason to omit it.

Nothing in this directory should be merged into the live library, exposed in
the API, or used to back any user-facing claim until Dan + Önder review it and
explicitly promote it (at which point it becomes a real curated `.py` file or
a DSL `StrategySpec`, reviewed like any other strategy addition).

See `docs/CURATED-CONSOLIDATION-BUILD-2026-07-09.md` for the full analysis,
methodology, and honest verdicts behind each proposal.
