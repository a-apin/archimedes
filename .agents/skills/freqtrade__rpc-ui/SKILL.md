---
name: freqtrade__rpc-ui
description: Telegram bot, Discord integration, Webhook, REST API (FastAPI), WebSocket, and external message consumer
triggers: [telegram, discord, webhook, rest api, rpc, fastapi]
---

# RPC & UI Layer

**Source**: freqtrade
**Category**: Integration

## When to use this skill
Configuring or extending the Telegram bot, Discord notifications, webhook calls, the REST API server, WebSocket feeds, or external message consumer for multi-bot communication.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/rpc/rpc_manager.py` — `RPCManager`: dispatches messages to all producers
- `/home/ricardo/github/freqtrade/freqtrade/rpc/rpc.py` — `RPC` class: core RPC methods (status, profit, balance, whitelist, etc.)
- `/home/ricardo/github/freqtrade/freqtrade/rpc/rpc_types.py` — RPC type definitions
- `/home/ricardo/github/freqtrade/freqtrade/rpc/telegram.py` — Telegram bot interface (`python-telegram-bot`)
- `/home/ricardo/github/freqtrade/freqtrade/rpc/discord.py` — Discord webhook integration
- `/home/ricardo/github/freqtrade/freqtrade/rpc/webhook.py` — Webhook sender (custom JSON payloads)
- `/home/ricardo/github/freqtrade/freqtrade/rpc/api_server/` — FastAPI REST API server with auto-generated OpenAPI docs
- `/home/ricardo/github/freqtrade/freqtrade/rpc/external_message_consumer.py` — `ExternalMessageConsumer`: multi-bot message bus

## Key concepts
- **RPCManager**: central dispatcher — all producers (Telegram, Discord, Webhook, API Server, WebSocket) receive every `RPCMessageType`.
- **REST API**: FastAPI server with authentication, CORS, WebSocket endpoint, and full trade management endpoints.
- **Telegram**: rich interactive bot with inline keyboards, charts, and real-time trade notifications.
- **ExternalMessageConsumer**: allows one bot instance to consume signals/trades from another (producer/consumer pattern).

## Related skills
- See `.agents/skills/freqtrade__trading-engine` — the bot these RPC interfaces control
- See `.agents/skills/freqtrade__strategy-engine` — strategy state visible via RPC
