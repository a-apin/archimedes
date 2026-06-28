---
name: arc-nanopayments__dashboard
description: Next.js app for ARC nanopayments — pages, layout, login, dashboard UI with Radix components
triggers: [nanopayments dashboard, arc nanopayments ui, nextjs nanopayments]
---

# ARC Nanopayments Dashboard

**Source**: arc-nanopayments
**Category**: Core

## When to use this skill
Working on the ARC Nanopayments Next.js frontend: pages, layout, login flow, dashboard components, or UI styling.

## Key files and folders
- **App root**: `/home/ricardo/github/arc-nanopayments/app/`
- **Pages**: `page.tsx` (login), `dashboard/page.tsx`, `dashboard/layout.tsx`
- **Components**: `/home/ricardo/github/arc-nanopayments/components/dashboard/`
- **UI primitives**: `/home/ricardo/github/arc-nanopayments/components/ui/`
- **Styles**: `/home/ricardo/github/arc-nanopayments/app/globals.css`
- **Config**: `/home/ricardo/github/arc-nanopayments/next.config.ts`, `tsconfig.json`
- **Hooks**: `/home/ricardo/github/arc-nanopayments/hooks/`

## Key concepts
- Next.js 16 with App Router
- Radix UI primitives (dialog, dropdown, select, tabs, tooltip)
- Login flow in `app/page.tsx` using server actions (`app/actions.ts`)
- Dashboard with gateway balance, withdrawal management
- Styled with Tailwind/postcss

## Related skills
- See `.agents/skills/arc-nanopayments__supabase-auth` — authentication layer
- See `.agents/skills/arc-nanopayments__x402-gateway` — gateway integration in the dashboard
- See `.agents/skills/arc-nanopayments__wallet-management` — wallet operations from the UI
