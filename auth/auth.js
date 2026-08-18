import { betterAuth } from 'better-auth'
import pg from 'pg'

const { Pool } = pg

function csv(value) {
  return String(value ?? '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean)
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

export function createAuth({ database, env = process.env } = {}) {
  const production = env.NODE_ENV === 'production'
  const baseURL = env.BETTER_AUTH_URL || 'http://localhost:5173'
  const secret = env.BETTER_AUTH_SECRET

  if (!secret || secret.length < 32) {
    throw new Error('BETTER_AUTH_SECRET must contain at least 32 characters')
  }

  const db = database ?? new Pool({ connectionString: env.DATABASE_URL })

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
        // Signup friction (#1194 revision b). Email verification is the real
        // answer but needs a sending capability this stack does not have yet
        // (no SES/SMTP anywhere in infra/ — provisioning SES + leaving its
        // sandbox is an external-turnaround dependency, tracked as the
        // post-sprint follow-up in docs/account-authentication.md). Until
        // then, account-minting is bounded by three layers: this rule
        // (3 signups / 10 min per Better Auth's rate key), nginx's
        // /api/auth/ limit_req zone, and — decisively — the per-IP DAILY
        // generation cap (services/generation_quota.py): a fresh account
        // does not raise its address's generation allowance, so disposable
        // accounts gain nothing at the endpoint that actually spends money.
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
