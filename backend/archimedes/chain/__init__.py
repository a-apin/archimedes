"""On-chain integration layer — Web3 client, contract wrappers, and executors.

This package implements the chain interfaces defined in archimedes/interfaces/chain.py:
  - IOracleUpdater  → chain/oracle_updater.py
  - IChainExecutor   → chain/executor.py
  - ITracePublisher  → chain/trace_publisher.py

All web3 calls go through the shared AsyncWeb3 client in chain/client.py.
"""

from archimedes.chain.client import ChainSettings, chain_client
from archimedes.chain.contracts import ContractLoader

__all__ = ["ChainSettings", "ContractLoader", "chain_client"]
