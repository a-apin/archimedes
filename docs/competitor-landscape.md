# Competitor Landscape — Strategy Validation & On-Chain Portfolio Agents

> **Status:** Canonical competitive reference. **Supersedes and voids the
> 2026-05-19 version** — see "What changed" below.
> **Date:** 2026-07-28.
> **Audience:** Archimedes team, and any agent that lands here via `CLAUDE.md`'s
> pointer to this file.
> **Sourcing:** every material claim below carries a source URL or an explicit
> `UNVERIFIED` marker. The Almanak SDK claims (file count, statistical-methods
> grep, `n_trials=100`, walk-forward module size) were independently
> re-verified on 2026-07-28 against a fresh shallow clone of the public repo —
> not taken on report. Where a figure is secondary-sourced or approximate, that
> caveat is carried through rather than dropped.
> **Scope note:** this document covers the technical ecosystem — who exists,
> what they ship, what rigor (if any) they apply, and what their architecture
> structurally permits. Published unit-of-sale is in scope where it follows from
> architecture (a broker-dealer rail cannot serve an agent wallet; a withdraw-only
> vault is not deploying capital). Acquisition economics, fundraising
> read-throughs, and speculation about competitors' intentions are out; those
> belong in internal strategy material, not a public technical reference.

## What changed, and why the old version is void

The version this replaces was frozen 2026-05-19, mid-hackathon. Its "Tier 1 —
actual competition" section named three Agora-hackathon peer projects
(Pantheon-Trades, ReasoningReceipt, CronusCapital) as the live competitive
set, with Morpho/Gauntlet/Upshift/Accountable held out as a "vision, not
competitor" tier. That framing had no present-day bearing: it predates the
Composer/SoFi acquisition entirely, and contains nothing on Almanak — the
closest architecture to Archimedes that exists today. Whatever the current
status of those three hackathon peers, they are not the market Archimedes is
actually being compared against ten weeks on, and a document that still
frames them as "the actual competition" is actively misleading to anyone
using it as a current reference.

This version replaces the hackathon-peer framing with the real market: funded,
live products doing strategy construction, backtesting, ranking, or
recommendation — the products a trader actually chooses between today.

---

## The landscape

### Strategy construction, backtesting & recommendation platforms

