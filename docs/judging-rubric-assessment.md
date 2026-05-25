# Judging-Rubric Self-Assessment — Day-10 (2026-05-22)

> **Status:** Day-10 rewrite (2026-05-22). The Day-3 version of this doc scored us
> 13/40 ≈ 33%; almost every line item it called out as "missing" has since shipped.
> This version re-scores against shipped reality, adds the new **Arc OSS Showcase**
> dimension, and lays out the remaining gap-closure work for the final 3 days to
> submission.
> **Audience:** Archimedes hackathon team.
> **Purpose:** Honest assessment of where we stand against the rubric with 3 days
> to go. Identifies the biggest *remaining* gaps and what's already done. Re-read
> daily.
> **Source for rubric weights / categories:** original Canteen rubric (see
> [`archive/agora_project_analysis.md`](archive/agora_project_analysis.md) § 1
> for historical detail).

## The rubric

| Weight | Category                | Reads from                                                  |
| ------ | ----------------------- | ----------------------------------------------------------- |
| 30%    | Agentic Sophistication  | How much the AI actually decides vs. just automates         |
| 30%    | Traction                | Real users, real transactions, real volume *during the event window* (arc-canteen telemetry is the scoreboard) |
| 20%    | Circle Tool Usage       | Creative use of Wallets, CCTP, Gateway, App Kit, Contracts, USYC, USDC, Paymaster |
| 20%    | Innovation              | Novel approaches, emergent behavior, research insight       |
| **+**  | **Arc OSS Showcase**    | **Separate parallel competition — reusable open-source primitives** (see [`../ARC-OSS-SHOWCASE.md`](../ARC-OSS-SHOWCASE.md)) |

## TL;DR — Day-10 running score: ~28 / 40 ≈ 70%, plus a strong Arc OSS bid

| Weight | Category               | Score / 10 | Trend           | Risk          |
| ------ | ---------------------- | ---------- | --------------- | ------------- |
| 30%    | Agentic Sophistication | **7**      | Improving fast  | Low           |
| 30%    | Traction               | **4**      | Telemetry-bound | **Highest — fixable via discipline, not code** |
| 20%    | Circle Tool Usage      | **6**      | Improving       | Low           |
| 20%    | Innovation             | **9** ⭐   | Strongest       | Low           |

**Net trajectory:** the Day-3 "promising specs, no live agent" diagnosis is gone. The hardware-and-software risks have collapsed; the remaining risk is **operational** — daily arc-canteen telemetry discipline and the launch communique reaching real eyeballs.

---

## 30% Agentic Sophistication — current: 7 / 10

**What the rubric values:** the agent making non-trivial decisions autonomously after deployment, not just at design time. Reasoning visible, regime-adaptive, strategy rotation in response to drift / market state.

**What we have (post Day-10):**

