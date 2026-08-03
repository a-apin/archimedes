"""Chain-layer constants with no import side effects.

Kept separate from executor.py (which instantiates the chain_executor
singleton at import time) so lightweight consumers — pydantic schemas,
OpenAPI generation, tooling — can import the fee caps without pulling in
Web3/chain configuration.
"""

# Off-chain mirror of the Vault constructor fee caps (PR #1129 —
# MAX_MANAGEMENT_FEE_BPS / MAX_PERFORMANCE_FEE_BPS in Vault.sol). The live
# VaultFactory predates those caps and fee bps are immutable once a vault is
# constructed, so a pre-cap vault with hostile fees can never be fixed
# on-chain. These constants bound what the backend will list or interact
# with (issue #1138) and MUST stay in lockstep with the Solidity values.
MAX_MANAGEMENT_FEE_BPS = 500  # 5%
MAX_PERFORMANCE_FEE_BPS = 5000  # 50%
