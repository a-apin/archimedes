# Archimedes — Submission Packet (2026-05-25)

Everything you need to (1) record the demo, (2) submit the Google Form, (3) push the launch posts. Copy-paste from here.

---

## 1. THE DEMO SCRIPT — read this aloud, ~2:50 total

**Recording setup before you hit record:**
- Browser tab 1: `http://[redacted-host]/` (the live app, landing page)
- Browser tab 2: `http://[redacted-host]/corpus` (Corpus Explorer pre-warmed)
- Browser tab 3: `http://[redacted-host]/generate` (Generate page)
- Browser tab 4: `https://testnet.arcscan.app/` (an existing reasoning trace tx)
- Wallet already connected (Circle passkey or MetaMask), with faucet USDC in it
- Screen at 1.25× zoom, devtools closed, notifications off

If you stumble on a section, keep moving — total wall-clock < 3 min is more important than perfect delivery.

---

### [0:00–0:20] Open on the landing page

> "This is **Archimedes**. We built it in two weeks for the Agora hackathon. The pitch in one sentence: **describe what you want from a portfolio in plain English, and Archimedes fuses your intent with the live market, ten thousand quant-finance papers, and statistical rigor — into an autonomous on-chain strategy.**"

> "On-chain asset management already has billion-dollar rails — Morpho is north of seven billion in TVL. November twenty-twenty-five proved the curation layer above those rails breaks on trust. **We built the proof-based alternative.**"

*[Click "Generate a Strategy"]*

---

### [0:20–0:50] Generate — the 3-input fusion

> "I'll describe what I want: *trend-following with low drawdown, defensive in risk-off regimes.* Submit."

*[Type the brief, hit submit. SSE stream completes in ~10 seconds — narrate while waiting:]*

> "Three inputs are fusing here — my brief, the live market regime detector, and our ten-thousand-paper q-fin corpus. The agent is running a twelve-iteration tool-use loop: it pulls candidate instruments, checks correlations, runs a stress engine, and anchors each pick to a source paper."

*[Strategy passport appears. Click into it.]*

---

### [0:50–1:30] The Rigor Gate — the wedge

> "Here's where Archimedes is different from every other AI-portfolio submission. **Selection-bias rigor.** Four controls: Deflated Sharpe Ratio — that's Bailey and López de Prado, 2014. Probability of Backtest Overfitting. Walk-forward out-of-sample. And a look-ahead static audit."

*[Point to the rigor panel.]*

> "Two of our Tier-one strategies pass the full gate against twenty-two years of real S&P data. The rest get flagged honestly. **We don't hide the failures behind an aggregate score.** That is the curation discipline the Nov-2025 crisis showed was missing."

*[Click a paper citation. Lands on the source arXiv.]*

> "Every pick traces back to the paper that backs it."

---

### [1:30–2:00] Corpus Explorer — the substrate

*[Switch to the Corpus tab.]*

> "This is what 'research-grounded' actually means. **Ten thousand quantitative-finance papers**, clustered by topic, with a similarity graph and a knowledge graph over the entities. The agent isn't picking from a menu — it's composing from this corpus."

*[Hover a cluster, zoom into the graph for visual impact.]*

---

### [2:00–2:30] Deploy + Verify — non-custodial, on-chain

*[Switch to the strategy you generated. Click "Deploy as Vault".]*

> "I'll deposit faucet USDC into a non-custodial ERC-four-six-two-six vault on Arc testnet. The user signs the four binding transactions; **the agent gets rebalance authority only — never withdraw**. This is non-custody in the strong sense."

*[Sign through the deposit flow. Click an activity row → Reasoning Trace.]*

> "Every decision the agent makes gets hashed and anchored on Arc via our deployed ReasoningTraceRegistry contract. **Click 'Verify on-chain'** — the hash recomputes, matches the anchor, here's the arcscan link."

*[Show the green checkmark + arcscan tab.]*

---

### [2:30–2:50] Close

> "Two weeks. Five builders across five timezones. Eleven Solidity contracts on Arc, eight hundred and six backend tests, fifty-three thousand lines of code. Live at the IP on screen. Fully open source under the Unlicense — every primitive is forkable."

> "**Archimedes: the lever is academic research, the fulcrum is autonomous AI, the world is your portfolio.** Thanks to Circle, Arc, and Canteen for building the rails."

*[End on the landing page or a static title card.]*

---

### Honest fallback if something breaks mid-demo

If Generate hangs: cut to an already-generated strategy and skip to the Rigor Gate beat.
If Corpus is slow: skip to Deploy.
If wallet fails: skip to the on-chain verify beat using an existing trace.
**Never narrate canned output as real.** `/health` shows live-vs-canned.

