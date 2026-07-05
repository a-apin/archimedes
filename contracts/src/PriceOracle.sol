// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable.sol";

/// @notice Minimal Chainlink price-feed interface (T1.3 — Chainlink-first read path).
///         Declared locally rather than pulling in the chainlink/contracts package
///         so we add no new submodule dependency for one interface. This is the canonical
///         AggregatorV3Interface signature — Chainlink feeds (and Arc-native /
///         Chainlink-compatible aggregators) implement it verbatim.
///         Reference: https://docs.chain.link/data-feeds/api-reference
interface AggregatorV3Interface {
    /// @return The number of decimals the feed answer is reported in (USD feeds: 8).
    function decimals() external view returns (uint8);

    /// @notice Latest completed round of price data.
    /// @return roundId         The round ID the answer was computed in.
    /// @return answer          The price (signed; non-negative for a healthy feed).
    /// @return startedAt       Timestamp the round started.
    /// @return updatedAt       Timestamp the answer was last updated (staleness key).
    /// @return answeredInRound The round in which the answer was computed (legacy
    ///                         carry-over detector; answeredInRound < roundId means
    ///                         the answer is stale carried from a prior round).
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound);
}

/// @title PriceOracle
/// @notice Per-asset price oracle for any asset on Arc testnet. Prefers a Chainlink
///         `AggregatorV3Interface` feed when one is configured (T1.3); falls back to
///         the admin-fed value (backend oracle runner pushes via `setPrice`) for
///         assets that have no native Chainlink feed.
/// @dev    ⚠️ Funds-adjacent: this price flows straight into Vault / SyntheticVault
///         collateral math (`getPrice()` → 6-decimal USDC price). Read-path changes
///         here need careful contract review (Dan owns contracts; Bogdan reviews).
///
///         Read-path precedence (see `getPrice()`):
///           1. Chainlink feed (if `priceFeed != address(0)`): read, validated (round
///              completeness + staleness + future-timestamp), scaled to 6 decimals, and
///              checked against the admin sanity band.
///           2. Admin-fed `price`: used when no feed is configured, AND as the automatic
///              DEGRADE target when a configured feed is unreadable / stale / invalid /
///              out-of-band. getPrice() only reverts when BOTH sources are unusable.
///         The no-arg `getPrice()` signature is preserved so every existing consumer
///         (Vault, SyntheticVault, SyntheticFactory) and the backend keep working.
contract PriceOracle is Ownable {
    /// @notice Asset price in USDC (6 decimals). e.g. $392.60 → 392600000.
    ///         This is the *admin-fed* value. With a Chainlink feed configured getPrice()
    ///         PREFERS the feed — but `price` is still consulted: as the sanity-band
    ///         reference, and as the value returned when the feed degrades (unreadable /
    ///         stale / invalid / out-of-band). Kept public + named `price` so the backend's
    ///         on-chain reference read (`.price()`) keeps working.
    uint256 public price;

    /// @notice Human-readable label (e.g. "TSLA", "NVDA", "SPY")
    string public symbol;

    /// @notice Timestamp of the last price update
    uint256 public lastUpdated;

    /// @notice Maximum age before the admin-fed price is stale (24 hours —
    ///         hackathon testnet, Circle daily push limit).
    /// @dev    TESTNET/HACKATHON TRADEOFF — MUST be tightened before mainnet.
    ///         24h is deliberately loose so NAV, deposits and withdrawals never
    ///         brick between the (currently daily) admin price pushes. It is far
    ///         too loose for value-moving operations: SyntheticVault.mint/burn
    ///         enforce their own tighter `mintBurnMaxStaleness` (default 1h) so a
    ///         stale admin price can't be arbitraged (#910). On mainnet, wire a
    ///         Chainlink feed (guarded by the 1h `feedStaleness` heartbeat) and
    ///         drop this window to match.
    uint256 public constant MAX_STALENESS = 24 hours;

    /// @notice Address allowed to push price updates (e.g. Circle wallet)
    address public updater;

    /// @notice Chainlink (or Chainlink-compatible) price feed for this asset (T1.3).
    ///         When non-zero, `getPrice()` reads `latestRoundData()` from this feed
    ///         instead of the admin-fed `price`. Owner-set via `setPriceFeed`; set
    ///         back to `address(0)` to revert to the admin-fed fallback.
    AggregatorV3Interface public priceFeed;

    /// @notice Target decimals for the returned price — USDC's 6 decimals, matching
    ///         the admin-fed `price` convention and every consumer's math. Chainlink
    ///         USD feeds report 8 decimals, so the feed answer is rescaled to this.
    uint8 public constant PRICE_DECIMALS = 6;

    /// @notice feed.decimals() cached at setPriceFeed time. Read once at config so the
    ///         hot read path makes a single external call (latestRoundData) and never
    ///         trusts a (potentially upgradeable/mutable) feed to report stable decimals
    ///         across calls — TOCTOU hardening (#724 review). 0 when no feed is set.
    uint8 public feedDecimals;

    /// @notice Per-feed staleness heartbeat (default 1h). The admin path's 24h
    ///         MAX_STALENESS is far too loose for a LIVE market feed — a Chainlink feed
    ///         frozen for hours must read as stale on a funds path. Owner-tunable, bounded
    ///         by MAX_STALENESS. (#724 review fix 3.)
    uint256 public feedStaleness = 1 hours;

    /// @notice Sanity band (bps) between a feed answer and the last FRESH admin price.
    ///         A mis-pointed / wrong-denomination / compromised feed is typically off by
    ///         orders of magnitude; if a feed deviates more than this from a fresh admin
    ///         reference, getPrice() degrades to the admin value rather than repricing
    ///         every vault off the bad feed (the #724-review HIGH: the feed path otherwise
    ///         bypasses all of the admin path's #587 guards). 0 disables the band (for
    ///         assets with no maintained admin reference). Default 5000 (50%) — generous
    ///         for normal volatility between admin pushes, tight enough to catch gross feed
    ///         errors. NOTE: only applied while the admin price is itself fresh (see getPrice).
    uint256 public maxFeedDeviationBps = 5000;

    /// @notice Timestamp of the last *setPrice* update (0 until the first one).
    ///         Distinct from lastUpdated (which the constructor seeds) so the
    ///         updateCooldown applies only between two actual updater pushes —
    ///         not between deploy and the first push. forceSetPrice does not
    ///         touch this field; the cooldown only governs the setPrice path.
    uint256 public lastSetPriceTime;

    /// @notice Max allowed move per setPrice, in basis points vs the prior price.
    ///         Default 2000 bps (20%) — generous for a daily-update cadence but
    ///         blocks fat-finger / glitched-feed / compromised-key writes that
    ///         would otherwise flow straight into Vault.totalAssets().
    ///         Owner-configurable via setMaxDeviationBps; for a legitimately
    ///         gapped market the owner can use forceSetPrice (two-step escape
    ///         hatch) so the oracle can never be bricked permanently.
    uint256 public maxDeviationBps = 2000;

    /// @notice Basis-point denominator (100% = 10_000 bps)
    uint256 public constant BPS_DENOMINATOR = 10_000;

    /// @notice Hard cap on single forceSetPrice moves (900% = 10× max change per call).
    ///         Prevents pathological oracle manipulation; legitimate >10× gaps require
    ///         two sequential calls. Unlike setPrice's maxDeviationBps this constant is
    ///         not owner-configurable — it exists precisely to bound a compromised key.
    uint256 public constant FORCE_MAX_DEVIATION_BPS = 90_000;

    /// @notice Minimum spacing between two consecutive setPrice calls, in seconds.
    ///         setPrice already bounds a single move to maxDeviationBps, but with no
    ///         spacing an attacker could chain N max-deviation calls in one block to
    ///         ratchet the price arbitrarily (issue #587). Requiring a gap of at least
    ///         updateCooldown seconds between accepted updates blocks that intra-block
    ///         chaining and limits a compromised key to one bounded move per window,
    ///         giving the owner time to react (rotate the key / forceSetPrice).
    ///         Owner-configurable via setUpdateCooldown; forceSetPrice (owner escape
    ///         hatch) is exempt so the owner can always reprice out-of-band.
    ///         Default 30s — well above Arc's sub-second block time (so it defeats the
    ///         same-block ratchet this guards against) yet comfortably below the oracle
    ///         runner's 60s push cadence (ORACLE_INTERVAL_SECONDS), so legitimate
    ///         updates never trip it. Operators who slow the on-chain push cadence can
    ///         raise this toward MAX_UPDATE_COOLDOWN for a stronger rate limit.
    uint256 public updateCooldown = 30;

    /// @notice Upper bound on the owner-configurable cooldown (1 hour). Prevents the
    ///         owner from setting a cooldown so long that legitimate daily updates are
    ///         blocked, while still allowing the bound to be tuned for the deploy cadence.
    uint256 public constant MAX_UPDATE_COOLDOWN = 1 hours;

    event PriceUpdated(uint256 oldPrice, uint256 newPrice, uint256 timestamp);
    event PriceForced(uint256 oldPrice, uint256 newPrice, uint256 timestamp);
    event MaxDeviationBpsChanged(uint256 oldBps, uint256 newBps);
    event UpdaterChanged(address oldUpdater, address newUpdater);
    event UpdateCooldownChanged(uint256 oldCooldown, uint256 newCooldown);
    event PriceFeedChanged(address oldFeed, address newFeed);
    event FeedStalenessChanged(uint256 oldStaleness, uint256 newStaleness);
    event MaxFeedDeviationBpsChanged(uint256 oldBps, uint256 newBps);

    error StalePrice();
    error UnauthorizedUpdater();
    error ZeroPrice();
    error PriceDeviationTooLarge(uint256 oldPrice, uint256 newPrice, uint256 maxBps);
    error InvalidDeviationBound();
    error UpdateRateLimited(uint256 lastUpdated, uint256 cooldown, uint256 nowTs);
    error InvalidCooldown();
    // ── Chainlink read-path errors (T1.3) ──────────────────────────
    error InvalidFeedDecimals(uint8 decimals); // feed decimals would overflow scaling
    error InvalidFeedStaleness(); // feedStaleness must be in (0, MAX_STALENESS]

    modifier onlyUpdater() {
        if (msg.sender != owner() && msg.sender != updater) revert UnauthorizedUpdater();
        _;
    }

    constructor(string memory _symbol, uint256 _initialPrice, address _owner) Ownable(_owner) {
        symbol = _symbol;
        price = _initialPrice;
        lastUpdated = block.timestamp;
        updater = _owner;
        emit PriceUpdated(0, _initialPrice, block.timestamp);
    }

    /// @notice Push a new price. Bounded: rejects zero and any move larger
    ///         than maxDeviationBps vs the prior price. If the market truly
    ///         gapped beyond the bound, the owner uses forceSetPrice (or
    ///         widens the bound via setMaxDeviationBps).
    function setPrice(uint256 _newPrice) external onlyUpdater {
        if (_newPrice == 0) revert ZeroPrice();
        // Rate-limit: require at least updateCooldown seconds between two
        // accepted setPrice pushes (issue #587). Skipped on the very first
        // push (lastSetPriceTime == 0). Combined with the per-call deviation
        // bound, this caps a compromised updater key to one bounded move per
        // cooldown window — it cannot chain many max-deviation calls in a block
        // to ratchet the price. forceSetPrice (owner escape hatch) is exempt.
        if (lastSetPriceTime != 0 && block.timestamp < lastSetPriceTime + updateCooldown) {
            revert UpdateRateLimited(lastSetPriceTime, updateCooldown, block.timestamp);
        }
        uint256 oldPrice = price;
        // Deviation bound only applies once a prior price exists; a zero
        // prior price (bootstrap) accepts any positive first price.
        if (oldPrice != 0) {
            uint256 diff = _newPrice > oldPrice ? _newPrice - oldPrice : oldPrice - _newPrice;
            if (diff * BPS_DENOMINATOR > oldPrice * maxDeviationBps) {
                revert PriceDeviationTooLarge(oldPrice, _newPrice, maxDeviationBps);
            }
        }
        price = _newPrice;
        lastUpdated = block.timestamp;
        lastSetPriceTime = block.timestamp;
        emit PriceUpdated(oldPrice, _newPrice, block.timestamp);
    }

    /// @notice Emergency override — owner-only escape hatch for legitimately
    ///         gapped markets (e.g. a >maxDeviationBps overnight move). Bounded
    ///         by FORCE_MAX_DEVIATION_BPS (900% / 10×) when a prior price exists;
    ///         a legitimate >10× gap requires two sequential calls. Emits a
    ///         distinct event so forced updates are auditable on-chain.
    function forceSetPrice(uint256 _newPrice) external onlyOwner {
        if (_newPrice == 0) revert ZeroPrice();
        uint256 oldPrice = price;
        if (oldPrice != 0) {
            uint256 diff = _newPrice > oldPrice ? _newPrice - oldPrice : oldPrice - _newPrice;
            if (diff * BPS_DENOMINATOR > oldPrice * FORCE_MAX_DEVIATION_BPS) {
                revert PriceDeviationTooLarge(oldPrice, _newPrice, FORCE_MAX_DEVIATION_BPS);
            }
        }
        price = _newPrice;
        lastUpdated = block.timestamp;
        emit PriceForced(oldPrice, _newPrice, block.timestamp);
    }

    /// @notice Adjust the per-update deviation bound. Must be in (0, 10_000];
    ///         a zero bound would brick setPrice entirely and >100% is
    ///         meaningless (use forceSetPrice for gap recovery instead).
    function setMaxDeviationBps(uint256 _maxDeviationBps) external onlyOwner {
        if (_maxDeviationBps == 0 || _maxDeviationBps > BPS_DENOMINATOR) revert InvalidDeviationBound();
        uint256 old = maxDeviationBps;
        maxDeviationBps = _maxDeviationBps;
        emit MaxDeviationBpsChanged(old, _maxDeviationBps);
    }

    /// @notice Adjust the minimum spacing between setPrice calls (issue #587).
    ///         Bounded above by MAX_UPDATE_COOLDOWN so the owner cannot set a
    ///         cooldown long enough to block legitimate daily updates. A value
    ///         of 0 disables rate-limiting (the per-call deviation bound still
    ///         applies) — a deliberate, owner-only choice.
    function setUpdateCooldown(uint256 _cooldown) external onlyOwner {
        if (_cooldown > MAX_UPDATE_COOLDOWN) revert InvalidCooldown();
        uint256 old = updateCooldown;
        updateCooldown = _cooldown;
        emit UpdateCooldownChanged(old, _cooldown);
    }

    function setUpdater(address _updater) external onlyOwner {
        address old = updater;
        updater = _updater;
        emit UpdaterChanged(old, _updater);
    }

    /// @notice Configure (or clear) the Chainlink feed for this asset (T1.3).
    ///         Owner-only. Pass `address(0)` to disable the feed and fall back to
    ///         the admin-fed `price`. When a non-zero feed is set, its `decimals()`
    ///         is validated up front so a feed that would overflow the scaling math
    ///         (decimals > 36) is rejected at configuration time rather than on read.
    /// @dev    ⚠️ Funds-adjacent: pointing this at the wrong feed silently reprices
    ///         every vault that reads this oracle. Verify the feed address +
    ///         denomination (must be the asset/USD pair) before calling. Does NOT
    ///         re-validate the feed's *answer* here (a feed can be healthy at config
    ///         time and stale later) — staleness is enforced on every `getPrice()`.
    function setPriceFeed(address _feed) external onlyOwner {
        uint8 d;
        if (_feed != address(0)) {
            // Probe decimals() so a non-conforming or overflow-prone feed can't be
            // wired in. PRICE_DECIMALS (6) + 36 keeps the up-scale (10 ** (6 - d))
            // and down-scale (10 ** (d - 6)) factors well inside uint256. The value is
            // CACHED (feedDecimals) so the hot read path never re-reads it (TOCTOU + gas).
            d = AggregatorV3Interface(_feed).decimals();
            if (d > 36) revert InvalidFeedDecimals(d);
        }
        address old = address(priceFeed);
        priceFeed = AggregatorV3Interface(_feed);
        feedDecimals = d; // 0 when the feed is cleared
        emit PriceFeedChanged(old, _feed);
    }

    /// @notice Tune the per-feed staleness heartbeat (#724 review). Must be in
    ///         (0, MAX_STALENESS] — 0 would make every feed read stale; above the
    ///         24h admin bound is pointless. Owner-only.
    function setFeedStaleness(uint256 _seconds) external onlyOwner {
        if (_seconds == 0 || _seconds > MAX_STALENESS) revert InvalidFeedStaleness();
        uint256 old = feedStaleness;
        feedStaleness = _seconds;
        emit FeedStalenessChanged(old, _seconds);
    }

    /// @notice Tune the feed-vs-admin sanity band in bps, or set 0 to disable it for
    ///         assets with no maintained admin reference (#724 review). Owner-only.
    ///         Capped at FORCE_MAX_DEVIATION_BPS (10×) — a band looser than that is
    ///         no band at all.
    function setMaxFeedDeviationBps(uint256 _bps) external onlyOwner {
        if (_bps > FORCE_MAX_DEVIATION_BPS) revert InvalidDeviationBound();
        uint256 old = maxFeedDeviationBps;
        maxFeedDeviationBps = _bps;
        emit MaxFeedDeviationBpsChanged(old, _bps);
    }

    /// @notice Current asset price in USDC 6-decimal units.
    ///         Prefers the Chainlink feed when configured (T1.3) and it reads clean +
    ///         within the admin sanity band; otherwise DEGRADES to the admin-fed `price`
    ///         (which keeps its own staleness check). Only reverts when BOTH sources are
    ///         unusable — a bad feed degrades rather than bricking every consumer.
    /// @dev    Signature unchanged (no-arg) so Vault / SyntheticVault / SyntheticFactory
    ///         keep compiling and behaving identically. (#724 review: fixes 1 + 2.)
    function getPrice() external view returns (uint256) {
        // The admin-fed value is the trusted fallback reference; freshness computed once.
        bool adminFresh = block.timestamp <= lastUpdated + MAX_STALENESS;

        if (address(priceFeed) != address(0)) {
            (bool ok, uint256 feedPrice) = _tryReadChainlink();
            if (ok) {
                // Sanity band: with a FRESH trusted admin reference, reject a feed that
                // grossly deviates from it (catches a mis-pointed / wrong-denomination /
                // compromised feed before it reprices vaults — the feed path has none of
                // the admin path's #587 deviation guards). With no fresh reference, no
                // admin price, or the band disabled, we cannot sanity-check, so we trust
                // the feed's own round-data + heartbeat validation.
                if (maxFeedDeviationBps == 0 || !adminFresh || price == 0) {
                    return feedPrice;
                }
                uint256 diff = feedPrice > price ? feedPrice - price : price - feedPrice;
                // Overflow-proof, revert-free sanity band (#724 review round-3). BOTH factors
                // of the band multiply are unbounded: `diff` can be a malicious huge feedPrice,
                // and `price` is admin-set + ratchetable upward over time via forceSetPrice. So
                // (a) the bps multiply sits on `price` (putting it on the `diff` subtraction
                // would overflow `diff * BPS_DENOMINATOR`), and (b) we GUARD that multiply
                // explicitly — if even `price * maxFeedDeviationBps` would exceed uint256, the
                // admin reference is itself absurd, so we DEGRADE (treat the feed as out of
                // band) rather than overflow-revert and brick getPrice. maxFeedDeviationBps > 0
                // here (the ==0 disable returned above), so the division guard cannot divide
                // by zero.
                if (
                    price <= type(uint256).max / maxFeedDeviationBps
                        && diff <= (price * maxFeedDeviationBps) / BPS_DENOMINATOR
                ) {
                    return feedPrice; // in band → trust the live feed
                }
                // out of band (or band uncomputable) → fall through and degrade to admin
            }
            // feed unreadable / invalid / out-of-band → degrade to the admin fallback.
            // NOT a hard revert: a bad feed must not brick deposits, withdrawals, NAV,
            // or rebalances. The admin value below still enforces its own staleness.
        }

        if (!adminFresh) revert StalePrice();
        return price;
    }

    /// @notice True when the *active* price source is usable: the feed reads clean, or
    ///         (when the feed is absent/bad) the admin fallback is fresh. View-safe and
    ///         never reverts — no external self-call (#724 review). Note: this does not
    ///         re-check the sanity band; it reports source liveness, not band agreement.
    function isFresh() external view returns (bool) {
        bool adminFresh = block.timestamp <= lastUpdated + MAX_STALENESS;
        if (address(priceFeed) != address(0)) {
            (bool ok,) = _tryReadChainlink();
            return ok || adminFresh; // feed clean, or degrade-target (admin) still fresh
        }
        return adminFresh;
    }

    /// @notice Read + validate + scale the Chainlink feed answer to 6 decimals WITHOUT
    ///         reverting: returns (false, 0) on any problem so getPrice() can degrade to
    ///         the admin fallback instead of bricking every consumer (#724 review fix 1).
    /// @dev    Fail-soft → (false, 0) on: the feed call itself reverting (paused /
    ///         self-destructed / non-conforming); answer <= 0; incomplete round
    ///         (updatedAt == 0); a FUTURE updatedAt (malformed round metadata);
    ///         carried-over round (answeredInRound < roundId); stale
    ///         beyond the per-feed heartbeat (now - updatedAt > feedStaleness — fix 3);
    ///         or a nonzero answer that floors to 0 after down-scaling (fix 4). Uses the
    ///         decimals CACHED at config time — no per-read decimals() call (TOCTOU + gas).
    ///
    ///         SEQUENCER UPTIME (#724 review, scoped OUT, documented not silent): the
    ///         Chainlink L2 Sequencer Uptime Feed pattern targets single-sequencer
    ///         optimistic/zk rollups. Arc is Circle's USDC-settlement chain (sub-second
    ///         finality, permissioned validators) and Chainlink publishes no Arc uptime
    ///         feed, so the canonical mitigation is unwireable; the underlying "stale
    ///         price survives a restart" risk is covered by the degrade + tight heartbeat.
    function _tryReadChainlink() internal view returns (bool ok, uint256 scaledPrice) {
        try priceFeed.latestRoundData() returns (
            uint80 roundId, int256 answer, uint256, uint256 updatedAt, uint80 answeredInRound
        ) {
            if (answer <= 0) return (false, 0);
            if (updatedAt == 0) return (false, 0);
            if (updatedAt > block.timestamp) return (false, 0); // future timestamp = malformed round metadata
            if (answeredInRound < roundId) return (false, 0);
            if (block.timestamp > updatedAt + feedStaleness) return (false, 0);

            uint256 raw = uint256(answer); // safe: answer > 0 checked above
            uint8 d = feedDecimals; // cached at setPriceFeed — no per-read external call
            uint256 scaled;
            if (d == PRICE_DECIMALS) {
                scaled = raw;
            } else if (d > PRICE_DECIMALS) {
                scaled = raw / (10 ** (d - PRICE_DECIMALS)); // e.g. 8-dec USD feed → /100 (division: no overflow)
            } else {
                // Up-scale (sub-6-decimal feed). A malicious huge answer could overflow
                // raw * factor and REVERT inside the try — uncaught by the catch, bricking
                // getPrice. Bounds-check so it fails SOFT (degrade) instead (#724 review).
                uint256 factor = 10 ** (PRICE_DECIMALS - d);
                if (raw > type(uint256).max / factor) return (false, 0);
                scaled = raw * factor;
            }
            if (scaled == 0) return (false, 0); // nonzero answer floored to 0 by scaling
            return (true, scaled);
        } catch {
            return (false, 0); // feed reverted / missing → degrade to admin
        }
    }
}
