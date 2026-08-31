# Future Plans issue draft — re-evaluate the strategy emission language

> **Status:** draft issue body, not yet filed
> **Owner:** Dan Browne
> **Updated:** 2026-08-30
> **Superseded-by:** —
>
> **What this is:** the body text for a Future Plans GitHub issue that has not been opened
> yet. It exists as a file because the session that drafted it could not post to GitHub.
> **When it is filed, replace this front-matter block with the issue link and leave the file
> as the record of what was asked.** Nothing here is a commitment to do the work — it is a
> commitment to re-open the question if, and only if, one of the triggers below fires.
>
> **Parent decision:** [`adr/strategy-dsl-hardening-over-lean4.md`](../adr/strategy-dsl-hardening-over-lean4.md)
> (Accepted 2026-08-30). That ADR is in force. This issue does not relitigate it and must not
> be read as doing so.

---

## Title

`APIN - Strategy Engine - Re-evaluate the emission language (trigger-gated)`

## Summary

On 2026-08-30 we decided **no Lean 4 on the emission path** and committed to hardening the
existing closed-enum JSON DSL instead
([ADR](../adr/strategy-dsl-hardening-over-lean4.md)). That decision was correct **for the
evidence available at the time**, and every piece of that evidence is the kind that can
change.

This issue exists so the question re-opens on a **signal** rather than on a mood. It should
sit open and quiet, possibly for a long time. Nobody should work it until a trigger below
actually fires — and when one does, the first job is to prove the trigger fired, not to start
writing a new language.

**Do not close this issue for inactivity.** Its whole function is to be dormant.

## Trigger conditions

Any **one** of these justifies re-opening the language question. Each is stated so that
"has it fired?" is a question with evidence behind it rather than a matter of opinion.

### Trigger 1 — property tests stop catching real violations

The hardening plan replaces the self-declared `look_ahead_safe` boolean with a **derived**
check computed from the condition tree (ADR decision item 3). The plan is to back that
checker with hypothesis-based property tests over generated condition trees.

**The trigger fires if those property tests repeatedly pass while real lookahead
violations reach a graded backtest.** That would mean the property-based approach is not
sound over the grammar we actually have, and hand-written checking has hit its ceiling —
which is the specific circumstance in which a proof assistant starts to earn its cost.

Evidence to attach when claiming this trigger:

- At least two distinct violations that reached grading with the derived check reporting
  clean. One is an ordinary bug; the pattern is the signal.
- The property-test run that should have caught each, showing why it did not.
- Confirmation the checker was not simply misconfigured or bypassed. A disabled guard is a
  disabled guard, not evidence against the approach.

**Anti-trigger:** a single escaped bug that a new property test then catches. That is the
system working.

### Trigger 2 — a cross-asset grammar pushes the DSL general-purpose

Today every DSL operand resolves against a **single price series**; multi-symbol universes
are run as independent per-symbol backtests and equal-weighted
([`fusion_evaluator.py`](../../backend/archimedes/services/fusion_evaluator.py) →
`run_dsl_backtest_portfolio`). Positions are long-flat. This is the ADR's recorded ceiling.

Genuine cross-asset strategies — cross-sectional ranking, long/short pairs, conditions
reading two series at once — require operands that reference *other assets*. Adding that
means adding scope, binding, and iteration to the grammar. **At that point the DSL stops
being a closed enum and starts being a programming language**, and the argument that made it
safe (it is data, not code; a fixed hand-written interpreter is sufficient) weakens with each
addition.

**The trigger fires when a cross-asset grammar extension is actually proposed with a real
strategy behind it.** Not when someone observes that cross-asset strategies exist.

Evidence to attach:

- The specific strategy shape and the paper motivating it.
- The grammar extension it needs, concretely.
- An honest assessment of whether the hand-written interpreter can still cover the extended
  grammar — because if it can, this trigger has **not** fired and the extension is just
  ordinary DSL work.

