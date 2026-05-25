# Agora Agents Submission Form — Team Review Draft

> **Status:** Day-14 refresh (2026-05-25, submission day) — content updated for 12 forkable primitives, 806 backend tests, `https://archimedes-arc.app/` URL. v1 submitted earlier in the window per the `archive/sunday-night-handoff-2026-05-24.md` note ("video recorded, deck near-final, form submitted once"). This doc holds the team-agreed answer text for the resubmit pass. Some fields remain placeholders pending team input (marked with `<<…>>`).
> **Important:** the team should review the substantive long-form answers (#9, #10, #11, #14, #16, #17, #18) before submission. The factual short answers (#1–#7, #12–#13, #15) are mechanical.
> **Submission cadence:** the form note says *"You can submit many times! We recommend you submit early and often!"* — so a v1 submission with placeholder team handles + a follow-up final v2 is fine.

---

## 1. Email* — Your email

`<<TEAM_EMAIL>>` — the email Canteen should use to contact us. Dan's email is fine here, or a shared/group email if we have one.

## 2. Project Name*

```
Archimedes
```

## 3. Github Handle*

The submitter's GitHub handle. Use whoever is actually submitting (likely Dan = `dbrowneup`).

```
<<SUBMITTER_GITHUB_HANDLE>>
```

## 4. Discord Handle*

```
<<SUBMITTER_DISCORD_HANDLE>>
```

## 5. Telegram Handle*

```
<<SUBMITTER_TELEGRAM_HANDLE>>
```

## 6. Twitter / X Profile (optional)

```
<<SUBMITTER_TWITTER_URL_OR_LEAVE_BLANK>>
```

## 7. Number of Team Members*

`5+` — five active team members.

## 8. Team Members Names*

```
Dan Browne
Marten Windler
Daniel Reis dos Santos
Chuan Bai
Önder Akkaya
```

## 9. Problem Statement*

> *What problem is your project solving? What is compelling about this problem?*

**Suggested answer (~150 words):**

```
AI portfolio tools today are unfalsifiable. Robo-advisors are rule-based black boxes; AI-flavored crypto agents are token-mediated speculation with opaque reasoning; even sophisticated DeFi yield curators (Yearn, Morpho-curated vaults) saw $400M+ in losses during the Nov 2025 curation crisis because nobody could audit the methodology before the strategies failed. The common failure mode is in-sample overfitting that doesn't survive contact with reality — and the common response is "trust us."

The compelling part: the textbook tools to prevent this — Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward out-of-sample testing — have existed in quant academia for over a decade. Every serious quant shop uses them. No AI-portfolio product surfaces them to users. We can build the proof-based alternative: AI-generated strategies grounded in peer-reviewed research, rigor-gated against the same selection-bias controls a Citadel risk team would apply, with every reasoning step anchored on-chain.
```

## 10. Project Description*

> *Describe what your project does, how it works, and what tech you used.*

**Suggested answer (~250 words):**

```
Archimedes is "Linus for quantitative finance" — a research-grounded strategy-generation instrument for capable non-experts who want their idle USDC to compound thoughtfully. The user describes what they want; Archimedes fuses that intent with live market regime data and a 10,000-paper quantitative-finance research library into a candidate strategy. The strategy passes through a four-control selection-bias rigor gate (Deflated Sharpe Ratio, Probability of Backtest Overfitting, walk-forward out-of-sample testing, look-ahead static audit) before admission to the Tier-1 library. 2 strategies currently pass the full gate against 22 years of real SPY data. Execution is into non-custodial ERC-4626 vaults on Arc testnet with USDC settlement; every reasoning trace is keccak256-hashed and anchored on a deployed ReasoningTraceRegistry contract.

The stack: Python 3.12 / FastAPI / SQLAlchemy backend in a 6-container docker-compose stack (backend + postgres + redis + nginx + oracle feeder + autonomous agent runner); React 19 + Vite 8 + viem frontend; 10 Solidity contracts deployed via Foundry on Arc testnet; Circle Developer-Controlled Wallets for autonomous on-chain execution (no raw private keys in production); LLM-provider-agnostic backend supporting GLM / Anthropic / OpenAI / Ollama. The newest addition is an LLM-driven agentic portfolio advisor running a 12-iteration tool-use loop that picks individual instruments (not just ETF baskets) and anchors each pick to a paper-grounded strategy passport. Live at https://archimedes-arc.app/.
```

## 11. Traction*

> *How many real people have tried the product? How much validation were you able to get from end users? Also include things like RTs / follows / stars here =)*

