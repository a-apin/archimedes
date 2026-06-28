# @version ^0.4.0

interface IERC20:
    def transferFrom(_from: address, _to: address, _value: uint256) -> bool: nonpayable
    def transfer(_to: address, _value: uint256) -> bool: nonpayable

struct Pool:
    creator: address
    platform: address
    total_collected: uint256
    total_disbursed: uint256
    active: bool

pools: public(HashMap[bytes32, Pool])
usdc: public(address)
owner: public(address)

event PoolCreated:
    pool_id: indexed(bytes32)
    creator: indexed(address)
    platform: indexed(address)

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
        active=True
    )

    log PoolCreated(pool_id, creator, platform)

@external
def split(pool_id: bytes32, amount: uint256):
    assert self.pools[pool_id].active, "pool not active"

    creator_share: uint256 = amount * 90 // 100
    platform_share: uint256 = amount - creator_share

    pool: Pool = self.pools[pool_id]

    assert extcall IERC20(self.usdc).transferFrom(msg.sender, pool.creator, creator_share), "creator transfer failed"
    assert extcall IERC20(self.usdc).transferFrom(msg.sender, pool.platform, platform_share), "platform transfer failed"

    self.pools[pool_id].total_collected += amount
    self.pools[pool_id].total_disbursed += amount

    log PaymentSplit(pool_id, amount, creator_share, platform_share)

@external
def deactivatePool(pool_id: bytes32):
    assert msg.sender == self.owner, "only owner"
    assert self.pools[pool_id].active, "pool not active"
    self.pools[pool_id].active = False