**Note the branch here.** This trigger does not point at Lean by default. The ADR reserves
the **restricted Python sandbox** (scored 32 vs the DSL's 33 — one point) as the intended
answer for shapes the DSL cannot express. Trigger 2 most likely re-opens *sandbox vs. wider
DSL*, and only reaches the verification question if the sandbox's argued containment boundary
is judged unacceptable.

### Trigger 3 — LLM-Lean writability materially improves

The decisive evidence against Lean 4 was that models cannot reliably write it. Public
benchmark reporting at decision time put frontier-model success at correct Lean 4 proofs of
competition-grade statements in roughly the **4–11%** band — and our live emitter is
`amazon.nova-micro-v1:0`, well below frontier
([`adr/glm-to-bedrock-llm-migration.md`](../adr/glm-to-bedrock-llm-migration.md)).

**The trigger fires when that changes by an order of magnitude** — meaning both of:

- Frontier success on comparable benchmarks reaches roughly **50%+**, sustained across more
  than one model family, not a single headline result.
- A model **we can actually afford to run on the emission path** is at or near that level.
  Frontier capability we cannot pay for on every generation is a fact about the field, not
  about our pipeline.

Even then, two ADR findings survive independently and must be re-checked rather than
assumed obsolete:

- **The numeric ecosystem.** Reasoning about `float64` rolling windows, not real analysis,
  is what a strategy needs proved. Has that gap actually closed?
- **The Python bridge.** A proof about a Lean artifact says nothing about the backtrader run
  that produced the published Sharpe unless the translation between them is itself verified.
  Without that, the proof and the numbers describe different objects.

A trigger-3 re-evaluation that does not address both is incomplete.

## Evidence links

**The decision and its reasoning**

- [`adr/strategy-dsl-hardening-over-lean4.md`](../adr/strategy-dsl-hardening-over-lean4.md) —
  the ADR, including the four-option scored matrix (harden-DSL 33 · sandbox 32 · checked-AST
  27 · QuantConnect LEAN 26) and the Lean 4 / QuantConnect LEAN disambiguation.

**The DSL as it stands**

- [`backend/archimedes/services/strategy_dsl.py`](../../backend/archimedes/services/strategy_dsl.py)
  — the closed enums, the condition-tree validator, and the `look_ahead_safe` admission
  check that trigger 1 is about replacing.
- [`backend/archimedes/services/dsl_to_backtrader.py`](../../backend/archimedes/services/dsl_to_backtrader.py)
  — the fixed interpreter. `interpret_spec`'s "No eval/exec/importlib" is the structural
  safety property; `_enter_position` is where the unimplemented sizing types live.
- [`backend/archimedes/services/fusion_evaluator.py`](../../backend/archimedes/services/fusion_evaluator.py)
  — `run_dsl_backtest_portfolio`, the equal-weighted per-symbol path that trigger 2 is about.
- [`backend/archimedes/agents/debate_engine.py`](../../backend/archimedes/agents/debate_engine.py)
  — `_CONFORMANT_INDICATORS`, the filter that routes around `realized_vol` validating cleanly
  and then raising inside `cerebro.run()`. Retiring it is ADR decision item 2.
- [`docs/specs/strategy-dsl-spec.md`](../specs/strategy-dsl-spec.md) — the spec doc, which
  currently describes a schema the validator does not accept. ADR decision item 4 rewrites
  it; **re-read the rewritten version, not this one, when the trigger fires.**

**Where the self-attestation surfaces today**

- [`backend/archimedes/agents/generation_pipeline.py`](../../backend/archimedes/agents/generation_pipeline.py)
  — `look_ahead_audit_source="self_attested"`.
- [`backend/archimedes/api/strategies_routes.py`](../../backend/archimedes/api/strategies_routes.py)
  — `look_ahead_label`, the honest label distinguishing self-attestation from the AST audit.

**Adjacent decisions that constrain any answer**

- [`adr/backtrader-backtest-engine.md`](../adr/backtrader-backtest-engine.md) — why the
  evaluation stack is Python, and the engine-provenance column any replacement inherits.
- [`adr/debate-society-sole-generation-pipeline.md`](../adr/debate-society-sole-generation-pipeline.md)
  — the sole generation path; a new emission language lands here or nowhere.
- [`adr/rigor-gate-unification.md`](../adr/rigor-gate-unification.md) — one authoritative
  gate. A new language does not get its own.
- [`adr/glm-to-bedrock-llm-migration.md`](../adr/glm-to-bedrock-llm-migration.md) — the live
  model, which is the emitter trigger 3 is measured against.

## Out of scope

- **Relitigating the ADR.** It is `Accepted`. If it turns out to be wrong, the mechanism is a
  superseding ADR, not a comment thread on this issue.
- **The restricted-Python-sandbox decision.** Reserved by the ADR for shapes the DSL cannot
  express; it gets its own ADR when a real strategy demands it. Trigger 2 may point at it, but
  this issue does not decide it.
- **Doing the DSL hardening.** Items 1–4 of the ADR are ordinary work tracked separately.
  This issue is only about whether the *language choice* should re-open.

## Acceptance

This issue is worked only when a trigger fires, and "worked" means producing **one of**:

- A superseding ADR that changes the language decision, with the trigger evidence in its
  Context; or
- A comment recording that the trigger fired, was evaluated, and the existing decision stands
  — with the evidence, so the next person does not re-run the same analysis.

Closing it any other way loses the reason it was opened.
