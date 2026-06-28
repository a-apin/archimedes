# @version ^0.4.0

interface IERC20:
    def transferFrom(_from: address, _to: address, _value: uint256) -> bool: nonpayable
    def transfer(_to: address, _value: uint256) -> bool: nonpayable

interface IPaymentSplitter:
    def split(pool_id: bytes32, amount: uint256): nonpayable

struct Subscription:
    subscriber: address
    pool_id: bytes32
    ephemeral_wallet: address
    reserved_usdc: uint256
    webhook_url: String[256]
    active: bool
    created_at: uint256

struct EphemeralWallet:
    owner: address
    balance: uint256
    subscription_id: bytes32

subscriptions: public(HashMap[bytes32, Subscription])
ephemeral_wallets: public(HashMap[address, EphemeralWallet])
splitter: public(address)
usdc: public(address)
owner: public(address)
flat_fee_per_action: public(uint256)

event Subscribed:
    sub_id: indexed(bytes32)
    subscriber: indexed(address)
    pool_id: indexed(bytes32)
    webhook_url: String[256]

event EphemeralWalletCreated:
    sub_id: indexed(bytes32)
    wallet_address: indexed(address)
    subscriber: indexed(address)

event ActionCharged:
    sub_id: indexed(bytes32)
    actions: uint256
    total_charged: uint256

event Unsubscribed:
    sub_id: indexed(bytes32)

@deploy
def __init__(_splitter: address, _usdc: address, _flat_fee: uint256):
    self.owner = msg.sender
    self.splitter = _splitter
    self.usdc = _usdc
    self.flat_fee_per_action = _flat_fee

@external
def subscribe(
    pool_id: bytes32,
    webhook_url: String[256],
    initial_deposit: uint256
) -> bytes32:
    assert len(webhook_url) > 0, "webhook_url required"

    sub_id: bytes32 = keccak256(
        abi_encode(pool_id, msg.sender, block.timestamp, method_id=0x00000000)
    )

    assert not self.subscriptions[sub_id].active, "already subscribed"

    wallet_address: address = convert(
        slice(keccak256(abi_encode(sub_id, block.number, method_id=0x00000000)), 0, 20),
        address
    )

    assert self.ephemeral_wallets[wallet_address].owner == empty(address), "wallet exists"

    if initial_deposit > 0:
        assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, initial_deposit), "deposit failed"

    self.ephemeral_wallets[wallet_address] = EphemeralWallet(
        owner=msg.sender,
        balance=initial_deposit,
        subscription_id=sub_id
    )

    self.subscriptions[sub_id] = Subscription(
        subscriber=msg.sender,
        pool_id=pool_id,
        ephemeral_wallet=wallet_address,
        reserved_usdc=initial_deposit,
        webhook_url=webhook_url,
        active=True,
        created_at=block.timestamp
    )

    log EphemeralWalletCreated(sub_id, wallet_address, msg.sender)
    log Subscribed(sub_id, msg.sender, pool_id, webhook_url)

    return sub_id

@external
def renewEphemeralWallet(sub_id: bytes32, top_up_amount: uint256):
    sub: Subscription = self.subscriptions[sub_id]
    assert sub.active, "subscription not active"
    assert sub.subscriber == msg.sender, "not subscriber"

    if top_up_amount > 0:
        assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, top_up_amount), "top-up failed"

    self.ephemeral_wallets[sub.ephemeral_wallet].balance += top_up_amount
    self.subscriptions[sub_id].reserved_usdc += top_up_amount

    log EphemeralWalletCreated(sub_id, sub.ephemeral_wallet, msg.sender)

@external
def chargeActions(sub_id: bytes32, action_count: uint256):
    assert action_count > 0, "action_count must be > 0"

    sub: Subscription = self.subscriptions[sub_id]
    assert sub.active, "subscription not active"

    total_charge: uint256 = action_count * self.flat_fee_per_action
    wallet: EphemeralWallet = self.ephemeral_wallets[sub.ephemeral_wallet]
    assert wallet.balance >= total_charge, "insufficient balance"

    self.ephemeral_wallets[sub.ephemeral_wallet].balance -= total_charge
    self.subscriptions[sub_id].reserved_usdc -= total_charge

    assert extcall IERC20(self.usdc).transfer(self.splitter, total_charge), "transfer to splitter failed"

    extcall IPaymentSplitter(self.splitter).split(sub.pool_id, total_charge)

    log ActionCharged(sub_id, action_count, total_charge)

@external
def unsubscribe(sub_id: bytes32):
    sub: Subscription = self.subscriptions[sub_id]
    assert sub.active, "subscription not active"
    assert sub.subscriber == msg.sender, "not subscriber"

    self.subscriptions[sub_id].active = False
    self.subscriptions[sub_id].reserved_usdc = 0

    remaining: uint256 = self.ephemeral_wallets[sub.ephemeral_wallet].balance
    if remaining > 0:
        self.ephemeral_wallets[sub.ephemeral_wallet].balance = 0
        assert extcall IERC20(self.usdc).transfer(msg.sender, remaining), "refund failed"

    log Unsubscribed(sub_id)

@external
def setFlatFee(fee: uint256):
    assert msg.sender == self.owner, "only owner"
    self.flat_fee_per_action = fee