| Company | Primary product | Validation rigor | Open source | Real money | Who it's for |
|---|---|---|---|---|---|
| **Archimedes** | Plain-English idea → multi-agent debate over a ~10k-paper arXiv corpus → cost-aware out-of-sample statistical gate → public strategy leaderboard, failures included | Commission-net, chronological 30% holdout, 90% one-sided confidence with HAC/Newey-West-robust standard errors. The agent-generated path additionally deflates that strategy's Sharpe against its own candidate pool (the Deflated Sharpe Ratio). **The curated (paper-sourced) path currently runs single-trial — no cross-strategy multiple-testing correction is applied on that path today** ([`adr/rigor-gate-unification.md`](adr/rigor-gate-unification.md), [`rigor-methods.md`](rigor-methods.md)) | Yes — [Unlicense](../LICENSE), full public-domain dedication | No — Arc testnet only; non-custodial vaults, no live capital deployed | An individual trader or researcher who wants an idea checked before risking money on it |
| **Composer** (Composer by SoFi) | No-code natural-language strategy builder + automated live execution through a regulated broker-dealer | 2,000+ community-built strategies on the public Symphony Database; no published statistical correction for the multiple-testing exposure a supply-optimized marketplace creates, and no published rejection rate | No | Yes — regulated broker-dealer execution | Retail investors building and running rules-based strategies without writing code |
| **Almanak** | Python SDK for building, backtesting, and deploying autonomous DeFi trading/LP agents; formerly paired with public pooled vaults | **Zero inferential statistics in the codebase** (independently verified — see below). Its optimizer runs 100 trials per walk-forward window and reports the winning Sharpe with no correction for the search | Yes — SDK is public on GitHub | Formerly — public vaults moved to withdraw-only, new deposits disabled, December 2025 | DeFi-native quant teams building and running their own on-chain execution agents |
| **QuantConnect** | Algorithmic trading platform (the LEAN engine) + a named academic-grants program addressing the finance replication crisis | Owns the framing — a program literally titled *"Solving the Replication Crisis in Finance"* — but has not published a replication statistic for the papers it funds, and applies no DSR/PBO/equivalent gate to user-built strategies | Yes — LEAN, Apache-2.0 | Yes — live trading node | Developers and researchers building and running their own algorithms |
| **Quantpedia** | Subscription encyclopedia of 900+ published quant strategies, ~800 with out-of-sample backtests | Shows in-sample vs. out-of-sample decay per strategy — the closest commercial analog to a rigor gate on the market, and evidence that "show the decay, not just the backtest" already has paying subscribers | No | No — reference/research product, not an execution venue | Quant researchers and PMs screening the academic literature for tradeable ideas |
| **Danelfin** | Daily AI stock-ranking score (1–10) across technical, fundamental, and sentiment signals | The most statistically rigorous of this group: a public audit page documents Benjamini–Hochberg FDR-adjusted p-values, a Harvey/Liu/Zhu t ≥ 3.0 hurdle, Newey–West HAC standard errors, and out-of-sample testing across three separate market regimes | No | Yes — signal drives real brokerage trades | Retail stock-pickers wanting an AI-ranked signal |
| **Quantopian** (defunct, shut down Nov 2020) | Crowdsourced strategy platform — users wrote algorithms; top performers were funded with the platform's own capital | No admission gate ahead of capital allocation: strategies that looked strong in-sample were what the crowd produced, because nothing filtered for out-of-sample survival before capital followed them | Its backtesting engine lives on as the independently maintained **Zipline-Reloaded**, Apache-2.0 | Was — technology and team quietly picked up by Robinhood after shutdown | — |

### The on-chain vault layer, and the risk-modelling layer next to it

Two more categories complete the map, and **neither validates strategy alpha**:

