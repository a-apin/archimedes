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

        usdc.safeTransferFrom(msg.sender, address(this), amountUsdc);
        protocolFees += fee;
        synthToken.mint(msg.sender, synthAmount);

        emit Minted(msg.sender, amountUsdc, synthAmount, fee);
        return synthAmount;
    }

    /// @notice Burn synth tokens and receive USDC back.
    function burn(uint256 synthAmount) external nonReentrant returns (uint256) {
        if (synthAmount == 0) revert ZeroAmount();

        uint256 assetPrice = oracle.getPrice();
        uint256 usdcValue = (synthAmount * assetPrice) / (10 ** SYNTH_DECIMALS);
        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        uint256 usdcOut = usdcValue - fee;

        if (usdc.balanceOf(address(this)) < usdcOut + protocolFees) {
            revert InsufficientCollateral();
        }

        synthToken.burn(msg.sender, synthAmount);
        protocolFees += fee;
        usdc.safeTransfer(msg.sender, usdcOut);

        emit Burned(msg.sender, synthAmount, usdcOut, fee);
        return usdcOut;
    }

    // ─── Views ───────────────────────────────────────────────────────

    function previewMint(uint256 amountUsdc) external view returns (uint256) {
        uint256 assetPrice = oracle.getPrice();
        uint256 fee = (amountUsdc * mintFeeBps) / BPS;
        uint256 netUsdc = amountUsdc - fee;
        return (netUsdc * (10 ** SYNTH_DECIMALS) * BPS) / (assetPrice * collateralRatio);
    }

    function previewBurn(uint256 synthAmount) external view returns (uint256) {
        uint256 assetPrice = oracle.getPrice();
        uint256 usdcValue = (synthAmount * assetPrice) / (10 ** SYNTH_DECIMALS);
        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        return usdcValue - fee;
    }

    function totalCollateral() external view returns (uint256) {
        return usdc.balanceOf(address(this)) - protocolFees;
    }

    function vaultCollateralization() external view returns (uint256) {
        uint256 totalSynth = synthToken.totalSupply();
        if (totalSynth == 0) return type(uint256).max;
        uint256 assetPrice = oracle.getPrice();
        uint256 totalBacking = (totalSynth * assetPrice) / (10 ** SYNTH_DECIMALS);
        uint256 collateral = usdc.balanceOf(address(this)) - protocolFees;
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
