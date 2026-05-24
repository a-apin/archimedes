# Archimedes — Interface Protocols
#
# These are the frozen contracts between team members.
# Each protocol defines the boundary of one person's component.
# Implement behind the protocol; depend only on the protocol.
#
# Owner mapping:
#   Önder  → IRegimeDetector, IPortfolioConstructor, IBacktestEvaluator
#   Dan    → IStrategyProvider
#   Marten → IChainExecutor, IOracleUpdater, ITracePublisher
#   Chuan  → IAgentOrchestrator (orchestrates all the above)
#   Daniel → consumes REST API (see api/ module), not these protocols

from archimedes.interfaces.agent import IAgentOrchestrator
from archimedes.interfaces.chain import IChainExecutor, IOracleUpdater, ITracePublisher
from archimedes.interfaces.math import (
    IBacktestEvaluator,
    IPortfolioConstructor,
    IRegimeDetector,
)
from archimedes.interfaces.strategy import IStrategyProvider

__all__ = [
    "IAgentOrchestrator",
    "IBacktestEvaluator",
    "IChainExecutor",
    "IOracleUpdater",
    "IPortfolioConstructor",
    "IRegimeDetector",
    "IStrategyProvider",
    "ITracePublisher",
]
