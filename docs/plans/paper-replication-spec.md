# Paper Replication & Original Extension — workflow spec

> **Status:** Post-hackathon scaffold. The hackathon ships a *framework*; this
> spec describes how to use that framework to produce **one original piece of
> research** worth putting on a quant CV. Owner: Önder.
>
> **Why this exists:** Per the May-22 self-critique, the current codebase is a
> strong portfolio-construction *framework* with paper-grounded strategies.
> It is not yet a piece of *original research*. To convert the project from
> "decent quant-engineering portfolio piece" → "credible quant-research
> portfolio piece," we need one publication-quality writeup that:
>
> 1. Faithfully replicates a published quant strategy using the framework
> 2. Proposes and tests ONE original extension
> 3. Reports results honestly, including failures
> 4. Uses purged k-fold CV (López de Prado AFML Ch. 7) — not naive walk-forward
> 5. Reports cost-adjusted info ratio + factor-neutralized residual alpha
>
> The *honesty* of the failures-section is the credential — anyone can claim
> positive results, only people doing real work report negatives.

---

## 0. What "done" looks like

A merged PR titled *"Research: replication + extension of [Paper X]"* that adds:

| Artifact | Path | Length |
|---|---|---|
| Writeup | `docs/research/replications/<paper-slug>.md` | 3–5 pages |
| Strategy implementation | `analytics-engine/strategies/<paper-slug>_replication.py` | as needed |
| Extension implementation | `analytics-engine/strategies/<paper-slug>_extension.py` | as needed |
| Results table | embedded in the writeup as Markdown | one table |
| Code-level tests | `analytics-engine/tests/test_<paper-slug>.py` | as needed |
| arc-canteen update | logged via `arc-canteen update product` | one entry |

Plus a 3-minute Loom recording demoing the replication output.

---

## 1. Pick the paper

Three candidates that fit the framework's strengths and produce defensible results:

| Paper | Why this one | Replication difficulty | Extension surface |
|---|---|---|---|
| **Frazzini & Pedersen (2014) — *Betting Against Beta*** | Single clean factor, replicable on US equities only, well-documented in textbooks. | Low-medium | Easy: regime-conditional BAB; LLM-selected sector neutralization. |
| **Asness, Moskowitz, Pedersen (2013) — *Value and Momentum Everywhere*** | Multi-asset, our framework's native shape. Cross-sectional signals on global markets. | Medium-high | Multi: regime overlay; LLM-driven metric selection per sector; combined Value+Mom signal weight learning. |
| **Bansal & Yaron (2004) — *Risks for the Long Run*** | Macro-driven (long-run consumption risk). Different from the technical-momentum lineage already in the framework. | High (consumption data) | Hard but novel: macro-regime conditioning, LLM-mediated consumption-data interpretation. |

**Recommended: Frazzini & Pedersen 2014 (BAB).** Lowest replication risk, clearest extension surface, fastest to a defensible result. If BAB lands cleanly you can do Value+Mom as #2.

---

## 2. Workflow — execute in this order

### Phase 1: Faithful replication (week 1)