**Draft answer — TEAM TO FILL ACTUAL NUMBERS:**

```
Live testnet deploy at https://archimedes-arc.app/ has been up since the Day-3+ EC2 deploy and accepting traffic throughout the build. We're in active outreach phase as of Day-10 — coordinated launch via Discord (Canteen + Arc Builder), Twitter, and r/algotrading is happening in the final 3-day window before submission.

Internal team validation: 5 builders across 5 timezones (US/UK/EU/Brazil/Turkey) actively using the platform daily for our own scope-validation; 2 of those are working portfolio professionals (one with CTO experience at a quant trading platform, one ASA-credentialed actuarial trainee) treating the rigor surface as a serious work tool.

External (the numbers that matter for this question):
- arc-canteen telemetry: <<RUN `arc-canteen status` AND PASTE THE PRODUCT + TRACTION COUNTS HERE>>
- GitHub stars: <<RUN `gh repo view a-apin/archimedes-arcadia --json stargazerCount`>>
- Discord engagement: <<CONVERSATION COUNT FROM #canteen-archimedes>>
- Live testnet vault deposits (real outsiders, not team): <<COUNT>>
- Twitter / X impressions: <<NUMBER FROM LAUNCH TWEET>>

We are actively driving this number up through submission day.
```

## 12. Project Source Code*

```
https://github.com/a-apin/archimedes-arcadia
```

(Public, Unlicense.)

## 13. Project Live (optional)

```
https://archimedes-arc.app/
```

## 14. Project Video Demo*

> *Loom / YouTube / Vimeo link — max 3 minutes recommended. Focus on the core features.*

```
<<RECORDED_DEMO_URL>>
```

**Suggested demo structure (~2:50 total):**

1. **0:00–0:20** — Title + 1-sentence pitch ("Research-grounded strategies, rigor-gated, anchored on Arc")
2. **0:20–1:00** — `/generate` page: describe intent → agentic LLM advisor picks instruments → result card with paper citations + rigor verdict
3. **1:00–1:40** — Strategy passport: open Moreira-Muir Tier-1; show DSR p-value, PBO score, OOS Sharpe, real 22-year SPY equity curve, paper-claim delta
4. **1:40–2:10** — Corpus Explorer: 10,000 papers, the graph + KG endpoints, "this is what 'research-grounded' actually means"
5. **2:10–2:35** — Reasoning trace anchor: click "Verify trace" → hash recomputes → green checkmark → "View on Arc Explorer" link to the on-chain anchor
6. **2:35–2:50** — Close: "Live on Arc testnet today, fully open source under the Unlicense"

Demo recording owner: `<<DECK_OWNER>>`.

## 15. (Arc OSS) Checkbox

```
[X] Yes - I would love to apply for Arc OSS! I can commit to keeping my code open source!
```

Already true — repo is under the [Unlicense](LICENSE), the most permissive license possible.

## 16. Arc OSS Question*

> *If you are applying for Arc OSS, why should we choose your project? What primitives are you exposing that other builders could find useful?*

**Suggested answer (~250 words). Full positioning lives in [`ARC-OSS-SHOWCASE.md`](ARC-OSS-SHOWCASE.md).**

```
Archimedes exposes twelve forkable primitives that other Arc builders can adopt as a stack or individually:

1. Strategy Passport schema — provenance binding for AI-generated strategies (source paper, methodology hash, paper-claim deltas)
2. Selection-bias rigor gate — DSR + PBO + walk-forward OOS + look-ahead audit, all in pure numpy with no Archimedes coupling
3. On-chain reasoning trace anchoring — TracePublisher + ReasoningTraceRegistry.sol; keccak256-hash an off-chain trace, anchor on Arc, verify by recomputation
4. DB-backed knowledge-base substrate — Postgres-canonical + idempotent seed + live intake; we use it for q-fin papers but it generalizes to any "AI grounded in domain knowledge" pattern
5. LLM provider-agnostic backend factory — one env var (LLM_PROVIDER) switches between Anthropic, Anthropic-compatible (z.ai/GLM), OpenAI, Ollama; canned fallback on missing credentials
6. 3-input fusion engine — user intent × live context × knowledge base → AI proposal; the pattern generalizes beyond strategy synthesis
7. Circle-signer pattern — Circle Developer-Controlled Wallets for autonomous on-chain writes, no raw private keys in production
8. Xia 2026 named-protocol implementation — Outcome Embargo + Time-Aware Retrieval + Hierarchy of Truth + Source Tracking + V_check; all five protocols enforced as mechanisms, not advisory guidelines
9. StockBench harness adapter — wraps `StrategyFusion.propose` + `PortfolioAgent.propose_portfolio` against Chen et al. 2026's contamination-free benchmark; we ran it honestly (#15/15, Sortino -0.91)
10. paper-qa semantic-retrieval wrap — defense-in-depth ranker behind keyword-filtered fusion candidate selection; falls back gracefully when deps missing
11. Strategy episodic memory (`strategy_proposals` table) — every fusion proposal + rigor verdict + user-reject is content-hashed and persisted; "the library compounds" is demonstrable, not aspirational
12. Security pillar — Fernet PII encryption, log scrubbing, rate limits, CORS lockdown, IAM least-privilege, HSTS/CSP/X-Frame-Options, pre-commit detect-secrets

Each primitive has a dedicated spec or walkthrough doc — a forker can implement against the spec without reading the full source. We're under the Unlicense (no attribution required, more permissive than MIT). The existing Arc reference repos (arc-commerce, arc-p2p-payments, etc.) cover transaction-flow plumbing; Archimedes adds the AI-decision-provenance layer on top. They compose; they don't overlap.

806 backend tests + 16 analytics-engine tests green; 10 contracts deployed on Arc testnet; live at https://archimedes-arc.app/. Full positioning + per-primitive how-to-fork docs in ARC-OSS-SHOWCASE.md in the repo root.
```

