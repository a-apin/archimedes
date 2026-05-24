"""Service implementations of the interface contracts in `interfaces/`.

Each module here is owned by a single teammate and implements one or more
Protocols from `archimedes.interfaces.*`. Wired together by Chuan's agent
orchestrator.
"""

# Backwards-compat re-exports — the agentic services have moved to
# ``archimedes.agents``.  These aliases keep any external consumers
# (scripts, notebooks, stale imports) working without changes.
from archimedes.agents import generation_pipeline as generation_pipeline
from archimedes.agents import portfolio_agent as portfolio_agent
from archimedes.agents import strategy_architect as strategy_architect
from archimedes.agents import strategy_fusion as strategy_fusion