1. **Read the paper end-to-end.** Take notes on the *exact* signal construction (formula, lookback, rebalance frequency, universe, neutralization). 80% of bad replications fail here.
2. **Build the universe** with point-in-time correctness:
   - Historical S&P 500 (or paper's stated universe) constituents WITH delisted names — scrape from Wikipedia revisions or Sharadar if you can get a free tier.
   - **Do NOT use current-constituents-only.** That is survivorship bias and will inflate Sharpe by ~0.2–0.4.
3. **Implement the signal** in `analytics-engine/strategies/<paper-slug>_replication.py`. Match the published rule *exactly* — no improvements, no shortcuts, no parameter tuning. Treat this as a test.
4. **Run a purged k-fold CV** using `archimedes_analytics_engine.purged_kfold`. 5 folds, 1% embargo. Forward-horizon label = rebalance period.
5. **Apply transaction costs**: 5 bps per side for liquid ETFs, 10–15 bps for individual stocks, 30+ bps for thinly-traded names. Round-trip = 2× one-side cost.
6. **Report results vs paper's published numbers**:
   - Sharpe ratio (raw)
   - Sharpe ratio (cost-adjusted)
   - Annualized return
   - Max drawdown
   - **Paper-claim delta** (real − paper-claimed) — this is the honesty moment.

**Expected outcome:** Your replication Sharpe will be 60–80% of the paper's. That's normal — papers benefit from selection bias, no-cost backtests, and survivorship. If you match the paper exactly, suspect a bug.

### Phase 2: Factor neutralization (week 1, late)

1. Download Fama-French 5-factor + momentum returns from Ken French's data library (free, monthly + daily).
2. Regress your strategy's daily returns on the 6 factors.
3. Report the **alpha residual** (intercept) and its t-statistic.
4. If the residual alpha is statistically zero, *say so*. The credibility is in the honest report. The strategy may still be useful as a factor-overlay component.

### Phase 3: One original extension (weeks 2–3)

Pick ONE. Implement, test under the same purged-k-fold protocol. Examples that leverage existing infrastructure:

#### Option A: Regime-conditional version

Use the existing regime detector to condition the signal. E.g.:

- BAB is active in `RISK_ON` regimes, dampened in `CRISIS`.
- TSMOM signal is gated by regime confidence (only act on signals when confidence > 0.7).

**Test:** does the regime overlay improve Sharpe net-of-cost? Does it improve drawdown? Honest reporting of both.

#### Option B: LLM-selected sub-metric

The agent picks WHICH value (or quality, or momentum) metric to use per sector based on a one-shot prompt over current macro. Constrains the metric choice to a fixed allow-list; the LLM is a metric-selector, not a signal generator.

**Test:** does LLM-mediated metric selection produce Sharpe > equally-weighted-over-metrics baseline? Report.

#### Option C: DSR-deflated signal threshold

Instead of a fixed zero-threshold rule ("long if 12m return > 0"), use DSR-deflated threshold ("long if 12m return > deflated threshold for N trials"). Reduces position-flipping in low-conviction regimes.

**Test:** does deflation lower turnover (good — saves costs) while preserving alpha (or even improving net Sharpe)?

### Phase 4: Writeup (week 3, late)

Use the template in `docs/research/replications/_template.md` (template not yet created) (see Section 5 of this spec for the template skeleton). 3–5 pages. Sections:

1. **Motivation** — one paragraph. Why this paper, why this extension.
2. **Paper precedent** — formal signal definition, paper's stated results, citation.
3. **Implementation** — universe construction, signal computation, neutralization choices, parameter values.
4. **OOS protocol** — purged k-fold details, embargo, cost model.
5. **Replication results** — table vs paper, paper-claim delta. Be honest.
6. **Extension** — what you tested, why, how it differs.
7. **Extension results** — table vs replication baseline, vs factor-neutralized.
8. **What didn't work** — at least one negative result. Honest reporting wins credibility.
9. **References** — proper citation format.

### Phase 5: Loom (week 4)

3-minute screen recording walking through the writeup. Open with the punchline ("After cost adjustment + factor neutralization, the BAB extension delivers X bps/yr — modest but real" — or "doesn't deliver, here's why"). Close with the framework angle ("This was built entirely on the Archimedes framework — any quant paper can be plugged in the same way").

---

## 3. Data requirements (don't skip)

| Need | Free/cheap source | Why |
|---|---|---|
| Point-in-time S&P 500 constituents | Wikipedia revision history (free) + Sharadar trial; or `SP500-historical-components` GitHub repo | Removes survivorship bias |
| Daily prices including delisted names | Yahoo for active; scrape SEC filings for delistings; Sharadar trial | Required for credible Sharpe |
| Fama-French 5+momentum factors | [Ken French data library](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html) — free | Factor neutralization |
| Per-stock transaction cost estimates | Hardcode by liquidity bucket (5/10/15/50 bps) or use TAQ on WRDS student account | Cost-adjusted Sharpe |
| Risk-free rate history | 3-month T-bill rate from FRED (free) | Excess returns for Sharpe |

---

## 4. Acceptance criteria (machine-checkable)

The PR is mergeable when:

- [ ] `pytest analytics-engine/tests/test_<paper-slug>.py` → all pass
- [ ] Writeup at `docs/research/replications/<paper-slug>.md` exists, ≥ 1500 words
- [ ] Writeup includes a results table with raw-Sharpe, cost-adj-Sharpe, alpha-residual t-stat
- [ ] Writeup includes an explicit "What didn't work" section, ≥ 1 paragraph
- [ ] Implementation imports `archimedes_analytics_engine.purged_kfold` (not naive walk-forward)
- [ ] Implementation applies transaction costs (search for "cost" or "bps" in the strategy file)
- [ ] Backtest does NOT use survivorship-biased constituents (search for "current_constituents" / spy_components_2026)
- [ ] At least one Fama-French 5+momentum regression with reported t-statistic
- [ ] 3-minute Loom linked in the PR description

---

## 5. Writeup template

Copy this skeleton into `docs/research/replications/<paper-slug>.md` to start.

```markdown
# Replication & Extension: [Paper Title] ([Author, Year])

> **One-line summary:** [Did the replication match the paper? Did the extension work? Honest.]

## 1. Motivation
[One paragraph: why this paper, why this extension.]

## 2. Paper precedent
- **Citation:** [Authors] ([Year]). *[Title]*. [Journal].
- **Signal definition:** [Formal mathematical statement of the published signal.]
- **Paper's stated results:** Sharpe X.XX, CAGR Y.Y%, max-DD Z.Z%, sample 19XX-20XX.

## 3. Implementation
- **Universe:** [Point-in-time constituents, source, delisted-name treatment.]
- **Signal:** [Code path: `analytics-engine/strategies/...`]
- **Rebalance:** [Monthly / quarterly / etc.]
- **Neutralization:** [Sector / market / none.]
- **Parameters used:** [Lookback, threshold, leverage cap. Identical to paper.]

## 4. OOS protocol
- **CV:** Purged k-fold (López de Prado AFML Ch. 7), n_splits=5, embargo_pct=0.01
- **Cost model:** [X bps per side, Y bps per round-trip on stocks; Z bps on ETFs]
- **Risk-free rate:** 3-month T-bill from FRED, daily-resampled

## 5. Replication results

| Metric | Paper | This replication | Delta |
|---|---|---|---|
| Sharpe (raw, full sample) | X.XX | Y.YY | ±Z.ZZ |
| Sharpe (cost-adjusted) | (not in paper) | Y.YY | — |
| CAGR | X.X% | Y.Y% | ±Z.Z% |
| Max drawdown | -X.X% | -Y.Y% | ±Z.Z% |
| Annualized vol | X.X% | Y.Y% | ±Z.Z% |
| Win rate | X.X% | Y.Y% | ±Z.Z% |

**Paper-claim delta on Sharpe: ±Z.ZZ.**
[One paragraph: what explains the gap — survivorship, costs, sample period, etc. Honest.]

## 6. Factor neutralization
Regression on Fama-French 5 + momentum (daily, full sample):

| Factor | Coefficient | t-statistic |
|---|---|---|
| Alpha (intercept, annualized) | X.X% | X.XX |
| Mkt-Rf | Y.YY | Y.YY |
| SMB | … | … |
| HML | … | … |
| RMW | … | … |
| CMA | … | … |
| Mom | … | … |

**Residual alpha verdict:** [statistically zero / weakly positive / robust positive].
[One paragraph interpretation.]

## 7. Extension: [Name]
**Motivation:** [Why this specific extension. What gap in the paper.]
**Implementation:** [`analytics-engine/strategies/<name>_extension.py`]
**What changes vs replication:** [Diff in plain language.]

## 8. Extension results

| Metric | Replication baseline | Extension | Delta |
|---|---|---|---|
| Sharpe (cost-adj) | Y.YY | Z.ZZ | ±W.WW |
| CAGR | … | … | … |
| Alpha residual t-stat | … | … | … |
| Turnover (annual) | … | … | … |

[One paragraph: did the extension work? Net of costs? Net of factors? Honest.]

## 9. What didn't work
[Required section. ≥ 1 paragraph. Examples:
- "I initially tried [X] which boosted gross Sharpe by 0.2 but after costs the
  improvement was zero because turnover doubled."
- "I expected regime conditioning to reduce drawdown; it modestly reduced vol
  but had no detectable effect on drawdown because the signals were already
  defensive in stress regimes."
- "The LLM-driven metric selection helped in 2015–2019 but reversed sign in
  2020–2023; the apparent edge was specific to a regime, not a stable signal."]

## 10. Code & reproducibility
- Replication: `analytics-engine/strategies/<paper-slug>_replication.py`
- Extension: `analytics-engine/strategies/<paper-slug>_extension.py`
- Tests: `analytics-engine/tests/test_<paper-slug>.py`
- Data: [point-in-time constituent CSV, FRED series, French Library factors] — see `data/` for fetch scripts.

## 11. References
1. [Authors] ([Year]). *[Paper title]*. [Journal/working paper].
2. López de Prado, M. (2018). *Advances in Financial Machine Learning*. Wiley. Ch. 7.
3. Bailey, D., & López de Prado, M. (2014). The deflated Sharpe ratio. *Journal of Portfolio Management*.
4. Bailey, D., Borwein, J., López de Prado, M., & Zhu, J. (2014). The probability of backtest overfitting. *Journal of Computational Finance*.
5. Fama, E., & French, K. (2015). A five-factor asset pricing model. *Journal of Financial Economics*.
```

---

## 6. Why this is the right play for the portfolio-piece goal

A hiring manager reading the writeup gets:

1. **Math literacy** — you can read papers and implement them correctly
2. **Engineering** — you have a framework to plug them into
3. **Judgment** — you can tell when something is real (replicated, cost-adjusted, factor-neutralized) vs noise (raw Sharpe on survivorship-biased data)
4. **Honesty** — the "What didn't work" section is a credibility move that 90% of portfolios skip

A 5-page writeup in this format, with a 3-minute Loom, is the difference between *"this candidate built a quant project"* and *"this candidate did quant research"*. The first is hireable as a quant-engineer; the second is hireable as a junior quant-researcher. Both pay well. Both look for specific things in interviews. Aim for the second.

---

## 7. Timeline

| Phase | Calendar | Outcome |
|---|---|---|
| Phase 1: Replication | Week 1 (post-hackathon) | Sharpe within 60–80% of paper, cost-adjusted |
| Phase 2: Factor neutralization | Week 1, late | Alpha-residual reported with t-stat |
| Phase 3: Extension | Weeks 2–3 | One original extension, tested under purged-k-fold |
| Phase 4: Writeup | Week 3, late | 3–5 page writeup committed |
| Phase 5: Loom | Week 4 | 3-minute screen recording linked in PR |
| Total | ~4 weeks | One merged research PR + Loom |

Anything longer than 4 weeks → cut scope, not quality. A small, honest, cost-and-factor-adjusted result beats a sprawling, optimistic, unverified one in *every* hiring conversation.
