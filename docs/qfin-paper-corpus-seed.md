# Q-Fin Paper Corpus — Bleeding-Edge arXiv Preprint Stream

> **Date:** 2026-05-17 (Day 7) — full rewrite, supersedes the 2026-05-12 classical seed list
> **Author:** Corpus reorientation pass. Curation gate still owned by Dan; numbers below
> are derived from the real scraped manifest, not curated by hand yet.
> **Audience:** Dan (curator), Önder (math validator), team (review ahead of standup)
> **Purpose:** Replace the canonical-literature seed menu with a description of, and
> rationale for, a **living bleeding-edge arXiv q-fin preprint corpus**. The earlier
> version of this file proposed ~30 textbook-famous papers (Jegadeesh-Titman, Fama-French,
> López de Prado, Hamilton). That list is the *opposite* of what Archimedes should be
> reading. This rewrite explains why, and grounds the new direction in the 200-paper
> corpus Stream A actually scraped.

## Why the classical seed was the wrong instinct

The previous seed list opened with "These are the canonical quant strategies. Every quant
team in the world has implemented them." That sentence is the bug, not the feature.

Archimedes' entire thesis is that **published alpha decays as its novelty wears off**. The
cleanest evidence is McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return
Predictability?" (*Journal of Finance* 71(1), 5–32): they re-tested 97 documented
anomalies and found returns decay roughly **58% out-of-sample after publication** — about
26% from genuine statistical mean-reversion and the rest from arbitrageurs trading the
signal once it is public knowledge. The half-life of an academic edge starts ticking the
moment the paper is widely read.

Run that mechanism forward and the conclusion is uncomfortable for a "canonical anchors of
trust" library: a corpus built from the *most-cited, most-implemented* papers in finance is
a corpus of signals that have been arbitraged for one to three decades. Jegadeesh-Titman
momentum has ~30,000 citations precisely *because* it is old, famous, and everyone trades
it — which is exactly why its post-2010 Sharpe is a shadow of its 1993 in-sample number.
**Citation count is anti-correlated with surviving alpha.** Fame is the tombstone of an
edge, not a certificate of one.

So the corpus that feeds Archimedes' strategy engine should live as close to the
**bleeding edge of arXiv preprints** as the scrape pipeline can keep it. Not because new
preprints are automatically *correct* — most are not — but because the *undiscovered* and
*not-yet-arbitraged* ideas only exist there. Novelty is the moat. A strategy whose backing
paper is six weeks old and uncited has, at minimum, not yet been competed away by the rest
of the market reading the same paper. The classical canon is the control group we expect to
*lose* to fresh research, not the trust anchor we lean on.

