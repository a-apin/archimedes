---
name: strategy-passport
description: How to read a strategy passport honestly — every field on strategy_passports (backend/archimedes/models/strategy_passport_record.py), what passes_rigor_gate does and does NOT mean, why status="live" is not a rigor claim, and the five specific claims this skill (and anyone summarizing a passport) must never make.
triggers:
  - reading /api/strategies/passports/{id} or a StrategyPassportRecord row
  - "does this strategy pass the rigor gate"
  - summarizing a strategy's DSR / PBO / Sharpe / backtest numbers for a user
  - "is status=live the same as passing the gate"
  - writing copy that describes Archimedes' strategy rigor
---

# Reading a strategy passport

A passport is one row in the `strategy_passports` table
(`StrategyPassportRecord`, [`backend/archimedes/models/strategy_passport_record.py`](../../backend/archimedes/models/strategy_passport_record.py):44-139)
— the unified store for curated, fusion, and architect-generated strategies
alike (record.py:1-11 module docstring). Read that table's columns as ground
truth; anything summarized from it must trace back to a named field, never to
vibes.

## Every field, grouped as the model groups them

Line numbers below are the `Column(...)` declarations in `strategy_passport_record.py`.

**Identity / provenance**
| Field | Line | Meaning |
|---|---|---|
| `id` | 49 | Primary key |
| `methodology_hash` | 50 | Hash of the methodology text — the provenance anchor also written on-chain via `ReasoningTraceRegistry` |
| `content_hash` | 51 | keccak256 for dedup (unique) |
| `generation_method` | 54 | `curated \| fusion \| architect` |
| `methodology_summary` / `methodology_text` | 55-56 | Human-readable + full text |
| `asset_universe` | 57 | JSON list |
| `universe_source` | 58-61 | `"user" \| "model" \| "full" \| NULL` — provenance of the asset picks (#857); a model-picked universe is a mild look-ahead channel (the model may pick names it "knows" did well in training data), surfaced here for audit, **not gated**. `NULL` for rows predating this column — never backfilled with a guess. |
| `position_sizing` / `rebalance_frequency` | 62-63 | Strategy mechanics |
| `risk_constraints` / `risk_profiles` | 64-65 | JSON |

**Lifecycle**
| Field | Line | Meaning |
|---|---|---|
| `status` | 68 | `candidate \| validated \| live \| retired \| rejected` — see "status ≠ gate" below |
| `regime_tag` | 69 | e.g. `regime_neutral` |

**Curation trail**
| Field | Line |
|---|---|
| `extraction_llm`, `extraction_prompt_hash` | 72-73 |
| `curator_wallet` (FK → `wallet_identities`), `curator_note` | 77-78 |

**Ownership**
| Field | Line | Meaning |
|---|---|---|
| `owner_wallet` (FK → `wallet_identities`) | 87 | SIWE-derived generating wallet, server-bound; `NULL` for curated/legacy rows |

**Code binding**
| Field | Line |
|---|---|
| `strategy_code_path`, `strategy_code_hash` | 90-91 |

**On-chain anchor**
| Field | Line |
|---|---|
| `on_chain_registration_tx`, `on_chain_registration_block` | 94-95 |

**Paper claims** (what the *source paper* reported — not what Archimedes measured)
| Field | Line |
|---|---|
| `paper_claimed_sharpe`, `paper_claimed_cagr`, `paper_claimed_max_dd` | 98-100 |
| `paper_claim_blended_sharpe` | 101 |

**Backtest results** (what Archimedes actually measured, denormalized for query speed)
| Field | Line |
|---|---|
| `sharpe_ratio`, `sortino_ratio`, `max_drawdown`, `cagr`, `win_rate`, `total_trades`, `calmar_ratio`, `correlation_to_spy` | 104-111 |
| `backtest_start`, `backtest_end` | 112-113 |

**Rigor gate results** — the four-primitive admission gate (DSR / PBO / OOS / look-ahead audit, per `docs/specs/selection-bias-corrections-spec.md`)
| Field | Line | Meaning |
|---|---|---|
| `deflated_sharpe_ratio`, `dsr_p_value` | 116-117 | Deflated Sharpe Ratio + its p-value (Bailey & López de Prado 2014) |
| `pbo_score` | 118 | Probability of Backtest Overfitting |
| `out_of_sample_sharpe` | 119 | Walk-forward holdout Sharpe |
| `passes_rigor_gate` | 120 | **The badge boolean — read the whole next section before quoting this** |
| `kelly_fraction` | 121 | Position-sizing primitive |
| `sharpe_ci_lower`, `sharpe_ci_upper` | 122-123 | Confidence interval on Sharpe |
| `n_obs_daily` | 124 | Sample size the above were computed over |

Timestamps (`created_at`/`updated_at`) at 127-128.

`StrategyPassportRecord.to_dict()` (record.py:200-217) is the compact API
serialization; `to_strategy_passport()` (record.py:141-198) is the fuller
dataclass conversion including everything above. The live HTTP surface is
`GET /api/strategies/passports/{strategy_id}`
([`api/strategies_routes.py`](../../backend/archimedes/api/strategies_routes.py):1592-1609)
— 404s (never 403) for a non-owner on an unpublished non-example passport, so a
mismatched caller can't confirm the id exists (strategies_routes.py:1596-1598).

## `passes_rigor_gate` — what it means and what it does NOT mean

**What it means:** a stored boolean, written by the generation pipeline (or the
live rigor-gate recompute), reflecting whether the strategy passed DSR + PBO +
OOS + look-ahead **at the strictest strictness level** at the time it was
computed
(`services/rigor_profiles.py` docstring line 22: "The badge (`passes_rigor_gate`)
is always evaluated at `STRICTEST_LEVEL`"). For a **generated** (fusion/architect)
strategy it is a real, persisted **live**-gate verdict —
`strategies_routes.py`:1733-1741 is explicit that this is "a stored *live*
verdict, not a fixture boolean," legitimate per issue #821 ("read a persisted
live-gate verdict"). The same route also derives a tri-state
`rigor_gate_status`: `"pending"` when there's no real backtest yet
(`sharpe_ratio is None`), else `"pass"`/`"fail"` from the boolean
(strategies_routes.py:1739-1741) — **prefer `rigor_gate_status` over the bare
boolean when a "pending" state matters**, since a bare `False` can't
distinguish "failed" from "never backtested."

**What it does NOT mean:**

- **It does not mean "will make money."** DSR and PBO *reduce* the false-positive
  rate of backtest-driven selection; they do not eliminate it, and they say
  nothing about future returns. See "The five forbidden phrasings" below.
- **It is not the only strictness level that exists.** `/api/selection-bias/gate`
  computes a full 1–5 strictness ladder per strategy
  (`selection_bias_routes.py`:186-200) and returns `min_passing_level` — the
  loosest level the strategy *would* clear. `passes_rigor_gate` on the passport
  is always the level-1 (strictest, "Archimedes Verified" badge) verdict, never
  the loosest one a UI might be showing under a relaxed filter.
- **For a curated strategy, it was graded at `num_trials=1`** — its own return
  series only, not deflated against the rest of the library
  (`selection_bias_routes.py`:313-320; full mechanics in `skills/verdict-api/SKILL.md`
  point 2). Say that scope out loud if you're explaining *why* a curated
  strategy's DSR looks strong.

## `status` ≠ gate result — say this explicitly, every time

This is the single easiest honesty mistake to make reading a passport, because
`status: "live"` *sounds* like "verified and running well." It is not that.

`StrategyStatus.LIVE` is defined as **"Active in at least one portfolio"**
([`models/strategy.py`](../../backend/archimedes/models/strategy.py):40) — a
*usage* fact, not a *rigor* fact. Two independent code paths make the
decoupling concrete:

- **Curated strategies:** `status` comes straight from a curator-declared
  `STATUS` field in the strategy file's metadata
  (`services/strategy_provider.py`:314-319), and the surrounding comment is
  explicit: "`CANDIDATE → VALIDATED` promotion is **not** driven by the fixture
  boolean... the served status is overlaid [from the live gate] elsewhere; here
  we keep only the file's declared STATUS **so a curator can still hand-declare
  live/retired**; the fixture boolean no longer promotes anything"
  (strategy_provider.py:321-328). A curator can mark a strategy `live` by hand.
- **Generated strategies (`StrategyRecord`, a *different* table from the
  passport):** `status` transitions to `"live"` purely as a function of the
  rigor verdict at write time (`models/strategy_store.py`:269-278, 307) — but
  that's a one-time transition at persist time, not a live-recomputed value; a
  strategy whose returns later degrade doesn't automatically flip back.

**The honest framing:** `status` tells you whether a strategy is deployed /
in a portfolio / curator-declared active. `passes_rigor_gate` /
`rigor_gate_status` tell you whether it currently clears the statistical
admission bar. **Never present one as evidence for the other.** A strategy can
be `status="live"` and `rigor_gate_status="fail"` simultaneously, and the
passport should be read (and summarized) that way.

## The five forbidden phrasings

These are documented team-wide pitch-rigor anti-claims —
[`docs/anti-features.md`](../../docs/anti-features.md), "Pitch-rigor anti-claims"
section, lines 197–249 — restated here because a passport-summarizing skill is
exactly where they get violated by accident. **Never claim any of the following
when describing a passport, regardless of how good its numbers look:**

1. **"Blockchain as memory" as the load-bearing claim.** (anti-features.md:203-214)
   The defensible framing is narrower: the on-chain registry is "the agent's
   externalized memory for the specific financial-decision artifacts no party —
   including Archimedes — can later rewrite," not "blockchain as universal
   computational substrate."
2. **Predicted alpha or future-return guarantees.** (anti-features.md:216-222)
   McLean & Pontiff (2016): published cross-sectional predictors lose 26%
   out-of-sample and 58% post-publication. Bailey & López de Prado (2014):
   backtest-optimized strategies often do not exceed the median out-of-sample
   result. Never say a strategy "delivers" or "will achieve" a Sharpe/CAGR —
   only that it was backtested to one, over a stated window.
3. **That an on-chain trace hash proves the agent *used* that trace.**
   (anti-features.md:224-230) A hash anchored at time T proves the reasoning
   *existed* at T. It does not prove the trade was *caused* by it — that needs
   the commit-reveal upgrade (`docs/specs/commit-reveal-trace-spec.md`, v1.5,
   not yet the default path). Say "verifiable record of the reasoning at the
   moment of the trade," never "proof the trade followed from the reasoning."
4. **Regulatory clarity or production-readiness.** (anti-features.md:232-240)
   Frame every passport as describing a research-prototype strategy on Arc
   testnet, not a launchable investment product.
5. **That the rigor gate makes a strategy "right."** (anti-features.md:242-249)
   DSR/PBO/OOS *reduce* the false-positive rate of a backtest-selected
   strategy; they do not make any specific strategy a confirmed true positive.
   The honest claim is "we apply the corrections and surface the numbers" —
   never "so this strategy is correct/validated as profitable."

## Verify (re-run these before trusting this document)

```bash
# Column list still matches:
grep -n '= Column(' backend/archimedes/models/strategy_passport_record.py

# passes_rigor_gate is always graded at the strictest level:
grep -n "STRICTEST_LEVEL" backend/archimedes/services/rigor_profiles.py

# status=live means "active in a portfolio", not a rigor verdict:
grep -n 'LIVE = "live"' backend/archimedes/models/strategy.py
grep -n "CANDIDATE .. VALIDATED.*fixture boolean\|hand-declare" backend/archimedes/services/strategy_provider.py

# The five anti-claims are still all present under the Pitch-rigor section (expect 5):
grep -n "^## Pitch-rigor" docs/anti-features.md
awk 'NR>=197 && NR<=249 && /^### NOT/' docs/anti-features.md
```

## What this skill deliberately does not cover

- How to *request* a generation and get a job's SSE stream to the point a
  passport exists — see `skills/verdict-api/SKILL.md`.
- The marketplace fee-charging flow — unrelated to passport truthfulness; see
  `skills/x402-payment/SKILL.md`.
