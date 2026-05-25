# Demo Script & Pitch Deck Outline

> **Audience:** Archimedes hackathon team (deck owner + demo runner + Q&A primaries).
> **Status:** Day-10 update (2026-05-22) on top of the 2026-05-19 rewrite to the
> **locked product spine** ([`user-stories.md`](user-stories.md)) and the
> **tiered competitive thesis** ([`competitor-landscape.md`](competitor-landscape.md)).
> Day-10 deltas to fold into the deck on next pass: (a) the LLM-driven agentic
> portfolio advisor ([`portfolio_agent.py`](../backend/archimedes/services/portfolio_agent.py))
> now picks individual instruments + anchors each to a strategy passport — that's
> a new demo beat; (b) the stress engine ([`stress_engine.py`](../backend/archimedes/services/stress_engine.py))
> ships 6 canonical scenario shocks for any portfolio — judges-will-recognize-this
> standard-at-every-shop tooling; (c) the multi-asset NAV vault upgrade means
> `Vault.totalAssets()` now prices all synthetic holdings via oracles — fixes the
> "what does my position actually mean" judge question; (d) Arc OSS Showcase
> dimension added — see [`../ARC-OSS-SHOWCASE.md`](../ARC-OSS-SHOWCASE.md) and the
> Day-10 rubric assessment in [`judging-rubric-assessment.md`](judging-rubric-assessment.md).
> Supersedes the Day-4 "connect wallet → pick a risk profile" script entirely.
> **Length assumption:** ~3-minute pitch + ~2-minute live demo + Q&A. Adjust if Canteen says otherwise.
>
> **Honesty rules baked into this script (non-negotiable — they are our credibility):**
> - **Arc is testnet-only — there is no mainnet.** We say so. Faucet USDC, no real funds
>   at risk *by design*. This is the correct posture for an Arc-stage project, not a hedge.
> - **AI can be wrong.** The goal is to *win more than you lose, not to never lose*. Whether
>   the engine generates *profitable* strategies is genuinely TBD — the proof is in the
>   pudding and we say that out loud. What we *can* prove today is provenance and rigor.
> - Real-funds custody + the regulatory architecture (off-chain redemptions, RIA posture)
>   are the **mainnet/business-plan roadmap**, not claimed as shipped.

## The headline message

> **"The on-chain asset-management stack now has billion-dollar rails and billion-dollar
> curators — and November 2025 proved the curation layer above them breaks on rigor.
> Archimedes is the proof-based answer: a research-grounded strategy-generation instrument,
> rigor-gated, with every decision traceable to a bleeding-edge academic paper and anchored on Arc."**

One line beneath it, the product in a sentence:

> *Describe what you want. Archimedes fuses your intent with live market data and a
> 10,000-paper quantitative-finance research library into novel strategies, gates them
> through deflated-Sharpe / overfitting-probability rigor, and lets you execute and monitor
> them on Arc — every reasoning step traceable down to the source paper.*

## The Agora narrative *(unchanged — this framing is earned, keep it)*

