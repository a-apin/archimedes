# Account Authentication and App Boundary Implementation Plan

> **REQUIRED SUB-SKILL:** Use the executing-plans skill to implement this plan task-by-task.

**Goal:** Make Better Auth users canonical, separate public site from protected app, link wallets only after proof, and gate Quant Lab centrally.

**Architecture:** Run Better Auth 1.6.25 as small Node sidecar beside existing FastAPI and nginx containers, backed by existing PostgreSQL. nginx sends `/api/auth/*` to Better Auth and performs server-side session checks for `/app/*`; FastAPI resolves same Better Auth cookie through sidecar and applies dependencies for API authorization. Existing wallet-keyed records remain intact for on-chain provenance while additive `owner_user_id` links and verified wallet claims establish canonical ownership.

**Tech Stack:** React 19/Vite 8/npm, Better Auth 1.6.25/Node 22/`pg`, FastAPI/SQLAlchemy/Alembic/PostgreSQL, viem/Circle Modular Wallets, nginx, Docker Compose, ECS Fargate.

---

## Repository findings

- Frontend: React 19 SPA built by Vite 8; hand-written History API router; npm lockfile in `ui/`; no workspace and no frontend test framework.
- Backend: Python 3.12 FastAPI; synchronous SQLAlchemy; Alembic owns PostgreSQL migrations; SQLite `create_all` supports hermetic tests.
- Stores: Aurora/PostgreSQL durable data and Redis runtime state. Current wallet-keyed records include `user_profiles`, strategy owner fields, strategy generators, vault metadata, chat, marketplace rows, and wallet identity ledger.
- Current auth: EIP-4361 SIWE challenge creates HMAC cookie. Browser wallet or Circle passkey connection automatically attempts SIWE; this currently conflates wallet control with app session.
- Wallets: EIP-6963/MetaMask/Coinbase EOA support through viem; Circle Modular Wallet SDK creates/reopens passkey-controlled MSCAs and supports ERC-1271/6492 proof verification.
- Routes: `/` and nearly all SPA paths are served publicly by nginx. Wallet gates are mostly client-side. Several write/private APIs use SIWE dependencies; public discovery APIs intentionally remain anonymous.
- Feature flags: none centralized. `/quant` is a normal wallet-gated route and its optimization APIs are public apart from rate limiting.
- Commands: `npm ci`, `npm run lint`, `npm run build` in `ui/`; `pytest -m "not integration"`; `ruff format --check .`; `ruff check --select E9,F63,F7,F40,F82 .`; `cd backend && alembic upgrade head`; `docker compose config`; production build via backend/nginx Dockerfiles. Analytics and Foundry are unchanged and only need smoke checks if shared code unexpectedly reaches them.
- Existing unrelated work in original checkout: `.gitignore`, `context.md`, `research.md`, `reports/`. Implementation uses isolated worktree and must never stage those paths.
- `ui/src/components/Architecture.jsx` and architecture-page content are excluded. Route-shell references may remain, but page file receives no edits.

## Decision record

1. **Chosen: Better Auth sidecar + shared Postgres.** Uses Better Auth itself, preserves FastAPI, and adds one small runtime container.
2. Rejected: migrate backend to Node/SSR. Excessive rewrite and unrelated risk.
3. Rejected: reproduce Better Auth tables/cookies in Python. Not Better Auth and couples Python to undocumented token internals.
4. Initial methods: email/password plus Google and GitHub when credentials exist. Better Auth passkeys stay out of this PR because Circle already asks users for a wallet passkey; adding another passkey without product UX would create the duplicate-passkey problem the objective forbids.
5. Better Auth user ID owns app data. Wallet address remains provenance and transaction authority, never app-session identity.
6. Legacy wallet data is claimed only during successful proof for that wallet. No bulk or address-only claim.

## Acceptance evidence

- `auth` Node tests prove sign-up, sign-in, session read, invalid session rejection, and sign-out revocation.
- Backend tests prove session dependency, public health/landing support contract, protected API rejection, wallet challenge validation/replay/uniqueness, safe unlink/primary behavior, legacy-data claim, user ownership, and feature defaults.
- UI Node tests prove route classification, safe redirects, and typed feature navigation; UI lint/build prove compilation and code splitting.
- nginx config test proves `/app` auth subrequest and `/api/auth` sidecar routing.
- Alembic upgrade/downgrade/upgrade on temporary SQLite proves migration reversibility; schema comparison covers Better Auth tables and additive ownership fields.
- Full backend non-integration suite, Ruff gates, UI tests/lint/build, auth tests/check/build, Docker image builds, `docker compose config`, and relevant audits run before commits are finalized.

### Task 1: Better Auth service and schema

**Files:**
- Create: `auth/package.json`, `auth/package-lock.json`, `auth/server.js`, `auth/auth.js`, `auth/Dockerfile`, `auth/test/auth.test.js`
- Create: `backend/archimedes/models/account.py`
- Create: `backend/migrations/versions/<revision>_better_auth_accounts_and_linked_wallets.py`
- Modify: `backend/archimedes/db.py`, `backend/migrations/env.py`

**Steps:**
1. Write Node tests for email registration/login/session/logout and run `cd auth && npm test`; verify RED because service does not exist.
2. Add Better Auth 1.6.25 and `pg` 8.22.0 only. Configure `auth_users`, `auth_accounts`, `auth_sessions`, `auth_verifications`, secure cookies, trusted origins, encrypted OAuth tokens, email/password, and env-conditional Google/GitHub.
3. Add SQLAlchemy compatibility models plus linked-wallet/challenge models; generate one Alembic revision.
4. Run Node tests and migration upgrade/downgrade/upgrade; verify GREEN.
5. Commit `feat(auth): add Better Auth account service`.

