# Spec: On-chain quorum price oracle (Stork + Pyth + Chainlink-ready), pricing-only

> **Status:** Proposed (2026-06-30). Funds-adjacent contract change — **Dan approves as
> contract owner; Bogdan (`mnemonik-dev`) two-eyes review. DO NOT MERGE until both.**
> **Builds on:** [`docs/adr/chainlink-primary-oracle.md`](../adr/chainlink-primary-oracle.md)
> (#724, merged — single-feed Chainlink-first read path) and the Stork `AggregatorV3`
> adapter (#828, held). **Decision owner:** Dan. **Author:** Claude (Dan's session).

## TL;DR

Generalize the merged single-feed [`PriceOracle`](../../contracts/src/PriceOracle.sol)
into an **on-chain N-feed quorum medianizer**: read every configured
`AggregatorV3Interface` source for an asset, validate each with the *exact* #724 round/
staleness/scaling guards, and return the **median of the fresh ones** — requiring **≥2
fresh feeds that agree within a band** (fail-closed on divergence), degrading cleanly to
single-feed-plus-admin-crosscheck and then to the bounded admin fallback when fewer feeds
are live. This removes the single-oracle trust point for *pricing* without touching
*settlement* (which stays mainnet-deferred per the North Star).

## Why now (the Arc × Chainlink unlock, verified 2026-06-30)

- **Arc joined Chainlink Scale today.** Verified against the Arc Core announcement + the
  Arc×Chainlink blog: **what shipped with addresses on testnet today is CCIP only** (Router
  `0xdE4E7FED43FAC37EB21aA0643d9852f75332eab8`, chain selector `3034092155422581607`, ARM
  proxy, registries). **Data Feeds + Data Streams are announced *capability* — no Arc
  price-feed aggregator addresses are published yet.** So per the #1 rule we do **not** claim
  "priced by Chainlink on Arc" today; we make the read path *Chainlink-ready* and wire feeds
  the moment Arc publishes them.
- **Stork + Pyth price feeds ARE verified live on Arc** (Stork has published Arc EVM
  addresses; Pyth on-chain pull is deployed on Arc). → **a real ≥2-feed quorum is achievable
  today** (Stork + Pyth), with Chainlink as the third leg on publish.
- **North Star alignment:** *price on-chain now, settle on-chain only at mainnet.* This is
  the **pricing** layer (real on testnet) — it does **not** introduce real settlement. "Don't
  go full send": build the quorum core + the live legs; leave heavy mainnet hardening as
  marked TODOs.

## Design

### Drop-in surface (zero consumer churn)

The live consumers — `SyntheticVault` (`PriceOracle immutable oracle; oracle.getPrice()`),
`Vault` (`PriceOracle(tokenOracle[t]).getPrice()`), `SyntheticFactory` — all cast to the
concrete `PriceOracle` type and call the **no-arg `getPrice() → uint256` (6-dec USDC)**.
`QuorumPriceOracle is PriceOracle`, so those casts resolve to the override with **no consumer
change** and the contract IS-A `PriceOracle` everywhere one is expected (e.g.
`SyntheticVault`'s constructor).

### Minimal, behavior-preserving change to the audited #724 contract

1. **Extract** the per-feed validation body of `_tryReadChainlink()` into
   `_tryReadFeed(AggregatorV3Interface feed, uint8 dec) internal view returns (bool ok,
   uint256 scaled)` (round completeness, `answer>0`, future-timestamp, `answeredInRound`,
   per-feed heartbeat, overflow-safe 6-dec scaling — unchanged). `_tryReadChainlink()`
   becomes `return _tryReadFeed(priceFeed, feedDecimals);`. Pure extraction — guarded by the
   existing `PriceOracleChainlink.t.sol`.
2. **Mark `getPrice()` `virtual`** so the subclass overrides it. (One keyword.)

No other change to #724. All admin-path guards (`maxDeviationBps`, `updateCooldown`,
`forceSetPrice`/`FORCE_MAX_DEVIATION_BPS`, `MAX_STALENESS`, the sanity band) are **inherited
intact** and back the quorum's degrade target.

### `QuorumPriceOracle is PriceOracle` — the medianizer

State (additive):
- `AggregatorV3Interface[] public feeds; uint8[] public feedDecs;` — the quorum source set
  (each entry is a Chainlink feed, the Stork adapter, or a Pyth adapter — all present
  `AggregatorV3Interface`). Owner-managed via `addFeed(address)` / `removeFeed(uint256)`
  (decimals probed + cached at add time, exactly like `setPriceFeed`).
- `quorumBandBps` (default e.g. 200 = 2%) — max spread `(max−min)/median` for the fresh feeds
  to be considered *agreeing*. Distinct from the inherited `maxFeedDeviationBps` (feed-vs-admin
  band). Owner-tunable, bounded.

`getPrice()` override — **N-aware tiers, fail-closed**:
- **n ≥ 2 fresh** (`_tryReadFeed` over `feeds`): sort, take the **median**; if
  `(max−min)/median ≤ quorumBandBps` → **return median** (tier `QUORUM`). Else the feeds
  *disagree* → **fail-closed**: do not trust a divergent set; degrade to the admin fallback
  if it is fresh, else `revert StalePrice()`. (We refuse to price off feeds that don't agree.)
- **n == 1 fresh**: return it, cross-checked against the fresh admin reference via the
  inherited `maxFeedDeviationBps` band (the existing #724 single-feed behavior) (tier
  `SINGLE_CHECKED`).
- **n == 0**: bounded admin fallback — inherited `price` + `MAX_STALENESS` (tier `ADMIN`);
  `revert StalePrice()` if the admin price is also stale.

`priceWithProvenance() external view returns (uint256 price, uint8 tier, uint256 nFresh)` —
the **claims-true provenance surface**: which tier priced and how many feeds were fresh, for
telemetry, the UI badge, and the #759 `oracle_tier` mapping. (On-chain fact, not a cached
boolean.)

### Median + agreement (small-N, gas-trivial)
Feed count is tiny (2–3). Insertion-sort the fresh scaled values; median = middle (odd) or
mean-of-two-middles (even); spread = `(max−min)`. All arithmetic overflow-checked the same
revert-free way as #724's band (guard the `median * bandBps` multiply; degrade rather than
overflow-revert).

## Live configuration today → tomorrow (claims-true)

| Asset coverage | Feeds wired today | Tier today | On Chainlink-Arc publish |
|---|---|---|---|
| Stork **and** Pyth cover it | Stork adapter + Pyth adapter | **QUORUM** (2-of-2) | add Chainlink → 2-of-3 |
| Only one of Stork/Pyth | that one | SINGLE_CHECKED | add the others → QUORUM |
| Neither (long tail) | none | ADMIN (bounded) | add when a feed exists |

Adding Chainlink later is **one `addFeed` call per asset** — no code change, no redeploy of
consumers. This is the forward-compatibility that makes "first to build the Chainlink-on-Arc
quorum" honest: the path is live and Chainlink-ready; the Chainlink leg lights up on publish.
Maps directly onto the #759 universe `oracle_tier` field.

## Pyth as an `AggregatorV3` leg

Pyth's native interface is pull-based (`IPyth.getPriceNoOlderThan`), not `AggregatorV3`.
Two honest options, both leave the medianizer untouched (it consumes any `AggregatorV3`
address):
1. **Preferred:** deploy Pyth's official `PythAggregatorV3` wrapper
   (`@pythnetwork/pyth-sdk-solidity`) per asset — audited upstream, least new trusted code.
2. Thin in-repo `PythAggregatorV3Adapter` mirroring the #828 Stork adapter, if we want a
   single adapter style. (Carries its own review burden — prefer (1) for a funds path.)
The PR ships tests against a mock `AggregatorV3` for both, and documents the deploy-config
choice; the **adapter address is a deploy-time config detail, not a code dependency.**

## Scope boundary — build now vs mainnet-defer ("don't go full send")

**Build now:** the `_tryReadFeed` extraction + `virtual` on #724; `QuorumPriceOracle`
(feeds set, median, tiers, fail-closed, provenance view); Foundry tests (median, 2-of-2
agree, divergence fail-closed, 1-feed cross-check, 0-feed admin, tier reporting, overflow
guards); this spec + an ADR-update note; the deploy/config runbook for wiring Stork+Pyth.

**Mainnet TODO (marked, not built):** wiring the actual Chainlink Arc feed addresses (none
published yet); N≥3 outlier rejection beyond median+band; gas-tuned sort; per-source
heartbeat tuning against real cadence; **any settlement change** (explicitly out of scope —
this is pricing only).

## Anti-goals
- **No settlement change.** Vaults still don't do real on-chain settlement on testnet
  (North Star §6). This changes only the *price* a vault reads.
- **No weakening of #724's guards.** The admin fallback keeps every deviation/cooldown/
  staleness bound; the quorum *adds* a stricter on-chain path, it does not relax the existing
  one.
- **No claim of "priced by Chainlink on Arc"** until Arc publishes feed addresses and we wire
  + verify them. The honest claim today is "live Stork+Pyth on-chain quorum, Chainlink-ready."
- **Don't mutate the no-arg `getPrice()` selector** — consumer drop-in compat is load-bearing.

## Verify (reviewer commands)
- `cd contracts && forge test --match-contract QuorumPriceOracle -vvv` → all pass.
- `forge test --match-contract PriceOracleChainlink` → still green (refactor preserved #724).
- `grep -n "virtual" contracts/src/PriceOracle.sol` → only `getPrice` (+ extracted helper) marked.
- Provenance: a 2-of-2 agreeing read returns tier `QUORUM`; a divergent read returns admin/
  revert, never a feed value.