Agora (the hackathon) chose its name carefully. [From the official description](https://luma.com/7i50p2r9):

> *"In classical Athens, the agora was the heart of the city — where citizens traded
> grain and oil, money-changers leaned on their tables, oracles were consulted, and news
> was made by the speaking of it. The original information-processing machine. Markets
> are still doing the same job today; they are the social technology by which a
> civilization aggregates knowledge and decides what things are worth. AI agents are the
> new citizens."*

**Archimedes is named after another Athenian-era figure** — the Syracusan mathematician
and engineer (~287–212 BCE), the patron saint of *empirical reasoning from first
principles*. He calculated π by exhaustion, designed war machines, shouted "Eureka!" in
his bath, and said "Give me a lever long enough and a fulcrum on which to place it, and I
shall move the world."

**Our framing:**

> The agora is where citizens reasoned out loud. Archimedes reasoned with rigor —
> empirically, from first principles, with proofs. **Archimedes the product is an AI
> citizen who participates in the modern agora with that same rigor: every strategy is
> fused from bleeding-edge academic research, every reasoning step is auditable down to its source
> document, every trade settles on Arc. The lever is academic research; the fulcrum is
> autonomous AI; the world is your portfolio.**

Specific, defensible, earned — the architecture matches the original empiricist.

## The "wow moment" the demo must hit (the locked spine)

Run live on the testnet deploy. **Read-only until Deposit** — a judge can traverse the
whole story with no wallet, hitting the wall only at the one funded action. Mark anything
still converging as **🚧 in progress** so we are honest about live-vs-narrated.

1. **Land + the honest frame.** The hero states what it is and that it runs on the Arc
   *testnet* with faucet USDC — no real money, by design. (Sets the credibility tone
   immediately; judges are operators, they reward this.)
2. **Describe intent (the instrument).** Free-text brief + a risk appetite — e.g.
   *"trend-following with low drawdown, defensive in risk-off regimes."* This is the
   creative lever: unique inputs → unique strategies. **WOW #1 setup.**
3. **Generate (async job).** The request *queues a background job* (no blocking spinner) —
   fusion combines **three inputs**: the user brief, **live market/regime data**, and the
   **q-fin research library**. Show the job going queued → running → done.
4. **The WOW: research-grounded strategies appear.** Several novel fused strategies, each
   with a **rigor badge** (Deflated Sharpe / PBO verdict) and **source-paper citations** —
   click one and it deep-links into the **Corpus Explorer** to the exact arXiv papers it
   was fused from. *"It didn't pick from a menu — it composed these from the literature,
   and here are the papers."*
5. **The Corpus Explorer (WOW #2).** ~10,000 q-fin papers, clustered by topic, an
   interactive similarity graph and a high-level library breakdown — the research
   substrate, visible. *This is what underpins the generation;* the primary sources are
   exposed, so any claim is verifiable or falsifiable.
6. **Rigor gate.** The strategy's passport: paper provenance, methodology hash, the
   selection-bias scorecard — **the curation protocol that the rest of the industry is
   missing.** Honest deltas, no placeholder numbers.
7. **Execute (the one gated step).** Deposit *faucet* USDC into the non-custodial vault
   on Arc — a real testnet tx, real Arc explorer link.
8. **Monitor + autonomous rebalance (WOW #3).** The portfolio dashboard, and a visible
   *"the agent is autonomously managing this"* panel: last rebalance, the regime that
   triggered it, and its reasoning trace — **trace → strategy → source paper**, hashed in
   the deployed `ReasoningTraceRegistry`. Verifiable history, not promised performance.
9. **Explore.** The user's compounding strategy library — every generation and rigor
   verdict accrues and is provenance-anchored. The asset that compounds.

**The three differentiators:** (4) research-grounded *generation* (not selection),
(8) autonomous decisions *traceable to source* + on-chain, (5/6) the **proof-based
curation** the industry lacks. Without these it's a robo-advisor on a testnet. With them
it's Archimedes.

### Honest fallback (if a piece is mid-flight)

Still a strong story with only what's live: the **Corpus Explorer** over a real
multi-thousand-paper library; a **generated strategy with its rigor scorecard and
source-paper provenance**; a **real testnet deposit** into the deployed `Vault`; the
selection-bias gate as the intellectual differentiator. We never demo canned output as
real — `/health` shows live-vs-canned and we'd say so.

## Pitch deck — 9-slide structure

### Slide 1: Title + tagline
**Archimedes** — *Peer-reviewed AI portfolios, settled on Arc.*
Subtitle: *The original empiricist meets autonomous on-chain finance.*
Visual: logo + Agora/Archimedes motif; Canteen × Circle × Arc attribution.

### Slide 2: The problem (30s) — the curation wound, with numbers
On-chain asset management already has scale **and** a proven failure:
- **Morpho** — ~**$7.5B TVL**, ~$73M raised (incl. a $50M round). The *rails*.
- **Gauntlet** — the largest curator (~$1.5–1.9B TVL, $23.8M Series B, ~$1B val). The
  fee-incentivized *curation* layer.
- **November 2025 crisis** — a ~$93M failure cascaded ecosystem-wide (~$160M frozen).
  Morpho's isolation held; **the curation layer above it broke precisely on rigor.**

**Curation is run on trust, not proof. That is the open wound.** (Robo-advisors /
DeFi-yield / token-mediated AI agents are the legacy foils; the *real* gap is proof-based
curation.) Visual: the rails-held / curation-broke split.

### Slide 3: What we built (60s — the meat)
A **research-grounded strategy-generation instrument**:
- **3-input fusion** — your intent × live market data × a **~10,000-paper q-fin research
  library** (built on our own KnowledgeBase pipeline: SPECTER2 embeddings, topic
  clustering, similarity + knowledge graphs). Generation, not a static menu.
- **Proof-based curation** — every strategy passes Deflated Sharpe + Probability of
  Backtest Overfitting + walk-forward OOS + look-ahead audit. *This is the thing the
  Nov-2025 crisis showed the incumbents lack.*
- **Memory-first + provenance** — every generation and rigor verdict **accrues** into a
  compounding, content-hashed library; every reasoning trace resolves down to the origin
  paper and is anchored on Arc (`ReasoningTraceRegistry`). Verifiable/falsifiable after
  the trade resolves.
- **Live on Arc testnet** — 10 contracts (chain ID `5042002`), multi-wallet UI, fusion
  wired to `POST /api/strategies/generate`. Honest about what's converging.

### Slide 4: Live demo (90s)
Just "**DEMO**" + the testnet URL. Run the spine above.

**Right-column callouts to land during this slide (both shipped as of 2026-05-25):**

- **Portfolio Advisor banner on /generate** (PR #216) — after a strategy generates,
  scroll down to the advisor: Kelly-weighted allocations, stress-test matrix, variance
  decomposition, and a keccak-256 trace anchor. This is the "preview before you deposit"
  beat. Say: *"Before you commit faucet USDC, the advisor shows you the math — who gets
  how much, why, and what happens in a 2008 drawdown."*

- **Regime-aware allocation** (PR #217, Ang & Bekaert 2002) — the advisor's risk-aversion
  coefficient updates with the live regime: 1× in risk-on, 2× in risk-off, 4× in crisis.
  If the regime panel shows anything other than risk-on, the allocation table will be
  visibly more conservative. Say: *"The optimizer read the regime and adjusted — not
  because we coded a rule, but because Ang & Bekaert proved it's the right move."*

### Slide 5: Competitive landscape (30s) — tiered, from `competitor-landscape.md`
```
TIER 0 — live mainnet infra (vision/TAM, NOT today's competitor)
  Morpho $7.5B rails · Gauntlet largest curator · Upshift trusts curators
  Accountable proves capital (partner-shaped)         → all trust-based curation
TIER 1 — our real peer set (Arc, pre-product)
  Pantheon-Trades · ReasoningReceipt · CronusCapital  → none claim research rigor
ARCHIMEDES → proof-based curation: research-grounded + DSR/PBO-gated + on-chain provenance
```
Line: *"Circle gave agents wallets. The Nov-2025 crisis showed curation is the unsolved
layer. We're the proof layer."*

### Slide 6: Why we'll score well
| Criterion | Weight | How we score |
|---|---|---|
| Agentic Sophistication | 30% | 3-input fusion, async generation jobs, autonomous rebalance with traces resolving to source papers, on-chain anchoring. Multi-turn tool-use portfolio agent (up to 12 iterations: `get_asset_stats` → `get_correlation` → `stress_test_portfolio` → finalize). Regime-aware γ scaling: optimizer adapts risk aversion live (1×/2×/4× per Ang & Bekaert 2002) — agent behavior changes with market state, not just user input. |
| Traction | 30% | arc-canteen telemetry on every ship + user conversation; live testnet; the Corpus Explorer is a tangible, shareable artifact. |
| Circle Tool Usage | 20% | Circle Wallets oracle signer, USDC settlement, faucet/testnet flow, 10 Arc contracts; Circle Agent Stack alignment tracked. |
| Innovation | 20% | Proof-based curation (DSR+PBO) vs the Nov-2025 industry failure; memory-first compounding provenance substrate; ~10k-paper research engine. Portfolio Advisor surfaced on /generate as preview-before-deposit: Kelly weights, stress matrix, variance decomposition, keccak trace anchor — visible before a single USDC is committed. |
See [`judging-rubric-assessment.md`](judging-rubric-assessment.md) for the running self-score.

### Slide 7: Why now (30s)
- **[Circle Agent Stack, May 2026](https://decrypt.co/367490/circle-ai-agents-usdc-stablecoin-powers-222m-arc-token-sale)** — the agent economy is funded ($222M Arc presale).
- **The Nov-2025 curation crisis** — the incumbents' failure mode is *fresh and public*;
  the proof-based alternative is timely.
- Demand (Olas ~700K tx/mo) and supply (Virtuals 18K+ agents) of on-chain agents are real.
**Nobody is shipping proof-based, research-grounded curation. That's the gap.**

### Slide 8: What we ship next (30s)
Honest roadmap (explicitly *not* claimed as done): Arc **mainnet** + real-funds custody;
the regulatory architecture (off-chain redemptions, RIA posture, exploit alerting);
**multi-user + the social network of shared/forked strategies**; productized corpus
ingestion; EURC/multi-currency. Visual: now / 30 / 90 / 180.

### Slide 9: Team + ask (30s)
Five people + one-line credentials:
- **Dan Browne** — PhD biochemistry, Sr Scientist @ LanzaTech. (Research rigor + the KnowledgeBase pipeline.)
- **Marten Windler** — Systems Engineering, U. Bremen. (Off-chain ↔ on-chain.)
- **Daniel Reis dos Santos** — backend/distributed systems. (Frontend ownership.)
- **Chuan Bai** — CTO @ Gyld Finance; built CoinShares' next-gen trading platform. (Architecture + on-chain.)
- **Önder Akkaya** — ASA Statistical Insight World Champion; trainee actuary. (Portfolio math + rigor gate. Ships: regime-aware Kelly/MVO optimizer with Ang & Bekaert 2002 adaptive γ; Portfolio Advisor banner on /generate with stress tests + on-chain trace anchor.)

Ask: *"Not funding. (a) Feedback on the strategy passport + provenance trace as a
candidate open standard; (b) intros to quant researchers to contribute to the corpus;
(c) RWA-issuer partnerships for the mainnet roadmap."*

## Q&A preparation — anticipated judge questions *(aligned to the real vision)*

**Q: Is this live? Can I use it with real money?**
A: **No — and we'll be the first to say it.** Arc is testnet-only; there is no mainnet
yet. You use faucet USDC; no real funds are at risk *by design*. That's the correct
posture for an Arc-stage project. Mainnet + the custody/regulatory architecture is the
roadmap, and we present it as roadmap, not as shipped.

**Q: Does it actually generate *profitable* strategies?**
A: Honestly — that's TBD; the proof is in the pudding and we won't claim otherwise. What
we *prove today* is the things that are checkable: research provenance (every strategy
traces to bleeding-edge academic papers), rigor (DSR/PBO/OOS/look-ahead), and on-chain
verifiability. The goal is to win more than you lose, not to never lose — and to make
every decision auditable so performance accrues as *verifiable history*.

**Q: Won't the AI hallucinate strategies?**
A: Three defenses. (1) Generation is *grounded* — fused from a ~10k-paper corpus with
source citations you can open. (2) The **selection-bias gate** (Deflated Sharpe + PBO +
walk-forward + look-ahead) is the curation protocol — the exact layer the Nov-2025
industry crisis showed the incumbents lacked. (3) Every reasoning trace resolves to its
source document and is hashed on Arc, so a bad call is *attributable*, not deniable. The
4 example strategies in the repo are explicitly illustrative — real strategies come from
the generator.

**Q: How is this different from Gauntlet / Morpho / Upshift?**
A: They're live mainnet infra at billion-dollar scale — a different league, and we say
so; they're our *vision/TAM*, not today's competitor. The point is they all run
*trust-based* curation, and Nov-2025 showed that breaks on rigor. Archimedes is the
*proof-based* alternative: research-grounded + rigor-gated + provenance-anchored.
Accountable is partner-shaped (it proves capital is real; we prove the *method* is real).

**Q: What's the moat?**
A: A **compounding, provenance-anchored research substrate**. The durable asset isn't the
model or the UI — it's the accruing, citation-typed library where every generation and
rigor verdict is recorded and on-chain-anchored. That compounds; a static curator doesn't.

**Q: How does autonomous rebalancing decide?**
A: Drift / regime-change / strategy-decay / calendar triggers, each weighing expected
benefit vs. cost; every rebalance emits a reasoning trace that resolves to source papers
and is anchored on Arc. Live triggers are converging; the trace→source provenance is the
differentiator we lead with.

**Q: Regulatory profile?**
A: On testnet with no real funds, there is no custody/RIA trigger — that's *why* the
regulatory architecture (off-chain redemptions, preset-strategy / RIA posture, exploit
alerting) is presented as the **mainnet business-plan**, designed-for not claimed-as-shipped.

**Q: Why USDC, no token?** A: No native token ([anti-features](anti-features.md));
revenue = take-rate on settlement + USYC yield share. Optimizing for users with a job to
do, not tokenholders.

**Q: Market size?** A: TradFi robo ~$1T+; on-chain curation infra is already
billion-dollar TVL (Morpho ~$7.5B). We don't need a precise forecast — the wound
(trust-based curation) is large, funded, and freshly exposed.

## Logistics for demo day
- Dedicated testnet wallet, pre-funded with **faucet** USDC. No real-value assets.
- Backup video recording as insurance — don't lean on it.
- Test the live demo at the actual judging time-of-day (network conditions).
- Rehearse Q&A out loud with the team, twice, before submission.

## Owner: who drives this?
- **Deck owner:** Dan (owns slide content + the rigor/research framing; Marten reviews flow).
- **Demo runner:** Daniel (owns the frontend; plays the user).
- **Q&A primaries:** Dan (vision, research rigor, the curation thesis) · Chuan (on-chain, Arc, Circle) · Önder (rigor math, Kelly) · Daniel (live system/UX) · Marten (infra, off↔on-chain).
Lock roles before submission.

## Open questions
- Include a live Corpus-Explorer segment in the 90s demo, or save graph/KG for a slide?
  **Recommendation:** show it briefly live — it is a genuine wow and proves the substrate.
- Name specific RWA tokens or use the minted synthetics? **Recommendation:** demo the
  actually-minted synthetics; real RWA bridging via CCTP is the mainnet roadmap line.
- One-page judge handout? **Recommendation:** yes if Canteen allows — a one-pager of this deck.