This does not abandon rigor — it relocates it. We do not trust a preprint because it is new;
we trust a strategy because it survives the four-primitive selection-bias gate (DSR + PBO +
walk-forward OOS + look-ahead audit, per
[`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md)).
Novelty decides *what enters the funnel*; rigor decides *what survives it*. The old seed
list inverted that — it used fame as a proxy for rigor and never looked for novelty at all.

## The corpus as it actually exists today

Stream A has built a real scraped corpus. The numbers below come directly from
`data/corpus/manifest.jsonl` (read cross-worktree from `stream-a`; 200 JSON-per-line
records) — they are measured, not aspirational.

| Property | Value |
| --- | --- |
| Total papers | **200** |
| Published date range | **2026-04-03 → 2026-05-14** (a 41-day window) |
| Span | ~6 weeks; roughly evenly spread (≈27–40 papers per trailing week) |
| Distinct primary categories | **30** |
| q-fin coverage | **200/200** carry at least one `q-fin.*` tag; 133 have a `q-fin.*` *primary* category, the remaining 67 are q-fin cross-listings from `cs.*` / `stat.*` / `econ.*` / `math.*` |
| Already-revised preprints | **34/200** have `updated` ≠ `published` (a v2+ already exists) |
| Fetch timestamp | single batch, `2026-05-17T04:51:54Z` |

The corpus is, in plain terms, **a snapshot of the most recent ~200 q-fin submissions to
arXiv as of mid-May 2026** — not a hand-picked historical reading list.

### Primary-category distribution

The scrape is genuinely broad across the q-fin taxonomy and its machine-learning /
econometrics borderlands:

| Primary category | Count | | Primary category | Count |
| --- | --- | --- | --- | --- |
| q-fin.TR (Trading & Microstructure) | 26 | | math.OC (Optimization & Control) | 6 |
| q-fin.MF (Mathematical Finance) | 23 | | stat.ME (Methodology) | 5 |
| q-fin.RM (Risk Management) | 22 | | stat.AP / cs.AI / cs.CE | 4 each |
| q-fin.PM (Portfolio Management) | 18 | | math.PR / cs.MA / stat.ML / math.ST / physics.soc-ph | 3 each |
| q-fin.CP (Computational Finance) | 17 | | econ.GN | 2 |
| cs.LG (Machine Learning) | 11 | | nlin.PS, cs.IT, eess.SY, math.NA, econ.TH, cs.GT, quant-ph, cs.SD, math.GN | 1 each |
| q-fin.PR (Pricing of Securities) | 10 | | | |
| q-fin.ST (Statistical Finance) | 10 | | | |
| q-fin.GN (General Finance) | 7 | | | |
| econ.EM (Econometrics) | 7 | | | |

The long tail matters: a `cs.LG` paper that cross-lists to `q-fin.PM` is exactly the kind
of not-yet-canonical, ML-meets-portfolio idea the classical seed list reached for
(Gu-Kelly-Xiu) but could only find a *six-years-old, >1000-citation* example of. The scrape
surfaces eleven `cs.LG`-primary q-fin crossovers from the last six weeks alone.

### A representative sample (real records, pulled from the manifest)

Every row below is a real record from `manifest.jsonl` — `arxiv_id`, exact title, primary
category, publish date. Selected to span the major categories and the full date range; **no
titles or IDs are invented**. This is a cross-section, not a curation verdict — none of
these has been through the selection-bias gate.

| arXiv ID | Title | Primary | Published |
| --- | --- | --- | --- |
| `2605.09310` | Beyond ESG Scores: Learning Dynamic Constraints for Sequential Portfolio Optimization | cs.AI | 2026-05-10 |
| `2605.13407` | Vector-Quantized Discrete Latent Factors Meet Financial Priors: Dynamic Cross-Sectional Stock Ranking Prediction for Portfolio Construction | cs.LG | 2026-05-13 |
| `2605.11645` | GeomHerd: A Forward-looking Herding Quantification via Ricci Flow Geometry on Agent Interactive Simulations | cs.MA | 2026-05-12 |
| `2605.09712` | Quantifying the Risk-Return Tradeoff in Forecasting | econ.EM | 2026-05-10 |
| `2605.01178` | Modeling Stochastic Multi-Agent Interaction in Intraday Battery Energy Storage Dispatch with Market Power | math.OC | 2026-05-02 |
| `2605.09061` | A Market-Rule-Informed Neural Network for Efficient Imbalance Electricity Price Forecasting | q-fin.CP | 2026-05-09 |
| `2605.13998` | Synthetic American Option Pricing via Jump-HMM-Driven Heston Implied Volatility | q-fin.CP | 2026-05-13 |
| `2605.13320` | The fine structure of electricity price volatility | q-fin.GN | 2026-05-13 |
| `2605.12764` | Yield Curves Dynamics Using Variational Autoencoders Under No-arbitrage | q-fin.MF | 2026-05-12 |
| `2605.12698` | Optimal investment and Pension policy in Pay-As-You-Go systems under forward utility and ageing population | q-fin.MF | 2026-05-12 |
| `2605.01176` | Decision-Induced Ranking Explains Prediction Inflation and Excessive Turnover in SPO-Based Portfolio Optimization | q-fin.PM | 2026-05-02 |
| `2605.09123` | The Engineering of Skew: A Path-Dependent Framework for Asymmetric Volatility Management | q-fin.PM | 2026-05-09 |
| `2605.12189` | A deep learning approach for pricing convertible bonds with path-dependent reset and call provisions | q-fin.PR | 2026-05-12 |
| `2605.11200` | The Epistemic Risk of Risk: A Modal Framework for Quantitative Risk Management | q-fin.RM | 2026-05-11 |
| `2605.10066` | On the modeling assumptions of Historical Simulation for Value-at-Risk | q-fin.RM | 2026-05-11 |
| `2604.19580` | Probabilistic Forecasting for Day-ahead Electricity Prices, Battery Trading Strategies and the Economic Evaluation of Predictive Accuracy | q-fin.ST | 2026-04-21 |
| `2605.12151` | RED-2400: A Public Benchmark of Algorithmically-Rejected Trading Events with Outcome Labels | q-fin.TR | 2026-05-12 |
| `2605.11640` | Fill-Side Non-Retail Trading on Polymarket: An Empirical Study of Behavioral Tiers and Microstructure Signatures Under Quote-Attribution Constraints | q-fin.TR | 2026-05-12 |
| `2605.14976` | Multi-regime Markov-switching models with time-varying transition probabilities: An application to U.S. Treasury yields | stat.ME | 2026-05-14 |
| `2605.06438` | Neural-Actuarial Longevity Forecasting: Anchoring LSTMs for Explainable Risk Management | stat.ML | 2026-05-07 |

**20 real papers** are referenced above, spanning 13 primary categories and the full
publish window (`2604.19580` on the early edge, `2605.14976` on the late edge). The flavor
is unmistakably current: VQ discrete latent factors for stock ranking, Ricci-flow geometry
for herding, decision-focused-learning failure modes in SPO portfolios, VAE yield curves
under no-arbitrage. None of these is in any textbook. That is the point.

### The honest characteristic: fresh but temporally narrow

This corpus is **bleeding-edge but only six weeks deep**. The 200 records are the *most
recent* q-fin submissions, scraped in a single batch — they cluster between 2026-04-03 and
2026-05-14 and were all fetched at one timestamp. There is no 2024 paper here, no 2025
paper, nothing from January–March 2026.

That is a deliberate freshness-vs-breadth tradeoff and we should be honest about both
sides:

- **Upside (why we want this):** every paper is pre-arbitrage by the McLean-Pontiff clock.
  Nothing here has had time to be competed away. This is precisely the regime where
  undiscovered alpha can still exist.
- **Downside (what we give up):** a six-week window is not a research *library*; it is a
  research *feed*. Methodological diversity is high, but a strategy that needs a long
  out-of-sample track record cannot be sourced from a paper that is six weeks old, and a
  single batch will go stale the moment the next week of arXiv lands.

The correct response is **not** to widen the scrape backward into older papers — that would
reintroduce exactly the arbitraged-away canon we are trying to escape. The correct response
is to **make the scrape recurrent**: a continuously-refreshed rolling window, not a
one-time seed. Treat `manifest.jsonl` as the current frame of a stream, not a static
catalogue. See *Recurrent auto-scrape* below.

## Curation criteria, reoriented for preprints

The old criteria were written for famous published papers and do not survive contact with
a six-week-old preprint. They are replaced, not amended:

| Old criterion (retired) | New criterion (preprint-native) |
| --- | --- |
| Peer-reviewed journal **or** arXiv with >50 citations / established author | **Novelty signal.** Preprints are too new to be cited at all — citation count is structurally unavailable and, per the thesis above, *negatively* informative if it weren't. Screen on idea novelty and the absence of a known canonical equivalent, not on social proof. |
| Strategy implementable, no proprietary feeds | **Implementability from the abstract.** Same intent, but assessed early: does the abstract describe a signal with definable entry/exit on data we can actually get (liquid public markets, public benchmarks)? Papers like `2605.12151` (RED-2400) even ship their own public benchmark — that is a strong signal. |
| Backtest period ≥ 10 years | **Data availability over track-record length.** A six-week-old paper has no decade of OOS by construction. Require that *we* can assemble a defensible walk-forward window from public data — the paper's own backtest length is informational, not a gate. |
| Re-runnable to Sharpe ≥ 0.5 | **Methodological signal in the abstract.** Before any code is written, the abstract must contain enough method (the mechanism, the inputs, the claimed effect) for Dan to judge whether it is even worth implementing. Vague "we use deep learning" abstracts are screened out cheaply. |
| No look-ahead bias in re-implementation | **Unchanged — and now the load-bearing gate.** With novelty cheap and citations gone, the look-ahead audit + full four-primitive selection-bias gate is what separates a real edge from a six-week-old overfit. This is where rigor moved to. |

Net: **novelty gets a paper into the funnel; the selection-bias gate decides if it
survives.** Quality is, at this stage, *abstract-screened, not full-text-vetted* — see
limitations.

## How this corpus feeds strategy fusion

A bleeding-edge preprint stream is the natural input to the **multi-paper strategy fusion**
direction (companion spec `docs/specs/strategy-fusion-spec.md`, branch
`dbrowneup/strategy-fusion` — referenced here, not required reading for this doc). The
relevant properties:

- **Cross-paper amalgamation.** No single six-week-old preprint is a finished, validated
  strategy. The value is in *combining* primitives across papers — e.g. a regime model
  from a `stat.ME` paper, a portfolio constraint from a `cs.AI` ESG-constraint paper, a
  transaction-cost-aware ranking correction from a `q-fin.PM` SPO paper — into a fused
  candidate that no individual paper proposed. A broad, current corpus is the raw material;
  fusion is the synthesis step.
- **User-steered.** The 30-category breadth lets fusion be steered by user intent
  (risk-management-heavy vs. portfolio-construction-heavy vs. microstructure) because the
  corpus actually has depth in each lane this month.
- **Novelty-optimizing.** Fusion should prefer combinations grounded in the freshest,
  least-arbitraged inputs — which only works if the corpus is kept fresh. A fusion engine
  pointed at the classical canon would amalgamate already-dead signals into a more elaborate
  dead signal.

## Recurrent auto-scrape (the direction, not a one-time seed)

The single most important architectural consequence of the alpha-decay thesis: **the
corpus must be a recurring job, not a deliverable.** A static `manifest.jsonl` is
already aging the moment it is written — by McLean-Pontiff logic its contents are
*monotonically losing value* as the rest of the market reads the same arXiv listing.

The intended shape (sketch, for the team — not a built feature yet):

1. A scheduled scrape (daily or weekly) pulls the newest q-fin submissions and appends to
   the manifest, keyed by `arxiv_id` + `pdf_sha256`.
2. A rolling window (e.g. trailing N weeks) defines the *active* corpus the strategy engine
   reads; older records age out of "active" but are retained for OOS once enough calendar
   time has accrued — that is how a six-week-old paper *earns* a real walk-forward window
   without us widening the scrape backward into the arbitraged canon.
3. New records enter the funnel under the preprint curation criteria above; only
   selection-bias-gate survivors get a strategy passport.

This turns the freshness-vs-breadth tradeoff from a static compromise into a process: the
corpus stays bleeding-edge *and* eventually accumulates the OOS depth that a single batch
cannot have.

## Limitations & next steps

Honest accounting of what this corpus is *not* yet:

- **Temporal breadth is six weeks.** No history before 2026-04-03; no walk-forward depth is
  derivable from the papers themselves yet. Mitigation is the recurrent scrape + age-out
  retention above, not a backward widening of the scrape.
- **No dedup against published versions.** 34/200 records already have a `v2+`
  (`updated` ≠ `published`); none are checked against a later peer-reviewed journal
  version. A preprint that has since been published (and therefore started its decay clock)
  is not currently distinguished from a still-obscure one. Dedup-against-published is an
  open task.
- **Quality is abstract-screened, not full-text-vetted.** The curation criteria above
  operate on titles/abstracts/categories. No paper here has been read in full, implemented,
  or run through the four-primitive selection-bias gate. Nothing in this corpus is a
  validated strategy — it is a *candidate stream*.
- **Single-batch fetch.** Everything was pulled at one timestamp. Until the scrape is
  recurrent, this file describes a snapshot that is already aging.

**Next steps:**

1. Team review of this reorientation (this doc, ahead of standup).
2. Wire the scrape into a recurring job; define the active-window length and the age-out
   retention policy.
3. Add dedup-against-published-version (arXiv `v2+` and external DOI match).
4. First curation pass: Dan runs ~5 candidates from the live manifest through the
   selection-bias gate to validate the preprint-native criteria end to end.
5. Connect the active corpus to the strategy-fusion intake per
   `docs/specs/strategy-fusion-spec.md`.

---

_This file used to be a menu of canonical papers. It is now a description of a living
preprint stream and the rationale for keeping it that way. If the team disagrees with the
reorientation, the place to resolve it is Discord — not a silent re-add of the classical
list._
