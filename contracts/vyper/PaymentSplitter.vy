# @version ^0.4.0

interface IERC20:
    def transferFrom(_from: address, _to: address, _value: uint256) -> bool: nonpayable
    def transfer(_to: address, _value: uint256) -> bool: nonpayable

struct Pool:
    creator: address
    platform: address
    total_collected: uint256
    total_disbursed: uint256
    held_balance: uint256
    active: bool

pools: public(HashMap[bytes32, Pool])
usdc: public(address)
owner: public(address)

event PoolCreated:
    pool_id: indexed(bytes32)
    creator: indexed(address)
    platform: indexed(address)

event PoolFunded:
    pool_id: indexed(bytes32)
    funder: indexed(address)
    amount: uint256

event PaymentSplit:
    pool_id: indexed(bytes32)
    amount: uint256
    creator_share: uint256
    platform_share: uint256

@deploy
def __init__(_usdc: address):
    self.owner = msg.sender
    self.usdc = _usdc

@external
def createPool(pool_id: bytes32, creator: address, platform: address):
    assert msg.sender == self.owner, "only owner"
    assert not self.pools[pool_id].active, "pool already exists"
    assert creator != empty(address), "invalid creator"
    assert platform != empty(address), "invalid platform"

    self.pools[pool_id] = Pool(
        creator=creator,
        platform=platform,
        total_collected=0,
        total_disbursed=0,
        held_balance=0,
        active=True
    )

    log PoolCreated(pool_id=pool_id, creator=creator, platform=platform)

@external
@nonreentrant
def depositToPool(pool_id: bytes32, amount: uint256):
    """Permissionless. Anyone may fund a pool — the credit is only ever as
    real as the USDC actually pulled from msg.sender, so there is no
    attribution-trust problem in leaving this open. Deposits stop once a
    pool is deactivated (D6 §2.5)."""
    assert self.pools[pool_id].active, "pool not active"
    assert amount > 0, "amount must be positive"

    assert extcall IERC20(self.usdc).transferFrom(msg.sender, self, amount), "deposit transfer failed"

    self.pools[pool_id].held_balance += amount
    self.pools[pool_id].total_collected += amount

    log PoolFunded(pool_id=pool_id, funder=msg.sender, amount=amount)

@external
@nonreentrant
def withdraw(pool_id: bytes32, amount: uint256):
    """Restricted to the pool's creator or its platform address (D6 §2.3).
    Bounded by held_balance, not the contract's total balance (D6 §2.4).
    Deliberately NOT gated on pool.active — a stopped/retired pool must
    still be able to recover funds already earned (D6 §2.5)."""
    pool: Pool = self.pools[pool_id]
    assert pool.creator != empty(address), "pool does not exist"
    assert msg.sender == pool.creator or msg.sender == pool.platform, "not authorized"
    assert amount > 0, "amount must be positive"
    assert amount <= pool.held_balance, "amount exceeds held balance"

    creator_share: uint256 = amount * 90 // 100
    platform_share: uint256 = amount - creator_share

    # Effects before interactions.
    self.pools[pool_id].held_balance -= amount
    self.pools[pool_id].total_disbursed += amount

    assert extcall IERC20(self.usdc).transfer(pool.creator, creator_share), "creator transfer failed"
    assert extcall IERC20(self.usdc).transfer(pool.platform, platform_share), "platform transfer failed"

    log PaymentSplit(pool_id=pool_id, amount=amount, creator_share=creator_share, platform_share=platform_share)

@external
def deactivatePool(pool_id: bytes32):
    assert msg.sender == self.owner, "only owner"
    assert self.pools[pool_id].active, "pool not active"
    self.pools[pool_id].active = False
