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

- `/`, `/architecture`, `/sign-in`, `/sign-up`, and `/reset-password`: public SPA routes.
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

### Signup friction and abuse bounds

Email verification is **wired but not yet enforced**. Every signup sends a
verification email (`auth/auth.js` `emailVerification.sendOnSignUp` via
`auth/mailer.js` — SES in deployed environments, a console mailer in local
compose so dev needs no AWS). Sign-in refusal for unverified accounts is
gated on `EMAIL_VERIFICATION_ENFORCED` (Better Auth
`requireEmailVerification`), currently `"false"` in `infra/ecs.tf` because
the AWS account is in the **SES sandbox** — sandbox can only deliver to
individually-verified addresses, so enforcing now would lock out every real
signup. SES state: the `archimedes-arc.com` domain identity is created, its
DKIM CNAMEs are live in Route53, and the production-access request is filed.
**When it clears, flip `EMAIL_VERIFICATION_ENFORCED` to `"true"` — an env
change, not a deploy.** The task role's `ses:SendEmail` is scoped to the one
domain identity (`ecs_task_ses_send` in `infra/ecs.tf`). A failed send is
deliberately fail-soft (loud log, signup proceeds) so a sandboxed or degraded
SES never 500s registration.

Independently of enforcement, disposable accounts are bounded by three layers:

1. Better Auth's production rate limiter: `/sign-up/email` at 3 per 10
   minutes (`auth/auth.js` `rateLimit.customRules`).
2. nginx's `/api/auth/` `limit_req` zone.
3. Decisively: the **per-IP daily generation cap**
   (`backend/archimedes/services/generation_quota.py`). Generation is the
   endpoint that spends money, and its IP bucket does not reset when a new
   account is minted — so a signup farm gains nothing where it matters.

### Password reset (#1323)

Forgotten-password recovery is wired end to end — before this, a lost password
permanently destroyed the account that owns the user's strategies. `auth/auth.js`
`emailAndPassword.sendResetPassword` mirrors `sendVerificationEmail`: same mailer
(`auth/mailer.js`), same fail-soft-on-send-failure handling. Better Auth's built-in
`POST /api/auth/request-password-reset` returns the identical response body/status for
a known and an unknown email — `sendResetPassword` is only ever invoked for a real
account, so the fail-soft catch matters doubly here: a 500 that only real accounts could
trigger would itself leak account existence via status code. `POST
/api/auth/reset-password` consumes the mailed token, sets the new password, and (via
`revokeSessionsOnPasswordReset: true`) signs out every existing session.

UI: `ui/src/components/AuthPage.jsx` — "Forgot password?" on the sign-in view opens an
inline request form (`ui/src/auth-client.js` `requestPasswordReset`); the mailed link
points at the public `/reset-password` route, which reads `?token=` and calls
`resetPassword`. A sign-in refused with 403 (unverified email) surfaces a "Resend
verification email" action calling the existing `send-verification-email` endpoint
(`resendVerificationEmail`) — closing the matching lockout for a lost verification mail.

### Account linking (#1420 follow-up)

The owner has one email/password account and wants Google and GitHub to reach the SAME
account, so any of the three signs in as one identity. `auth/auth.js`'s `account.
accountLinking` config (verified against the **installed** `better-auth@1.6.25` source —
see the long comment on that config block for exact file/line pointers) drives two
independent paths that behave differently and are both live:

**1. Implicit auto-link** — a plain "Continue with Google/GitHub" click on the sign-in
screen for an email that already owns a password account. Stays **off**
(`disableImplicitLinking: true`, unchanged from before this feature) and refuses
unconditionally (`?error=account_not_linked`), regardless of either side's
`emailVerified`. **Round-2 review finding (major):** an earlier revision of this PR
flipped `disableImplicitLinking` to `false` and added `trustedProviders: ['google',
'github']` to enable this path once the base account was verified. That was reverted
before merge — `trustedProviders` is consulted in exactly one place in the installed
library (skipping the re-check of the *provider's* own `emailVerified` claim) and does
nothing for the explicit flow below, which is the half that actually ships; arming
implicit auto-link is its own security decision, deferred to a future change made and
reviewed on its own once `EMAIL_VERIFICATION_ENFORCED` is genuinely on (until then,
`requireLocalEmailVerified` — the library's still-unset default `true` — would have kept
this refused for most accounts anyway, but not once real accounts start verifying). See
the long comment on `accountLinking` in `auth/auth.js` for the exact gate lines.

**2. Explicit link** — signed-in "Link Google" / "Link GitHub" in Account Settings →
Connected accounts (`ui/src/components/AccountSettings.jsx`, `linkSocial`/`listAccounts`/
`unlinkAccount` in `ui/src/auth-client.js`, calling Better Auth's own `/link-social`,
`/list-accounts`, `/unlink-account`). This is the path that works **today**, regardless of
the base account's verification state: proof of ownership comes from the live session
`/link-social` was called with, not from `emailVerified`. It still enforces, unconditionally:
the provider's own `emailVerified` claim (no `trustedProviders` needed — Google/GitHub
both attest verified emails for their OAuth users), and `allowDifferentEmails` (kept
`false`) — the OAuth account's email must equal the signed-in account's email, or the
callback redirects with `?error=email_doesn't_match`. The state/PKCE/CSRF handshake for
the OAuth round trip (the `state` param plus its double-submit `better-auth.state`
cookie) is entirely library-managed on both ends; nothing here hand-rolls any part of it.

