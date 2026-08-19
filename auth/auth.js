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
      accountLinking: {
        enabled: true,
        disableImplicitLinking: true,
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
