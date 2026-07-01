---
name: hummingbot__remote-control
description: MQTT-based remote interface for external bot management and messaging
triggers: [mqtt, remote, remote interface, external control]
---

# Remote Control (MQTT)

**Source**: hummingbot
**Category**: Integration

## When to use this skill
Setting up or extending MQTT-based remote control of Hummingbot instances, sending commands or receiving status updates over MQTT.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/remote_iface/mqtt.py` — MQTT client implementation (aiomqtt)
- `/home/ricardo/github/hummingbot/hummingbot/remote_iface/messages.py` — Remote interface message types
- `/home/ricardo/github/hummingbot/hummingbot/remote_iface/__init__.py` — Remote interface package init

## Key concepts
- **MQTT protocol**: lightweight pub/sub messaging for IoT/remote control. Hummingbot uses `aiomqtt` for async MQTT communication.
- **Remote commands**: control bot operations (start, stop, config changes) from external MQTT clients.
- **Status broadcasting**: bot publishes status updates (positions, P&L, errors) to MQTT topics for external dashboards.

## Related skills
- See `.agents/skills/hummingbot__cli-tui` — local CLI alternative to remote control
- See `.agents/skills/hummingbot__core-engine` — the engine whose state is broadcast via MQTT