## 17. Circle / Arc Feedback*

> *What worked with Circle / Arc, and where can Circle / Arc improve as a product and resources?*

**Draft answer (~200 words). TEAM TO REVIEW + AUGMENT WITH PERSONAL EXPERIENCE:**

```
What worked:

- Circle Developer-Controlled Wallets via REST API was the highest-leverage primitive for us — letting us run an autonomous agent on testnet without ever holding raw private keys in production was a clean security story we couldn't have built in two weeks otherwise.
- USDC as native gas on Arc removed an entire class of UX friction. New users grab faucet USDC once and can execute everything; no "first acquire ETH for gas" detour.
- The context-arc submodule with circlefin-skills/* was the canonical reference and saved us hours of doc-spelunking. The task-routed entry-point in AGENTS.md is the right shape.
- Foundry-based contract dev against Arc testnet via the arc-canteen RPC proxy was friction-free once the swrm_ token was sourced.

Where to improve:

- Arc mainnet timeline visibility — "upcoming" with no date makes it hard to plan the business case for any project graduating off-hackathon.
- The Circle CLI Agent Wallets path (with x402 nanopayments) is well-documented but the install + setup flow has friction points; we evaluated it and stayed with Developer-Controlled Wallets to avoid the destabilization this close to demo.
- USYC as an on-chain asset isn't surfaced cleanly in the Arc testnet faucet flow; adding it as a faucet-able test asset would help any project building risk-off tiers.
- More fork-from primitives in the circlefin/arc-* reference repos for AI/agent use cases specifically (the current ones are transaction-flow focused).
```

## 18. General Feedback*

> *What worked well? What didn't? What could the Canteen team improve for future hackathons?*

**Draft answer (~200 words). TEAM TO REVIEW + ADD PERSONAL TAKES:**

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

## Submission checklist

- [ ] All `<<…>>` placeholders filled in
- [ ] Substantive answers (#9, #10, #11, #14, #16, #17, #18) reviewed by at least one teammate besides the drafter
- [ ] Demo video recorded + URL pasted in #14
- [ ] arc-canteen telemetry counts pulled for #11 (`arc-canteen status` output)
- [ ] GitHub repo public + accessible (verify via incognito window)
- [ ] Live testnet deploy responding to `/health` at submission time
- [ ] Submit at <https://forms.gle/ok3Gr9zhmHnApvK48>
- [ ] Run `arc-canteen update-product "ArcOSS: <one-line>"` to register the OSS submission

## Related docs

- [`ARC-OSS-SHOWCASE.md`](ARC-OSS-SHOWCASE.md) — full per-primitive positioning
- [`docs/judging-rubric-assessment.md`](docs/judging-rubric-assessment.md) — Day-10 rubric self-assessment
- [`docs/archive/launch-plan-2026-05-19.md`](docs/archive/launch-plan-2026-05-19.md) — historical coordinated launch plan (drives #11 traction numbers); current submission-day execution plan lives in [`docs/dead-code-audit-2026-05-24-v2.md`](docs/dead-code-audit-2026-05-24-v2.md) § Submission-day execution plan
- [`docs/demo-script-pitch-deck-outline.md`](docs/demo-script-pitch-deck-outline.md) — demo structure source for #14
