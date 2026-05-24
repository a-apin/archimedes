"""Config endpoints — /api/config/*."""

from __future__ import annotations

from fastapi import APIRouter

from archimedes.api._route_helpers import config_svc
from archimedes.api.schemas import ContractAddressesResponse

config_router = APIRouter(prefix="/api/config", tags=["config"])


@config_router.get("/contracts", response_model=ContractAddressesResponse)
async def get_contract_addresses():
    """Get all deployed contract addresses."""
    return await config_svc.get_contract_addresses()