Both `/link-social` and `/unlink-account` also now require a **session younger than
`session.freshAge`** (pinned explicitly in `auth/auth.js`, 24h) — a round-2 review
blocker finding: the library gates `/unlink-account` behind its own
`freshSessionMiddleware` but left `/link-social` ungated, so a session stale enough to
have lost the ability to *remove* a credential could still *add* one. `auth/auth.js`'s
`hooks.before` mirrors the library's own check onto `/link-social` so both directions move
together.

Unlinking: `/unlink-account` refuses to remove an account's last remaining credential
(`allowUnlinkingAll` stays `false` → `FAILED_TO_UNLINK_LAST_ACCOUNT`, HTTP 400) —
server-enforced regardless of the UI. `AccountSettings.jsx` additionally disables the
Unlink control in that state client-side (`canUnlink()` in `ui/src/account-linking.js`) so
the control is never even clickable, not just rejected after a round trip.

The `?error=account_not_linked` message on `/sign-in` (routed there from `/`'s bare
redirect — see routing note above) now points at this: sign in with the password, then
link the provider under Account Settings → Connected accounts so it signs in directly next
time (`ui/src/auth-errors.js` `oauthErrorMessage`). A second, separate map in the same
file, `linkErrorMessage`, covers the explicit flow's own error codes
(`email_doesn't_match`, `account_already_linked_to_different_user`, `access_denied`, and a
generic fallback) and is rendered on Account Settings via the `error` query param the
`/link-social` → `/callback/:id` round trip redirects back with — the same generic
`route.error` plumbing `routes.js` already parses for every route, not a new mechanism.

Both paths, and the unlink guard, are covered by real behavioral tests in
`auth/test/auth.test.js` that drive the actual Better Auth endpoints (`auth.api.
signInSocial`/`linkSocialAccount`/`unlinkAccount`/`listUserAccounts`) against an in-memory
sqlite adapter, faking only the network boundary (Google's token endpoint) via a `fetch`
mock — the authorization-URL construction, CSRF state, and linking decisions are the real
library code. UI wiring and the `canUnlink` guard are covered in `ui/test/auth-client.test.js`,
`ui/test/auth-errors.test.js`, and `ui/test/account-settings.test.js`.

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
alembic downgrade b41c7d9e2a05
```

The target is `b41c7d9e2a05` — the revision immediately below this feature's
own two migrations, NOT `3f643d292e04`, this branch's original fork point. As `main` moved, its backtest-provenance migrations (`363d1c6ff0c0`, then
#1242's `b41c7d9e2a05`) were serialized between the fork point and this
feature's two migrations (see the re-pointing note in
`migrations/versions/9ad1c4e2b7f0_add_better_auth_and_linked_wallets.py`).
Downgrading past it to `3f643d292e04` would collaterally drop four
`backtest_results` provenance columns unrelated to auth — and that damage does
not round-trip: re-upgrading re-creates the columns but backfills them with
*reconstructed* values, so every genuinely-recorded `source_git_sha` is
permanently lost and real provenance comes back relabeled as inferred.

Then remove auth sidecar and nginx auth upstream together. Never roll nginx to config
that references absent auth service, or deploy new nginx before ECS task has auth
container. CI fails closed when current task definition lacks `auth`.
