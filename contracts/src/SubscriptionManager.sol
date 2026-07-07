// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @notice Minimal interface to the on-chain PaymentSplitter (contracts/src/PaymentSplitter.sol,
///         shipped in PR #958). `depositToPool` pulls USDC via transferFrom and credits the pool;
///         `pools` exposes the pool tuple so we can gate on `active`.
interface IPaymentSplitter {
    function depositToPool(bytes32 poolId, uint256 amount) external;
    function pools(bytes32 poolId)
        external
        view
        returns (address creator, address platform, uint256 heldBalance, uint256 totalCollected, bool active);
}

/// @title SubscriptionManager
/// @notice On-chain subscription rail for the strategy marketplace: subscribers prepay USDC into a
///         per-subscription reserve, an authorized off-chain charger meters delivered actions and
///         debits the reserve, and each charge is routed into the PaymentSplitter's pool for the
///         90/10 creator/platform split. Unsubscribing refunds the remaining reserve to the caller.
///
/// @dev **PROVENANCE + STATUS.** This is a Solidity port of the deleted Vyper design that lived on
///      `dbrowneup/marketplace-payments-contracts` (`contracts/vyper/SubscriptionManager.vy`),
///      recovered as a starting point for the on-chain / non-custodial subscription migration
///      tracked in issue #975. PR #958 shipped the *off-chain, custodial-INTERIM* fee model
///      (Circle DCWs) and replaced the Vyper PaymentSplitter with the Solidity one this integrates
///      against. **NOT deployment-ready:** it has no test suite yet, has not been reviewed by
///      Bogdan, and its custody model (a prepaid reserve held by this contract) must be reconciled
///      with the #975 non-custodial direction before any broadcast. Compiles under the repo's
///      Foundry config; treat as a design reference until #975 scopes it properly.
contract SubscriptionManager is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ─── Types ───────────────────────────────────────────────────────

    struct Subscription {
        address subscriber;
        bytes32 poolId;
        address ephemeralWallet;
        uint256 reservedUsdc;
        string webhookUrl;
        bool active;
        uint256 createdAt;
    }

    /// @dev `ephemeralWallet` is a deterministic pseudo-address keyed to the subscription — an
    ///      accounting handle for the prepaid reserve, NOT a deployed wallet contract.
    struct EphemeralWallet {
        address owner;
        uint256 balance;
        bytes32 subscriptionId;
    }

    // ─── State ───────────────────────────────────────────────────────

    mapping(bytes32 => Subscription) public subscriptions;
    mapping(address => EphemeralWallet) public ephemeralWallets;

    IPaymentSplitter public immutable splitter;
    IERC20 public immutable usdc;
    uint256 public flatFeePerAction;

    /// @notice The only address allowed to call `chargeActions` — the off-chain platform charger
    ///         (the strategy runner that meters delivered actions). Defaults to the owner and is
    ///         re-pointable via `setAuthorizedCharger`. Leaving it permissionless would let anyone
    ///         bill any subscriber for actions never performed.
    address public authorizedCharger;

    // ─── Events ──────────────────────────────────────────────────────

    event Subscribed(bytes32 indexed subId, address indexed subscriber, bytes32 indexed poolId, string webhookUrl);
    event EphemeralWalletCreated(bytes32 indexed subId, address indexed walletAddress, address indexed subscriber);
    event ActionCharged(bytes32 indexed subId, uint256 actions, uint256 totalCharged);
    event Unsubscribed(bytes32 indexed subId);
    event FlatFeeSet(uint256 oldFee, uint256 newFee);
    event AuthorizedChargerSet(address indexed oldCharger, address indexed newCharger);

    // ─── Errors ──────────────────────────────────────────────────────

    error WebhookRequired();
    error PoolNotActive();
    error AlreadySubscribed();
    error WalletExists();
    error NotSubscriber();
    error SubscriptionNotActive();
    error OnlyAuthorizedCharger();
    error ZeroActionCount();
    error InsufficientBalance();
    error InvalidCharger();

    // ─── Constructor ─────────────────────────────────────────────────

    constructor(address _splitter, address _usdc, uint256 _flatFee, address _owner) Ownable(_owner) {
        splitter = IPaymentSplitter(_splitter);
        usdc = IERC20(_usdc);
        flatFeePerAction = _flatFee;
        authorizedCharger = _owner;
    }

    // ─── Subscriber Actions ──────────────────────────────────────────

    /// @notice Open a subscription against an active PaymentSplitter pool, prepaying `initialDeposit`.
    function subscribe(bytes32 poolId, string calldata webhookUrl, uint256 initialDeposit)
        external
        nonReentrant
        returns (bytes32 subId)
    {
        if (bytes(webhookUrl).length == 0) revert WebhookRequired();

        (,,,, bool poolActive) = splitter.pools(poolId);
        if (!poolActive) revert PoolNotActive();

        subId = keccak256(abi.encode(poolId, msg.sender, block.timestamp));
        if (subscriptions[subId].active) revert AlreadySubscribed();

        // First 20 bytes of the hash (matches the Vyper slice(...,0,20) semantics).
        address walletAddress = address(bytes20(keccak256(abi.encode(subId, block.number))));
        if (ephemeralWallets[walletAddress].owner != address(0)) revert WalletExists();

        if (initialDeposit > 0) {
            usdc.safeTransferFrom(msg.sender, address(this), initialDeposit);
        }

        ephemeralWallets[walletAddress] =
            EphemeralWallet({owner: msg.sender, balance: initialDeposit, subscriptionId: subId});

        subscriptions[subId] = Subscription({
            subscriber: msg.sender,
            poolId: poolId,
            ephemeralWallet: walletAddress,
            reservedUsdc: initialDeposit,
            webhookUrl: webhookUrl,
            active: true,
            createdAt: block.timestamp
        });

        emit EphemeralWalletCreated(subId, walletAddress, msg.sender);
        emit Subscribed(subId, msg.sender, poolId, webhookUrl);
    }

    /// @notice Top up the prepaid reserve of an existing subscription.
    function renewEphemeralWallet(bytes32 subId, uint256 topUpAmount) external nonReentrant {
        Subscription storage sub = subscriptions[subId];
        if (!sub.active) revert SubscriptionNotActive();
        if (sub.subscriber != msg.sender) revert NotSubscriber();

        if (topUpAmount > 0) {
            usdc.safeTransferFrom(msg.sender, address(this), topUpAmount);
        }

        ephemeralWallets[sub.ephemeralWallet].balance += topUpAmount;
        sub.reservedUsdc += topUpAmount;

        emit EphemeralWalletCreated(subId, sub.ephemeralWallet, msg.sender);
    }

    /// @notice Close a subscription and refund the remaining reserve to the subscriber.
    function unsubscribe(bytes32 subId) external nonReentrant {
        Subscription storage sub = subscriptions[subId];
        if (!sub.active) revert SubscriptionNotActive();
        if (sub.subscriber != msg.sender) revert NotSubscriber();

        sub.active = false;
        sub.reservedUsdc = 0;

        uint256 remaining = ephemeralWallets[sub.ephemeralWallet].balance;
        if (remaining > 0) {
            ephemeralWallets[sub.ephemeralWallet].balance = 0;
            usdc.safeTransfer(msg.sender, remaining);
        }

        emit Unsubscribed(subId);
    }

    // ─── Charger Action ──────────────────────────────────────────────

    /// @notice Debit `actionCount * flatFeePerAction` from the subscription's reserve and route it
    ///         into the PaymentSplitter pool for the 90/10 split. Callable only by the authorized charger.
    function chargeActions(bytes32 subId, uint256 actionCount) external nonReentrant {
        if (msg.sender != authorizedCharger) revert OnlyAuthorizedCharger();
        if (actionCount == 0) revert ZeroActionCount();

        Subscription storage sub = subscriptions[subId];
        if (!sub.active) revert SubscriptionNotActive();

        uint256 totalCharge = actionCount * flatFeePerAction;
        EphemeralWallet storage wallet = ephemeralWallets[sub.ephemeralWallet];
        if (wallet.balance < totalCharge) revert InsufficientBalance();

        wallet.balance -= totalCharge;
        sub.reservedUsdc -= totalCharge;

        // depositToPool pulls via transferFrom — approve exactly the charge for this call.
        usdc.forceApprove(address(splitter), totalCharge);
        splitter.depositToPool(sub.poolId, totalCharge);

        emit ActionCharged(subId, actionCount, totalCharge);
    }

    // ─── Owner Config ────────────────────────────────────────────────

    function setFlatFee(uint256 fee) external onlyOwner {
        emit FlatFeeSet(flatFeePerAction, fee);
        flatFeePerAction = fee;
    }

    function setAuthorizedCharger(address charger) external onlyOwner {
        if (charger == address(0)) revert InvalidCharger();
        emit AuthorizedChargerSet(authorizedCharger, charger);
        authorizedCharger = charger;
    }
}
