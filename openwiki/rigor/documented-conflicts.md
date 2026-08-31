---
type: known-issues
title: Conflicts inside the quant docs
description: Seven places where the quant documentation contradicts itself — stale thresholds, headings that disagree with the status lines beneath them, pass counts the same slice forbids quoting, and an out-of-sample Sharpe compared against a probability threshold.
tags: [documentation-drift, rigor-gate, dsr, contradictions, verification]
verified:
  - by: openwiki/0.4.3
    at: 2026-08-31T05:55:43.566Z
sources:
  - id: openwiki-source-f88b8fdeab2b23286a6aa730
    resource: repo://docs/quant/admission-criteria.md
  - id: openwiki-source-bcc51b9a099705e0822aa4c7
    resource: repo://docs/quant/backtest-interpretation.md
  - id: openwiki-source-7bbe81a7a83756c8ac62dc6e
    resource: repo://docs/quant/library-pbo.md
  - id: openwiki-source-03a78d9aa62747d43f49c7bc
    resource: repo://docs/quant/methodology.md
  - id: openwiki-source-aea1728d3a591b704463ef0e
    resource: repo://docs/quant/README.md
  - id: openwiki-source-0f74af6fbdba9344305572f1
    resource: repo://docs/quant/second-wave-universe-experiment.md
  - id: openwiki-source-6ce3a655f0a466a9e2cc4338
    resource: repo://docs/quant/strategy-library.md
  - id: openwiki-source-0dce6a8f1e2a58d1ccdb4aad
    resource: repo://docs/quant/third-wave-retest.md
generated: { by: "claude-code", at: "2026-08-31T05:55:43.566Z" }
---

# Conflicts inside the quant docs

This page exists so a reader does not resolve a contradiction by trusting whichever page
they happened to open first. Everything below is a disagreement **inside this slice**,
found by reading all eight documents together.

None of these is resolved here. Two of them cannot be resolved from documentation at all —
they need the running system or the implementing code, both of which sit outside the
boundary this wiki was generated within.

---

## 1. Is 0.90 a literal threshold or the top rung of a ladder?

**The disagreement.** The admission document states the DSR bar as a literal gate value:
`dsr_p_value ≥ 0.90`, recalibrated from 0.95 in PR #901, and presents the four thresholds
as "transcribed from `passes_all`, not invented". The methodology document says something
structurally different: 0.90 is the threshold **at the strictest level (1, Conservative —
the badge bar)**, loosening down a five-level strictness ladder to a floor at level 5, with
the live values living in a rigor-profiles table, and it states that the gate "reads the
selected profile, never a literal".

**Why it matters.** These are not two phrasings of one rule. One says there is a single
number; the other says the number depends on which profile is selected. An agent that reads
only the admission page will assert a fixed 0.90 bar for every evaluation.

**Not resolvable from docs.** The profiles table is source code outside this slice.

## 2. The same document gives `num_trials` two different values

**The disagreement.** Within the admission document, the threshold section says that on the
curated library `num_trials = 1`, so no deflation is applied. The promotion-flow section
directly below says the caller passes `num_trials = len(strategy_library)`.

**Why it matters.** This is the difference between a DSR that is multiple-testing corrected
and one that is not — the single most load-bearing caveat in the whole rigor story. Both
statements appear on one page, roughly a hundred lines apart.

The likely reconciliation is that the two describe different paths — generated versus
curated — but neither passage says so, and the promotion flow is written as the general
case.

## 3. A stale `p ≥ 0.95` bar survives in the universe experiment

**The disagreement.** Two documents carry dated corrections noting that a `≥ 0.95` figure
was wrong and that 0.90 has been the bar since PR #901. The universe experiment's
`num_trials` sweep table was not corrected: its column header still asks **"passes
p ≥ 0.95?"**, and its yes/no answers are computed against that bar.

**Why it matters.** Read against the current 0.90 bar, that table's verdicts change. The row
at `num_trials = 22` shows p = 0.941 marked "no" — but 0.941 clears 0.90.

## 4. Three headings contradict the status lines beneath them

