// SPDX-License-Identifier: Unlicense
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import "./interfaces/IAMMPool.sol";

/// @title AMMPool
/// @notice Uniswap V2-style constant-product AMM pool (x·y = k).
///         Each pool pairs two ERC-20 tokens. LP tokens are minted as ERC-20.
contract AMMPool is IAMMPool, ERC20, Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── State ───────────────────────────────────────────────────────

    address public immutable override token0;
    address public immutable override token1;

    uint256 public override reserve0;
    uint256 public override reserve1;

    uint16 public immutable override swapFeeBps;

    uint256 private _totalSupply;

    /// @notice First-depositor LP-inflation guard (same Option-B mitigation as
    ///         Vault.sol). On the first deposit MIN_LIQUIDITY LP tokens are
    ///         minted to an unrecoverable sink, so an attacker can no longer own
    ///         ~100% of a dust LP supply and donate tokens to inflate NAV per
    ///         share and round later mints to zero. Cost: the first LP forfeits
    ///         MIN_LIQUIDITY LP-wei — negligible.
    uint256 public constant MIN_LIQUIDITY = 1e3;

    /// @notice Sink for the locked dead shares (OZ ERC20 forbids minting to address(0)).
    address public constant DEAD_SHARES_SINK = address(0xdEaD);

    // ─── Errors ──────────────────────────────────────────────────────

    error SameToken();
    error ZeroAmount();
    error InsufficientLiquidity();
    error InsufficientOutput();
    error InvalidToken();
    error SlippageProtection();

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(
        address _token0,
        address _token1,
        uint16 _swapFeeBps,
        address _owner
    ) ERC20("Archimedes LP Token", "ARCH-LP") Ownable(_owner) {
        if (_token0 == _token1) revert SameToken();
        token0 = _token0;
        token1 = _token1;
        swapFeeBps = _swapFeeBps;
    }

    // ─── ERC-20 overrides ────────────────────────────────────────────

    function totalSupply() public view override(ERC20, IAMMPool) returns (uint256) {
        return ERC20.totalSupply();
    }

    // ─── Swap ────────────────────────────────────────────────────────

    /// @notice Swap one token for another. Caller must have approved this contract.
    /// @param tokenIn      Token being sold.
    /// @param amountIn     Exact amount of tokenIn to sell.
    /// @param to           Recipient of the output tokens.
    /// @param minAmountOut Minimum acceptable output; reverts with SlippageProtection
    ///                     if the computed amountOut is below this threshold.
    ///                     Pass 0 to skip the check (e.g. when the caller already
    ///                     enforces slippage at a higher layer, as AMMRouter does).
    function swap(address tokenIn, uint256 amountIn, address to, uint256 minAmountOut)
        external
        nonReentrant
        returns (uint256 amountOut)
    {
        if (amountIn == 0) revert ZeroAmount();

        bool isToken0 = tokenIn == token0;
        if (!isToken0 && tokenIn != token1) revert InvalidToken();

        (uint256 rIn, uint256 rOut) = isToken0
            ? (reserve0, reserve1)
            : (reserve1, reserve0);

        // Constant-product with fee: amountOut = (rOut * amountIn * (BPS - fee)) / (rIn * BPS + amountIn * (BPS - fee))
        uint256 amountInWithFee = amountIn * (10000 - swapFeeBps);
        amountOut = (rOut * amountInWithFee) / (rIn * 10000 + amountInWithFee);

        if (amountOut == 0) revert InsufficientOutput();
        if (amountOut >= rOut) revert InsufficientLiquidity();
        if (amountOut < minAmountOut) revert SlippageProtection();

        uint256 kBefore = rIn * rOut;

        // Update reserves
        if (isToken0) {
            reserve0 += amountIn;
            reserve1 -= amountOut;
        } else {
            reserve1 += amountIn;
            reserve0 -= amountOut;
        }

        // Constant-product invariant: fees mean k must never shrink across a
        // swap. Guards against any future fee-math change silently leaking value.
        if (reserve0 * reserve1 < kBefore) revert InsufficientOutput();

        // Transfer tokens
        address tokenOut = isToken0 ? token1 : token0;
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);
        IERC20(tokenOut).safeTransfer(to, amountOut);

        emit Swap(msg.sender, tokenIn, amountIn, tokenOut, amountOut);
    }

    // ─── Liquidity ───────────────────────────────────────────────────

    /// @notice Add liquidity to the pool. First depositor sets the ratio.
    function addLiquidity(
        uint256 amount0,
        uint256 amount1,
        address to
    ) external nonReentrant returns (uint256 lpTokens) {
        if (amount0 == 0 || amount1 == 0) revert ZeroAmount();

        uint256 _total = totalSupply();

        if (_total == 0) {
            // First deposit: geometric mean, minus a permanently-locked
            // MIN_LIQUIDITY minted to the dead sink (inflation-attack guard).
            uint256 minted = _sqrt(amount0 * amount1);
            if (minted <= MIN_LIQUIDITY) revert InsufficientLiquidity();
            _mint(DEAD_SHARES_SINK, MIN_LIQUIDITY);
            lpTokens = minted - MIN_LIQUIDITY;
        } else {
            // Subsequent deposits: min of the two ratios
            uint256 lp0 = (amount0 * _total) / reserve0;
            uint256 lp1 = (amount1 * _total) / reserve1;
            lpTokens = lp0 < lp1 ? lp0 : lp1;
        }

        if (lpTokens == 0) revert InsufficientLiquidity();

        // Update reserves
        reserve0 += amount0;
        reserve1 += amount1;

        // Transfer tokens from sender
        IERC20(token0).safeTransferFrom(msg.sender, address(this), amount0);
        IERC20(token1).safeTransferFrom(msg.sender, address(this), amount1);

        // Mint LP tokens
        _mint(to, lpTokens);

        emit LiquidityAdded(msg.sender, amount0, amount1, lpTokens);
    }

    /// @notice Remove liquidity by burning LP tokens.
    function removeLiquidity(uint256 lpTokens, address to)
        external
        nonReentrant
        returns (uint256 amount0, uint256 amount1)
    {
        if (lpTokens == 0) revert ZeroAmount();

        uint256 _total = totalSupply();

        amount0 = (reserve0 * lpTokens) / _total;
        amount1 = (reserve1 * lpTokens) / _total;

        if (amount0 == 0 || amount1 == 0) revert InsufficientLiquidity();

        // Update reserves
        reserve0 -= amount0;
        reserve1 -= amount1;

        // Burn LP tokens
        _burn(msg.sender, lpTokens);

        // Transfer tokens to sender
        IERC20(token0).safeTransfer(to, amount0);
        IERC20(token1).safeTransfer(to, amount1);

        emit LiquidityRemoved(msg.sender, amount0, amount1, lpTokens);
    }

    /// @notice Emitted when sync() folds a direct-transfer surplus into the reserves.
    event Sync(uint256 reserve0, uint256 reserve1);

    /// @notice Force the tracked reserves to match the pool's actual token balances.
    /// @dev    Reserves are updated only inside swap/addLiquidity/removeLiquidity, so
    ///         tokens transferred DIRECTLY to the pool (not via addLiquidity) are
    ///         otherwise stranded — invisible to pricing and unrecoverable. sync()
    ///         folds any such surplus into the reserves, where it accrues to every LP
    ///         (k grows); it never extracts value. Permissionless, mirroring Uniswap
    ///         V2's Pair.sync (issue #955).
    function sync() external nonReentrant {
        reserve0 = IERC20(token0).balanceOf(address(this));
        reserve1 = IERC20(token1).balanceOf(address(this));
        emit Sync(reserve0, reserve1);
    }

    // ─── Views ───────────────────────────────────────────────────────

    function getAmountOut(address tokenIn, uint256 amountIn)
        external
        view
        override
        returns (uint256 amountOut)
    {
        if (amountIn == 0) return 0;

        bool isToken0 = tokenIn == token0;
        if (!isToken0 && tokenIn != token1) return 0;

        (uint256 rIn, uint256 rOut) = isToken0
            ? (reserve0, reserve1)
            : (reserve1, reserve0);

        if (rIn == 0 || rOut == 0) return 0;

        uint256 amountInWithFee = amountIn * (10000 - swapFeeBps);
        amountOut = (rOut * amountInWithFee) / (rIn * 10000 + amountInWithFee);
    }

    function getSpotPrice(address, address) external pure override returns (uint256 price) {
        // Simplified for hackathon — would need oracle integration for real spot price
        return 0;
    }

    // ─── Internal ────────────────────────────────────────────────────

    function _sqrt(uint256 y) internal pure returns (uint256 z) {
        if (y > 3) {
            z = y;
            uint256 x = y / 2 + 1;
            while (x < z) {
                z = x;
                x = (y / x + x) / 2;
            }
        } else if (y != 0) {
            z = 1;
        }
    }
}
