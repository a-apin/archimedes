"""REST API route re-exports.

All endpoints are defined in per-resource router files. This module
re-exports each ``*_router`` so that ``main.py`` imports remain unchanged.

Router files:
  assets_routes      /api/assets/*
  vaults_routes      /api/vaults/*
  strategies_routes  /api/strategies/*
  traces_routes      /api/traces/*
  regime_routes      /api/regime/*
  swap_routes        /api/swap/*
  config_routes      /api/config/*
  agent_routes       /api/agent/*
  papers_routes      /api/papers/*
"""

from archimedes.api.agent_routes import agent_router
from archimedes.api.assets_routes import assets_router
from archimedes.api.config_routes import config_router
from archimedes.api.papers_routes import papers_router
from archimedes.api.regime_routes import regime_router
from archimedes.api.strategies_routes import strategies_router
from archimedes.api.swap_routes import swap_router
from archimedes.api.traces_routes import traces_router
from archimedes.api.vaults_routes import vaults_router

__all__ = [
    "agent_router",
    "assets_router",
    "config_router",
    "papers_router",
    "regime_router",
    "strategies_router",
    "swap_router",
    "traces_router",
    "vaults_router",
]