### Task 2: Canonical FastAPI session and user ownership

**Files:**
- Create: `backend/archimedes/api/account_auth.py`, `backend/tests/test_account_auth.py`
- Modify: `backend/archimedes/main.py`
- Modify minimal ownership paths in strategy/proposal models and generation services/routes.

**Steps:**
1. Write failing tests for missing, valid, invalid, expired, and revoked Better Auth sessions.
2. Add session-resolution middleware calling sidecar `/api/auth/get-session`, sanitized `CurrentUser`, and `require_current_user` dependency.
3. Add `owner_user_id` to new generation jobs/strategies/passports/proposals. Keep `owner_wallet` only as compatibility/provenance and set it only from a verified linked wallet.
4. Update owner visibility queries to prefer user ID and preserve published/public examples.
5. Run focused generation/ownership tests, then commit `refactor(identity): make account user canonical`.

### Task 3: Verified optional wallet linking

**Files:**
- Create: `backend/archimedes/api/wallet_routes.py`, `backend/tests/test_wallet_linking.py`
- Modify: `backend/archimedes/api/auth_siwe.py` only to reuse signature verification helpers; remove its router from live app.
- Create: `ui/src/wallet-link.js`
- Modify: `ui/src/components/WalletConnect.jsx`, `ui/src/config.js`

**Steps:**
1. Write failing API tests for unsigned, valid, invalid, expired, replayed, domain/URI/chain mismatch, current-user idempotence, cross-user conflict, primary selection, and safe unlink.
2. Issue random, 5-minute, user/address/chain/domain/URI-bound challenges; store nonce hash; consume with conditional update.
3. Verify EOA and Circle ERC-1271/6492 signatures using existing production verifier.
4. Create linked wallet with unique `chain_id:lowercase-address`, preserve checksum display, and never transfer across users.
5. After proof only, attach matching legacy profile/strategy/passport/proposal/vault records whose user link is empty. Reject platform-controlled Circle addresses.
6. Change wallet UI from “wallet signs into app” to “authenticated user links wallet.” Signature rejection leaves connection unlinked.
7. Run focused API/UI tests and commit `feat(wallets): link verified wallets to users`.

### Task 4: Public/private route split, account settings, and Quant flag

**Files:**
- Create: `ui/src/PublicSite.jsx`, `ui/src/AuthenticatedApp.jsx`, `ui/src/components/PublicLayout.jsx`, `ui/src/components/AuthPage.jsx`, `ui/src/components/AccountSettings.jsx`, `ui/src/auth-client.js`, `ui/src/routes.js`, `ui/test/routes.test.js`
- Modify: `ui/src/App.jsx`, `ui/src/main.jsx`, `ui/src/components/Layout.jsx`, `ui/src/api.js`, `ui/src/App.css`
- Create: `backend/archimedes/features.py`, `backend/archimedes/api/feature_routes.py`, `backend/tests/test_features.py`
- Modify: `backend/archimedes/main.py`, `backend/archimedes/api/portfolio_routes.py`, `nginx/nginx.conf`

**Steps:**
1. Write failing route/redirect/flag tests.
2. Keep public routes under `/`; put authenticated shell under `/app`; preserve legacy redirects.
3. Lazy-load authenticated app so landing does not load Circle/viem/application chunks.
4. Add sign-in/sign-up states and account settings for profile, methods, wallets, primary/unlink, and sign-out.
5. Add typed `quantPage`: true in development/tests, false in production; public safe endpoint exposes only client flags; backend Quant APIs return 404 when off; nav and route use same result.
6. Add nginx server-side `/app` auth check through sidecar. Client checks remain UX only.
7. Run UI/backend/nginx tests and commit `feat(app): separate public and authenticated routes`.

### Task 5: Runtime wiring and documentation

**Files:**
- Modify: `docker-compose.yml`, `docker-compose.production.yml`, `nginx/Dockerfile` only if required, `infra/ecs.tf`, `infra/ecr.tf`, `.github/workflows/deploy.yml`, `.github/workflows/quality-gate.yml`
- Modify: `.env.example`, `backend/.env.example`, `ui/.env.example`
- Create: `docs/authentication.md`, `.github/PULL_REQUEST_TEMPLATE_ACCOUNT_AUTH.md` or `docs/plans/2026-07-28-account-auth-pr.md`
- Modify navigation docs that are not Architecture page.

**Steps:**
1. Add auth image/service/sidecar health checks and localhost/Compose upstream configuration.
2. Wire only secret names from SSM; never values. Document operator seeding without applying or reading production secrets.
3. Add auth/UI test jobs and auth supply-chain audit.
4. Document env, callbacks, trusted origins, sessions, wallet proof, Circle separation, feature flags, QA, migration, rollback, and follow-ups.
5. Draft PR description with requested summary/decision/rationale/tradeoffs/testing/deployment/review sections and `@dbrowneup` tag.
6. Validate static infra and commit `chore(deploy): wire account authentication service` plus `docs: document account authentication rollout` if separation improves review.

### Task 6: Final verification and review

1. Run focused checks, then full applicable checks listed in Acceptance evidence.
2. Confirm `git diff --name-only origin/main...HEAD` excludes `ui/src/components/Architecture.jsx` and all pre-existing unrelated paths.
3. Use fresh read-only security, backend, frontend, and infra review only where risk warrants; original agent remains sole writer.
4. Fix material findings for at most three rounds; re-run affected checks.
5. Stage only owned paths, inspect staged diff, create remaining Conventional Commits, and report local PR-ready status. Never push, merge, deploy, apply migrations to production, or mutate secrets.