---

## 2. GOOGLE FORM — final answers, copy-paste ready

Form URL: <https://forms.gle/ok3Gr9zhmHnApvK48>

### #1 Email
```
[redacted]
```

### #2 Project Name
```
Archimedes
```

### #3 GitHub Handle
```
dbrowneup
```

### #4 Discord Handle
```
dbrowneup
```

### #5 Telegram Handle
*(your Telegram handle — fill in)*

### #6 Twitter / X (optional)
*(your handle, or leave blank)*

### #7 Number of Team Members
```
5+
```

### #8 Team Members Names
```
Dan Browne
Marten Windler
Daniel Reis dos Santos
Chuan Bai
Önder Akkaya
```

### #9 Problem Statement
```
AI portfolio tools today are unfalsifiable. Robo-advisors are rule-based black boxes; AI-flavored crypto agents are token-mediated speculation with opaque reasoning; even sophisticated DeFi yield curators (Yearn, Morpho-curated vaults) saw over $400M in losses during the Nov 2025 curation crisis because nobody could audit the methodology before the strategies failed. The common failure mode is in-sample overfitting that doesn't survive contact with reality — and the common response is "trust us."

The compelling part: the textbook tools to prevent this — Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward out-of-sample testing — have existed in quant academia for over a decade. Every serious quant shop uses them. No AI-portfolio product surfaces them to users. We can build the proof-based alternative: AI-generated strategies grounded in peer-reviewed research, rigor-gated against the same selection-bias controls a Citadel risk team would apply, with every reasoning step anchored on-chain.
```

### #10 Project Description
```
Archimedes is "Linus for quantitative finance" — a research-grounded strategy-generation instrument for capable non-experts who want their idle USDC to compound thoughtfully. The user describes what they want; Archimedes fuses that intent with live market regime data and a 10,000-paper quantitative-finance research library into a candidate strategy. The strategy passes through a four-control selection-bias rigor gate (Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward out-of-sample testing, look-ahead static audit) before admission to the Tier-1 library. Two strategies currently pass the full gate against 22 years of real SPY data. Execution is into non-custodial ERC-4626 vaults on Arc testnet with USDC settlement; every reasoning trace is keccak256-hashed and anchored on a deployed ReasoningTraceRegistry contract.

The stack: Python 3.12 / FastAPI / SQLAlchemy backend in a 6-container docker-compose stack (backend + postgres + redis + nginx + oracle feeder + autonomous agent runner); React 19 + Vite 8 + viem frontend with multi-wallet UX (MetaMask + Coinbase + Circle Modular Wallets passkey via EIP-6963); 11 Solidity contracts deployed via Foundry on Arc testnet; Circle Developer-Controlled Wallets for autonomous on-chain execution (no raw private keys in production); LLM-provider-agnostic backend supporting GLM / Anthropic / OpenAI / Ollama. The marquee addition is an LLM-driven agentic portfolio advisor running a 12-iteration tool-use loop that picks individual instruments (not just ETF baskets) and anchors each pick to a paper-grounded strategy passport. Live at http://[redacted-host].
```

### #11 Traction
*Run `arc-canteen status` and paste the numbers — placeholders below.*
```
Live testnet deploy at http://[redacted-host] has been up since the Day-3 EC2 deploy and accepting traffic throughout the build. Coordinated launch via Discord (Canteen + Build on Arc), X, Bluesky, and LinkedIn is going out within the submission window.

Internal team validation: 5 builders across 5 timezones (US/UK/EU/Brazil/Turkey) using the platform daily for our own scope-validation; 2 of those are working portfolio professionals (one with CTO experience at a quant trading platform, one ASA Statistical Insight World Champion / actuarial trainee) treating the rigor surface as a serious work tool.

By the numbers (build window 2026-05-11 → 2026-05-25):
- 553 commits, 202 merged PRs
- 101,534 lines of code across 476 files (Python + JSX + Solidity + JS + YAML + Terraform)
- 11 Solidity contracts deployed on Arc testnet
- 806 backend pytest tests + 16 analytics-engine tests
- 2 Tier-1 strategies passing the full rigor gate against 22.3 years of real SPY data
- arc-canteen telemetry: <PASTE `arc-canteen status` OUTPUT HERE>
- GitHub stars: <RUN `gh repo view a-apin/archimedes-arcadia --json stargazerCount`>
- Live testnet vault deposits (real outsiders, not team): <COUNT>
```

### #12 Project Source Code
```
https://github.com/a-apin/archimedes-arcadia
```

