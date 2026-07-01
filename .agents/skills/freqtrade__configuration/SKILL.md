---
name: freqtrade__configuration
description: Config loading, validation, JSON schema, environment variable overrides, secrets management
triggers: [config, configuration, validation, config schema]
---

# Configuration

**Source**: freqtrade
**Category**: Infrastructure

## When to use this skill
Loading, validating, or modifying freqtrade configuration, understanding the JSON schema, managing secrets, handling environment variable overrides, or deploying configuration.

## Key files and folders
- `/home/ricardo/github/freqtrade/freqtrade/configuration/configuration.py` — `Configuration`: config loading and resolution
- `/home/ricardo/github/freqtrade/freqtrade/configuration/config_validation.py` — Config validation logic
- `/home/ricardo/github/freqtrade/freqtrade/configuration/config_setup.py` — Initial config setup
- `/home/ricardo/github/freqtrade/freqtrade/configuration/config_secrets.py` — Secrets management
- `/home/ricardo/github/freqtrade/freqtrade/configuration/environment_vars.py` — ENV variable overrides (`FREQTRADE__*` pattern)
- `/home/ricardo/github/freqtrade/freqtrade/configuration/detect_environment.py` — Environment detection
- `/home/ricardo/github/freqtrade/freqtrade/configuration/deprecated_settings.py` — Deprecated setting migrations
- `/home/ricardo/github/freqtrade/freqtrade/configuration/load_config.py` — File loading
- `/home/ricardo/github/freqtrade/freqtrade/configuration/directory_operations.py` — Config directory management
- `/home/ricardo/github/freqtrade/freqtrade/configuration/timerange.py` — Timerange parsing
- `/home/ricardo/github/freqtrade/freqtrade/config_schema/` — JSON Schema definition for full config validation (~62KB)

## Key concepts
- **Multi-file config**: configs can be merged from multiple JSON/YAML files, CLI args, and environment variables.
- **ENV overrides**: `FREQTRADE__<section>__<key>` pattern overrides any config value.
- **JSON Schema**: complete schema at `config_schema/` — validates all config dimensions including exchange, strategy, pairlists, protections, FreqAI, and RPC.
- **Secrets**: `config_secrets.py` handles API keys and sensitive data separately from main config.

## Related skills
- See `.agents/skills/freqtrade__trading-engine` — consumes the configuration
- See `.agents/skills/freqtrade__exchange-integration` — exchange config (API keys, endpoints)