| Company | What it does | What it does not do |
|---|---|---|
| **Enzyme** | On-chain asset-management protocol — vault creation, custody, accounting, fee routing | No claim of strategy validation; hosts an overfit strategy exactly as readily as a sound one |
| **dHEDGE** | Non-custodial tokenized vault protocol; manager-run funds on L2s | Same — custody and fee routing, no strategy-level gate |
| **Set Protocol / TokenSets** | ERC-20 "Set" tokens wrapping a basket or strategy; V2 contracts still run autonomously | Active feature development stopped ([Set Labs deprecation notice, 2023](https://medium.com/set-protocol/announcing-the-deprecation-of-set-protocol-v2-and-tokensets-f019410f2d2a)); the protocol runs on immutable contracts but nothing new is being built on it |
| **Gauntlet** | Simulation-driven parameter recommendations (collateral factors, supply caps, rate curves) for lending/DeFi protocols | Validates **protocol solvency**, not strategy alpha — a different question |
| **Chaos Labs** | Real-time risk oracles that adjust protocol risk parameters automatically within governance-approved bounds | Same scope as Gauntlet — protocol-level risk, not strategy-level validation |

No product in either table runs a statistical gate on strategy alpha before a
user or an agent can act on the strategy. That gap — not "on-chain," not
"open source" — is the one that matters. See "The wedge, stated narrowly"
below.

### The second gap: nobody sells a verdict, and nobody an agent can buy one from

The gate is one axis. There is a second, and it is structural rather than a
matter of anyone's roadmap: **no product above prices an adjudication, and the
two closest competitors are each architecturally precluded from selling one to
an autonomous agent.** Stated as fact rather than as a knock — in both cases the
architecture is correct for the product they are actually building.

- **Composer executes through a regulated broker-dealer.** Its own launch
  language positions it around *"clear, predefined rules"* and explicitly away
  from continuous agentic trading. Broker-dealer rails require a KYC'd human
  account holder; an autonomous agent with a wallet is not one, and cannot
  become one without the human. This is not a gap Composer could close by
  shipping a feature — it is the rail it deliberately chose, and it is the
  opposite rail from agent-payable USDC settlement.
- **Almanak exited capital deployment.** Public vaults moved to withdraw-only
  with new deposits disabled from December 2025. What remains is an SDK — a tool
  you clone and run yourself, not a service you call and pay. There is no
  endpoint to charge and no adjudication being sold; the machine-checkable
  permissioning described below governs an agent *you* operate, not a
  transaction between you and Almanak.
- **Neither charges per adjudication, and neither does anyone else in either
  table.** Composer and Quantpedia charge subscriptions for access to a
  platform or a library; Danelfin charges for a signal feed; QuantConnect
  charges for compute and live-trading nodes; Almanak's SDK is free and its
  revenue was vault-based. **The unit of sale is a seat, a feed, or a node.
  Nobody prices the verdict itself.**

Whether a verdict is a *good* thing to sell is an open question and this
document does not claim to have answered it — see "Honest limitations." What is
established is narrower and worth stating precisely: it is unoccupied, and the
two nearest neighbours cannot occupy it without changing rails.

**Archimedes' own position on this axis, stated with the same discipline:** the
settlement machinery is real (a complete 402 → sign → verify → settle round-trip
through Circle Gateway) and the service is agent-discoverable, but
`PAYMENTS_DRY_RUN` is on in production, the route-level paywall on generation is
not built, and **no agent has ever paid us.** The rail exists; it has carried
nothing.

---

## Almanak, in more detail — the closest architecture to Archimedes

Almanak is the most instructive comparison because the shape is nearly
identical: an agentic pipeline that turns an idea into a backtested,
deployable DeFi strategy. The differences are what's worth reading closely.

**Independently verified 2026-07-28** against a fresh shallow clone of
[`github.com/almanak-co/sdk`](https://github.com/almanak-co/sdk) (public
repo, actively developed — commits landing the same day this was checked):

- **4,454 Python files.** (A private research pass on 2026-07-27 counted
  4,449 on the previous day's commit — the ~5-file drift is same-day
  development, not a discrepancy.)
- **Zero matches, repo-wide, for:** `PBO`, `CSCV`, deflated Sharpe,
  probabilistic Sharpe, `bonferroni`, `benjamini`, `false.discovery`,
  `family.wise`, `multiple.testing`, `selection.bias`, `data.snooping`.
- `p_value` appears in 57 files and `confidence.interval` in 7, but every hit
  inspected is in test fixtures unrelated to strategy-return inference (LP
  valuation, position-discovery test names) — not a statistical test on
  strategy returns anywhere in the tree.
- **`almanak/framework/backtesting/pnl/optuna_tuner.py`** runs
  `n_trials=100` per walk-forward window and reports the winning Sharpe with
  no haircut for the number of trials searched. This is precisely the
  multiple-comparisons exposure DSR/PBO exist to correct for, present in
  their own optimizer.
- **`almanak/framework/backtesting/pnl/walk_forward.py` is 1,409 lines** and
  includes a genuine capability Archimedes does not have: a
  `ParameterStability` check that computes the coefficient of variation of
  optimal parameters across walk-forward windows and flags instability
  (`stability_threshold`, default CV 0.3). That is a different, orthogonal
  overfitting signal from DSR/PBO/OOS-Sharpe, and it is real, shipped code.
- Almanak's permissioning is a `PermissionManifest` — auto-derived from
  strategy code, scoped per-chain, per-contract, and **per-4-byte function
  selector**, exportable as [Zodiac Roles](https://docs.roles.gnosisguild.org/)
  (Gnosis Guild) for a Safe multisig. That constrains not just *where* funds sit but *what the agent is
  allowed to do with them* — a materially stronger and more machine-checkable
  claim than "non-custodial ERC-4626," which only constrains custody.
- Their execution-realism tooling (MEV/liquidation simulation, funding-rate
  and health-factor modeling, Anvil mainnet-fork paper trading, named crisis
  replays) is more developed than Archimedes' equivalent.

**What changed in Almanak's product, factually:** their public vaults moved
to withdraw-only with new deposits disabled starting December 2025; the SDK
remains public and under active development (commits observed the same day
as this check). Their own public product communications describe planned
2026 additions including a dedicated backtesting simulator, a "Backtesting
Agent," an autonomous research-scanning "Alpha Agent Team," and a Quant IDE.
None of those four exist in the SDK as of the 2026-07-28 grep above — the
repository currently contains no reference to any of those feature names.
**`UNVERIFIED` (source-URL not re-located):** the four-feature roadmap
description is reported from Almanak's own public December 2025 product
update; the specific post could not be re-located during this pass, so treat
the *content* of the roadmap claim as primary-source-reported rather than
independently re-confirmed. The *absence of those features in the current
codebase*, by contrast, is independently verified by direct grep, above.

---

## Quantopian — why it died, and why that's relevant

Quantopian ran from 2011 to November 2020: a crowdsourced platform where
users wrote trading algorithms, and the best-performing were allocated the
platform's own capital. It shut down with no reason given at the time;
retrospectives converge on the same diagnosis — strategies that looked
strong in backtests (often curve-fit to history, with slippage and
transaction costs frequently unmodeled) collapsed once they traded live,
because nothing in the pipeline filtered for out-of-sample survival before
capital followed a strategy. CEO John Fawcett's own framing on shutdown:
*"Crowd-sourcing alpha was a moonshot."* The team and technology were
subsequently picked up by Robinhood.

That is directly relevant here: it is a real, historical case where the
absence of a validation gate ahead of capital allocation was the load-bearing
failure, not an incidental detail. The backtesting engine itself survives as
[Zipline-Reloaded](https://github.com/stefan-jansen/zipline-reloaded)
(Apache-2.0), independently maintained.

---

## The academic replication literature

This is the strongest external grounding available — it turns "our gate is
harsh" into "our gate is consistent with, and if anything more conservative
than, the published replication record."

| Paper | Finding | Relevance |
|---|---|---|
| **Hou, Xue & Zhang (2020)**, *Replicating Anomalies*, Review of Financial Studies ([SSRN 3275496](https://ssrn.com/abstract=3275496)) | Re-tested 452 published anomalies: 65% fail at t > 1.96; 82% fail at a multiple-testing-adjusted t > 2.78. Survivors show much smaller effects than originally published | The single best external corroboration that a strict gate is not an outlier position |
| **Harvey, Liu & Zhu (2016)**, *…and the Cross-Section of Expected Returns*, RFS ([SSRN 2249314](https://ssrn.com/abstract=2249314)) | Given the scale of factor data-mining in the literature, the conventional t > 2.0 bar is far too lax; a new factor needs t > 3.0 to survive correction | Establishes that a strict multiple-testing bar is the academically-mandated position, not a house rule |
| **Bailey & López de Prado**, *The Deflated Sharpe Ratio* ([SSRN 2460551](https://ssrn.com/abstract=2460551)) | The Deflated Sharpe Ratio methodology | The method Archimedes implements on the agent-generated path |
| **Bailey, Borwein, López de Prado & Zhu**, *The Probability of Backtest Overfitting*, J. Computational Finance ([SSRN 2326253](https://ssrn.com/abstract=2326253)) | The PBO/CSCV methodology, later implemented in the open-source (through 2019) `mlfinlab` library before it moved to a commercial license | The method underlying the PBO control in the rigor gate |
| **Xia et al. (2026)**, *Agentic Trading: When LLM Agents Meet Financial Markets* ([arXiv 2605.19337](https://arxiv.org/abs/2605.19337)) | Audited 19 trading-agent papers: 15/19 ship no code or data, 0/19 are fully reproducible, 2/19 report time-consistent train/test splits, 1/19 models transaction costs | Extends the replication-crisis finding from academic factors to LLM trading agents specifically — the category this product and most of the table above sit in |

**Honesty guardrail carried over from the source research:** these are not
strictly apples-to-apples with any single product's strategy set — a broad
academic factor sweep is a different population from any one platform's
strategy library. Treat the relationship as *"consistent with, and generally
sharper than"* the published survival rates, not as a direct equivalence.

---

## The wedge, stated narrowly

**The defensible claim is not "we're the only open-source player."** That
claim is false and falsifiable in one search: QuantConnect's LEAN
(Apache-2.0), the independently maintained Zipline-Reloaded (Apache-2.0), and
VectorBT (Apache-2.0 with a Commons Clause; a closed-source `vectorbt.pro`
tier sits alongside it) are all open source today. `mlfinlab`
(Hudson & Thames) — the library that first implemented the CPCV overfitting
lineage the PBO control descends from — was open source (BSD) through 2019
and has since moved to a commercial, all-rights-reserved license; it is
**not** part of a current open-source claim.

The defensible claim is narrower and holds up: **nobody has open-sourced the
end-to-end product.** Not the backtesting engine, not the optimizer, not the
risk oracle — the whole pipeline: idea intake → paper-corpus debate → an
automated, cost-aware, out-of-sample statistical gate → a public pass/fail
leaderboard that includes the failures → an on-chain reasoning trail, in one
codebase. The backtesting *libraries* in this space are open source. The
*products* generally are not, and none of the open-source libraries above
ship the gate, the published rejection rate, or the on-chain provenance
layer as part of the package.

---

## The wedge has a clock on it

This position is real today, and it is not permanent.

- **Almanak's own public 2026 roadmap names a Backtesting Simulator and a
  dedicated Backtesting Agent.** Neither exists in their SDK today —
  independently confirmed above, zero grep hits across 4,454 files as of
  2026-07-28. Almanak already has the harder half of the problem solved
  (execution realism, permissioning, a working optimizer) and has publicly
  said validation-before-deployment is next.
- **PolyQuant published a near-identical replication study one month before
  this document was written** ([Quantpedia, 2026-06-30](https://quantpedia.com/guardrails-make-the-researcher-what-an-ai-agent-got-right-and-wrong-replicating-nine-equity-anomalies/)):
  an autonomous LLM researcher replicated nine published US equity
  anomalies; zero survived out-of-sample on a faithful build, and the one
  apparent survivor turned out to be a construction error. An unaffiliated
  team, a near-identical method, a harsher result than anything claimed
  here — published independently, one month ago.
- The gap between a replication study being published and a product being
  shipped around it is short, and shrinking further as LLM agents make it
  cheaper to build both the strategy generator and the checker. A reader
  should take from this document that the position described here is real
  today and time-limited, not a permanent moat. See "Honest limitations"
  below.

---

## Pre-empting the AQR counter

The most credentialed published position runs against the framing above:
Jensen, Kelly & Pedersen, *"Is There a Replication Crisis in Finance?"*,
Journal of Finance, 2023 ([Wiley](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249),
[NBER working paper](https://www.nber.org/papers/w28432), [code + data](https://github.com/bkelly-lab/ReplicationCrisis)),
argues the crisis is overstated — that most asset-pricing factors replicate
once the multiple-testing structure is modeled hierarchically (a Bayesian
model across ~150 factors, clustered into themes, largely holding
out-of-sample across a 93-country dataset).

The answer is narrower than a rebuttal, and does not contradict them:

> This is not a claim that published factors are false in aggregate. It is a
> claim about **implementability under realistic, point-in-time, cost-aware,
> out-of-sample conditions** for a specific strategy, not a factor cluster.
> Published is not the same as tradeable — PolyQuant's replication found 3.5
> Sharpe points of pure look-ahead illusion in a single anomaly once
> point-in-time data was enforced. Jensen/Kelly/Pedersen are arguing about
> whether priced factors exist. The question this gate answers is whether a
> specific, individually-stated strategy can be captured net of realistic
> costs on a chronological holdout — a narrower and more operational
> question than theirs.

---

## Honest limitations

Stated once, factually, and not revisited elsewhere in this document:

- **The rigor math is published and non-proprietary.** DSR, PBO/CSCV, and
  walk-forward OOS testing are all documented, citable academic methods (see
  the literature table above). Nothing in the gate itself is a trade secret.
- **Validation may be a feature, not a company.** Danelfin already runs FDR
  correction on its own signal; Quantpedia already shows in-sample vs.
  out-of-sample decay per strategy. Either could add a public rejection-rate
  leaderboard to what they already have.
- **The orchestration is forkable.** The codebase is Unlicense-dedicated
  public domain; there is no license barrier to a fork the day it looks
  worth forking.
- **There is no traction moat today.** Whatever advantage exists here comes
  from being early and from the credibility of publishing failed strategies
  publicly, not from technology that is hard to replicate.

---

## Sources

- Composer / SoFi: [acquisition announcement, 2026-06-23](https://www.businesswire.com/news/home/20260623095057/en/Introducing-Composer-by-SoFi-AI-Powered-Investing-From-Idea-to-Execution) ("clear, predefined rules" language, positioned explicitly away from continuous agentic trading); [Symphony Database](https://www.composer.trade/symphony) (public, uncorrected leaderboard); [pricing](https://www.composer.trade/pricing-alt)
- Almanak: [SDK repo](https://github.com/almanak-co/sdk) (independently cloned + grepped 2026-07-28); [almanak.co](https://almanak.co/); [DefiLlama TVL](https://defillama.com/protocol/almanak)
- QuantConnect: [LEAN repo](https://github.com/QuantConnect/Lean) (Apache-2.0); [replication-crisis grant announcement](https://www.quantconnect.com/announcements/16153/solving-the-replication-crisis-in-finance/)
- Quantpedia: [quantpedia.com](https://quantpedia.com/); [PolyQuant replication study, 2026-06-30](https://quantpedia.com/guardrails-make-the-researcher-what-an-ai-agent-got-right-and-wrong-replicating-nine-equity-anomalies/); [pricing/strategy counts](https://quantpedia.com/pricing/)
- Danelfin: [public audit page](https://audit.danelfin.com/) (FDR/HLZ/Newey-West methodology)
- Quantopian: [Wikipedia](https://en.wikipedia.org/wiki/Quantopian); [Bloomberg shutdown coverage](https://www.bloomberg.com/news/articles/2020-12-16/quant-trading-platform-quantopian-closes-down); [Zipline-Reloaded](https://github.com/stefan-jansen/zipline-reloaded)
- mlfinlab license history: [current license](https://github.com/hudson-and-thames/mlfinlab/blob/master/LICENSE.txt)
- VectorBT: [open-source repo](https://github.com/polakowo/vectorbt); [VectorBT PRO terms](https://vectorbt.pro/terms/remarks/)
- Enzyme: [enzyme.finance](https://enzyme.finance/)
- dHEDGE: [dhedge.org](https://dhedge.org/)
- Set Protocol / TokenSets: [deprecation notice, 2023](https://medium.com/set-protocol/announcing-the-deprecation-of-set-protocol-v2-and-tokensets-f019410f2d2a)
- Gauntlet: [gauntlet.xyz](https://www.gauntlet.xyz/)
- Chaos Labs: [chaoslabs.xyz](https://chaoslabs.xyz/)
- Academic corroboration: see the literature table above for per-paper links
- AQR counter: Jensen, Kelly & Pedersen (2023) — [Wiley](https://onlinelibrary.wiley.com/doi/full/10.1111/jofi.13249), [NBER](https://www.nber.org/papers/w28432), [code](https://github.com/bkelly-lab/ReplicationCrisis)

---

_Rewritten 2026-07-28, superseding the 2026-05-19 hackathon-era version in
full. Re-verify before citing in anything time-sensitive — this market moves
in months, not years, per "The wedge has a clock on it" above._
