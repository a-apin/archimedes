// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import "./SyntheticTSLA.sol";
import "./TSLAPriceOracle.sol";

/// @title SyntheticVault
/// @notice Holds USDC collateral and mints/burns sTSLA tokens at the oracle price.
///         Users deposit USDC → receive sTSLA. Users burn sTSLA → receive USDC.
///         Collateralization ratio is configurable (default 120%).
contract SyntheticVault is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── State ───────────────────────────────────────────────────────

    IERC20    public immutable usdc;
    SyntheticTSLA  public immutable sTSLA;
    TSLAPriceOracle public immutable oracle;

    /// @notice Collateralization ratio in basis points. 12000 = 120%
    uint256 public collateralRatio = 12000;

    /// @notice Protocol fee in basis points on mint. 50 = 0.5%
    uint256 public mintFeeBps = 50;

    /// @notice Protocol fee in basis points on burn. 50 = 0.5%
    uint256 public burnFeeBps = 50;

    /// @notice Accumulated protocol fees in USDC (6 decimals)
    uint256 public protocolFees;

    uint256 public constant BPS = 10000;
    uint256 public constant USDC_DECIMALS = 6;
    uint256 public constant STSLA_DECIMALS = 18;

    // ─── Events ──────────────────────────────────────────────────────

    event Minted(address indexed user, uint256 usdcIn, uint256 sTslaOut, uint256 fee);
    event Burned(address indexed user, uint256 sTslaIn, uint256 usdcOut, uint256 fee);
    event CollateralRatioUpdated(uint256 oldRatio, uint256 newRatio);
    event FeesCollected(uint256 amount);
    event PriceOracleUpdated(address oldOracle, address newOracle);

    // ─── Errors ──────────────────────────────────────────────────────

    error ZeroAmount();
    error StalePrice();
    error InsufficientCollateral();
    error InsufficientBalance();
    error TransferFailed();

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(
        address _usdc,
        address _sTSLA,
        address _oracle,
        address _owner
    ) Ownable(_owner) {
        usdc   = IERC20(_usdc);
        sTSLA  = SyntheticTSLA(_sTSLA);
        oracle = TSLAPriceOracle(_oracle);
    }

    // ─── User Actions ────────────────────────────────────────────────

    /// @notice Mint sTSLA by depositing USDC.
    ///         amountUsdc = how much USDC to deposit (6 decimals).
    ///         Returns the amount of sTSLA minted (18 decimals).
    function mint(uint256 amountUsdc) external nonReentrant returns (uint256) {
        if (amountUsdc == 0) revert ZeroAmount();

        // Get fresh price
        uint256 tslaPrice = oracle.getPrice(); // 6 decimals

        // Calculate fee
        uint256 fee = (amountUsdc * mintFeeBps) / BPS;
        uint256 netUsdc = amountUsdc - fee;

        // sTSLA amount = netUsdc / price * collateralRatio
        // Convert: netUsdc (6 dec) / price (6 dec) * 10^18 * BPS / collateralRatio
        uint256 sTslaAmount = (netUsdc * (10 ** STSLA_DECIMALS) * BPS) /
                              (tslaPrice * collateralRatio);

        // Transfer USDC from user
        usdc.safeTransferFrom(msg.sender, address(this), amountUsdc);

        // Accumulate fee
        protocolFees += fee;

        // Mint sTSLA to user
        sTSLA.mint(msg.sender, sTslaAmount);

        emit Minted(msg.sender, amountUsdc, sTslaAmount, fee);
        return sTslaAmount;
    }

    /// @notice Burn sTSLA and receive USDC back.
    ///         amountSTsla = how many sTSLA to burn (18 decimals).
    ///         Returns the amount of USDC returned (6 decimals).
    function burn(uint256 sTslaAmount) external nonReentrant returns (uint256) {
        if (sTslaAmount == 0) revert ZeroAmount();

        // Get fresh price
        uint256 tslaPrice = oracle.getPrice(); // 6 decimals

        // USDC value of sTSLA:
        // usdcValue = sTslaAmount * price / 10^18  (convert 18 dec → 6 dec)
        uint256 usdcValue = (sTslaAmount * tslaPrice) / (10 ** STSLA_DECIMALS);

        // Calculate fee
        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        uint256 usdcOut = usdcValue - fee;

        // Check vault has enough USDC
        if (usdc.balanceOf(address(this)) < usdcOut + protocolFees) {
            revert InsufficientCollateral();
        }

        // Burn sTSLA from user
        sTSLA.burn(msg.sender, sTslaAmount);

        // Accumulate fee
        protocolFees += fee;

        // Transfer USDC to user
        usdc.safeTransfer(msg.sender, usdcOut);

        emit Burned(msg.sender, sTslaAmount, usdcOut, fee);
        return usdcOut;
    }

    // ─── Views ───────────────────────────────────────────────────────

    /// @notice How much sTSLA you'd get for a given USDC amount
    function previewMint(uint256 amountUsdc) external view returns (uint256) {
        uint256 tslaPrice = oracle.getPrice();
        uint256 fee = (amountUsdc * mintFeeBps) / BPS;
        uint256 netUsdc = amountUsdc - fee;
        return (netUsdc * (10 ** STSLA_DECIMALS) * BPS) /
               (tslaPrice * collateralRatio);
    }

    /// @notice How much USDC you'd get for burning sTSLA
    function previewBurn(uint256 sTslaAmount) external view returns (uint256) {
        uint256 tslaPrice = oracle.getPrice();
        uint256 usdcValue = (sTslaAmount * tslaPrice) / (10 ** STSLA_DECIMALS);
        uint256 fee = (usdcValue * burnFeeBps) / BPS;
        return usdcValue - fee;
    }

    /// @notice Total USDC collateral in the vault (excluding protocol fees)
    function totalCollateral() external view returns (uint256) {
        return usdc.balanceOf(address(this)) - protocolFees;
    }

    /// @notice Collateralization ratio of the entire vault
    function vaultCollateralization() external view returns (uint256) {
        uint256 totalSTsla = sTSLA.totalSupply();
        if (totalSTsla == 0) return type(uint256).max;

        uint256 tslaPrice = oracle.getPrice();
        uint256 totalBacking = (totalSTsla * tslaPrice) / (10 ** STSLA_DECIMALS);
        uint256 collateral = usdc.balanceOf(address(this)) - protocolFees;

        return (collateral * BPS) / totalBacking;
    }

    // ─── Admin ───────────────────────────────────────────────────────

    /// @notice Update collateral ratio
    function setCollateralRatio(uint256 newRatio) external onlyOwner {
        require(newRatio >= BPS, "ratio must be >= 100%");
        emit CollateralRatioUpdated(collateralRatio, newRatio);
        collateralRatio = newRatio;
    }

    /// @notice Withdraw accumulated protocol fees
    function collectFees() external onlyOwner {
        uint256 amount = protocolFees;
        protocolFees = 0;
        usdc.safeTransfer(msg.sender, amount);
        emit FeesCollected(amount);
    }

    /// @notice Owner can deposit additional USDC to increase collateral
    function depositCollateral(uint256 amount) external onlyOwner {
        usdc.safeTransferFrom(msg.sender, address(this), amount);
    }
}
