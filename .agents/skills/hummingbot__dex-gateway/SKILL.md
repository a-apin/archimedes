---
name: hummingbot__dex-gateway
description: DEX middleware integration via Gateway HTTP client for on-chain AMM protocols
triggers: [gateway, dex, amm, uniswap, defi]
---

# DEX Gateway Integration

**Source**: hummingbot
**Category**: Integration

## When to use this skill
Working with DEX connectors via the Gateway middleware, interacting with on-chain AMM protocols (Uniswap, Curve, Balancer), or configuring the Gateway service.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/connector/gateway/` — Gateway connector base classes
- `/home/ricardo/github/hummingbot/hummingbot/core/gateway/` — Gateway monitor and HTTP client
- `/home/ricardo/github/docker-compose.yml` — Docker Compose with optional Gateway DEX middleware profile

## Key concepts
- **Gateway**: separate middleware service that handles blockchain interactions (transaction building, signing, broadcasting) so Hummingbot doesn't need direct chain access.
- **DEX connectors**: use Gateway HTTP API to interact with on-chain protocols instead of exchange REST APIs.
- **AMM support**: Uniswap V2/V3, Curve, Balancer, and other AMM protocols via Gateway.
- **Docker profile**: Gateway runs as a separate container; enable with `docker-compose --profile gateway up`.

## Related skills
- See `.agents/skills/hummingbot__exchange-connectors` — CEX connector patterns differ from DEX
- See `.agents/skills/hummingbot__v1-strategies` — AMM arb strategy uses Gateway
