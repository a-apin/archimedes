# Arc + Circle Integration

How Archimedes uses Arc and the Circle SDK, plus pointers into the `context-arc` submodule for any Arc / Circle question.

> **Status:** Day-10 (2026-05-22).

## Arc testnet quick reference

| Field | Value |
|-------|-------|
| Chain name | Arc testnet |
| Chain ID | `5042002` (`0x4CEF52`) |
| RPC | `https://rpc.testnet.arc.network` (direct) or per-user proxy URL from `arc-canteen` |
| Explorer | <https://testnet.arcscan.app> |
| Faucet | <https://faucet.circle.com> — 20 USDC per request, refills every 2h |
| CCTP Domain | `26` |
| USDC (ERC-20, 6 decimals) | `0x3600000000000000000000000000000000000000` |
| Gas token | USDC (18 decimals native) — no ETH needed |

**Onboarding flow:** visit <https://faucet.circle.com> → connect wallet → receive 20 testnet USDC on Arc. Refills every 2 hours. USDC is the native gas token, so faucet funds cover both gas and trading. **No Arc mainnet yet** — mainnet launch, real-funds custody, and the regulatory architecture are roadmap. Testnet only means no real money is at risk, by design.

## Circle sponsor alignment

Archimedes uses Circle's sponsor tooling at three layers:

| Layer | What we use | Where it lives |
|-------|-------------|----------------|
| **Arc chain** | 10 Solidity contracts deployed on Arc testnet (AMM, vaults, oracle, trace registry) | `contracts/src/` |
| **Developer-Controlled Wallets** | `circle_signer.py` — all on-chain writes via Circle API (no raw private keys) | `backend/archimedes/chain/circle_signer.py` |
| **Arc docs + skills** | `context-arc` submodule with Circle Skills (`use-arc`, `use-smart-contract-platform`, `bridge-stablecoin`) as canonical reference | `submodules/context-arc/` |

The 10 deployed contracts:

| Contract | Purpose |
|---|---|
| `AMMPool.sol` | x*y=k AMM for synthetic asset trading |
| `AMMRouter.sol` | Swap entry / routing |
| `AssetRegistry.sol` | Strategy + asset registry |
| `PriceOracle.sol` | Oracle prices, Circle-Wallets-signed pushes |
| `ReasoningTraceRegistry.sol` | On-chain anchor for agent reasoning trace hashes |
| `SyntheticFactory.sol` | Synthetic asset minting factory |
| `SyntheticToken.sol` | ERC-20 synthetic tokens (sTSLA, sSPY, sGOLD, …) |
| `SyntheticVault.sol` | Per-synth collateral vault |
| `Vault.sol` | Core user vault (ERC-4626; multi-asset NAV via oracles since Day-10) |
| `VaultFactory.sol` | Vault deployer |

**Not yet adopted (evaluated, deferred):** Circle CLI Agent Wallets + x402 nanopayments. These require the `@circle-fin/cli` npm package and an interactive setup flow. Our existing `circle_signer.py` path (Developer-Controlled Wallets) already covers the "agent transacts on Arc" use case. Agent Wallets would add spending-policy caps and x402 service discovery, but would replace the working `circle_signer` path — too destabilizing this close to the demo. Post-hackathon consideration.

## Using the `context-arc` submodule

[`submodules/context-arc/`](../submodules/context-arc) is Circle's curated bundle of Arc + Circle developer documentation and 5 reference codebases (`arc-commerce`, `arc-escrow`, `arc-fintech`, `arc-multichain-wallet`, `arc-p2p-payments`). It is the single best place to look up anything Arc- or Circle-specific.

Start with [`submodules/context-arc/AGENTS.md`](../submodules/context-arc/AGENTS.md) for the entry-point index. Task-routed quick reference:

| Task                                            | Start with                                                            |
| ----------------------------------------------- | --------------------------------------------------------------------- |
| Anything Arc-specific (chain config, deploy)    | `circlefin-skills/use-arc.md`                                         |
| USDC transfers / balances / approvals           | `circlefin-skills/use-usdc.md`                                        |
| Cross-chain USDC (CCTP, Bridge Kit)             | `circlefin-skills/bridge-stablecoin.md`                               |
| Custodial / dev-controlled wallets              | `circlefin-skills/use-developer-controlled-wallets.md`                |
| Unified balance / nanopayments (Gateway)        | `circlefin-skills/use-gateway.md`                                     |
| Contract templates, deploy, monitor             | `circlefin-skills/use-smart-contract-platform.md`                     |
| AI agent that holds + spends USDC itself        | `docs/developers.circle.com/agent-stack.md` (Circle CLI + Agent Wallets) |
| Onchain agent identity / job settlement         | `docs/docs.arc.network/build/agentic-economy.md` (ERC-8004 / ERC-8183) |

Refresh upstream when Circle pushes new docs:

```bash
git submodule update --remote submodules/context-arc
```

Or via the canteen CLI: `arc-canteen context sync` (drops a copy into `~/.arc-canteen/context/`).

## Known limitation — AMM pool liquidity

Relocated from `README.md` (2026-08-20), with its numbers removed rather than refreshed.

The AMM pools are deployed but **thinly or not at all funded**. The synthetic-token mint
authority is a separate Foundry deployer wallet that the bootstrap script's Circle signer
cannot reach, so seeding reserves is a manual step that has only been done for a handful of
pairs. How many pools exist and which ones hold reserves is a live fact, not a doc fact —
read `GET /api/config/contracts` for the pool census and query the pool for its reserves.

What matters is the mechanism, and it is the rigor system working as designed: **the
autonomous agent's liquidity guard skips swaps into empty pools and logs the reason** rather
than routing capital into a doomed trade. A `liquidity_failure` trace is the honest outcome,
not a bug. See the failure-mode table in
[`specs/vault-semantics-spec.md`](specs/vault-semantics-spec.md).

## Related docs

- [`README.md`](../README.md) — project overview + quickstart
- [`SETUP.md`](../SETUP.md) — prerequisites + 5-step install
- [`docs/runbooks/operations.md`](runbooks/operations.md) — RPC URL deep-dive + LLM backends + security
- ``docs/archive/agora-2026-05/ARC-OSS-SHOWCASE.md`` (routed to the private docs repo, 2026-08-19) — forkable primitives for the Arc OSS Showcase competition
- [`docs/architecture.md`](architecture.md) — current deployment topology (ECS Fargate + ALB + CloudFront + WAF)