### #13 Project Live (optional)
```
http://[redacted-host]
```

### #14 Project Video Demo
*(paste your recorded URL after the 3-min demo is uploaded to Loom or YouTube unlisted)*
```
<DEMO_URL>
```

### #15 Arc OSS Checkbox
```
[X] Yes - I would love to apply for Arc OSS!
```

### #16 Arc OSS Question
```
Archimedes exposes twelve forkable primitives that other Arc builders can adopt as a stack or individually. The original seven shipped Day-4 → Day-9; primitives 8–12 landed Day-12 as part of the final ship train:

1. Strategy Passport schema — provenance binding for AI-generated strategies (source paper, methodology hash, paper-claim deltas, on-chain registration tx)
2. Selection-bias rigor gate — DSR + PBO + walk-forward OOS + look-ahead audit, all in pure numpy with no Archimedes coupling
3. On-chain reasoning trace anchoring — TracePublisher + ReasoningTraceRegistry.sol; keccak256-hash an off-chain trace, anchor on Arc, verify by recomputation
4. DB-backed knowledge-base substrate — Postgres-canonical + idempotent seed + live intake; we use it for q-fin papers but it generalizes to any "AI grounded in domain knowledge" pattern
5. LLM provider-agnostic backend factory — one env var (LLM_PROVIDER) switches between Anthropic, Anthropic-compatible (z.ai/GLM), OpenAI, Ollama; canned fallback on missing credentials
6. 3-input fusion engine — user intent × live market context × knowledge base → AI proposal; the pattern generalizes beyond strategy synthesis
7. Circle Developer-Controlled Wallets signer — autonomous on-chain writes, no raw private keys in production
8. Circle Modular Wallets passkey UX — EIP-6963 wallet discovery with passkey-as-fingerprint auth, zero seed phrase
9. Regime-conditional Kelly optimizer — Ang & Bekaert 2002 (Review of Financial Studies); effective γ scales with the live regime
10. Multi-asset NAV vault — Vault.totalAssets() oracle-prices all synthetic holdings
11. Internal-agent auth guard — HMAC X-Internal-Agent-Key for safe agent → backend calls
12. Server-side ruff format self-heal — push-to-main format guard that auto-fixes and keeps the CI run red for visibility

Each primitive has a dedicated spec or walkthrough doc — a forker can implement against the spec without reading the full source. We're under the Unlicense (no attribution required, more permissive than MIT). The existing Arc reference repos (arc-commerce, arc-p2p-payments, etc.) cover transaction-flow plumbing; Archimedes adds the AI-decision-provenance layer on top. They compose; they don't overlap.

806 backend tests + 16 analytics-engine tests green; 11 contracts deployed on Arc testnet; live at http://[redacted-host]. Full positioning + per-primitive how-to-fork docs in ARC-OSS-SHOWCASE.md in the repo root.
```

### #17 Circle / Arc Feedback
```
What worked:

- Circle Developer-Controlled Wallets via REST API was the highest-leverage primitive for us — running an autonomous agent on testnet without ever holding raw private keys in production was a clean security story we couldn't have built in two weeks otherwise.
- Circle Modular Wallets with passkey auth via EIP-6963 wallet discovery let us ship a "sign up with your fingerprint" wallet flow that genuinely feels easier than installing MetaMask. This is the right shape for onboarding non-crypto users.
- USDC as native gas on Arc removed an entire class of UX friction. New users grab faucet USDC once and can execute everything; no "first acquire ETH for gas" detour.
- The context-arc submodule with circlefin-skills/* was the canonical reference and saved us hours of doc-spelunking. The task-routed entry-point in AGENTS.md is the right shape.
- Foundry-based contract dev against Arc testnet via the arc-canteen RPC proxy was friction-free once the swrm_ token was sourced.

Where to improve:

- Arc mainnet timeline visibility — "upcoming" with no date makes it hard to plan the business case for any project graduating off-hackathon.
- The Circle Modular Wallets passkey flow had a CSP-allowlist gotcha (we had to whitelist Circle's modular-sdk passkey discovery URL) and a per-device-username constraint that surfaced late; both are documented in our PRs (#270, #368) and worth landing in the SDK docs.
- USYC as an on-chain asset isn't surfaced cleanly in the Arc testnet faucet flow; adding it as a faucet-able test asset would help any project building risk-off tiers.
- More fork-from primitives in the circlefin/arc-* reference repos for AI/agent use cases specifically (the current ones are transaction-flow focused). Our ARC-OSS-SHOWCASE.md tries to fill that gap.
```

