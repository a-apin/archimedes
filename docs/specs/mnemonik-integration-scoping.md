---
status: current
owner: Dan
updated: 2026-08-30
---

# Mnemonik integration scoping — #714 sub-task C

**Verdict: adopt the protocol math, reimplement natively on Arc, take no dependency.**
Zero dependencies adopted; three design ideas promoted to work items. This memo is the
deliverable #714 sub-task C has owed since 2026-07 (originally Bogdan's; completed by
owner review of a commissioned evaluation, 2026-08-30, then revised the same day after a
second evaluation read the protocol whitepaper the first had missed — see provenance).

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

## What the whitepaper actually contributes

The first draft of this memo assessed `docs/how-it-works.md` (the Solana/Arweave/MCP
deployment) and missed `docs/WHITEPAPER.md`, where the protocol is specified
chain-agnostically. Read against the whitepaper, the "do not take a dependency" call
holds, but "nothing here is worth much" does not — three ideas are load-bearing for our
own on-chain reasoning-memory vision, and they map onto seams we already have.

1. **Integrity-bound embeddings (§1, §4.3.1).** The artifact is `A = ⟨C, R, v⃗_M, Σ⟩`
   and the quantized vector `v_q` is nested *inside* the canonical payload **before** the
   content hash is taken: `CID(A) = BLAKE3(S_canonical)`, `Σ = Sign_sk(CID(A))`. This is
   the missing primitive for semantic recall over anchored traces — the recall index
   stays untrusted while the vector remains *provably* the one that was anchored. Our
   `models/trace.py` canonicalization (`_HASH_FIELDS` → `canonical_json` → `keccak`) is
   the same shape, **one field short**.
2. **Lineage by embedded parent hash (§3.1-III, §13.5).** `A_t` carries `CID(A_{t-1})`;
   cycles rejected by BFS at ingest. Our `consulted_paper_hashes` is already half of this
   DAG — the reasoning-descends-from-these-papers edge we never modelled structurally.
3. **Batch roots (§5.6.1) and quantized ranking (§4.3.2, §13.2).** One Merkle anchor per
   batch of traces; 4-bit quantization retains 98.2% top-10 recall at 87.5% of the memory
   — which is exactly what makes carrying a vector inside a hashed payload affordable.

Also noted, not yet scoped: **κ capability tokens** (scope = lineage-subtree ∩ kind ∩
tags ∩ ids; `read|list|share-onward|quote` bitmask; cross-owner share = ECDH + dual-signed
receipt anchored back into the DAG) are the right shape for a future cross-user trace
market, and the **`FRAME` isolation-marker** stage is a prompt-injection defense we
currently lack on recalled text.

## Phased path (native, no dependency)

**v0 — recall, no integrity claim.** `embedding vector(384)` + `embedding_model` on traces
and `debate_transcripts` (alembic `5728d9ef1901`), reusing `paper_rag._rerank_with_embeddings`.
MiniLM runs locally: embedding our ~10k-item corpus is CPU-minutes, cents of compute, no
API spend. Recall over our own traces only; nothing in the UI claims verification yet.

**v1 — integrity-bound.** `embedding_digest = keccak(model_id ‖ quantized_vector)` carried
in `ReasoningTraceRegistry`'s existing free-form `metadata` bytes (`publishTrace:103`, or
`commit`'s `tradeIntentSummary:114`) or `Commitment.storagePointer` at `reveal:190` —
**no contract change**. **Do not add the digest to `_HASH_FIELDS`** — that breaks
`verifyTrace` keccak parity for every trace already anchored. Add `parent_trace_hash` for
lineage.

**v2 — open verification.** Batch-root anchoring, κ-scoped cross-user query, `FRAME`
markers on any recalled trace before it enters a prompt.

## The dependency call is unchanged (and the optional seam, if ever wanted)

Everything in **Why it does not fit our commit-reveal need** still stands: we take **no
Mnemonik dependency** — different chain stack (Solana/Arweave vs Arc), no Python SDK,
unaudited single-maintainer, and a different core problem (public authorship-timestamping
vs our hide-then-reveal commitment). We reimplement the *ideas* natively: keccak not
BLAKE3, JSON not CBOR, Arc not Solana, pgvector/SQLite not their RecallIndex, SIWE not
COSE/Ed25519.

If a live seam is ever wanted anyway: a best-effort **secondary attestation only** — after
our own `reveal()` succeeds on Arc, a sibling of `ipfs_publisher.py` POSTs the same
canonical public-provenance bytes to Mnemonik's hosted MCP endpoint
(`https://mcp.mnemonik.xyz/mcp`, `mnemonic_sign_memory`) as a redundant, independently
anchored timestamp copy. Never load-bearing for any UI claim (fail-soft principle), behind
its own default-off flag, failure logged and ignored. Not recommended now.

## Higher-leverage residuals in #714 (do these instead)

- **3 legacy `trace_publisher.publish()` call sites** in
  `backend/archimedes/chain/agent_runner.py` (~1554, 1652, 2101) still bypass the real
  commit/reveal path — the issue's own anti-goal grep is not yet 0.
- **IPFS pinning prod gap** split to **#1526**: `PINATA_JWT` never reached `ecs.tf`
  secrets, so reveal `storagePointer`s reference pins that never happened — wire it or
  remove the path.

## Evaluation provenance

Two evaluations, both 2026-08-30, both owner-adjudicated:

1. **First pass** (sonnet) read `docs/how-it-works.md` (the Solana/Arweave/MCP deployment)
   and our local tree, and concluded "borrow patterns, do not integrate." Its
   *integration* conclusion — no dependency — was correct and survives.
2. **Re-review** (opus, commissioned after the owner flagged that the protocol's value
   lives in its math) read the document the first pass missed: `docs/WHITEPAPER.md` §1,
   §3.1, §4.2–4.3.2, §5.6.1, §5.7, §13.1–13.5, and `docs/spec/memory-composition.md`. It
   found the integrity-bound-embedding primitive and revised the *significance* conclusion
   upward — hence the current verdict and the phased path above.

Primary sources: `mnemonik-xyz/monorepo` `docs/WHITEPAPER.md` + `docs/how-it-works.md`;
local `contracts/src/ReasoningTraceRegistry.sol`, `backend/archimedes/models/trace.py`,
`backend/archimedes/services/paper_rag.py`, `origin/dbrowneup/debate-transcript-capture`;
issue #714 thread. Social note: the decline-to-depend rests on technical mismatch
(different problem, different chain stack), not on the author's current inactivity — and
the ideas are judged on their merits independent of it.
