// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title PaymentSplitter
/// @notice On-chain 90/10 USDC splitter for marketplace payments.
///         Ported from Vyper (contracts/vyper/PaymentSplitter.vy).
///         Permissionless deposit and withdrawal — a stopped pool must still drain.
contract PaymentSplitter is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── Types ───────────────────────────────────────────────────────

    struct Pool {
        address creator;
        address platform;
        uint256 total_collected;
        uint256 total_disbursed;
        uint256 held_balance;
        bool active;
    }

    // ─── State ───────────────────────────────────────────────────────

    mapping(bytes32 => Pool) public pools;
    IERC20 public usdc;

    // ─── Events ──────────────────────────────────────────────────────

    event PoolCreated(bytes32 indexed pool_id, address indexed creator, address indexed platform);
    event PoolFunded(bytes32 indexed pool_id, address indexed funder, uint256 amount);
    event PaymentSplit(bytes32 indexed pool_id, uint256 amount, uint256 creator_share, uint256 platform_share);

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(address _usdc) Ownable(msg.sender) {
        usdc = IERC20(_usdc);
    }

    // ─── Owner-only ──────────────────────────────────────────────────

    /// @notice Create a new pool. Reverts if the pool_id already exists (even if deactivated).
    ///         Deterministic pool_id from (strategy_id, creator) means a deactivated pool
    ///         cannot be re-created — funds remain recoverable via withdraw.
    function createPool(bytes32 poolId, address creator, address platform) external onlyOwner {
        require(pools[poolId].creator == address(0), "pool exists");
        require(creator != address(0), "invalid creator");
        require(platform != address(0), "invalid platform");

        pools[poolId] = Pool({
            creator: creator,
            platform: platform,
            total_collected: 0,
            total_disbursed: 0,
            held_balance: 0,
            active: true
        });

        emit PoolCreated(poolId, creator, platform);
    }

    /// @notice Deactivate a pool. Stops new deposits; existing funds remain withdrawable.
    function deactivatePool(bytes32 poolId) external onlyOwner {
        require(pools[poolId].active, "pool not active");
        pools[poolId].active = false;
    }

    // ─── Permissionless ──────────────────────────────────────────────

    /// @notice Deposit USDC into a pool. Permissionless — anyone may fund a pool.
    ///         Deposits stop once a pool is deactivated.
    function depositToPool(bytes32 poolId, uint256 amount) external nonReentrant {
        require(pools[poolId].active, "pool not active");
        require(amount > 0, "amount must be positive");

        usdc.safeTransferFrom(msg.sender, address(this), amount);

        pools[poolId].held_balance += amount;
        pools[poolId].total_collected += amount;

        emit PoolFunded(poolId, msg.sender, amount);
    }

    /// @notice Withdraw USDC from a pool. Permissionless — bounded by held_balance.
    ///         Deliberately NOT gated on pool.active so a stopped/retired pool can
    ///         still recover funds already earned. Payout is always to the pool's
    ///         creator and platform addresses, regardless of who calls.
    function withdraw(bytes32 poolId, uint256 amount) external nonReentrant {
        Pool storage pool = pools[poolId];
        require(pool.creator != address(0), "pool does not exist");
        require(amount > 0, "amount must be positive");
        require(amount <= pool.held_balance, "amount exceeds held balance");

        uint256 creatorShare = amount * 90 / 100;
        uint256 platformShare = amount - creatorShare;

        // Effects before interactions.
        pool.held_balance -= amount;
        pool.total_disbursed += amount;

        usdc.safeTransfer(pool.creator, creatorShare);
        usdc.safeTransfer(pool.platform, platformShare);

        emit PaymentSplit(poolId, amount, creatorShare, platformShare);
    }
}
