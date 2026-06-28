---
name: arc-nanopayments__supabase-auth
description: Supabase authentication integration — server/client setup, session management, and login actions
triggers: [nanopayments auth, arc supabase, nanopayments login, nanopayments session]
---

# ARC Nanopayments Supabase Auth

**Source**: arc-nanopayments
**Category**: Infrastructure

## When to use this skill
Working on authentication: Supabase client setup, server-side auth, session management, login/logout flows.

## Key files and folders
- **Server client**: `/home/ricardo/github/arc-nanopayments/lib/supabase/server.ts`
- **Browser client**: `/home/ricardo/github/arc-nanopayments/lib/supabase/client.ts`
- **Proxy client**: `/home/ricardo/github/arc-nanopayments/lib/supabase/proxy.ts`
- **Login action**: `/home/ricardo/github/arc-nanopayments/app/actions.ts` (login server action)
- **Login page**: `/home/ricardo/github/arc-nanopayments/app/page.tsx`

## Key concepts
- Supabase SSR with `@supabase/ssr` for Next.js App Router
- Server component auth via `server.ts`, client component auth via `client.ts`
- Login uses Supabase email/password or magic link
- Session persists for API calls to x402 gateway

## Related skills
- See `.agents/skills/arc-nanopayments__dashboard` — the UI that consumes auth
- See `.agents/skills/arc-nanopayments__wallet-management` — wallet operations scoped to authenticated users
