// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "./PriceOracle.sol";

/// @title QuorumPriceOracle
/// @notice On-chain N-feed **quorum** price oracle: reads every configured
///         `AggregatorV3Interface` source (e.g. Chainlink Data Feed, the Stork
///         `AggregatorV3` adapter, a Pyth `AggregatorV3` wrapper), validates each with the
///         *exact* #724 round/staleness/scaling guards (inherited `_tryReadFeed`), and
///         returns the **median of the fresh ones** — requiring **≥2 fresh feeds that agree
///         within a band** (fail-closed on divergence), degrading cleanly to a single-feed
///         admin-cross-checked read and then to the bounded admin fallback as fewer feeds
///         are live.
/// @dev    ⚠️ Funds-adjacent (Dan owns contracts; Bogdan reviews). IS-A {PriceOracle}, so it
///         drops in anywhere a `PriceOracle` is expected (Vault / SyntheticVault /
///         SyntheticFactory all cast to the concrete type and call the no-arg
///         `getPrice() → uint256`) with **zero consumer change**. Every audited admin-path
///         guard (`maxDeviationBps`, `updateCooldown`, `forceSetPrice` / `MAX_STALENESS`,
///         the feed-vs-admin sanity band) is inherited intact and backs the degrade target.
///
///         **Pricing only — NOT settlement.** This changes only the *price* a vault reads.
///         No settlement behavior changes (testnet trading stays paper/simulated per the
///         North Star). Spec: docs/specs/onchain-quorum-oracle-spec.md.
///
///         **Feed management.** Quorum sources are managed via {addFeed} / {removeFeed};
///         the inherited single `priceFeed` (`setPriceFeed`) is **ignored** by this
///         contract's `getPrice()` override — use `addFeed`, not `setPriceFeed`, on a
///         QuorumPriceOracle. The inherited admin-fed `price` remains the bounded fallback.
contract QuorumPriceOracle is PriceOracle {
    /// @notice Pricing tiers reported by {priceWithProvenance} — an on-chain provenance fact
    ///         (which source priced the asset), not a cached boolean.
    uint8 public constant TIER_ADMIN = 0; // no fresh on-chain feed → bounded admin fallback
    uint8 public constant TIER_SINGLE = 1; // exactly 1 fresh feed → feed + admin sanity band
    uint8 public constant TIER_QUORUM = 2; // ≥2 fresh feeds that agree → median

    /// @notice Minimum fresh feeds required to form a quorum (median of ≥2 agreeing feeds).
    uint256 public constant MIN_QUORUM = 2;

    /// @notice The quorum source set. Each entry presents `AggregatorV3Interface` — a
    ///         Chainlink feed, the Stork adapter (#828/#794), or a Pyth `AggregatorV3`
    ///         wrapper. Owner-managed via {addFeed} / {removeFeed}.
    AggregatorV3Interface[] public feeds;

    /// @notice `feeds[i].decimals()` cached at add time (parallel to `feeds`), so the hot
    ///         read path makes a single external call per feed (`latestRoundData`) and never
    ///         trusts a feed to report stable decimals across calls — same TOCTOU hardening
    ///         as the inherited single-feed `feedDecimals` (#724).
    uint8[] public feedDecs;

    /// @notice Max spread `(max−min)/median` (bps) for the fresh feeds to be considered
    ///         *agreeing* and thus eligible for the median. Distinct from the inherited
    ///         `maxFeedDeviationBps` (feed-vs-admin band). Default 200 (2%) — tight enough
    ///         that a single compromised/mis-pointed feed in the set drags the spread out of
    ///         band and forces a fail-closed degrade rather than poisoning the median.
    ///         0 → require exact agreement. Owner-tunable, bounded by BPS_DENOMINATOR.
    uint256 public quorumBandBps = 200;

    event FeedAdded(address indexed feed, uint8 decimals, uint256 count);
    event FeedRemoved(address indexed feed, uint256 count);
    event QuorumBandBpsChanged(uint256 oldBps, uint256 newBps);

    error ZeroFeed();
    error FeedAlreadyAdded(address feed);
    error IndexOutOfBounds(uint256 index, uint256 length);

    constructor(string memory _symbol, uint256 _initialPrice, address _owner)
        PriceOracle(_symbol, _initialPrice, _owner)
    {}

    // ── Feed-set management (owner-only) ──────────────────────────────────

    /// @notice Add a quorum feed. Probes + caches `decimals()` (bounded ≤36 like
    ///         {setPriceFeed}); rejects the zero address and duplicates.
    /// @dev    ⚠️ Funds-adjacent: adding a wrong/mis-denominated feed lets a bad source into
    ///         the median. Verify the feed address + asset/USD denomination before calling.
    function addFeed(address feed) external onlyOwner {
        if (feed == address(0)) revert ZeroFeed();
        uint256 len = feeds.length;
        for (uint256 i; i < len; ++i) {
            if (address(feeds[i]) == feed) revert FeedAlreadyAdded(feed);
        }
        uint8 d = AggregatorV3Interface(feed).decimals();
        if (d > 36) revert InvalidFeedDecimals(d);
        feeds.push(AggregatorV3Interface(feed));
        feedDecs.push(d);
        emit FeedAdded(feed, d, feeds.length);
    }

    /// @notice Remove the feed at `index` (swap-and-pop; order not preserved). Lets the
    ///         owner retire a deprecated/compromised feed without redeploying.
    function removeFeed(uint256 index) external onlyOwner {
        uint256 len = feeds.length;
        if (index >= len) revert IndexOutOfBounds(index, len);
        address removed = address(feeds[index]);
        feeds[index] = feeds[len - 1];
        feedDecs[index] = feedDecs[len - 1];
        feeds.pop();
        feedDecs.pop();
        emit FeedRemoved(removed, feeds.length);
    }

    /// @notice Tune the quorum agreement band (bps), or 0 to require exact agreement.
    ///         Owner-only; bounded by BPS_DENOMINATOR (a band ≥100% is no band at all).
    function setQuorumBandBps(uint256 bps) external onlyOwner {
        if (bps > BPS_DENOMINATOR) revert InvalidDeviationBound();
        uint256 old = quorumBandBps;
        quorumBandBps = bps;
        emit QuorumBandBpsChanged(old, bps);
    }

    /// @notice Number of configured quorum feeds.
    function feedCount() external view returns (uint256) {
        return feeds.length;
    }

    // ── Read path (the quorum) ────────────────────────────────────────────

    /// @notice Current asset price in USDC 6-decimal units, via the quorum.
    ///         Reverts ({StalePrice}) only when no tier can produce a usable price.
    /// @dev    Signature identical to {PriceOracle.getPrice} (no-arg) — drop-in for every
    ///         consumer. Overrides the inherited single-feed path with the N-feed quorum.
    function getPrice() external view override returns (uint256) {
        (uint256 p,,, bool usable) = _resolve();
        if (!usable) revert StalePrice();
        return p;
    }

    /// @notice Non-reverting provenance read for telemetry / UI / the #759 `oracle_tier`
    ///         mapping: the price, which tier produced it, how many feeds were fresh, and
    ///         whether the result is usable. Claims-true: this is the on-chain fact of how
    ///         the asset was priced, not a label.
    function priceWithProvenance()
        external
        view
        returns (uint256 priceUsdc, uint8 tier, uint256 nFresh, bool usable)
    {
        return _resolve();
    }

    /// @notice True when {getPrice} would return (any tier produces a usable price).
    function isFresh() external view override returns (bool) {
        (,,, bool usable) = _resolve();
        return usable;
    }

    // ── Internals ─────────────────────────────────────────────────────────

    /// @dev The N-aware tiered resolution. Pure of side effects; both {getPrice} and
    ///      {priceWithProvenance} read through it so the price and its provenance can never
    ///      disagree. Fail-closed: a divergent ≥2-feed set is NEVER priced off the feeds.
    function _resolve()
        internal
        view
        returns (uint256 resolvedPrice, uint8 tier, uint256 nFresh, bool usable)
    {
        bool adminFresh = block.timestamp <= lastUpdated + MAX_STALENESS;

        // Collect the fresh, valid on-chain feed reads (each via the inherited #724 guard).
        uint256 len = feeds.length;
        uint256[] memory vals = new uint256[](len);
        uint256 n;
        for (uint256 i; i < len; ++i) {
            (bool ok, uint256 p) = _tryReadFeed(feeds[i], feedDecs[i]);
            if (ok) {
                vals[n] = p;
                ++n;
            }
        }
        nFresh = n;

        // ── Tier QUORUM: ≥2 fresh feeds ──────────────────────────────────
        if (n >= MIN_QUORUM) {
            (uint256 med, uint256 lo, uint256 hi) = _median(vals, n);
            if (_agree(med, hi - lo)) {
                return (med, TIER_QUORUM, n, true); // agree → median is the price
            }
            // Feeds DISAGREE → fail-closed: refuse to price off a divergent set.
            // Degrade to the bounded admin fallback if fresh; else unusable.
            if (adminFresh) return (price, TIER_ADMIN, n, true);
            return (0, TIER_QUORUM, n, false);
        }

        // ── Tier SINGLE: exactly 1 fresh feed (the #724 single-feed behavior) ──
        if (n == 1) {
            uint256 feedPrice = vals[0];
            if (maxFeedDeviationBps == 0 || !adminFresh || price == 0) {
                return (feedPrice, TIER_SINGLE, 1, true); // nothing to cross-check against
            }
            uint256 diff = feedPrice > price ? feedPrice - price : price - feedPrice;
            // Overflow-proof, revert-free feed-vs-admin band (mirrors #724 getPrice).
            if (price <= type(uint256).max / maxFeedDeviationBps && diff <= (price * maxFeedDeviationBps) / BPS_DENOMINATOR)
            {
                return (feedPrice, TIER_SINGLE, 1, true); // in band → trust the live feed
            }
            if (adminFresh) return (price, TIER_ADMIN, 1, true); // out of band → degrade
            return (0, TIER_SINGLE, 1, false);
        }

        // ── Tier ADMIN: no fresh feed → bounded admin fallback ───────────
        if (adminFresh) return (price, TIER_ADMIN, 0, true);
        return (0, TIER_ADMIN, 0, false);
    }

    /// @dev Are the fresh feeds in agreement? `spread = max − min`; agree when
    ///      `spread ≤ median * quorumBandBps / BPS`. Overflow-guarded + revert-free: if even
    ///      `median * quorumBandBps` would exceed uint256 the median is absurd, so we treat
    ///      it as DISAGREE (degrade) rather than overflow-revert and brick the read.
    function _agree(uint256 med, uint256 spread) internal view returns (bool) {
        if (quorumBandBps == 0) return spread == 0; // band 0 → require exact agreement
        if (med > type(uint256).max / quorumBandBps) return false; // median absurd → disagree
        return spread <= (med * quorumBandBps) / BPS_DENOMINATOR;
    }

    /// @dev Median + (min, max) of the first `n` entries of `vals` (n ≥ 1). Insertion sort
    ///      on a copy — feed counts are tiny (2–3), so this is gas-trivial and avoids
    ///      mutating the caller's array. Even n → mean of the two middles (overflow-safe
    ///      `a + (b−a)/2`).
    function _median(uint256[] memory vals, uint256 n)
        internal
        pure
        returns (uint256 med, uint256 lo, uint256 hi)
    {
        uint256[] memory a = new uint256[](n);
        for (uint256 i; i < n; ++i) {
            a[i] = vals[i];
        }
        for (uint256 i = 1; i < n; ++i) {
            uint256 key = a[i];
            uint256 j = i;
            while (j > 0 && a[j - 1] > key) {
                a[j] = a[j - 1];
                --j;
            }
            a[j] = key;
        }
        lo = a[0];
        hi = a[n - 1];
        if (n % 2 == 1) {
            med = a[n / 2];
        } else {
            uint256 left = a[n / 2 - 1];
            uint256 right = a[n / 2];
            med = left + (right - left) / 2;
        }
    }
}
