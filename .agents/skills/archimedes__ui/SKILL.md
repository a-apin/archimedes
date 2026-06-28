---
name: archimedes__ui
description: React/Vite frontend with SIWE authentication, portfolio dashboard, and strategy explorer
triggers: [archimedes ui, archimedes frontend, archimedes react, archimedes dashboard, archimedes siwe]
---

# Archimedes UI

**Source**: archimedes
**Category**: Core

## When to use this skill
Working on the Archimedes frontend: React components, SIWE auth flow, portfolio/strategy visualization, or styling.

## Key files and folders
- **UI root**: `/home/ricardo/github/archimedes/ui/`
- **Entry point**: `/home/ricardo/github/archimedes/ui/index.html`
- **Config**: `/home/ricardo/github/archimedes/ui/vite.config.js`, `uno.config.js`
- **Dockerfile**: `/home/ricardo/github/archimedes/ui/Dockerfile`

## Key concepts
- React + Vite with UnoCSS for styling
- SIWE (Sign-In with Ethereum) for wallet-based authentication
- Connects to the FastAPI backend at `/api/*`

## Related skills
- See `.agents/skills/archimedes__backend` — the API layer the UI consumes
- See `.agents/skills/shared__arc-blockchain` — for wallet connection (MetaMask / Circle Wallet)