- ✅ **Live autonomous orchestrator** — `chain/agent_runner.py` (744 lines) runs on the EC2 testnet stack. Polls market, evaluates per-strategy signals, rebalances vaults, publishes reasoning trace hashes on-chain. Env-configurable (`AGENT_INTERVAL_SECONDS`, `AGENT_VAULT_ADDRESSES`, `AGENT_USDC_FLOOR`, etc.).
- ✅ **Regime detection — two implementations live** — `regime_detector.py` (v1 heuristic) and `statistical_regime.py` (v2 GMM-based with multi-signal scoring + transition matrix). The `RegimePanel` UI surface shipped.
- ✅ **Four portfolio constructors** — `portfolio_constructor.py` (orchestrator), `portfolio_optimizer.py` (Önder's MVO), `kelly_portfolio.py` (Kelly + risk parity + regime-aware deleveraging), and the new (Day-10) `portfolio_agent.py` (**LLM-driven agentic advisor with tool-use, 12-iteration agent loop**, picks individual instruments not just ETFs).
- ✅ **Backtest evaluator** — Önder's analytics engine produces real `BacktestResult` records with the full selection-bias contract (DSR / PBO / OOS Sharpe / look-ahead audit). **2 Tier-1 strategies pass the full gate today** (Faber 2007 SMA-200, Moreira-Muir 2017 vol-managed); the others' verdicts are visible in the passport.
- ✅ **3-input fusion engine** — `services/strategy_fusion.py` consumes user brief × live market regime × 10,000-paper corpus → grounded strategy spec. Async generation jobs (`POST /api/strategies/generate`).
- ✅ **Reasoning traces persisted + indexed** — every construct + fusion trace flows into `/api/traces` automatically; Portfolio's agent activity feed and the Reasoning page surface them.
- ✅ **Stress engine** — `services/stress_engine.py` (380 lines) computes six canonical scenario shocks (historical + scenario) against any portfolio. Backend ready.

**What's still missing:**

- ❌ **Commit-reveal trace integrity** spec (`specs/commit-reveal-trace-spec.md`) is spec'd but not live — the on-chain anchor still attests existence-at-T, not existence-before-trade.
- ❌ **Stress engine UI integration** — the backend is wired and clean, but no UI surface renders the scenario table yet.
- ❌ The Day-9 `regime_detector.py` vs `statistical_regime.py` redundancy — both exist; which one drives the live demo is unclear from the code (see `chuan-architecture-survey.md` Gap Cluster #2).

**To get to 9/10:** ship one of (a) commit-reveal trace integrity wired live to the UI's "Verify trace" affordance, or (b) the stress-engine table surfaced in `Portfolio.jsx` with a one-click run against the active portfolio.

**To get to 10/10:** both, plus a recorded demo segment where the agent rotates strategies in response to a regime change live on stage (this is what `agent_runner.py` was built to do; needs a controllable demo trigger).

---

## 30% Traction — current: 4 / 10 (telemetry-bound; the floor is "are we logging?")

**What the rubric values:** real users, real transactions, real volume during the event window. The Canteen team explicitly noted this weight is unusual; "great founders ship and get users in two weeks."

**The scoreboard:** `arc-canteen` telemetry — specifically the `update-traction` and `update-product` events that each teammate logs. **`arc-canteen status` is what the judges see.**

**What we have:**

- ✅ **Live testnet deploy** at `http://13.40.112.220/` with the full spine (Landing / Generate / Library / Corpus / Portfolio / Reasoning / Learnings) and the agentic advisor wired end-to-end.
- ✅ **Real product** users can browse + generate + deploy strategies against, with no fake data on the judge path.
- ✅ **Discord presence** + the launch communiques going out.
- ⚠️ **arc-canteen telemetry status: ambiguous.** Some `update-product` calls have been made; the per-merge cadence is inconsistent. Each teammate needs to verify with `arc-canteen status`.

**What's missing:**

- ❌ **Coordinated launch reach** — the launch plan (`docs/launch-plan.md`) targeted a 1–3 day window before submission. Execution depends on the team converging on timing + sharing the splash through real channels.
- ❌ **Outbound outreach** — Canteen Discord, Arc Builder Discord, crypto Twitter, r/algotrading, QuantConnect forums. Every conversation is a `update-traction` event.
- ❌ **Real testnet user count** — strategies generated by real outsiders (not us). The product works; the funnel needs people in it.

**Daily discipline (do this every day until submission):**

```bash
# After every meaningful ship today
arc-canteen update-product "What you shipped today, in one line"

# After every conversation with someone outside the team
arc-canteen update-traction "Who you talked to, what they thought"

# Verify what judges see
arc-canteen status
```

**To get to 6/10:** sustained daily telemetry + 10 logged conversations + the coordinated launch executing through Discord/Twitter/relevant forums.

**To get to 8/10:** 30+ portfolios created on testnet by real (non-team) people, each one logged via `update-traction`.

**Risk:** **highest of the four categories.** A team that shipped brilliantly and forgot to log loses ~half of the 30% weight for nothing. *This is the cheapest, most fixable points on the board.*

---

## 20% Circle Tool Usage — current: 6 / 10

**What the rubric values:** depth and creativity across Wallets, CCTP, Gateway, App Kit, Contracts, USYC, USDC, Paymaster.

**What we have:**

- ✅ **10 contracts deployed on Arc testnet** — `AMMPool`, `AMMRouter`, `AssetRegistry`, `PriceOracle`, `ReasoningTraceRegistry`, `SyntheticFactory`, `SyntheticToken`, `SyntheticVault`, `Vault`, `VaultFactory`. Updated Day-10 with the multi-asset NAV vault (`Vault.sol` now prices all holdings via oracles in `totalAssets()`).
- ✅ **Circle Developer-Controlled Wallets** — `chain/circle_signer.py` (246 lines) is the production signing path. **No raw private keys** for vault operations; the oracle owner wallet is a Circle-managed wallet.
- ✅ **USDC as exclusive settlement** — every flow priced in USDC, no native-token gas friction.
- ✅ **Multi-wallet UX in frontend** — MetaMask, Coinbase, generic browser wallet via `viem`.
- ✅ **Multi-asset NAV vault** (Day-10) — vault total assets correctly reflect all synthetic holdings through oracle prices, not just USDC.

**What's missing:**

- ❌ **CCTP** integration (cross-chain USDC movement).
- ❌ **Gateway** integration (unified balance / nanopayments).
- ❌ **Paymaster** for USDC-denominated gas on user-facing transactions.
- ❌ **USYC** integration as the risk-off anchor — the Fixed Income tier was added (Önder #105) but USYC as an on-chain asset isn't wired yet.

**Reference material at hand** (the context-arc submodule):

- `submodules/context-arc/circlefin-skills/use-smart-contract-platform.md` — deploy
- `submodules/context-arc/circlefin-skills/bridge-stablecoin.md` — CCTP + Gateway
- `submodules/context-arc/circlefin-skills/use-gateway.md` — unified balance
- `submodules/context-arc/samples/arc-escrow/` — closest existing vault pattern
- `submodules/context-arc/samples/arc-multichain-wallet/` — CCTP integration
- `submodules/context-arc/samples/arc-p2p-payments/` — Paymaster + USDC

**To get to 8/10:** add Paymaster so user transactions are USDC-paid (no gas confusion). Add USYC as a real on-chain asset in the Fixed Income tier.

**To get to 9/10:** CCTP or Gateway for one meaningful cross-chain action (deposit-from-mainnet flow would be the obvious one).

---

## 20% Innovation — current: 9 / 10 ⭐ (our strongest category)

**What the rubric values:** novel approaches, emergent behavior, research insight.

**What we have that no other AI-portfolio submission will have:**

- ✅ **Strategy passport** ([`specs/strategy-passport-spec.md`](specs/strategy-passport-spec.md)) — every strategy carries paper arxiv-id + methodology hash + curator signature + on-chain registration tx. Other AI portfolios make "trust me" claims; ours is bound to bleeding-edge academic research with a verifiable hash chain. **Shipped + visible in the live UI.**
- ✅ **Selection-bias corrections — live, not aspirational** ([`specs/selection-bias-corrections-spec.md`](specs/selection-bias-corrections-spec.md)). DSR (Bailey & López de Prado 2014) + PBO (Bailey/Borwein/López de Prado/Zhu 2014) + walk-forward OOS + look-ahead static audit. **2 Tier-1 strategies pass the gate today; the failures are visible** (we don't hide them). Real 22-year SPY backtest data — every `is_backtest_placeholder=true` flag is gone.
- ✅ **Paper-claim deltas surfaced honestly** — `sharpe_vs_paper`, `cagr_vs_paper`, McLean-Pontiff (2016) post-publication-decay estimate. We show where the strategy was expected to live (paper) vs. where it actually lives (our re-run). Hidden by every competitor; surfaced by us as a feature.
- ✅ **On-chain reasoning trace anchoring** — `ReasoningTraceRegistry` deployed; `chain/trace_publisher.py` anchors keccak256 hashes for every construct + fusion trace. Verifiable on Arc.
- ✅ **10,000-paper q-fin corpus + DB-backed substrate + interactive Corpus Explorer** ([`corpus-architecture.md`](corpus-architecture.md)). The "research-grounded" claim is *quantitatively* real (10k papers across 9 q-fin categories, 2008–2026), not marketing copy.
- ✅ **Two-tier marketplace primitive coverage** — Tier 1 = paper-grounded + selection-bias-corrected + full agent; Tier 2 = freestyle + reasoning traces only. The badge means something concrete.
- ✅ **Agentic LLM portfolio advisor with tool-use** (Day-10, `portfolio_agent.py`) — the only AI-portfolio submission running a multi-iteration agent loop that picks individual instruments (not just ETF baskets) and anchors each pick to a paper-grounded strategy passport.
- ✅ **3-input fusion engine** — user brief × live market regime × research corpus → grounded strategy spec. Novel synthesis path that no competitor surfaces.
- ✅ **Stress test engine** (Day-10, `stress_engine.py`) — six canonical scenario shocks, per-asset-class shock vectors. Standard at every real shop, novel for a hackathon AI-portfolio entry.
- ✅ **Honest framing baked into product surfaces** — `docs/anti-features.md` § "pitch-rigor anti-claims" rules out "blockchain as memory", "predicted alpha", "trace proves causation", and "regulatory clarity" claims. We claim auditability + rigor; we don't claim returns. **Surfaced in the live UI as "What we're NOT promising."**

**What's still missing:**

- ⚠️ **Commit-reveal trace integrity** ([`specs/commit-reveal-trace-spec.md`](specs/commit-reveal-trace-spec.md)) — spec'd, not wired to the live anchor flow.
- ⚠️ **Live arxiv-extraction demo** — `services/arxiv_pipeline.py` can extract a strategy from a fresh paper but isn't wired to a one-click demo path.

**To get to 10/10:** add a recorded demo segment where Dan extracts a strategy from a fresh arxiv paper live on stage — extract → Claude → new strategy file → backtest gate → reasoning trace anchored on-chain in under 5 minutes. "We can turn a paper into an audited strategy on stage, right now."

---

## ⭐ NEW: Arc OSS Showcase — strong contender

The Arc OSS Showcase is a parallel competition for codebases other Arc builders can fork. Our positioning lives in [`../ARC-OSS-SHOWCASE.md`](../ARC-OSS-SHOWCASE.md).

**What we expose as forkable primitives:**

1. **Strategy Passport schema + validation** — `models/strategy.py` + `services/strategy_provider.py` + the spec
2. **Selection-bias rigor gate** — `services/rigor_evaluator.py` + the spec; DSR + PBO + OOS + look-ahead implementations are all open and citable
3. **On-chain reasoning trace anchoring** — `chain/trace_publisher.py` + `ReasoningTraceRegistry.sol` + the IPFS pinning design note
4. **DB-backed q-fin corpus substrate** — `services/corpus_service.py` + `models/corpus_store.py` + the corpus-architecture walkthrough
5. **`LLM_*` provider-agnostic backend factory** — `services/llm_backend.py` — fork-and-go LLM provider abstraction
6. **3-input fusion engine** — `services/strategy_fusion.py` + the spec
7. **Circle-signer pattern** — `chain/circle_signer.py` — reusable for any Arc app that wants Circle-managed wallets instead of raw keys

**Why we should be a top contender:**

- We hit *all* the OSS criteria: codebase is fully open (Unlicense), exposes 7+ distinct primitives, has clear per-primitive documentation, and the README + per-primitive cards make forking straightforward.
- Each primitive maps to a real Arc-builder need (passport for any strategy-publishing product; rigor gate for any AI-decision product; reasoning trace for any agent product).
- Unlike most hackathon submissions, the docs already exist and are kept current — not a post-hoc add.

---

## Cross-category forcing function: the launch + telemetry discipline

The Day-3 forcing function was *build the end-to-end loop*. That's now built. The Day-10 forcing function is **execute the launch + log religiously**.

### The minimal viable launch loop

1. **The launch communique** ships through Discord (Canteen + Arc Builder), Twitter, and the relevant subreddits, in the 1–3 day window before submission (per [`launch-plan.md`](launch-plan.md)).
2. **Every conversation it generates** gets logged via `arc-canteen update-traction`.
3. **Every meaningful ship** (this week: the agentic advisor, the multi-asset NAV vault, the stress engine, the spine-strip) gets logged via `arc-canteen update-product`.
4. **The recorded demo video** ships — Loom or YouTube, ≤ 3 minutes, hitting the spine end-to-end (Generate → rigor verdict → Deploy → reasoning trace → Verify).

### What checks every rubric category

| Category               | What the demo shows                                                  |
| ---------------------- | -------------------------------------------------------------------- |
| Agentic Sophistication | LLM agent loop picks instruments, anchored to strategy passports; autonomous rebalance + reasoning trace |
| Traction               | Real on-chain transactions by real users; logged via `update-traction` |
| Circle Tool Usage      | USDC settlement + Circle Wallets + 10 contracts + multi-asset NAV     |
| Innovation             | Strategy passport + selection-bias gate + paper-claim delta visible + corpus explorer  |
| Arc OSS Showcase       | 7 forkable primitives, each with how-to-fork docs                    |

## Recommendations for the final 3 days

1. **Today (everyone):** verify `arc-canteen status` shows your contributions. Backfill any merged PR not yet logged via `update-product`.
2. **Today (Marten as schedule owner):** drive the launch-timing convergence per [`launch-plan.md`](launch-plan.md). Pick a date; communicate in #standups.
3. **Tomorrow:** record the ≤3-minute demo video. Even a rough cut is better than no cut. Re-record after polish.
4. **This week:** execute the launch. Every team member shares through their own channels. Every reply gets logged via `update-traction`.
5. **Stretch (any cycles left):** wire one of (a) commit-reveal trace integrity → "Verify" button, or (b) stress-engine table on `Portfolio.jsx`. Either gets the agentic score to 9, and Innovation to 10.

## Open questions

1. **Is the Arc OSS Showcase form filled out + submitted?** See [`../ARC-OSS-FORM-DRAFT.md`](../ARC-OSS-FORM-DRAFT.md) for the team-review draft. Submission is via Google Form (per the showcase landing page) or via `arc-canteen update-product` with `"ArcOSS:"` prefix.
2. **Demo video recording — who owns it?** The deck owner is the obvious candidate. Length target: ≤3 minutes per the Canteen submission form.
3. **Live demo vs recorded demo at the in-person event?** Per the submission form, a video demo is required regardless. If both are an option, live is the safer demo-day choice (controllable failure modes) and recorded is the safer submission-form choice.
