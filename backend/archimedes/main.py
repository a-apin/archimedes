"""Archimedes — FastAPI application entrypoint.

Minimal bootstrap for the hackathon MVP. All routes are wired to
chain services that read/write Arc smart contracts.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from archimedes.api.routes import (
    agent_router,
    assets_router,
    vaults_router,
    strategies_router,
    traces_router,
    regime_router,
    swap_router,
    config_router,
)
from archimedes.api.chat_routes import chat_router
from archimedes.api.marketplace_routes import marketplace_router
from archimedes.db import init_db

app = FastAPI(
    title="Archimedes",
    description="Peer-reviewed AI portfolios, settled on Arc.",
    version="0.1.0",
)

# Allow the Next.js frontend to call the API during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database (creates chat tables if needed)
init_db()

# Wire all routers
app.include_router(assets_router)
app.include_router(vaults_router)
app.include_router(strategies_router)
app.include_router(traces_router)
app.include_router(regime_router)
app.include_router(swap_router)
app.include_router(config_router)
app.include_router(agent_router)
app.include_router(chat_router)
app.include_router(marketplace_router)


@app.get("/health")
async def health():
    """Health check — used by Docker healthcheck and CI/CD."""
    from archimedes.chain.client import chain_client

    connected = await chain_client.is_connected()
    return {
        "status": "ok" if connected else "degraded",
        "service": "archimedes-backend",
        "chain_connected": connected,
    }


@app.get("/")
async def root():
    return {
        "name": "Archimedes",
        "tagline": "Peer-reviewed AI portfolios, settled on Arc.",
        "docs": "/docs",
    }
