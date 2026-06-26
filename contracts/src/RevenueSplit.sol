// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title RevenueSplit
/// @notice Minimal, non-upgradeable creator/platform revenue splitter for the
///         x402 nanopayment marketplace (T1.2, first slice).
///
///         The x402 facilitator settles each paywalled `/construct` request by
///         moving USDC (via EIP-3009 `transferWithAuthorization`) to THIS
///         contract's address. Anyone can then call `release()` to forward the
///         accumulated balance 90% to `creator` and 10% to `platform`. Both
///         addresses are immutable at construction.
///
///         Design constraints for this first slice (see issue #713 / spec
///         SPEC-1 T1.2):
///           - No streaming, no per-strategy creator routing (a single splitter
///             for all paywalled strategies; per-strategy routing is a follow-up).
///           - No admin / owner function — the split cannot be redirected after
///             deploy. The contract is intentionally un-ownable so a paid creator
///             can trust that funds route as advertised.
///           - `release()` is permissionless: anyone can trigger the forward, but
///             funds can only ever go to the two immutable recipients.
///
///         LOCAL VALIDATION STATUS: `forge` is not installed on the authoring
///         machine, so this contract is UNVALIDATED locally. Bogdan / Dan must run
///         `forge build && forge test --match-contract RevenueSplit -vv` after the
///         Foundry standup before this is deploy-ready.
contract RevenueSplit {
    using SafeERC20 for IERC20;

    // ─── Constants ───────────────────────────────────────────────────
    /// @notice Basis points denominator (100% = 10_000 bps).
    uint256 public constant BPS_DENOMINATOR = 10_000;
    /// @notice Creator share in basis points (90%).
    uint256 public constant CREATOR_BPS = 9_000;
    /// @notice Platform share in basis points (10%).
    uint256 public constant PLATFORM_BPS = 1_000;

    // ─── Immutable state ─────────────────────────────────────────────
    /// @notice The USDC token this splitter forwards.
    IERC20 public immutable usdc;
    /// @notice Receives 90% of every release. Immutable.
    address public immutable creator;
    /// @notice Receives 10% of every release. Immutable.
    address public immutable platform;

    // ─── Events ──────────────────────────────────────────────────────
    /// @notice Emitted on every successful `release()`.
    event Released(uint256 totalAmount, uint256 creatorAmount, uint256 platformAmount);

    // ─── Constructor ─────────────────────────────────────────────────
    /// @param _usdc     USDC token address on Arc (the settlement asset).
    /// @param _creator  Address that receives the 90% creator share.
    /// @param _platform Address that receives the 10% platform share.
    constructor(address _usdc, address _creator, address _platform) {
        require(_usdc != address(0), "RevenueSplit: zero USDC");
        require(_creator != address(0), "RevenueSplit: zero creator");
        require(_platform != address(0), "RevenueSplit: zero platform");

        usdc = IERC20(_usdc);
        creator = _creator;
        platform = _platform;
    }

    // ─── Write ───────────────────────────────────────────────────────
    /// @notice Forward the contract's entire USDC balance 90/10 to creator/platform.
    ///         Permissionless — anyone may trigger, but funds only ever route to
    ///         the two immutable recipients. The platform share is computed as the
    ///         remainder after the creator share so no dust is ever stranded.
    function release() external {
        uint256 balance = usdc.balanceOf(address(this));
        require(balance > 0, "RevenueSplit: nothing to release");

        uint256 creatorAmount = (balance * CREATOR_BPS) / BPS_DENOMINATOR;
        uint256 platformAmount = balance - creatorAmount;

        usdc.safeTransfer(creator, creatorAmount);
        usdc.safeTransfer(platform, platformAmount);

        emit Released(balance, creatorAmount, platformAmount);
    }

    // ─── Read ────────────────────────────────────────────────────────
    /// @notice Current USDC balance held by the splitter awaiting release.
    function pending() external view returns (uint256) {
        return usdc.balanceOf(address(this));
    }
}
