import { betterAuth } from 'better-auth'
import { APIError, createAuthMiddleware, getSessionFromCtx } from 'better-auth/api'
import pg from 'pg'

import { createMailer } from './mailer.js'

const { Pool } = pg

function csv(value) {
  return String(value ?? '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
}

// Verification mail is SENT on every signup regardless of this flag (so
// verified_at data accrues from day one); the flag controls whether an
// unverified account is REFUSED sign-in. Explicit opt-in ("true") because
// SES starts in sandbox — enforcement flips on via env once production
// sending access is granted, with no code change.
// The literal shipped in .env.example. Public by design, refused in production
// by createAuth() — keep these two in lockstep; auth/test/auth.test.js pins it.
export const PLACEHOLDER_SECRET = 'insecure-local-dev-placeholder-change-me-before-any-deploy'

export function emailVerificationEnforced(env = process.env) {
  return env.EMAIL_VERIFICATION_ENFORCED === 'true'
}

export function enabledProviders(env = process.env) {
  return [
    env.GOOGLE_CLIENT_ID && env.GOOGLE_CLIENT_SECRET ? 'google' : null,
    env.GITHUB_CLIENT_ID && env.GITHUB_CLIENT_SECRET ? 'github' : null,
  ].filter(Boolean)
}

function socialProviders(env) {
  return Object.fromEntries(enabledProviders(env).map(provider => [provider, {
    clientId: env[`${provider.toUpperCase()}_CLIENT_ID`],
    clientSecret: env[`${provider.toUpperCase()}_CLIENT_SECRET`],
  }]))
}

const CONNECTED_ACCOUNT_LABELS = { credential: 'Email & password', google: 'Google', github: 'GitHub' }

// Round-2 review finding (minor): linking or unlinking a sign-in credential
// used to be completely silent to the account owner — no email, nothing —
// despite being exactly the kind of change an account-takeover attempt would
// make (see the freshAge/hooks.before comment above: this is the other half
// of closing that off, an out-of-band signal alongside the in-band guard).
// Wired via databaseHooks.account.{create,delete}.after below.
//
// MUST NOT throw. Unlike sendResetPassword/sendVerificationEmail above
// (hand-rolled fire-and-forget, or awaited inside their OWN try/catch),
// better-auth's databaseHooks create.after/delete.after are awaited by the
// library itself as part of the write (node_modules/@better-auth/core/dist/
// context/transaction.mjs: `for (const hook of pendingHooks) await hook();`
// followed by `if (hasError) throw error;`) — an uncaught throw here would
// fail the /link-social or /unlink-account request that triggered it, over
// a notification email that is not the actual security control. Every
// awaited step is inside the try/catch for exactly that reason.
async function notifyAccountChange(mailer, endpointContext, account, action) {
  try {
    // 'credential' fires on every signup (the password account itself) —
    // that already gets its own "verify your email" mail, and a second
    // "new sign-in method added" mail for the same event would just be
    // noise, not a signal. Removing a 'credential' account (unlinking the
    // password while Google/GitHub remain) is a real, later, distinct
    // event and still notifies.
    if (action === 'added' && account.providerId === 'credential') return
    const user = await endpointContext?.context?.internalAdapter?.findUserById(account.userId)
    if (!user?.email) return
    const label = CONNECTED_ACCOUNT_LABELS[account.providerId] || account.providerId
    const verb = action === 'added' ? 'added to' : 'removed from'
    await mailer.send({
      to: user.email,
      subject: `A sign-in method was ${verb} your Archimedes account`,
      text:
        `${label} was just ${action === 'added' ? 'linked as a sign-in method on' : 'unlinked as a sign-in method from'} `
        + 'your Archimedes account.\n\n'
        + "If this wasn't you, sign in and review Account Settings → Connected accounts immediately"
        + (action === 'added' ? ', then remove it and reset your password.' : '.'),
    })
  } catch (error) {
    console.error(`account ${action} notification email send failed:`, error instanceof Error ? error.name : 'UnknownError')
  }
}

export function createAuth({ database, env = process.env, mailer = createMailer(env) } = {}) {
  const production = env.NODE_ENV === 'production'
  const baseURL = env.BETTER_AUTH_URL || 'http://localhost:5173'
  const secret = env.BETTER_AUTH_SECRET

  if (!secret || secret.length < 32) {
    throw new Error('BETTER_AUTH_SECRET must contain at least 32 characters')
  }

  // .env.example ships PLACEHOLDER_SECRET so that `cp .env.example .env` gives a
  // working LOCAL stack — without it every docker compose command, including
  // `down`, dies during interpolation on `${BETTER_AUTH_SECRET:?}`. The value is
  // public by construction, so it must never boot a deployed environment: it
  // signs session cookies, and anyone reading the repo could forge them.
  if (production && secret === PLACEHOLDER_SECRET) {
    throw new Error(
      'BETTER_AUTH_SECRET is still the public .env.example placeholder. '
      + 'Set a real secret (production reads SSM /archimedes/prod/BETTER_AUTH_SECRET). '
      + 'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"',
    )
  }

  // node-postgres does NOT honor libpq's `sslmode` query parameter — it must
  // be translated into an explicit `ssl` config or the connection goes out in
  // plaintext and TLS-enforcing servers (Aurora) reject it. `sslmode=require`
  // in libpq means "encrypt, do not verify the CA", so rejectUnauthorized:false
  // is the faithful translation, not a shortcut. verify-ca/verify-full would
  // need the RDS CA bundle shipped in the image (follow-up: #1284's image work).
  // pg's connection-string parser gives an in-URL `sslmode` precedence over the
  // constructor's `ssl` object — with sslmode=require left in the string, full
  // certificate verification still ran and failed on Aurora with
  // UNABLE_TO_GET_ISSUER_CERT_LOCALLY (no RDS CA in the image). So: STRIP the
  // parameter from the string and pass the ssl config explicitly. Proven
  // against live Aurora in-container before this commit: SELECT succeeds.
  // verify-full + the RDS CA bundle remains the #1284 follow-up.
  const rawUrl = env.DATABASE_URL || ''
  const wantsTls = /[?&]sslmode=(require|prefer|verify-ca|verify-full)/.test(rawUrl)
  const connectionString = rawUrl.replace(/[?&]sslmode=[^&]+/, '')
  const db = database ?? new Pool({
    connectionString,
    ...(wantsTls ? { ssl: { rejectUnauthorized: false } } : {}),
  })

  return betterAuth({
    appName: 'Archimedes',
    baseURL,
    secret,
    database: db,
    trustedOrigins: csv(env.BETTER_AUTH_TRUSTED_ORIGINS || baseURL),
    emailAndPassword: {
      enabled: true,
      autoSignIn: false,
      minPasswordLength: 12,
      maxPasswordLength: 128,
      revokeSessionsOnPasswordReset: true,
      requireEmailVerification: emailVerificationEnforced(env),
      sendResetPassword: async ({ user, url }) => {
        // Fire-and-forget ON PURPOSE — do not `await` the send. Better Auth
        // already returns an identical response body/status for a known vs.
        // unknown email (requestPasswordReset in
        // better-auth/dist/api/routes/password.mjs: an unknown address
        // never reaches this callback at all, it takes a dummy-lookup
        // early-return instead), which is the anti-enumeration design. But
        // without `advanced.backgroundTasks.handler` configured, Better
        // Auth's own dispatcher (`runInBackgroundOrAwait` in
        // better-auth/dist/context/create-context.mjs) does `await promise`
        // on whatever this callback returns — so an awaited mailer.send()
        // here would make a known-address request measurably slower than an
        // unknown-address one (the real SES round trip vs. an immediate
        // early return), reopening the same enumeration channel through
        // response TIMING instead of status code. Not awaiting the send
        // makes this callback's own duration independent of mailer latency
        // regardless. The `.catch` below is what keeps a mailer failure
        // fail-soft (same reasoning as sendVerificationEmail below, and
        // load-bearing here for the same anti-enumeration reason: a 500
        // that only known accounts could trigger would leak account
        // existence via status code) — loud single line, logged
        // asynchronously after the response has already gone out.
        mailer.send({
          to: user.email,
          subject: 'Reset your Archimedes password',
          text:
            'A password reset was requested for your Archimedes account:\n\n'
            + `${url}\n\n`
            + 'If you did not request this, ignore this message — your password will not change.',
        }).catch(error => {
          console.error('reset password email send failed:', error instanceof Error ? error.name : 'UnknownError')
        })
      },
    },
    emailVerification: {
      sendOnSignUp: true,
      autoSignInAfterVerification: true,
      sendVerificationEmail: async ({ user, url }) => {
        try {
          await mailer.send({
            to: user.email,
            subject: 'Verify your Archimedes account',
            text:
              'Verify your email address to activate your Archimedes account:\n\n'
              + `${url}\n\n`
              + 'If you did not create this account, ignore this message.',
          })
        } catch (error) {
          // Fail-soft ON PURPOSE while SES is in sandbox: an undeliverable
          // verification mail must not 500 the signup. Loud single line;
          // requireEmailVerification is what actually gates sign-in.
          console.error('verification email send failed:', error instanceof Error ? error.name : 'UnknownError')
        }
      },
    },
    socialProviders: socialProviders(env),
    user: { modelName: 'auth_users' },
    session: {
      modelName: 'auth_sessions',
      expiresIn: 60 * 60 * 24 * 7,
      updateAge: 60 * 60 * 24,
      cookieCache: { enabled: false },
      // Explicit, not the library's inherited default (round-2 review
      // finding, blocker). Left unset, better-auth defaults freshAge to 24h
      // regardless of expiresIn (node_modules/better-auth/dist/context/
      // create-context.mjs:148: `freshAge: options.session?.freshAge ===
      // void 0 ? 3600 * 24 : options.session.freshAge`) — a 7-day session
      // silently carrying a 1-day "fresh" window nobody chose. Pinned here
      // so it is a deliberate value, tracked by
      // auth/test/auth.test.js's "freshAge is pinned, not an inherited
      // default" test. This ONE value now governs both the library's own
      // freshSessionMiddleware (/unlink-account, /list-sessions, ...) and
      // the hand-rolled twin on /link-social below (hooks.before) — see
      // that comment for why both must move together.
      freshAge: 60 * 60 * 24,
    },
    account: {
      modelName: 'auth_accounts',
      encryptOAuthTokens: true,
      // #1420 follow-up (account linking). Semantics below verified against
      // the INSTALLED better-auth@1.6.25 source
      // (node_modules/better-auth/dist/oauth2/link-account.mjs +
      // node_modules/better-auth/dist/api/routes/{account,callback}.mjs) —
      // not the changelog, not memory. Two independent link paths exist and
      // this config affects them differently:
      //
      // 1. IMPLICIT auto-link (plain "Continue with Google/GitHub" on the
      //    sign-in screen, no prior session — link-account.mjs
      //    handleOAuthUserInfo) stays OFF (disableImplicitLinking: true,
      //    same as before this feature). Gate, verbatim:
      //      (!isTrustedProvider && !userInfo.emailVerified)
      //      || (requireLocalEmailVerified && !dbUser.user.emailVerified)
      //      || accountLinking.enabled === false
      //      || accountLinking.disableImplicitLinking === true
      //    The last clause alone makes the whole OR always true, so this
      //    path unconditionally refuses (account_not_linked) regardless of
      //    provider trust or either side's emailVerified.
      //
      //    Round-2 review finding (major): an earlier revision of this PR
      //    flipped this to false and added trustedProviders: ['google',
      //    'github'] to enable it. That was reverted here before merge —
      //    trustedProviders is consulted in exactly ONE place in the
      //    installed library (link-account.mjs's isTrustedProvider check)
      //    and only skips re-checking the PROVIDER's own emailVerified
      //    claim; it does nothing for the EXPLICIT flow below, which never
      //    reads disableImplicitLinking or trustedProviders at all and is
      //    the half of this PR that actually ships. Enabling implicit
      //    auto-link is a real security decision — the moment
      //    EMAIL_VERIFICATION_ENFORCED flips on (emailVerificationEnforced
      //    above), dbUser.user.emailVerified stops being the guard that was
      //    blocking it in practice, and an anonymous "Continue with
      //    GitHub/Google" click could silently attach to an existing user's
      //    account with only the provider's own emailVerified claim
      //    standing between an attacker and a takeover. It is not
      //    reintroduced here without that decision being made and reviewed
      //    on its own, with EMAIL_VERIFICATION_ENFORCED actually on.
      //
      // 2. EXPLICIT link (signed-in user clicks "Link Google/GitHub" in
      //    Account Settings — api/routes/account.mjs linkSocialAccount +
      //    api/routes/callback.mjs's `if (link)` branch). This path does NOT
      //    consult disableImplicitLinking or trustedProviders, and does NOT
      //    check the base account's emailVerified at all — proof of account
      //    ownership comes from the live session state binds to at call
      //    time, not from any verification flag — so it works today
      //    regardless of the operator's own emailVerified value. It DOES
      //    still enforce, unconditionally: (a) the provider's own
      //    emailVerified claim (Google/GitHub both attest verified emails
      //    for their own OAuth users — no trustedProviders needed for that)
      //    and (b) allowDifferentEmails — the provider's OAuth email must
      //    equal the signed-in user's account email, or the callback
      //    redirects with ?error=email_doesn't_match. Kept false here
      //    (unchanged): the owner's Google/GitHub email is expected to
      //    match their password account's email, and this is not weakened
      //    to let them differ.
      //
      // allowUnlinkingAll stays false: /unlink-account (api/routes/
      // account.mjs) throws FAILED_TO_UNLINK_LAST_ACCOUNT when it is the
      // account's only remaining credential. ui/src/components/
      // AccountSettings.jsx additionally disables the Unlink control in that
      // state — belt and suspenders, not a substitute for this flag staying
      // false.
      accountLinking: {
        enabled: true,
        disableImplicitLinking: true,
        allowDifferentEmails: false,
        allowUnlinkingAll: false,
      },
    },
    // Round-2 review finding (blocker): /unlink-account is gated behind
    // better-auth's OWN freshSessionMiddleware (node_modules/better-auth/
    // dist/api/routes/account.mjs:229 `use: [freshSessionMiddleware]`) but
    // /link-social is not (same file, :117 `use: [sessionMiddleware]` —
    // no freshness check at all). With sessions living 7 days
    // (session.expiresIn above) and freshAge at 24h, that asymmetry let a
    // >24h-old session permanently ATTACH a new sign-in credential (an
    // attacker who rides a stale/hijacked session in) while the legitimate
    // owner's own stale session could not remove it — a one-way door not
    // even closed by a password reset (auth/node_modules/better-auth/dist/
    // api/routes/password.mjs revokes sessions on reset, but does not touch
    // already-linked accounts).
    //
    // better-auth has no per-endpoint config knob for this — the fix is a
    // global hooks.before that runs before every request (dispatch.mjs:139
    // `matcher: () => true`) and applies the exact same check
    // freshSessionMiddleware runs (api/routes/session.mjs:359-371) to
    // /link-social specifically. session.freshAge above is what both sides
    // now read, so they cannot drift apart again. Proven in
    // auth/test/auth.test.js: "/link-social now requires the same session
    // freshness as /unlink-account" ages a real session's createdAt past
    // freshAge and asserts BOTH endpoints 403 identically; mutation-proof —
    // commenting this hook out (done by hand before this commit) makes that
    // test fail with AGED link-social returning 200.
    hooks: {
      before: createAuthMiddleware(async (ctx) => {
        if (ctx.path !== '/link-social') return
        const session = await getSessionFromCtx(ctx)
        // No session at all: fall through to the endpoint's own
        // sessionMiddleware, which produces the correct 401 — freshness is
        // moot when there is no session to be fresh or stale.
        if (!session?.session) return
        const freshAge = ctx.context.sessionConfig.freshAge
        if (freshAge === 0) return
        const createdAt = new Date(session.session.createdAt).getTime()
        if (Date.now() - createdAt >= freshAge * 1000) {
          // Identical {message, code} shape to better-auth's own
          // BASE_ERROR_CODES.SESSION_NOT_FRESH (@better-auth/core/dist/
          // error/codes.mjs) — spelled out literally rather than imported
          // from @better-auth/core, which is a transitive dep of
          // better-auth, not one of auth/package.json's own dependencies.
          throw APIError.from('FORBIDDEN', { code: 'SESSION_NOT_FRESH', message: 'Session is not fresh' })
        }
      }),
    },
    // Round-2 review finding (minor): notify the account owner's email
    // whenever a sign-in credential is added or removed — see
    // notifyAccountChange above for why it must never throw.
    databaseHooks: {
      account: {
        create: { after: (account, ctx) => notifyAccountChange(mailer, ctx, account, 'added') },
        delete: { after: (account, ctx) => notifyAccountChange(mailer, ctx, account, 'removed') },
      },
    },
    verification: {
      modelName: 'auth_verifications',
      storeIdentifier: 'hashed',
    },
    rateLimit: {
      enabled: production,
      storage: 'database',
      modelName: 'auth_rate_limits',
      customRules: {
        '/sign-in/email': { window: 60, max: 10 },
        // Signup friction (#1194 revision b). Email verification is wired
        // above (mail sent on every signup; sign-in refusal gated on
        // EMAIL_VERIFICATION_ENFORCED, which flips on once SES production
        // access clears — see docs/account-authentication.md). These rate
        // rules stay as defense-in-depth regardless: this rule (3 signups /
        // 10 min per Better Auth's rate key), nginx's /api/auth/ limit_req
        // zone, and — decisively — the per-IP DAILY generation cap
        // (services/generation_quota.py): a fresh account does not raise its
        // address's generation allowance, so disposable accounts gain
        // nothing at the endpoint that actually spends money.
        '/sign-up/email': { window: 600, max: 3 },
      },
    },
    advanced: {
      useSecureCookies: production,
      defaultCookieAttributes: {
        httpOnly: true,
        secure: production,
        sameSite: 'lax',
        path: '/',
      },
    },
  })
}