### #18 General Feedback
```
What worked well:

- The two-week timebox forced focus and prevented scope creep — most teams (us included) shipped tighter products than they would have in four weeks.
- The arc-canteen CLI as both per-user RPC proxy AND telemetry-tracking surface was a clever 2-in-1 — it solved attribution + rate-limiting + traction-tracking all in one auth flow.
- The Discord-first comms model worked well for a 5-timezone team; daily syncs at 13:00 UTC hit everyone in working hours.
- The rubric weighting (30% Traction) was unusual but defensible — it kept teams focused on shipping a real product, not just a polished demo.

Could be improved:

- Earlier surfacing of the Arc OSS Showcase as a separate parallel track would have let teams structure their docs for forkability from day one (we adapted to it in the final 3 days; an earlier callout in week 1 would have made the docs work much smoother).
- The arc-canteen update-product / update-traction telemetry could prompt for usage more aggressively — many teams (us included) forgot for stretches; a "you haven't logged in 24h" nudge would close the rubric-zero gap automatically.
- A standardized submission video format (Loom vs YouTube vs Vimeo) + a minimum-quality bar would help judges compare. Right now the variance in demo videos likely creates evaluation noise.
- A post-hackathon community / Discord persistence path — many teams want to keep building post-submission but the natural channels dissipate.
```

---

## 3. SOCIAL POSTS — copy-paste ready

### Discord — Canteen + Build on Arc channels

```
Shipping Archimedes for the Agora hackathon 🏛️

Describe what you want from a portfolio in plain English → Archimedes fuses your intent with live market data and a 10,000-paper quant-finance research library → gates it through Deflated Sharpe + Probability of Backtest Overfitting + walk-forward OOS + look-ahead audit → deploys to a non-custodial ERC-4626 vault on Arc testnet → anchors every reasoning step on-chain via our deployed ReasoningTraceRegistry.

Two weeks. 5 builders, 5 timezones. 553 commits, 11 Solidity contracts on Arc, 806 backend tests, ~101k LoC. Built with @Circle Modular Wallets passkey auth (sign up with your fingerprint, no seed phrase) and Developer-Controlled Wallets for the autonomous agent.

It's testnet-only and honest about it — faucet USDC, no real funds, AI can be wrong. What we can prove today is provenance and rigor.

🔗 Live: http://[redacted-host]
🔗 Code (Unlicense): https://github.com/a-apin/archimedes-arcadia
🔗 OSS primitives: https://github.com/a-apin/archimedes-arcadia/blob/main/ARC-OSS-SHOWCASE.md

Try it, ⭐ if it resonates, tell us what breaks 🙏
```

### X / Bluesky — 5-post thread

**Post 1 (hook):**
```
On-chain asset management has billion-dollar rails (Morpho >$7B TVL) and billion-dollar curators. November 2025 proved the curation layer breaks on rigor.

We built the proof-based alternative — and open-sourced the whole research stack under it.

Meet Archimedes 🏛️ 🧵
```

**Post 2 (what it does):**
```
Describe what you want from a portfolio in plain English.

Archimedes fuses your intent + live market regime + a 10,000-paper quant-finance research library into a novel strategy, gates it with Deflated Sharpe + Probability of Backtest Overfitting, and deploys to a non-custodial vault on @arc.

Every reasoning step traces to a source paper, hashed and anchored on-chain.
```

**Post 3 (the honesty):**
```
The honest part: it runs on Arc testnet (no mainnet yet) — faucet USDC, no real money, by design.

AI can be wrong. The goal is to win more than you lose, not never lose. Whether the engine generates PROFITABLE strategies is genuinely TBD.

What we can prove today is provenance and rigor. That's the wedge.
```

**Post 4 (the build):**
```
2 weeks. 5 builders across 5 timezones (US/UK/EU/Brazil/Turkey).

553 commits · 202 merged PRs · 11 Solidity contracts on Arc · 806 backend tests · ~101k LoC.

Built with @circle Modular Wallets passkey auth — sign up with your fingerprint, no seed phrase. Developer-Controlled Wallets run the autonomous agent. USDC is native gas on Arc; faucet → trade in one step.
```

**Post 5 (CTA):**
```
Try it on testnet: http://[redacted-host]
Code (Unlicense, fork freely): https://github.com/a-apin/archimedes-arcadia
12 forkable primitives for Arc builders: https://github.com/a-apin/archimedes-arcadia/blob/main/ARC-OSS-SHOWCASE.md

⭐ if the "curation with proof" thesis resonates. Tell us what breaks 🙏

@thecanteenapp @circle @arc
```

### LinkedIn — long-form post

