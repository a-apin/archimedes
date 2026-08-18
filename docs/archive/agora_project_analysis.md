> **ARCHIVED 2026-07-28 — historical. Current: [`docs/architectural-principles.md`](../architectural-principles.md)**
>
> Day-2 red-team synthesis for the May-2026 Agora hackathon. Header still says "Draft for team review" and carries no date; it is neither current nor under review.

# Agora Hackathon Project Analysis — Working Document

**Purpose:** Synthesis of a conversation analyzing a proposed hackathon submission for the Agora Agents Hackathon (Canteen × Circle, on Arc). Combines the team's working idea description, supporting evidence, critical pushback, and a thorough regulatory survey across jurisdictions.

**Status:** Draft for team review. Citations are provided for traceability; verify before relying on any specific claim.

---

## 1. Hackathon Context

The Agora Agents Hackathon is hosted by Canteen in partnership with Circle, with all submissions settled on Arc (Circle's stablecoin-native L1). The framing positions AI agents as participants in markets, with Arc providing "the right physics": sub-second deterministic finality and ~$0.01 transaction fees paid in USDC rather than volatile gas tokens.[^1]

**Format:** Online, two weeks, May 11 → May 25, 2026.[^1]

**Prize pool:** $50K total across four tiers — grand prizes ($40K, 1st–3rd place), standout teams ($7.5K split across 10–12 teams), feedback incentives ($500), and easter eggs ($2K).[^1]

**Judging rubric (this matters):**[^1]
- 30% Agentic Sophistication — how much the AI actually decides vs. just automates
- 30% Traction — real users, real transactions, real volume *during the event window*
- 20% Circle tool usage — creative use of Wallets, CCTP, Gateway, App Kit, Contracts, USYC, USDC
- 20% Innovation — novel approaches, emergent behavior, research insight

The traction weight is unusual for a hackathon and deserves attention. The Canteen team explicitly notes that "great founders ship and get users in two weeks."[^1]

**Requests for Builders (RFBs)** — note: these are not tracks, just suggestions. The six published RFBs are:[^1]

| RFB | Title | Relevance to our project |
|---|---|---|
| 01 | Perpetual Futures Trading Agent | Partial — execution layer overlap |
| 02 | Prediction Market Trader Intelligence | Low |
| 03 | Prediction Market Verticals | Low |
| 04 | Adaptive Portfolio Manager | **Highest** — explicit cross-chain rebalancing, regime detection, USYC for risk-off allocation, goal-based portfolio interfaces |
| 05 | Cross-Platform Arbitrage Agent | Partial |
| 06 | Social Trading Intelligence | Possible — if strategies are user-shared |

The team initially read these as "RFP 1 or RFP 4" — they're RFBs (Requests for Builders), and based on the project description, RFB 04 is the cleanest match, with possible elements of RFB 01.

**Notable:** The Agora research section explicitly highlights an idea that maps directly onto the team's on-chain reasoning trace concept: "the full reasoning trace can be hashed and pinned (trace to IPFS / Irys, hash on Arc) without eroding PnL. That unlocks a new market type: bets on which reasoning patterns converge to profit."[^1] The organizers are pointing at this hack themselves, which is a positive signal for the architectural thesis.

---

## 2. Project Idea (as described)

A trading agent platform that:
1. Takes user inputs (target assets, risk preferences)
2. Synthesizes strategies from a corpus of quantitative finance research papers (arXiv and other academic sources)
3. Runs backtests, prices strategies according to risk profile
4. Surfaces strategy options for user selection
5. Executes selected strategies as agentic on-chain trades, settled on Arc with USDC
6. (Stretch / longer-term) Builds a marketplace of vaults containing tokenized real-world assets, with on-chain reasoning trace commitments

The team member contributing to this document is responsible for the strategy engine. Other components (execution, vaults, marketplace, on-chain commitment infrastructure) are owned by other team members.

---

## 3. Market Landscape: Stablecoins, USDC, and RWAs

### Pool vs. Flux

**Total crypto market cap:** ~$2.64 trillion as of May 2026, down from an October 2025 all-time high of $4.31 trillion.[^2]

**Stablecoin market cap (the pool):** ~$319.6 billion as of late April 2026.[^3]
- USDT (Tether): ~$189.6 billion, ~60% share
- USDC (Circle): ~$77.6 billion, ~24% share
- USDT + USDC together = ~90% of all stablecoins[^3]

**Stablecoin on-chain transfer volume (the flux):** Different methodologies give different numbers:
- Plasma/Artemis: ~$33 trillion in 2025[^4]
- a16z (cited via MEXC): $46 trillion in 2025[^5]
- Visa's *adjusted* methodology (filters out bots and same-entity transfers): a recent 30-day raw volume of $3.9 trillion adjusts down to $817.5 billion[^6]
- For context: Visa moved $15.7 trillion and Mastercard $9.8 trillion in 2024[^7]

The bioinformatics intuition holds: the standing pool (~$319B) is small, but the annual flux is two orders of magnitude larger, because each dollar cycles many times. Methodology variation between sources is analogous to PCR-duplicate filtering or read deduplication — same underlying signal, different filters.

### USDC growth trajectory
- USDC grew 73% in 2025 vs. USDT's 36% — second consecutive year USDC outpaced USDT, largely driven by GENIUS Act compliance preference.[^8]
- USDC is positioned as the regulated/institutional choice; USDT remains dominant in emerging markets.

### Tokenized RWA universe (Q1 2026)
- Total on-chain RWA value (excluding stablecoins): **~$22–29 billion** depending on methodology[^9][^10]
- Tokenized U.S. Treasuries: ~$13.4 billion (from $9.6B at end of 2025), growing fast[^11]
- Top issuers in Treasuries:[^11]
  - BlackRock BUIDL: $2.4B
  - Circle USYC: $2.7B
  - Ondo Finance suite (USDY, OUSG): $2.6B
  - Franklin Templeton BENJI: $1.0B
  - WisdomTree WTGXX: $861M
  - These 5 issuers account for ~80% of the tokenized Treasury market[^10]
- Other RWA segments: private credit (~$9.5B, led by Centrifuge and Maple), gold (~$1.2B–$7.3B depending on source, via Paxos Gold and Tether Gold), real estate (~$2.5B)[^10][^11]
- Important caveat for our project: **the tokenized-RWA universe outside of Treasuries and gold is shallow and illiquid.** "Tokenized RWAs" in 2026 mostly means tokenized Treasuries. Yields cluster at 3.5–5% APY for Treasuries, 8–15% for private credit (with materially higher risk).[^10]

---

## 4. The Core Architectural Ideas — Where They're Strong

### 4.1 Payments flow on Arc
The payments flow is genuinely well-suited to Arc's properties:

- **Per-query metering** via Circle Nanopayments: gas-free USDC payments as small as $0.000001 via batched transactions[^1]
- **Vault smart contracts** hold deposits and mint vault tokens (standard ERC-4626 pattern)
- **Cross-chain coordination** via CCTP (Cross-Chain Transfer Protocol) and Gateway, which provides a unified USDC balance across chains with sub-500ms transfers[^1]
- **Paymaster** allows users to pay transaction fees in USDC rather than volatile gas tokens[^1]
- **USYC** as the risk-off allocation — explicitly named in RFB 04 as "park capital in USYC during risk-off periods"[^1]

The whole system uses USDC as a single substrate end-to-end, which is structurally simpler than legacy finance's FX/custodian/settlement chain.

### 4.2 On-chain reasoning trace pattern
The pattern (hash on-chain, full content in IPFS/Irys) is well-grounded:
- The Trading-R1 paper (Xiao et al., Sep 2025, Tauric Research)[^12] demonstrates a 100K-sample reasoning corpus and structured outputs, where the value is the reasoning trace itself, not just the trade signal.
- Hashing 32 bytes on Arc at ~$0.01 makes commit-then-publish economically viable, where it isn't on Ethereum mainnet.
- The Agora research section explicitly calls this out as a buildable hack tied to RFB 06.[^1]

### 4.3 Garrison's "memory makes computation universal"
Erik Garrison's 2024 paper formally argues that recursive state maintenance and reliable history access are both necessary and sufficient for universal computation.[^13] A public blockchain provides these as substrate-level primitives. The framing is technically correct — but see the critique in §5.

---

## 5. Red Team: Where the Ideas Are Weakest

### 5.1 The Garrison-blockchain mapping is more aesthetic than substantive

The "memory enables universal computation" property is satisfied by trivially many systems: Postgres write-ahead logs, append-only files, git, S3 versioning, Kafka topics. Garrison's paper itself spans "cellular computation to neural networks to language models"[^13] — the bar is extraordinarily low.

What blockchain *uniquely* provides is **multi-party adversarial-trust memory** — public, censorship-resistant, tamper-evident, with cryptographic timestamping. That's a narrow and valuable property, but it's not "memory for universal computation" in general; it's a specific kind of memory worth using only when those adversarial-trust properties matter. Public blockchain storage is also slower and more expensive than local databases by many orders of magnitude.

**Honest framing:** 99% of the system's state should be in Postgres/S3. The on-chain commitments should be reserved for the specific facts that need multi-party verification (trade settlement, reasoning-trace commitments tied to financial outcomes). Pitching "blockchain as memory substrate" is rhetorically clean but architecturally misleading.

### 5.2 Adverse selection in on-chain reasoning traces

If reasoning is genuinely alpha-generating, the rational behavior is to keep it secret and publish only the trade. The reasoning traces that get published will tend to be the ones nobody thinks are valuable enough to hide. This is a classic lemons-market dynamic.

**Auditability is weaker than it looks.** A hash proves a trace existed at time T. It does *not* prove the agent used that trace to decide the trade. Generate 100 traces, pick the one that retroactively rationalizes the trade you wanted to make anyway, publish it. Solving this requires commit-reveal schemes or threshold encryption, which add complexity and partially leak intent.

**Markets-on-reasoning have a circularity.** Resolution still depends on future profit, so it's structurally a copy-trading market with extra features.

### 5.3 The strategy engine itself — the hardest critique

**Published academic alpha is mostly dead alpha.** McLean and Pontiff (2016, Journal of Finance) studied 97 cross-sectional return predictors from published academic papers and found:
- **26% lower returns out-of-sample** (vs. in-sample reported returns)
- **58% lower returns post-publication** (vs. in-sample)
- The post-publication decline (32 percentage points beyond out-of-sample decay) is attributed to publication-informed trading.[^14]

So even before considering implementation friction, the average academic predictor loses more than half its reported edge once it's published.

**Backtest overfitting compounds this.** Bailey & López de Prado (2014, Journal of Portfolio Management) formalized the Deflated Sharpe Ratio specifically to correct for two sources of performance inflation in backtests:
- Selection bias under multiple testing (trying many strategies, keeping the best)
- Non-normality of returns[^15]

Bailey, Borwein, López de Prado & Zhu (2014) introduced the "Probability of Backtest Overfitting" (PBO) framework and demonstrated that under realistic multiple-testing conditions, the optimized out-of-sample Sharpe ratio often does not dominate the median — meaning the "best" strategy in-sample is frequently no better than random out-of-sample.[^16]

**For an LLM-generated strategy search, this is acute.** If our engine generates N candidate strategies from a paper corpus and picks the top K by backtest, we are running an enormous multiple-testing experiment without explicit correction. The probability that the selected strategies are spurious is high. This is the financial-strategy version of the multiple-comparisons problem familiar from genomics. The Deflated Sharpe Ratio and PBO are the analogues of Benjamini-Hochberg FDR control — they should be in our methodology, not an afterthought.

**Empirical pattern from live LLM trading agents.** Recent benchmarking work (AI-Trader, Dec 2025, University of Hong Kong) evaluated six mainstream LLMs across U.S. stocks, A-shares, and crypto with a "minimal information paradigm" forcing agents to autonomously search and synthesize. The finding: "general intelligence does not automatically translate to effective trading capability, with most agents exhibiting poor returns and weak risk management."[^17]

TradeTrap (Dec 2025) demonstrated that "small perturbations at a single component can propagate through the agent's decision loop and induce extreme concentration, runaway exposure, and large portfolio drawdowns" across both adaptive and rule-based agent types.[^18]

A practicing quant developer summarizes the consensus: "LLMs are best at compressing thinking, cutting time to first draft and time to diagnosis. They're not great at inventing alpha, but they remove friction around the work that surrounds it."[^19]

Even the Trading-R1 authors themselves acknowledge: "Trading-R1 is best used as a research and thesis-generation tool, not as a substitute for independent due diligence." They note training instability, hallucinations in long contexts (avg 32K token outputs), and that their training universe is "biased toward blue-chip and large-cap companies, especially in AI-related sectors during the bullish 2024–2025 cycle."[^20]

### 5.4 Smart contract risk is real

In Q1 2026 alone, DeFi exploits drained over $137M, with multiple ERC-4626 vault attacks via share-inflation (first-depositor manipulation), reentrancy double-mints, and invariant calculation precision loss.[^21] The Balancer V2 exploit (Nov 2025) cost $128M despite multiple audits from leading firms.[^22]

A hackathon-grade vault contract is not auditable in two weeks. If real funds touch the system, the most likely outcome of a successful demo is also the most likely path to losing those funds.

### 5.5 Marketplace + tokenized RWAs has a meaningful regulatory surface

Discussed thoroughly in §6 below. The short version: a platform where users deposit funds, an agent manages them, and users hold tokens representing pro-rata claims with expectation of profit derived from others' efforts is — under standard analysis — a managed investment vehicle issuing securities, regardless of how it's wrapped on-chain.

---

## 6. Regulatory Landscape

The team is international (members from five countries, one American). Hosting and entity structure are open questions. This section surveys the live regulatory considerations across major jurisdictions.

### 6.1 The United States — what actually matters

**The Howey test still controls.** Despite the regulatory thaw under SEC Chair Paul Atkins, the SEC's March 17, 2026 interpretation reaffirms that the Howey test ("investment of money in a common enterprise with expectation of profits from the efforts of others") remains the standard for whether crypto-asset transactions are securities offerings.[^23] Skadden's August 2025 analysis of SEC v. Barry (9th Cir.) confirms: "tokenization projects therefore carry some risk of being deemed securities offerings, notwithstanding the recent regulatory thaw."[^24]

**What the SEC has clarified:**[^25]
- Protocol staking, including liquid staking, generally not a securities offering
- Memecoins, digital collectibles, "digital tools" without intrinsic economic properties: generally not securities
- "Covered Stablecoins" (per April 2025 Staff Statement) and GENIUS Act payment stablecoins: categorically not securities by statute
- **Tokenized securities are still securities** (Commissioner Peirce, July 2025; SEC Jan 2026 Staff Statement)[^23]

**What our project likely triggers:**
- Investment of money (USDC deposits): ✓
- Common enterprise (pooled vault): ✓
- Expectation of profit: ✓
- Efforts of others (our strategy engine and agent): ✓

All four prongs of Howey appear to be satisfied. This is true whether or not the vault is "decentralized" in marketing; under the recent SEC interpretation, "representations or promises that are vague" might escape — but a strategy engine, backtest, and risk-pricing pitch *is* an actionable business plan with explicit managerial efforts.[^25]

**Investment adviser exposure.** The SEC's 2026 Examination Priorities specifically call out AI-driven portfolio management as a priority area. Firms "claiming to use AI for portfolio management... must demonstrate that AI tools genuinely influence investment decisions rather than serve merely as supplemental research."[^26] "AI-washing" enforcement is active, and the existing Advisers Act framework applies to AI tools.[^27]

**Bright-line risk for the hackathon:** the SEC has taken enforcement action against firms that claimed to use AI-enabled investment models in their marketing when they weren't using such technology — for violating the Marketing Rule (untrue or unsubstantiated advertisements).[^28]

**A more favorable note:** The SEC's April 13, 2026 statement on "Covered User Interface Providers" creates a path for technology providers that build interfaces (without acting as broker-dealers themselves) to operate without broker-dealer registration, subject to conditions.[^29] This is a potentially relevant exemption depending on architecture, but it does not eliminate the investment-adviser question.

### 6.2 European Union — MiCA and beyond

**MiCA (Markets in Crypto-Assets Regulation) went into full effect December 30, 2024.** Transitional period for existing service providers ends July 1, 2026.[^30][^31] Key points for our project:

- MiCA does not cover **tokenized traditional financial instruments** (those fall under MiFID II) or **fully decentralized DeFi protocols with no identifiable operator** — but most projects are not fully decentralized in practice.[^32]
- Any centralized element (interface operator, smart contract upgrade key holder, identifiable team) can trigger full MiCA obligations.[^32]
- A managed strategy engine + agent platform likely qualifies as a **Crypto-Asset Service Provider (CASP)**, requiring authorization through a National Competent Authority of an EU member state, with passporting across all 27 states.[^33]
- MiCA explicitly excludes "blockchain-related assets already regulated under pre-existing financial legislation, such as securities, deposits, structured deposits, funds, and securitization positions" — so tokenized investment fund interests fall under existing securities/fund law, not MiCA.[^34]
- Compliance costs estimated at €50,000–€100,000 minimum for crypto startups.[^35]
- MiCA has **extraterritorial effect** — engaging EU users from outside the EU triggers cross-border compliance obligations.[^36]
- EU is preparing **targeted DeFi regulation for 2026**, with no MiCA II planned but rather "stacking of specific regulations."[^37]

### 6.3 Switzerland — DLT framework

Switzerland operates under a DLT (Distributed Ledger Technology) Act that took effect in 2021, providing a framework for tokenized securities and DLT-based trading systems. FINMA supervises crypto-related activities under existing financial market laws (Banking Act, FinIA, FinSA, AMLA). Investment funds remain regulated under CISA regardless of tokenization. Active crypto-friendly jurisdiction; Zug "Crypto Valley" remains a hub.

*Note: I was unable to pull the latest 2026 FINMA guidance in this session — recommend the team have any Switzerland-based member do a focused legal review if this becomes a candidate jurisdiction.*

### 6.4 Singapore — Variable Capital Company (VCC)

Singapore's **Variable Capital Company framework** plus MAS oversight is the strongest Asia-focused alternative to Cayman.[^38] Key features:
- Tax exemptions under Sections 13O/13R for qualifying funds
- MAS regulatory credibility (often favored by institutional LPs)
- Crypto/digital token activities subject to the Payment Services Act and (where applicable) the Securities and Futures Act
- For funds investing in digital tokens, additional MAS guidance applies

Singapore is the dominant choice for Asia-focused crypto funds after Cayman.[^38]

### 6.5 Cayman Islands — the dominant offshore choice

Over 70% market share of institutional crypto fund domiciliation.[^38] Key 2026 developments:

- **The Mutual Funds (Amendment) Act 2026** and **Private Funds (Amendment) Act 2026** explicitly recognize "tokenised mutual funds" and "tokenised private funds."[^39]
- A "tokenised mutual fund" is a mutual fund whose equity interests are represented by digital equity tokens.[^39]
- The issuance of digital equity tokens by these funds is **excluded from the definition of "issuance of virtual assets"** under the Virtual Asset (Service Providers) (Amendment) Act 2026 — resolving the prior double-regulation problem.[^40]
- CIMA (Cayman Islands Monetary Authority) supervises with a layered framework: fund law, securities-investment law (SIBA), virtual-asset law (VASP Act), and explicit measures on operator oversight, outsourcing, conflicts, client assets, insurance, and marketing.[^41]
- **Important caveat for our model:** Cayman's framework "already reaches vault discretion." A "curator" model where an operator makes investment decisions is treated as substantive fund management, not just technology provision — the regulatory wrapper applies based on facts, not labels.[^41]

### 6.6 British Virgin Islands (BVI)

Lower-cost alternative to Cayman; "incubator fund" regime starts around USD 20,000–60,000 for emerging managers.[^38] Key points:[^42]
- **Primary issuance of tokens is not in itself a regulated activity** — regulation depends on token characterization and the activities around it.
- Activities like exchange, transfer, custody, or investment-related services fall under the **VASP Act 2022** or **Securities and Investment Business Act 2010 (SIBA)**.
- Many tokenized fund projects have chosen BVI specifically because virtual asset issuance is not generally regulated there.

### 6.7 UAE / Dubai (VARA), Hong Kong, Liechtenstein, Malta, Bermuda, Seychelles

All operate digital asset frameworks with varying degrees of crypto-friendliness. Specific details vary substantially; recommend focused legal review for any serious consideration.

### 6.8 Practical takeaways for the team

1. **For a two-week hackathon prototype with no real public capital deployed:** regulatory exposure is minimal and the question is largely theoretical. Test users acknowledging it's a prototype is a defensible posture.
2. **The moment the platform onboards real users with real money,** the regulatory question becomes real regardless of nationality of team members.
3. **"Hosting elsewhere" is not a clean escape from US law.** MiCA, US securities law, and most major frameworks have extraterritorial reach when targeting their residents. The relevant question is: where are the users?
4. **Realistic post-hackathon paths if pursuing this seriously:**
   - **Cayman fund + BVI service company** is the conventional setup for crypto-native funds with institutional ambitions.
   - **BVI alone** is lighter touch for early-stage prototypes.
   - **Singapore VCC** if the user base will skew Asian institutional.
   - **EU MiCA authorization** if the user base will skew European retail — but the cost and timeline are non-trivial.
5. **None of these eliminate US securities-law exposure for US persons.** Restricting US persons via geo-blocking and KYC is the standard mitigation but doesn't eliminate risk if the platform "targets" US residents through marketing, English-language interfaces aimed at the US, etc.
6. **The "DeFi transcends nations" framing is rhetorically appealing but legally weak.** Every major regulator has explicitly addressed this question and the answer is consistently: jurisdiction follows users, not servers.

---

## 7. Open Questions the Team Should Be Able to Defend

These are the questions a sharp judge, investor, or due-diligence reviewer would ask. Having crisp answers is worth more than having more features.

1. **What is the smallest end-to-end loop that demonstrates the thesis** — strategy generation → user selection → on-chain settlement → on-chain commitment of reasoning trace — that we can actually ship with real users in the event window?
2. **What's the one capability that genuinely requires Arc specifically?** If the answer is only "cheap transactions," that's weaker than if the answer involves CCTP, Gateway, sub-second finality for some specific reason, or Paymaster for a UX property we couldn't otherwise deliver.
3. **What's our story for not generating spurious backtested strategies, given the multiple-testing problem?** Are we applying Deflated Sharpe Ratio, PBO, or other selection-bias corrections? Walk-forward validation with no look-ahead? Out-of-sample reservation? Transaction-cost and slippage modeling?
4. **What does a "strategy" actually look like as an artifact** — code, parameters, natural-language spec, structured JSON? How is it versioned? Can it be re-executed deterministically from its on-chain hash?
5. **How does the engine consume the arXiv corpus?** RAG over papers, fine-tuned model, agentic search? What's the filtering for quality vs. quantity? How do we handle the fact that most q-fin arXiv papers describe strategies that don't survive transaction costs?
6. **What does "variable risk profiles modeled and priced according to risk" mean concretely?** Volatility-targeted? Maximum drawdown? Conditional Value at Risk? These are very different mathematical problems.
7. **What's the trust model between the strategy engine, the agent that executes, and the user?** Is the user approving each trade? Setting bounds and approving classes of trades? Fully autonomous? Each option has different legal and UX implications.
8. **Which specific tokenized RWAs will the vaults hold?** "Tokenized RWAs" outside of USYC/BUIDL/OUSG/BENJI for Treasuries and PAXG/XAUT for gold is a thin universe. What's the actual asset selection?
9. **What part of the system makes money, for whom, in a way users can verify?** (This is also the answer to "why should anyone use this.")
10. **What's the demo path?** A judge spending 5 minutes with the live product needs to feel an "aha." What's the single visceral moment we're optimizing for?

---

## 8. Suggested Framings for the Submission

Without prescribing scope (the team decides what's achievable), some observations:

- **Recent winners in adjacent hackathons** (ETHGlobal HackMoney 2026 Arc track) shipped narrow products: a self-paying corporate treasury, a tap-to-pay POS, an email-based onboarding flow, a chain-agnostic FX DEX.[^43] None tried to be platforms.
- **97% of HackMoney Arc track submissions incorporated AI agents that make financial decisions independently.**[^43] AI agents are the baseline expectation, not the differentiator. Differentiation comes from the specific problem and the user experience.
- The traction criterion implies a demo where real users actually do something, not slides describing what users would do.
- If the marketplace/vault piece is too ambitious for the window, **the strategy engine + on-chain reasoning trace commitment + a single executed test trade** is a coherent slice that still demonstrates the architectural thesis. The marketplace becomes the roadmap, not the deliverable.
- If the vault piece is essential, **a single curated vault with a fixed strategy** (e.g., regime-aware USYC vs. USDC allocation) is more tractable than an open marketplace where users create vaults.

---

## 9. Conclusion

The project as described is architecturally ambitious and intellectually interesting. The market context (stablecoin growth, tokenized RWA expansion, AI agent infrastructure maturing) is supportive. The matching against RFB 04 is clean.

The weakest parts are not the on-chain mechanics — they are (a) the strategy engine's vulnerability to the multiple-testing problem and dead-alpha problem documented in the academic literature, (b) the adverse-selection problem in on-chain reasoning publication, and (c) the regulatory surface of the marketplace/vault component once it moves beyond a prototype.

The strongest move for the hackathon window is likely to commit to a narrow vertical slice that demonstrates the architectural thesis end-to-end with real (test) users, rather than building the platform horizontally. The longer-term opportunity — and the right answer to "what comes after the hackathon" — depends on whether the strategy engine produces strategies that survive proper out-of-sample testing, which is an empirical question that won't be resolved in two weeks.

---

## References

[^1]: Canteen × Circle, "Agora Agents Hackathon," official hackathon page. <https://agora.thecanteenapp.com/>

[^2]: CoinGecko global crypto market data; sources clustering between $2.57T–$2.79T total market cap, May 2026. All-time high reference: Oct 6, 2025 at $4.31T.

[^3]: Bitrue, "Stablecoin Trends May 2026," April 29, 2026. USDT $189.6B, USDC $77.6B, total stablecoin market cap $319.6B. <https://www.bitrue.com/blog/stablecoin-trend-may-2026>

[^4]: Plasma, "Stablecoin Transaction Volume Trends in 2026." Total stablecoin transaction volume reached $33 trillion in 2025. <https://www.plasma.to/learn/stablecoin-transaction-volume>

[^5]: CoinCentral via MEXC, "Stablecoins Surpass Visa with $46 Trillion in On-Chain Transactions," citing Andreessen Horowitz's 2025 report. <https://www.mexc.co/en-PH/news/stablecoins-surpass-visa-with-46-trillion-in-on-chain-transactions/140184>

[^6]: Visa Corporate, "Making sense of stablecoins," updated July 2025. Adjusted methodology brings raw $3.9T 30-day volume down to $817.5B of real economic activity. <https://corporate.visa.com/en/sites/visa-perspectives/trends-insights/making-sense-of-stablecoins.html>

[^7]: Visual Capitalist, "Stablecoins Are Now Bigger Than Visa or Mastercard," Nov 2025. <https://www.visualcapitalist.com/charted-stablecoins-are-now-bigger-than-visa-or-mastercard/>

[^8]: CoinDesk, "Circle's USDC outpaces USDT in growth for second consecutive year," Jan 6, 2026. <https://www.coindesk.com/markets/2026/01/06/circle-s-usdc-outpaces-growth-of-tether-s-usdt-for-second-year-running>

[^9]: KuCoin, "Real-World Assets (RWA) Crypto Growth 2026," citing RWA.xyz data: $19–$36B tokenized RWAs (ex-stablecoins) in early 2026.

[^10]: Jung-Hua Liu, "Tokenization of Real-World Assets (RWA): A Comprehensive Analysis," Medium, April 2026. Tokenized U.S. Treasury market grew from $380M (Q1 2023) to $14B (Q1 2026), CAGR ~230%. BlackRock, Ondo, Hashnote, Franklin Templeton = ~80% of market. Private credit ~$9.5B; gold ~$1.2B; real estate ~$2.5B. <https://medium.com/@gwrx2005/tokenization-of-real-world-assets-rwa-a-comprehensive-analysis-of-technology-stacks-platform-939d3269a32e>

[^11]: InvestaX, "Q1 2026 Real World Asset Tokenization Market Report." Tokenized Treasuries surpassed $10B late Feb, reached $13.4B by early April. Total RWA market (ex-stablecoins) ~$29B, 30% Q1 growth, 263% YoY. <https://investax.io/blog/q1-2026-real-world-asset-tokenization-market-report>

[^12]: Xiao, Y., Sun, E., Chen, T., Wu, F., Luo, D., Wang, W. "Trading-R1: Financial Trading with LLM Reasoning via Reinforcement Learning." arXiv:2509.11420, September 14, 2025. <https://arxiv.org/abs/2509.11420>

[^13]: Garrison, E. "Memory makes computation universal, remember?" arXiv:2412.17794, December 23, 2024. <https://arxiv.org/abs/2412.17794>

[^14]: McLean, R.D., and Pontiff, J. "Does Academic Research Destroy Stock Return Predictability?" *Journal of Finance*, 71(1): 5–32, 2016. **Key finding:** "Portfolio returns are 26% lower out-of-sample and 58% lower post-publication." DOI: 10.1111/jofi.12365. <https://onlinelibrary.wiley.com/doi/abs/10.1111/jofi.12365>

[^15]: Bailey, D.H., and López de Prado, M. "The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality." *Journal of Portfolio Management*, 40(5): 94–107, 2014. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>

[^16]: Bailey, D.H., Borwein, J., López de Prado, M., and Zhu, J. "The Probability of Backtest Overfitting." 2014. The CSCV (Combinatorially Symmetric Cross-Validation) framework demonstrates that optimized in-sample performance does not generally translate to out-of-sample performance. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>

[^17]: Fan, T., Yang, Y., Jiang, Y., et al. "AI-Trader: Benchmarking Autonomous Agents in Real-Time Financial Markets." University of Hong Kong, December 2025. arXiv:2512.10971. Finding: "general intelligence does not automatically translate to effective trading capability, with most agents exhibiting poor returns and weak risk management."

[^18]: "TradeTrap: Are LLM-based Trading Agents Truly Reliable and Faithful?" arXiv:2512.02261, December 2025.

[^19]: Dataconomy, "LLMs Don't Invent Alpha: A Quant Dev's Reality Check On AI In Trading," interview with Ilya Navogitsyn, January 30, 2026. <https://dataconomy.com/2026/01/30/llms-dont-invent-alpha-a-quant-devs-reality-check-on-ai-in-trading/>

[^20]: Trading-R1 paper, limitations section. "Trading-R1 is best used as a research and thesis-generation tool, not as a substitute for independent due diligence... Another limitation lies in the training universe, which has been biased toward blue-chip and large-cap companies, especially in AI-related sectors during the bullish 2024–2025 cycle." <https://arxiv.org/pdf/2509.11420>

[^21]: dev.to, "Q1 2026 DeFi Exploit Pattern Analysis: $137M Lost, 5 Attack Patterns Every Auditor Must Know," March 25, 2026. Specifically calls out ERC-4626 vault attacks via first-depositor share inflation, reentrancy double-mints, invariant calculation precision loss. <https://dev.to/ohmygod/q1-2026-defi-exploit-pattern-analysis-137m-lost-5-attack-patterns-every-auditor-must-know-2mh>

[^22]: Rescana, "Comprehensive Analysis of the $128 Million Balancer V2 DeFi Exploit," November 4, 2025. Despite multiple audits by leading firms, exploit succeeded via precision rounding errors and invariant manipulation.

[^23]: WilmerHale, "The SEC's New Framework for Crypto Assets Under Howey," March 24, 2026. <https://www.wilmerhale.com/en/insights/client-alerts/20260324-the-secs-new-framework-for-crypto-assets-under-howey>

[^24]: Skadden, Arps, Slate, Meagher & Flom LLP, "Howey's Still Here: A Recent Reminder on the Limits of the SEC's Crypto Thaw," August 18, 2025. Discusses Ninth Circuit decision in SEC v. Barry. <https://www.skadden.com/insights/publications/2025/08/howeys-still-here>

[^25]: Latham & Watkins Global Fintech blog, "SEC Clarifies the Application of the Securities Laws to Cryptoassets," April 2, 2026. Details the SEC's five-category token taxonomy. <https://www.fintechanddigitalassets.com/2026/04/sec-clarifies-the-application-of-the-securities-laws-to-cryptoassets/>

[^26]: Goodwin, "2026 SEC Exam Priorities for Registered Investment Advisers and Registered Investment Companies," January 5, 2026. <https://www.goodwinlaw.com/en/insights/publications/2025/12/alerts-privateequity-pif-2026-sec-exam-priorities-for-registered-investment-advisers>

[^27]: Kitces, "AI Compliance: Applying Existing SEC Regulatory Frameworks," December 9, 2025. <https://www.kitces.com/blog/artificial-intelligence-compliance-considerations-investment-advisers-sec-securities-exchange-commission-legal-regulation-framework/>

[^28]: ncontracts, "AI Compliance for Firms and RIAs in 2026," April 2, 2026. Discusses SEC enforcement actions against advisers for AI-washing. <https://www.ncontracts.com/nsight-blog/investment-advisers-artificial-intelligence>

[^29]: Sidley Data Matters Privacy Blog, "U.S. SEC Clears Path for Decentralized Crypto Asset Security Trading With Broker Registration Exception for User Interfaces," April 21, 2026. <https://datamatters.sidley.com/2026/04/21/u-s-sec-clears-path-for-decentralized-crypto-asset-security-trading-with-broker-registration-exception-for-user-interfaces/>

[^30]: ESMA, "Markets in Crypto-Assets Regulation (MiCA)." <https://www.esma.europa.eu/esmas-activities/digital-finance-and-innovation/markets-crypto-assets-regulation-mica>

[^31]: Hacken, "MiCA Regulation: What Crypto Projects Must Know For 2026 Compliance." MiCA transition period ends July 1, 2026. <https://hacken.io/discover/mica-regulation/>

[^32]: InnReg, "EU Crypto Regulation Explained: An Essential Guide (2026)." "Fully decentralized protocols with no identifiable operator are currently outside the scope of MiCA. However, most projects are not fully decentralized in practice." <https://www.innreg.com/blog/eu-crypto-regulation-guide>

[^33]: Sumsub, "MiCA Regulation and EU Crypto Rules: What Changes in 2026." <https://sumsub.com/blog/crypto-regulations-in-the-european-union-markets-in-crypto-assets-mica/>

[^34]: Complyfactor, "MiCA Regulation Guide 2026." MiCA exclusions for assets already regulated under existing financial directives. <https://complyfactor.com/mica-regulation-guide-2026-eu-crypto-asset-framework-explained/>

[^35]: CoinLaw, "EU MiCA Regulations Statistics 2026." Licensing costs €50,000–€100,000 for crypto startups. <https://coinlaw.io/eu-mica-regulations-statistics/>

[^36]: InnReg, MiCA extraterritoriality. Cited above.

[^37]: Cointelegraph, "European Crypto Regulation in 2026: DeFi, not MiCA II at Forefront," June 2025. <https://cointelegraph.com/news/eu-defi-regulation-2026-mica>

[^38]: HPT Group, "Crypto Fund Formation in 2026: Cayman, BVI & Singapore Compared." Cayman dominates with over 70% market share of institutional crypto fund domiciliation. <https://hpt.group/blog-posts/crypto-fund-formation-guide-2026>

[^39]: Ogier, "Cayman Islands welcomes new regulation for tokenised funds," March 26, 2026. Mutual Funds (Amendment) Act 2026 and Private Funds (Amendment) Act 2026. <https://www.ogier.com/news-and-insights/insights/cayman-islands-regulation-for-tokenised-funds/>

[^40]: Loeb Smith, "The Cayman Islands Clarifies Tokenised Funds Rules," January 22, 2026. <https://www.loebsmith.com/insight/the-cayman-islands-clarifies-tokenised-funds-rules/>

[^41]: Prokopiev Law, "The DeFi Curator Legal Problem: How Panama, BVI, and Cayman Islands Law Already Reaches Vault Discretion," March 31, 2026. <https://www.prokopievlaw.com/post/the-defi-curator-legal-problem-how-panama-bvi-and-cayman-islands-law-already-reaches-vault-discre>

[^42]: Maples Group, "The Rise of Digital Asset Funds and Tokenisation in the Cayman Islands and the BVI." <https://maples.com/knowledge/the-rise-of-digital-asset-funds-and-tokenisation-in-the-cayman-islands-and-the-bvi>

[^43]: Arc Network blog, "Meet the Arc Track Winners from the HackMoney 2026 Hackathon and What We Learned," April 2026. 155 submissions, 97% incorporated AI agents making financial decisions independently. <https://www.arc.network/blog/meet-the-arc-track-winners-from-the-hackmoney-2026-hackathon-and-what-we-learned>

---

*This document is a working synthesis. Verify all citations and claims independently before relying on them for decision-making. Legal observations are not legal advice; consult qualified counsel in the relevant jurisdictions for any material decision.*
