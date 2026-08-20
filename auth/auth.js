import { betterAuth } from 'better-auth'
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
      //    handleOAuthUserInfo). Gate, verbatim:
      //      (!isTrustedProvider && !userInfo.emailVerified)
      //      || (requireLocalEmailVerified && !dbUser.user.emailVerified)
      //      || accountLinking.enabled === false
      //      || accountLinking.disableImplicitLinking === true
      //    trustedProviders below makes isTrustedProvider true for google/
      //    github, which only skips re-checking the PROVIDER's emailVerified
      //    claim (both providers attest verified emails, which is the whole
      //    point of trusting them). It does NOT touch the second clause:
      //    requireLocalEmailVerified defaults to true (deliberately left
      //    unset here, NOT overridden to false) and independently requires
      //    the EXISTING password account already have emailVerified: true.
      //    Consequence, stated honestly: while EMAIL_VERIFICATION_ENFORCED
      //    is off (SES sandbox — see emailVerificationEnforced above) most
      //    password accounts, quite possibly including the operator's own,
      //    sit at emailVerified: false and this auto-link path stays
      //    REFUSED (account_not_linked) for them regardless of
      //    trustedProviders. That is correct, not a bug: it is the exact
      //    guard (added upstream in better-auth/better-auth#9578) against an
      //    attacker pre-registering a victim's email with a password account
      //    the attacker controls, then having the victim's later real OAuth
      //    sign-in silently linked into the attacker's row. Do not set
      //    requireLocalEmailVerified: false to "fix" the refusal — that
      //    removes the guard. The working path for an unverified base
      //    account is the EXPLICIT flow below.
      //
      // 2. EXPLICIT link (signed-in user clicks "Link Google/GitHub" in
      //    Account Settings — api/routes/account.mjs linkSocialAccount +
      //    api/routes/callback.mjs's `if (link)` branch). This path does NOT
      //    check the base account's emailVerified at all — proof of account
      //    ownership comes from the live session state binds to at call
      //    time, not from any verification flag — so it works today
      //    regardless of the operator's own emailVerified value. It DOES
      //    still enforce, unconditionally: (a) isTrustedProvider-or-
      //    providerEmailVerified (same trustedProviders effect as above) and
      //    (b) allowDifferentEmails — the provider's OAuth email must equal
      //    the signed-in user's account email, or the callback redirects
      //    with ?error=email_doesn't_match. Kept false here (unchanged): the
      //    owner's Google/GitHub email is expected to match their password
      //    account's email, and this is not weakened to let them differ.
      //
      // allowUnlinkingAll stays false: /unlink-account (api/routes/
      // account.mjs) throws FAILED_TO_UNLINK_LAST_ACCOUNT when it is the
      // account's only remaining credential. ui/src/components/
      // AccountSettings.jsx additionally disables the Unlink control in that
      // state — belt and suspenders, not a substitute for this flag staying
      // false.
      accountLinking: {
        enabled: true,
        disableImplicitLinking: false,
        trustedProviders: ['google', 'github'],
        allowDifferentEmails: false,
        allowUnlinkingAll: false,
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
