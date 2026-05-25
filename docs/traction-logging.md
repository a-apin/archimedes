# `arc-canteen` traction logging — cheat sheet

> **Why this exists:** CLAUDE.md § "Known risks" reads:
> > *Traction = 0 on the rubric scoreboard until arc-canteen telemetry starts flowing. This is the cheapest +points to recover.*
>
> The hackathon's **30% Traction weight** is computed from `arc-canteen` telemetry — not from anything else. Until we log, judges see zero. Every team member runs the CLI under their own auth (per-user `swrm_*` token), so this is a per-person task — but doing it once now closes the gap entirely.

## 0. One-time setup (each teammate)

```bash
arc-canteen login            # opens GitHub device flow
arc-canteen status           # confirms your dashboard view (what the judges see)
```

If the binary isn't on your PATH, see [README.md § arc-canteen CLI](../README.md).

---

## 1. Log every product ship from the last ~2 weeks

For each line below, run `arc-canteen update product` and paste the suggested message when prompted. **One product update per PR.** If you can record a Loom of the feature working, link it.

> The list is roughly newest → oldest. Skip docs-only / chore PRs (already filtered).

| Commit | Suggested `update product` message |
|---|---|
| `e0dd140` / PR #121 | Portfolio advisor goes agentic: 84-instrument global universe scan (US/EU/Asia/Turkish individual stocks + LME-aligned metals + bond ladder + crypto + futures + FX); LLM picks individual names with paper anchors and reasoning; rule-based fallback keeps it working without LLM creds. |
| `852597c` / PR #123 | Citadel-grade advisor upgrade: Kelly fix (excess returns), covariance-aware MVO with Markowitz/Kelly objective + per-profile γ + diagonal shrinkage, Magdon-Ismail max-DD estimator, parametric VaR-95, 6-scenario stress engine with coverage tracking, multi-turn tool-use LLM agent, on-chain reasoning-trace anchor with deterministic keccak hash. |
| PR #117 (`9fb1c34`) | Earlier portfolio advisor wiring fixes — rigor gate API gap + advisor 0.0 vs missing bug. |
| PR #116 (`eb1ca7f`) | Redis-down resilience for regime and agent-status endpoints. |
| PR #115 (`b2d57bd`) | Portfolio Advisor + regime intelligence panel + regime-aware strategy recommendations. |
| PR #114 (`2ad809b`) | Strategy passport: Sharpe confidence intervals, drift detector, efficient frontier, correlation heatmap, rigor explainer. |
| PR #113 (`2884912`) | Surface deflated-Sharpe p-value + rigor-gate pass/fail through API + UI. |
| PR #111 (`b046559`) | Front-end fixes batch (see PR). |
| PR #108 / #105 (`ba38d8a` / `8991c92`) | Strategy-corpus integration work-in-progress (lands in #107). |
| PR #107 (`8d55e74`) | Research-archive: paste-ready Linus↔Archimedes port-back specs preserved in `docs/research/`. |
| `9776734` | Corpus Explorer UI with graph + knowledge-graph endpoints (Issue #93). |
| `2449f4b` | Expanded q-fin paper corpus to 10,000 papers (Issue #97). |
| `89d6e29` | DB-backed paper endpoints with proper pagination (Issues #93, #97). |
| `a080724` | Bulk arXiv ingest script + HTTPS fix (Issue #97). |
| `2919af2` | Dynamic DB-backed q-fin paper corpus (Issue #106). |
| `7a03e71` | LLM backend ANTHROPIC_* legacy env-var fallback fix. |
| `d5bffa6` | LLM credential diagnostics surfaced through health endpoint. |

After each, `arc-canteen status` confirms the entry landed.

---

## 2. Log every user conversation

The other half of Traction. For every person who has:

- Demoed the product (live or via screen recording)
- Given feedback on the deck, the architecture, or the UX
- Read `user-stories.md` or `docs/design.md` and reacted
- Said *anything* substantive in Discord (Archimedes Arcadia + Canteen servers)
- Tested the live testnet deploy at `https://archimedes-arc.app/`

…run `arc-canteen update traction` and log the conversation. Include:

- Who (name or handle; Discord handle is fine)
- What you showed them
- Their reaction (one sentence)
- Any commitment they made (further demo, intro, feedback) — or "none" honestly

**Examples of who probably belongs in here already** (each team member fills in their own):

- Anuhya (Canteen admin / hackathon stakeholder)
- Anyone in #archimedes-arcadia Discord who has DM'd or reacted in-channel
- Whoever Chuan has shown the contracts to (Gyld Finance contacts, RWA folks)
- Whoever Dan has talked to about the paper corpus / KnowledgeBase
- Whoever Önder has talked to about the rigor stack (TİD-Genç, Hacettepe stats faculty, ASA contacts)
- Whoever Marten has DM'd about the off-chain→on-chain integration
- Whoever Daniel R has shown the UI to

If the answer is "I haven't actually shown this to anyone yet," **show it to someone today**, then log the conversation. Even a 5-minute screenshare with a fellow hackathon team counts.

---

## 3. Going forward — make it a habit

Every PR merge → `arc-canteen update product` within the same hour. Every demo conversation → `arc-canteen update traction` within the same day. Two minutes per entry. Without this, the rubric reads zero on the biggest weighted component.

If the CLI is slow or interactive prompts annoy you, a future helper could batch from `git log`, but for the next 72 hours: just type the entries by hand. Speed of recovery matters more than tooling polish.
