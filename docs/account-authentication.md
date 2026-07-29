# Account authentication and linked wallets

## Identity boundary

Better Auth user ID is canonical application identity. Better Auth owns account,
credential, session, and OAuth state in PostgreSQL. FastAPI never parses or signs
Better Auth cookies; middleware asks colocated auth service for `/api/auth/get-session`
and stores immutable current user on request state.

Wallets are optional verified external accounts. Connecting MetaMask, another EIP-1193
wallet, or Circle Modular Wallet does not create application user or session. Circle
passkey authorizes Circle smart wallet only; it is not Archimedes login credential.

Routes:

- `/`, `/architecture`, `/sign-in`, and `/sign-up`: public SPA routes.
- `/app/*`: Better Auth session required by nginx `auth_request` and client router.
- private FastAPI routes: repeat session checks through `require_current_user`.
- on-chain wallet actions: additionally require wallet present in `linked_wallets` for
  current user and selected chain/address.

## Services

`auth/` runs Better Auth 1.6.25 on Node 22, beside nginx and FastAPI. nginx owns public
namespace routing:

- `/api/auth/*` -> Better Auth
- `/api/*` -> FastAPI
- `/app/*` -> auth subrequest, then SPA only on success

All services share PostgreSQL. Better Auth writes `auth_users`, `auth_sessions`,
`auth_accounts`, `auth_verifications`, and `auth_rate_limits`; SQLAlchemy maps them for foreign
keys. Application writes `linked_wallets`, `wallet_link_challenges`, and nullable
`owner_user_id` compatibility columns.

## Login configuration

Email/password is always enabled with 12-character minimum and Better Auth password
hashing. Google and GitHub appear only when both client ID and secret for provider are
configured. OAuth callback URLs are:

- `https://<domain>/api/auth/callback/google`
- `https://<domain>/api/auth/callback/github`

Required production configuration:

- `BETTER_AUTH_SECRET`: random value of at least 32 characters, stored at
  `/archimedes/prod/BETTER_AUTH_SECRET` in SSM.
- `BETTER_AUTH_URL=https://<domain>`
- `BETTER_AUTH_TRUSTED_ORIGINS=https://<domain>`
- `NODE_ENV=production`
- `BETTER_AUTH_INTERNAL_URL=http://127.0.0.1:3000` for Fargate FastAPI sidecar calls.

Optional provider pairs: `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`, and
`GITHUB_CLIENT_ID` + `GITHUB_CLIENT_SECRET`. Never commit values. For ECS, seed
complete pair in SSM, then set matching `TF_VAR_google_oauth_enabled=true` or
`TF_VAR_github_oauth_enabled=true` and apply task-definition change. Flags default false
so absent optional parameters cannot block email/password startup.

## Wallet linking

1. Authenticated user requests `POST /api/wallets/challenge` with address, chain ID,
   and provider.
2. Server stores SHA-256 nonce hash bound to user, normalized address, chain, domain,
   URI, provider, issue time, and five-minute expiry.
3. Wallet signs server-built EIP-4361 message. EOA recovery is attempted first;
   ERC-6492/EIP-1271 smart-wallet verification uses Arc RPC second.
4. `POST /api/wallets/verify` atomically consumes challenge before creating link.
5. Unique normalized identity is `<chain-id>:<lowercase-address>`. Existing link owned
   by another user returns 409; ownership is never transferred automatically.

Valid proof claims only matching legacy rows whose `owner_user_id` is still null.
Rows already owned by another user remain untouched. Platform-controlled Circle
wallets in `controlled_wallets` cannot be silently claimed. Unlinking is refused while
wallet still owns legacy strategy, passport, proposal, profile, or vault metadata rows.

## Quant feature flag

Server is source of truth through `GET /api/features`.

- `FEATURE_QUANT=true|false` explicitly overrides.
- Blank/unset defaults enabled in development and tests.
- Blank/unset defaults disabled when `APP_ENV=production`.

Same result hides navigation, rejects direct `/app/quant`, and returns 404 from Quant
optimizer, parameter-sweep, CVaR, and Greeks APIs.

## Migration and rollout

Revisions:

1. `9ad1c4e2b7f0`: Better Auth and linked-wallet tables.
2. `b7e3f1a2c9d4`: nullable canonical ownership columns and indexes.

Production order:

1. Back up Aurora and verify restore point.
2. Create `archimedes-auth` ECR repository and seed `BETTER_AUTH_SECRET` in SSM.
3. Build/push backend, nginx, and auth images.
4. Run existing Alembic preflight, then `alembic upgrade head` before service rollout.
5. Apply ECS task definition containing auth sidecar and nginx auth upstream.
6. Roll application task; smoke public `/`, auth providers, signup/signin/signout,
   anonymous `/app` redirect, authenticated `/app`, and anonymous protected API 401.
7. Configure OAuth callbacks only after provider secrets exist.

No existing wallet data is bulk-assigned during migration. Ownership moves lazily only
after cryptographic link proof.

## Rollback

Preferred rollback is application-only: restore prior backend/nginx task definition and
leave additive tables/nullable columns in place. This preserves newly created accounts,
sessions, links, and ownership data for forward recovery.

Do not run schema downgrade during normal rollback. Downgrade drops identity data. Only
after backup and explicit confirmation that new tables/columns contain no data requiring
preservation:

```bash
cd backend
alembic downgrade 3f643d292e04
```

Then remove auth sidecar and nginx auth upstream together. Never roll nginx to config
that references absent auth service, or deploy new nginx before ECS task has auth
container. CI fails closed when current task definition lacks `auth`.