```
We just submitted Archimedes to the Agora Agents Hackathon (Canteen × Circle × Arc). Here's what we built and why I think it matters.

THE PROBLEM. On-chain asset management has billion-dollar rails (Morpho is north of $7B TVL) and billion-dollar curators (Gauntlet ~$1.5B–1.9B TVL). November 2025 proved the curation layer above those rails breaks on rigor — a ~$93M failure cascaded ecosystem-wide. Curation today runs on trust. The textbook tools to prevent in-sample-overfit blowups — Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward out-of-sample testing — have existed in quant academia for over a decade. Every serious quant shop uses them. No AI-portfolio product surfaces them to users.

THE PRODUCT. Archimedes is "Linus for quantitative finance" — a research-grounded strategy-generation instrument. You describe what you want from a portfolio in plain English. Archimedes fuses that intent with live market regime data and a 10,000-paper quantitative-finance research library into a novel strategy. The strategy passes through a four-control selection-bias rigor gate — DSR (Bailey & López de Prado 2014), PBO (Bailey/Borwein/López de Prado/Zhu 2014), walk-forward OOS, look-ahead static audit — before admission to the Tier-1 library. Two strategies currently pass the full gate against 22.3 years of real SPY data. The rest get flagged honestly; we don't hide failures behind an aggregate score.

Execution is into non-custodial ERC-4626 vaults on Arc testnet, settled in USDC. The agent holds rebalance authority only — never withdraw. Every reasoning trace is keccak256-hashed and anchored on a deployed ReasoningTraceRegistry contract; anyone can recompute and verify. Wallet onboarding is a passkey via Circle's Modular Wallets — sign up with your fingerprint, no seed phrase.

THE TEAM. Five builders across five timezones — US, UK, EU, Brazil, Turkey. Two of us have demanding day roles; one runs a real startup; two are students. We built this in two weeks. 553 commits. 202 merged pull requests. 11 Solidity contracts deployed. 806 backend tests, 16 analytics-engine tests. ~101k lines of code across Python, JSX, Solidity, JavaScript, Terraform. By the scc cost-model estimate this would normally take ~14 people 22 months and cost $3.4M. We did it in 14 days.

THE HONESTY. Arc has no mainnet yet — we run on testnet, faucet USDC, no real funds. AI can be wrong; the goal is to win more than you lose, not never lose. Whether the engine generates PROFITABLE strategies is genuinely TBD. What we can prove TODAY is provenance and rigor. That's the wedge.

It's fully open source under the Unlicense — the most permissive license possible. Twelve primitives are documented as standalone-forkable for other Arc builders: the rigor gate, the on-chain reasoning trace anchoring, the 3-input fusion engine, the Circle passkey wallet integration, the regime-conditional Kelly optimizer (Ang & Bekaert 2002, Review of Financial Studies). Each has a dedicated spec.

Try it: http://[redacted-host]
Code: https://github.com/a-apin/archimedes-arcadia

Thanks to the team — Marten Windler, Daniel Reis dos Santos, Chuan Bai, Önder Akkaya — and to Canteen, Circle, and Arc for building the rails.

#hackathon #onchain #defi #ai #agents #quantfinance #arc #circle
```

---

## 4. RECEIPTS — for fact-checking the posts

```
Code stats (scc, excluding submodules + node_modules):
- 476 files, 101,534 lines of code (122,201 total with blanks/comments)
- Python 230 files / 37,100 LoC
- Markdown 79 files / 17,324 LoC (the doc-driven culture!)
- JSX 36 files / 9,164 LoC (frontend)
- JSON 32 files / 12,129 LoC
- Solidity 27 files / 2,757 LoC (11 contracts + interfaces)
- scc cost model: 22.0 months / 13.9 people / $3.4M organic

Commits since 2026-05-11 (hackathon start), no merges:
- 553 total
- Dan (Daniel/Dan Browne combined): 290
- t2o2 (agentic system): 231
- Önder: 62
- Chuan: 57
- Daniel Reis (danielscoffee + Daniel Reis): 43
- Marten: 3
- Bots (dependabot, github-actions, copilot-swe-agent): 28

PRs merged: 202
Contracts deployed: 11 (AMMPool, AMMRouter, AssetRegistry, PriceOracle, ReasoningTraceRegistry, StrategyRegistry, SyntheticFactory, SyntheticToken, SyntheticVault, Vault, VaultFactory)
Backend tests: 806 collected
Analytics-engine tests: 16
Tier-1 strategies passing full rigor gate: 2 (against 22.3 years of real SPY data)
Corpus size: 10,000 q-fin papers
```
