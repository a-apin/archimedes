// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import "./SyntheticToken.sol";
import "./PriceOracle.sol";

/// @title SyntheticVault
/// @notice Holds USDC collateral and mints/burns synthetic tokens at the oracle price.
///         Generic — works for any asset (TSLA, NVDA, SPY, BTC, GOLD, OIL, NIKKEI).
contract SyntheticVault is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── State ───────────────────────────────────────────────────────

    IERC20        public immutable usdc;
    SyntheticToken public immutable synthToken;
    PriceOracle    public immutable oracle;

    /// @notice Collateralization ratio in basis points. 12000 = 120%
    uint256 public collateralRatio = 12000;

    /// @notice Protocol fee in basis points on mint. 50 = 0.5%
    uint256 public mintFeeBps = 50;

    /// @notice Protocol fee in basis points on burn. 50 = 0.5%
    uint256 public burnFeeBps = 50;

    /// @notice Accumulated protocol fees in USDC (6 decimals)
    uint256 public protocolFees;

    uint256 public constant BPS = 10000;
    uint256 public constant SYNTH_DECIMALS = 18;

    // ─── Events ──────────────────────────────────────────────────────

    event Minted(address indexed user, uint256 usdcIn, uint256 synthOut, uint256 fee);
    event Burned(address indexed user, uint256 synthIn, uint256 usdcOut, uint256 fee);
    event CollateralRatioUpdated(uint256 oldRatio, uint256 newRatio);
    event FeesCollected(uint256 amount);
    /// @notice Emitted when a redemption is scaled down by the pro-rata solvency cap
    ///         (collateral C < liability L). `grossValue` is the uncapped current-price
    ///         claim; `cappedValue` is the C/L-scaled payout (haircut = gross - capped).
    event RedemptionHaircut(address indexed user, uint256 grossValue, uint256 cappedValue);

    // ─── Errors ──────────────────────────────────────────────────────

    error ZeroAmount();
    error InsufficientCollateral();

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(
        address _usdc,
        address _synthToken,
        address _oracle,
        address _owner
    ) Ownable(_owner) {
        usdc       = IERC20(_usdc);
        synthToken = SyntheticToken(_synthToken);
        oracle     = PriceOracle(_oracle);
    }

    // ─── User Actions ────────────────────────────────────────────────

    /// @notice Mint synth tokens by depositing USDC.
    function mint(uint256 amountUsdc) external nonReentrant returns (uint256) {
        if (amountUsdc == 0) revert ZeroAmount();

        uint256 assetPrice = oracle.getPrice();
        uint256 fee = (amountUsdc * mintFeeBps) / BPS;
        uint256 netUsdc = amountUsdc - fee;

        uint256 synthAmount = (netUsdc * (10 ** SYNTH_DECIMALS) * BPS) /
                              (assetPrice * collateralRatio);

        // Reject dust deposits that round to zero synth: integer division can
        // make synthAmount == 0 for a tiny amount at a high price, in which case
        // the user would pay USDC (including the mint fee) and receive nothing.
        // (audit 2026-06-14)
        if (synthAmount == 0) revert ZeroAmount();

        usdc.safeTransferFrom(msg.sender, address(this), amountUsdc);
        protocolFees += fee;
        synthToken.mint(msg.sender, synthAmount);

        emit Minted(msg.sender, amountUsdc, synthAmount, fee);
        return synthAmount;
    }

    /// @notice Burn synth tokens and receive USDC back.
    ///
    /// @dev SOLVENCY MECHANISM — pro-rata redemption under stress (audit 2026-06-10
    ///      finding #14, issue #509).
    ///
    ///      Mint collateralizes against the *mint-time* price (a user deposits
    ///      `collateralRatio` (120%) of the mint-price value of the synth issued), but
    ///      burn pays the *current-price* value. If the asset appreciates more than the
    ///      collateral buffer (>20% at the default ratio), total redemption liability
    ///      exceeds vault collateral. Pre-fix behavior was first-come-first-served:
    ///      early redeemers extracted full current-price value and late redeemers
    ///      reverted with `InsufficientCollateral` against an empty vault.
    ///
    ///      Design decision — option (b) pro-rata haircut, NOT option (a) raising the
    ///      collateral ratio:
    ///      * No finite ratio fixes the mechanism: because collateral is keyed to the
    ///        mint-time price, raising 120% → 150% only moves the insolvency cliff from
    ///        +20% to +50% appreciation; beyond it the FCFS drain is unchanged. (a) also
    ///        worsens capital efficiency for every healthy-state user.
    ///      * Pro-rata is order-independent in this exact code shape. When available
    ///        collateral C < total liability L (= totalSupply × price), each redemption
    ///        is scaled by C/L, i.e. a redeemer burning s of supply S receives gross
    ///        s·C/S. Collateral then falls to C·(1 − s/S) and supply to S − s, so the
    ///        per-synth payout C/S is invariant across sequential redemptions: early
    ///        redeemers cannot extract more than their pro-rata share, and the last
    ///        redeemer is paid the same rate instead of reverting. Integer division
    ///        rounds the haircut payout down, so dust errs in the vault's favor.
    ///      * When the vault is healthy (C ≥ L) the scale factor is 1 and behavior is
    ///        identical to the pre-fix path: full current-price value, minus burn fee.
    function burn(uint256 synthAmount) external nonReentrant returns (uint256) {
        if (synthAmount == 0) revert ZeroAmount();

        uint256 assetPrice = oracle.getPrice();
        uint256 usdcValue = (synthAmount * assetPrice) / (10 ** SYNTH_DECIMALS);

        // Pro-rata solvency cap: under stress, scale the claim by C/L so redemption
        // order cannot redistribute value between holders (see @dev above + _applySolvencyCap).
        uint256 grossValue = usdcValue;
        bool haircutApplied;
        (usdcValue, haircutApplied) = _applySolvencyCap(usdcValue, assetPrice);
        if (haircutApplied) {
            emit RedemptionHaircut(msg.sender, grossValue, usdcValue);
        }

        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        uint256 usdcOut = usdcValue - fee;

        // Defense-in-depth only: with the pro-rata cap above, usdcOut <= available
        // always holds (s <= S implies s·C/S <= C), so this cannot revert in practice.
        if (usdc.balanceOf(address(this)) < usdcOut + protocolFees) {
            revert InsufficientCollateral();
        }

        synthToken.burn(msg.sender, synthAmount);
        protocolFees += fee;
        usdc.safeTransfer(msg.sender, usdcOut);

        emit Burned(msg.sender, synthAmount, usdcOut, fee);
        return usdcOut;
    }

    /// @dev Apply the pro-rata solvency cap to a redemption's gross USDC claim. Under
    ///      stress (total liability L > available collateral C) the claim is scaled by
    ///      C/L so redemption order cannot redistribute value between holders (audit
    ///      #509). Shared verbatim by burn() and previewBurn() so preview == actual.
    ///      Returns the (possibly capped) value and whether the cap bound. Safe
    ///      subtraction: protocolFees should never exceed balance, but fee rounding over
    ///      many small operations could cause a transient underflow; clamp to 0.
    function _applySolvencyCap(uint256 usdcValue, uint256 assetPrice)
        internal
        view
        returns (uint256 cappedValue, bool haircutApplied)
    {
        uint256 bal = usdc.balanceOf(address(this));
        uint256 available = bal > protocolFees ? bal - protocolFees : 0;
        uint256 totalLiability = (synthToken.totalSupply() * assetPrice) / (10 ** SYNTH_DECIMALS);
        if (totalLiability > available) {
            return ((usdcValue * available) / totalLiability, true);
        }
        return (usdcValue, false);
    }

    // ─── Views ───────────────────────────────────────────────────────

    /// @notice Mirror mint()'s zero-amount guards exactly so callers can rely on
    ///         previewMint to detect inputs that would revert before submitting a tx.
    function previewMint(uint256 amountUsdc) external view returns (uint256 synthAmount) {
        if (amountUsdc == 0) revert ZeroAmount();
        uint256 assetPrice = oracle.getPrice();
        uint256 fee = (amountUsdc * mintFeeBps) / BPS;
        uint256 netUsdc = amountUsdc - fee;
        synthAmount = (netUsdc * (10 ** SYNTH_DECIMALS) * BPS) / (assetPrice * collateralRatio);
        if (synthAmount == 0) revert ZeroAmount();
    }

    function previewBurn(uint256 synthAmount) external view returns (uint256) {
        uint256 assetPrice = oracle.getPrice();
        uint256 usdcValue = (synthAmount * assetPrice) / (10 ** SYNTH_DECIMALS);

        // Mirror burn()'s pro-rata solvency cap so preview == actual under stress.
        (usdcValue, ) = _applySolvencyCap(usdcValue, assetPrice);

        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        return usdcValue - fee;
    }

    function totalCollateral() external view returns (uint256) {
        uint256 _bal = usdc.balanceOf(address(this));
        return _bal > protocolFees ? _bal - protocolFees : 0;
    }

    function vaultCollateralization() external view returns (uint256) {
        uint256 totalSynth = synthToken.totalSupply();
        if (totalSynth == 0) return type(uint256).max;
        uint256 assetPrice = oracle.getPrice();
        uint256 totalBacking = (totalSynth * assetPrice) / (10 ** SYNTH_DECIMALS);
        uint256 _colBal = usdc.balanceOf(address(this));
        uint256 collateral = _colBal > protocolFees ? _colBal - protocolFees : 0;
        return (collateral * BPS) / totalBacking;
    }

    // ─── Admin ───────────────────────────────────────────────────────

    function setCollateralRatio(uint256 newRatio) external onlyOwner {
        require(newRatio >= BPS, "ratio must be >= 100%");
        emit CollateralRatioUpdated(collateralRatio, newRatio);
        collateralRatio = newRatio;
    }

    function collectFees() external onlyOwner {
        uint256 amount = protocolFees;
        protocolFees = 0;
        usdc.safeTransfer(msg.sender, amount);
        emit FeesCollected(amount);
    }

    function depositCollateral(uint256 amount) external onlyOwner {
        usdc.safeTransferFrom(msg.sender, address(this), amount);
    }
}
