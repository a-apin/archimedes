# ADR: Arc as the settlement chain, USDC as the settlement asset

> **Audience:** Archimedes team
> **Status:** Accepted
> **Date:** 2026-05-13 (Agora hackathon; Arc + Circle are the sponsor stack) — exact commit date [unestablished — needs Dan]
> **Owner:** Dan Browne
> **Supersedes:** —
> **Superseded-by:** —
> **Question being decided:** Which chain settles vault deposits, trades and provenance anchors, and in what asset?
> **Related:** [`docs/arc-integration.md`](../arc-integration.md), [`backend/archimedes/chain/client.py:117`](../../backend/archimedes/chain/client.py), [`ui/public/.well-known/agent.json`](../../ui/public/.well-known/agent.json), `backend/archimedes/chain/circle_signer.py`, [`docs/archive/agora-2026-05/arc-alignment.md`](../archive/agora-2026-05/arc-alignment.md).

## TL;DR

**Arc testnet (chain ID `5042002`) is the settlement chain and USDC is both the settlement
asset and the native gas token.** On-chain writes go through Circle's
Developer-Controlled Wallets (`circle_signer.py`) rather than raw private keys; passkey
vault deploys route through the Circle bundler. **There is no Arc mainnet yet** — every
public claim must say testnet.

## Context

The product needs a chain for three distinct jobs: **settlement** (vault deposits,
redemptions, AMM trades in a stable unit), **provenance anchoring** (reasoning-trace and
strategy hashes, commit-reveal), and **synthetic asset collateral**. Those jobs have
different requirements, but one property dominates all of them: a portfolio product
denominated in a volatile gas token is a portfolio product with an uncontrolled position in
that token.

Arc's distinguishing property is that **USDC *is* the native gas token** (18 decimals, no
ETH needed — [`docs/arc-integration.md`](../arc-integration.md)). That collapses three
problems at once:

- **No gas-token exposure.** A vault denominated in USDC pays fees in USDC. There is no
  second asset to hold, hedge or explain.
- **Onboarding is one drip.** <https://faucet.circle.com> gives 20 USDC per request
  (refills every 2h), and that single balance funds both gas and trading. On a two-token
  chain a user who arrives with the trading asset but no gas token is a failed demo.
- **The unit of account is the unit of settlement.** Performance, fees and NAV are all in
  the same asset the chain charges in.

The context was also the Agora hackathon, where Arc and Circle were the sponsor stack —
that is a real and disclosable input to the decision, not an accident. But the USDC-as-gas
property is the reason the choice survived the hackathon.

## Decision

1. **Arc testnet, chain ID `5042002`** (`0x4cef52`) is the settlement chain
   ([`chain/client.py:117`](../../backend/archimedes/chain/client.py); published in the
   agent manifest at [`ui/public/.well-known/agent.json`](../../ui/public/.well-known/agent.json)).
2. **USDC is the settlement asset and the gas token.** Vault collateral, synthetic
   collateral, AMM quote asset and fees are all USDC.
3. **All on-chain writes go through Circle Developer-Controlled Wallets**
   (`backend/archimedes/chain/circle_signer.py`) — the backend holds no raw private keys.
   Vault deploys for passkey wallets route through the Circle bundler
   (`f277f14`, "[frontend][chain] Route vault deploy through the Circle bundler for passkey
   wallets").
4. **`submodules/context-arc` is the canonical Arc/Circle reference** — Circle's curated
   docs bundle plus five reference codebases — rather than re-deriving Arc behaviour from
   prose.
5. **Testnet is stated, never elided.** The honest framing is fixed: this is testnet, funded
   by faucet USDC, no real funds, by design.
6. **Circle CLI Agent Wallets and x402 nanopayments were evaluated and deferred.** They
   would add spending-policy caps and service discovery but would replace the working
   `circle_signer` Developer-Controlled-Wallets path — judged too destabilizing close to the
   demo; a post-hackathon consideration.

## Consequences

### Positive
- **No gas-token exposure in a portfolio product.** This is the load-bearing consequence.
- **One-asset onboarding**, which is the difference between a working demo and a failed one.
- **No raw private keys in the backend.** Key custody is Circle's problem, and the
  compromise surface is an API credential rather than a signing key.
- **Native alignment with the settlement story** — "USDC settlement on Arc with on-chain
  reasoning-trace anchoring" is one sentence because it is one chain and one asset.

### Negative / costs we accept
- **Testnet only.** There is no Arc mainnet, so there is no path to real funds today. Every
  performance and vault claim is a testnet claim and must be labelled as such. This is the
  single largest gap between what the architecture supports and what a user can actually do.
- **Single-chain lock-in.** Vaults, synthetics, the AMM and the registries all assume one
  chain. Cross-chain RWA bridging via CCTP/Gateway was scoped and deferred. Moving or adding
  a chain later is a contract-deployment and address-wiring exercise across the whole tree
  (see the T3.2 address-wiring work, `6e92d27` / `82c5779`).
- **Circle is a dependency on the write path.** If the Circle API is unavailable, on-chain
  writes stop — including oracle pushes. This trades key-custody risk for availability risk,
  deliberately.
- **Faucet-rate-limited demos.** 20 USDC / 2h bounds what a demo or a test run can do.
- **The chain ID and contract addresses are configuration**, and they have drifted before —
  which is why addresses are served from `GET /api/config/contracts` rather than restated in
  prose.

## Alternatives considered
- **An EVM L2 with a volatile gas token (Base, Arbitrum, etc.) — rejected.** Forces every
  vault to hold a second, volatile asset purely to transact, and forces two-token
  onboarding. The gas-exposure problem is not solvable by UX.
- **Ethereum mainnet — rejected** on cost: per-rebalance gas for a continuously
  rebalancing agent is prohibitive, and the provenance anchoring is write-heavy by design.
- **No chain / off-chain ledger — rejected.** It would discard the two claims the product is
  built on: non-custodial custody and externally verifiable provenance.
- **Circle Agent Wallets + x402 — deferred, not rejected.** Real added value (spending
  policies, service discovery); the cost is replacing a working signer path.
