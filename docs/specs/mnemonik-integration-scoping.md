---
status: current
owner: Dan
updated: 2026-08-30
---

# Mnemonik integration scoping — #714 sub-task C

**Verdict: borrow patterns, do not integrate.** Zero dependencies adopted. This memo is
the deliverable #714 sub-task C has owed since 2026-07 (originally Bogdan's; completed by
owner review of a commissioned evaluation, 2026-08-30).

## What Mnemonik is

[Mnemonik](https://github.com/mnemonik-xyz/monorepo) (Apache-2.0, Rust/Cargo workspace,
~4 months old, effectively single-maintainer) gives AI agents **verifiable,
semantically-recallable memory**: content is canonicalized to deterministic CBOR, hashed
with blake3, signed as COSE_Sign1/Ed25519, durably stored on **Arweave** (via Irys), and
anchored on **Solana** with a plain SPL Memo transaction carrying
`{"h": blake3, "a": arweave_tx, "v": 2}`. Exposed over MCP with x402/balance payment
gating. Its own docs frame it for A2A/enterprise-compliance use (journalism, legal,
audit) — no financial-trading framing anywhere.

## Why it does not fit our commit-reveal need

| Dimension | Mnemonik | Archimedes need |
|---|---|---|
| Problem | Provenance-timestamping + authorship: "this content existed, authored at time T" — the full artifact is **public on Arweave at anchor time** | **Hide-then-reveal commitment**: hash committed *before* a trade, content revealed and re-verified *after* — hiding is the point |
| Chain | Solana mainnet-beta + Arweave | Arc (EVM, 5042002), our sole settlement chain per ADR |
| Client surface | Rust + Node CLI; **no Python SDK** | Python backend |
| Maturity | Unaudited, single-maintainer, ~4 months | Claim-integrity-load-bearing path (claims must be true) |

Our `ReasoningTraceRegistry.sol` already implements the stronger primitive for this use:
`commit()` requires `claimedExecutionTime > block.timestamp`, `executeTrade()` requires
`block.number > commitBlock` and binds a `tradeId` (#589), `reveal()` requires
`msg.sender == committer` and `keccak256(fullTraceContent) == contentHash` — natively on
Arc (`contracts/src/ReasoningTraceRegistry.sol:108-209`), live since the T3.2 redeploy
(#588 closed, commit-before-trade enforced at `Vault.sol:422`).

Integrating would mean operating a second chain stack (Solana RPC, SOL fees,
Arweave/Irys funding) plus a hand-rolled Python↔MCP bridge, for an attestation that is
redundant with what we already enforce on-chain. Multi-week effort, marginal benefit,
and an unaudited external dependency directly under the claims-must-be-true constraint.

## Two patterns worth borrowing (ideas, not code)

1. **Lineage DAG** (`core/src/lineage`): parent-child artifact links with cycle
   detection. Nothing in Archimedes structurally models "this reasoning trace descends
   from these corpus papers / that prior strategy version." If we ever want
   trace-to-source lineage as a first-class provenance feature, this is the shape.
2. **Local/full dual-mode** (SQLite-only no-chain-no-payment vs anchored-and-paid):
   independently validates our existing `ARCHIMEDES_IPFS_ENABLED`-style feature-flag
   pattern. No action needed.

## The optional seam, if ever wanted despite this verdict

A best-effort **secondary attestation only**: after our own `reveal()` succeeds on Arc, a
sibling of `ipfs_publisher.py` POSTs the same canonical public-provenance bytes to
Mnemonik's hosted MCP endpoint (`https://mcp.mnemonik.xyz/mcp`, `mnemonic_sign_memory`)
as a redundant, independently-anchored timestamp copy. Constraints if built: never
load-bearing for any UI claim (fail-soft principle), behind its own default-off flag,
failure logged and ignored. Not recommended now.

## Higher-leverage residuals in #714 (do these instead)

- **3 legacy `trace_publisher.publish()` call sites** in
  `backend/archimedes/chain/agent_runner.py` (~1554, 1652, 2101) still bypass the real
  commit/reveal path — the issue's own anti-goal grep is not yet 0.
- **IPFS pinning prod gap** split to **#1526**: `PINATA_JWT` never reached `ecs.tf`
  secrets, so reveal `storagePointer`s reference pins that never happened — wire it or
  remove the path.

## Evaluation provenance

Commissioned sonnet-agent evaluation (2026-08-30) reading the Mnemonik monorepo docs and
our local tree, reviewed and adjudicated by the owner session; primary sources:
`mnemonik-xyz/monorepo` `docs/how-it-works.md`, `contracts/src/ReasoningTraceRegistry.sol`,
issue #714 thread. Social note: the decline rests on technical mismatch (different
problem, different chain stack), not on the author's current inactivity.