The strategy shelf marks pass/fail in section headings while its status lines defer to the
live gate:

| Strategy | Heading says | Status line says |
|---|---|---|
| Moskowitz, Ooi & Pedersen (2012) TSMOM | ✅ *passes the gate* | "per the live rigor gate" |
| Moreira & Muir (2017) volatility-managed | ✅ *passes the gate* | `CANDIDATE` — **fails admission** |
| Faber (2007) SMA-200 timing | ❌ *fails the gate* | "per the live rigor gate" |

The Moreira & Muir row is the sharpest: a heading claiming a pass sits directly above a
status line claiming failure. That status line then continues "The earlier claim that Faber
passed was wrong" — Faber text stranded under a different strategy, which is the signature
of a partially-applied edit.

**Why it matters.** The same document states twice that which strategies pass "is not
recorded here" because the live gate is the only authority. The headings break that rule,
and a reader skimming headings gets the opposite answer from a reader reading status lines.

## 5. An out-of-sample Sharpe is compared against a probability threshold

**The disagreement.** Two passages say a strategy fails because "its walk-forward OOS Sharpe
ratio is 0.612, under the 0.90 gate".

**Why this cannot be right as written.** 0.90 is the **DSR p-value** threshold — a
probability in `[0, 1]`. The out-of-sample Sharpe has two thresholds of its own, and neither
is 0.90: an absolute floor of `> 0` and a cliff ratio of `OOS/IS ≥ 0.5`. An OOS Sharpe of
0.612 clears the floor comfortably. Whatever the real failure reason is, the sentence as
written compares a Sharpe ratio to a probability.

**Why it matters.** It is the only failure explanation the shelf gives, it appears twice,
and it teaches a wrong mental model of which threshold governs which statistic.

## 6. Three different library sizes, none reconciled

| Document | Count |
|---|---|
| Strategy shelf | 34 strategy files "at the time of writing" |
| Library-PBO findings | 22 of 23 strategies |
| Universe experiment | "the full 22-strategy library" |

The two findings notes agree with each other and were both run on 2026-06-11; the shelf is a
later, larger count. Nothing in the slice says the findings numbers were computed on a
smaller library than the one now shipped, which is exactly what a reader needs to know
before quoting a PBO figure as current.

**Partially self-defending.** The shelf tells you to count the files yourself rather than
trust its number — good practice, and the only such instruction in the slice.

## 7. Pass counts that the same slice forbids quoting

Three documents state the rule that the live gate is the only authority on pass/fail. Two
others state a count anyway:

- The universe experiment: "Of the full 22-strategy library, **two pass** the rigor gate
  today" — naming both, with DSR p-values of 0.995 and 0.976, and a third within 0.006.
- The third-wave re-test refers to "the two gate-passers" and to Faber having LIVE status.

Both are dated 2026-06-11 and were true observations at that vintage. The rule they violate
was written to stop exactly this figure from being carried forward as current, and the
shelf has since retracted at least one of the pass claims those notes rest on.

---

## One non-conflict worth knowing

The methodology document retracts one of its own claims rather than contradicting another
page: the flat 5% risk-free constant is now only a fallback, and because Kelly and the
portfolio optimizer still take the flat constant while the rigor statistics can be graded
against the historical T-bill series, **"every excess-return figure is computed on the same
basis" is stated to be no longer accurate**. That is a documented non-uniformity, not a
drift — it is worth carrying alongside these conflicts because it has the same practical
effect: two numbers that look comparable may not be.

---

## How to use this page

- **Never resolve one of these by picking a document.** For anything about a current
  verdict, threshold selection, or pass count, the live gate and the implementing code are
  the authority.
- Items 1, 2, and 5 need someone with access to the implementation to settle.
- Items 3, 4, 6, and 7 are documentation fixes: a stale table header, three headings, a
  count reconciliation, and two dated pass counts that need vintage stamps or removal.

An index gap sits alongside these: the slice's own README describes itself as "these four
docs" and tables four of them, while the directory holds eight. The three findings notes —
the ones carrying the numbers most likely to be quoted — are not in that table.
