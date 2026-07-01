---
name: hummingbot__cli-tui
description: Terminal UI built on prompt_toolkit, CLI command handling, configuration management
triggers: [cli, command, terminal ui, configuration, tui]
---

# CLI & Terminal UI

**Source**: hummingbot
**Category**: Core

## When to use this skill
Understanding the Hummingbot terminal interface, adding/modifying CLI commands, working with the prompt_toolkit-based TUI, or managing bot configuration.

## Key files and folders
- `/home/ricardo/github/hummingbot/hummingbot/client/hummingbot_application.py` — Main application coordinator
- `/home/ricardo/github/hummingbot/hummingbot/client/command/` — 30+ CLI command handlers (`balance`, `config`, `connect`, `start`, `stop`, `gateway_*`, `import`, `export`, `history`, `pmm_*`, etc.)
- `/home/ricardo/github/hummingbot/hummingbot/client/ui/` — prompt_toolkit TUI: layout, keybindings, autocomplete, custom styles
- `/home/ricardo/github/hummingbot/hummingbot/client/config/` — Config models, validators, migration
- `/home/ricardo/github/hummingbot/hummingbot/client/tab/` — Custom output tabs (order book display)
- `/home/ricardo/github/hummingbot/hummingbot/client/settings.py` — Connector/strategy registration in client
- `/home/ricardo/github/hummingbot/hummingbot/client/performance.py` — Performance display
- `/home/ricardo/github/hummingbot/hummingbot/templates/` — YAML config templates per strategy type
- `/home/ricardo/github/hummingbot/conf/` — User configuration directory (connectors, controllers, scripts, strategies)

## Key concepts
- **prompt_toolkit TUI**: full terminal UI with multi-pane layout, tab-completion, custom keybindings, Vim mode support.
- **Command pattern**: each CLI command is a separate handler in `client/command/`.
- **Config templates**: YAML templates in `templates/` are used to generate per-strategy config files.
- **Startup flow**: `bin/hummingbot.py` → `HummingbotApplication` → TUI → user connects exchanges → start strategy.

## Related skills
- See `.agents/skills/hummingbot__v1-strategies` — strategies controlled via CLI
- See `.agents/skills/hummingbot__v2-framework` — V2 strategies accessible via CLI
