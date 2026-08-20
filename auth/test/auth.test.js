import assert from 'node:assert/strict'
import { DatabaseSync } from 'node:sqlite'
import test from 'node:test'

import { getMigrations } from 'better-auth/db/migration'

import { createAuth, enabledProviders, PLACEHOLDER_SECRET } from '../auth.js'

const env = {
  NODE_ENV: 'test',
  BETTER_AUTH_SECRET: 'test-only-secret-with-at-least-thirty-two-characters',
  BETTER_AUTH_URL: 'http://localhost:3000',
  BETTER_AUTH_TRUSTED_ORIGINS: 'http://localhost:3000',
}

function cookieHeader(response) {
  return response.headers.get('set-cookie').split(';', 1)[0]
}

async function testAuth(overrides = {}) {
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env: { ...env, ...overrides } })
  await (await getMigrations(auth.options)).runMigrations()
  return auth
}

test('email login creates session and sign out revokes it', async () => {
  const auth = await testAuth()
  const credentials = { email: 'daniel@example.com', password: 'correct horse battery staple' }

  const registration = await auth.api.signUpEmail({
    body: { ...credentials, name: 'Daniel' },
    asResponse: true,
  })
  assert.equal(registration.status, 200)
  assert.equal(registration.headers.get('set-cookie'), null)

  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  assert.equal(login.status, 200)
  const cookie = cookieHeader(login)

  const session = await auth.api.getSession({ headers: new Headers({ cookie }) })
  assert.equal(session.user.email, credentials.email)
  assert.equal(session.user.name, 'Daniel')
  assert.ok(session.session.expiresAt > new Date())

  const logout = await auth.api.signOut({ headers: new Headers({ cookie }), asResponse: true })
  assert.equal(logout.status, 200)
  assert.equal(await auth.api.getSession({ headers: new Headers({ cookie }) }), null)
})

test('production rate limiter uses migrated auth table', async () => {
  const database = new DatabaseSync(':memory:')
  const auth = createAuth({ database, env: { ...env, NODE_ENV: 'production' } })
  assert.equal(auth.options.rateLimit.modelName, 'auth_rate_limits')
  await (await getMigrations(auth.options)).runMigrations()
  const columns = database.prepare('PRAGMA table_info(auth_rate_limits)').all().map(row => row.name)
  assert.deepEqual(columns, ['id', 'key', 'count', 'lastRequest'])
})

test('invalid session token is rejected', async () => {
  const auth = await testAuth()
  const session = await auth.api.getSession({
    headers: new Headers({ cookie: 'better-auth.session_token=invalid' }),
  })
  assert.equal(session, null)
})

test('Google and GitHub are enabled only with complete credentials', () => {
  assert.deepEqual(enabledProviders(env), [])
  assert.deepEqual(
    enabledProviders({
      ...env,
      GOOGLE_CLIENT_ID: 'google-id',
      GOOGLE_CLIENT_SECRET: 'google-secret',
      GITHUB_CLIENT_ID: 'github-id',
      GITHUB_CLIENT_SECRET: 'github-secret',
    }),
    ['google', 'github'],
  )
  assert.deepEqual(enabledProviders({ ...env, GOOGLE_CLIENT_ID: 'incomplete' }), [])
})

// ── Email verification (SES wiring; enforcement env-gated) ──────────────

function capturingMailer() {
  const sent = []
  return { sent, kind: 'test', sender: 'no-reply@test', send: async message => { sent.push(message) } }
}

async function testAuthWithMailer(overrides = {}) {
  const mailer = capturingMailer()
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env: { ...env, ...overrides }, mailer })
  await (await getMigrations(auth.options)).runMigrations()
  return { auth, mailer }
}

test('signup sends a verification email even while enforcement is off', async () => {
  const { auth, mailer } = await testAuthWithMailer()
  const credentials = { email: 'verifyme@example.com', password: 'correct horse battery staple' }

  const registration = await auth.api.signUpEmail({ body: { ...credentials, name: 'V' }, asResponse: true })
  assert.equal(registration.status, 200)

  assert.equal(mailer.sent.length, 1)
  assert.equal(mailer.sent[0].to, credentials.email)
  assert.match(mailer.sent[0].text, /https?:\/\/\S+/)

  // Enforcement off (default): unverified sign-in still succeeds.
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  assert.equal(login.status, 200)
})

test('EMAIL_VERIFICATION_ENFORCED=true refuses unverified sign-in until the emailed link verifies', async () => {
  const { auth, mailer } = await testAuthWithMailer({ EMAIL_VERIFICATION_ENFORCED: 'true' })
  const credentials = { email: 'enforced@example.com', password: 'correct horse battery staple' }

  const registration = await auth.api.signUpEmail({ body: { ...credentials, name: 'E' }, asResponse: true })
  assert.equal(registration.status, 200)

  const refused = await auth.api.signInEmail({ body: credentials, asResponse: true })
  assert.equal(refused.status, 403)

  // Complete the loop with the token from the captured mail — the real
  // verification path, not a DB poke.
  const url = mailer.sent.map(m => m.text.match(/https?:\/\/\S+/)?.[0]).find(Boolean)
  const token = new URL(url).searchParams.get('token')
  assert.ok(token, `verification mail carried no token: ${url}`)
  await auth.api.verifyEmail({ query: { token } })

  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  assert.equal(login.status, 200)
})

test('a failing mailer never breaks signup (fail-soft while SES is sandboxed)', async () => {
  const mailer = { kind: 'test', sender: 'x', send: async () => { throw new Error('SES sandbox: address not verified') } }
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env, mailer })
  await (await getMigrations(auth.options)).runMigrations()

  const registration = await auth.api.signUpEmail({
    body: { email: 'sandboxed@example.com', password: 'correct horse battery staple', name: 'S' },
    asResponse: true,
  })
  assert.equal(registration.status, 200)
})

// ── Password reset (SES wiring, mirrors email verification above) ──────

test('password reset dispatches reset mail for a known address and lets the old password go, revoking sessions', async () => {
  const { auth, mailer } = await testAuthWithMailer()
  const credentials = { email: 'resetme@example.com', password: 'correct horse battery staple' }

  const registration = await auth.api.signUpEmail({ body: { ...credentials, name: 'R' }, asResponse: true })
  assert.equal(registration.status, 200)

  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const cookie = cookieHeader(login)
  assert.ok(await auth.api.getSession({ headers: new Headers({ cookie }) }))

  const request = await auth.api.requestPasswordReset({
    body: { email: credentials.email, redirectTo: 'http://localhost:3000/reset-password' },
    asResponse: true,
  })
  assert.equal(request.status, 200)

  // signUpEmail already sent one verification mail (sendOnSignUp); the reset
  // request must add exactly one more, distinguishable by subject.
  const resetMail = mailer.sent.find(m => m.subject === 'Reset your Archimedes password')
  assert.ok(resetMail, `no reset mail among: ${JSON.stringify(mailer.sent.map(m => m.subject))}`)
  assert.equal(resetMail.to, credentials.email)
  const url = resetMail.text.match(/https?:\/\/\S+/)?.[0]
  assert.ok(url, `reset mail carried no url: ${resetMail.text}`)
  const token = new URL(url).searchParams.get('token') ?? url.match(/\/reset-password\/([^/?]+)/)?.[1]
  assert.ok(token, `reset url carried no token: ${url}`)

  const reset = await auth.api.resetPassword({ body: { token, newPassword: 'new correct horse battery' }, asResponse: true })
  assert.equal(reset.status, 200)

  // Old password is rejected now.
  const oldLogin = await auth.api.signInEmail({ body: credentials, asResponse: true })
  assert.equal(oldLogin.status, 401)

  // New password works.
  const newLogin = await auth.api.signInEmail({
    body: { email: credentials.email, password: 'new correct horse battery' },
    asResponse: true,
  })
  assert.equal(newLogin.status, 200)

  // The session that predated the reset was revoked (revokeSessionsOnPasswordReset).
  assert.equal(await auth.api.getSession({ headers: new Headers({ cookie }) }), null)
})

test('password reset gives an unknown address the identical response, and sends no mail (no account enumeration)', async () => {
  const { auth, mailer } = await testAuthWithMailer()
  const credentials = { email: 'realaccount@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'K' }, asResponse: true })

  const known = await auth.api.requestPasswordReset({ body: { email: credentials.email }, asResponse: true })
  const knownBody = await known.json()
  // One verification mail (signup) + one reset mail for the known address.
  assert.equal(mailer.sent.length, 2)
  assert.ok(mailer.sent.some(m => m.subject === 'Reset your Archimedes password'))

  const unknown = await auth.api.requestPasswordReset({ body: { email: 'nobody-here@example.com' }, asResponse: true })
  const unknownBody = await unknown.json()
  // Still exactly two mails sent — the unknown address triggered no dispatch.
  assert.equal(mailer.sent.length, 2)

  // Status and body are indistinguishable between the two cases.
  assert.equal(known.status, unknown.status)
  assert.deepEqual(knownBody, unknownBody)
})

test('a failing reset mailer never 500s the request, and the failure is actually logged (fail-soft)', async () => {
  const mailer = { kind: 'test', sender: 'x', send: async () => { throw new Error('SES sandbox: address not verified') } }
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env, mailer })
  await (await getMigrations(auth.options)).runMigrations()
  const credentials = { email: 'sandboxed-reset@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'S' }, asResponse: true })

  // Better Auth's own runInBackgroundOrAwait no longer touches this failure
  // at all — sendResetPassword is fire-and-forget (see auth.js), so the
  // ONLY thing standing between a mailer throw and an unhandled rejection
  // is the local .catch(). Spy on console.error to prove that catch is
  // doing real work, not merely restating a status-code assertion that
  // Better Auth's own wrapper would already satisfy on its own.
  const originalError = console.error
  const logged = []
  console.error = (...args) => logged.push(args)
  let response
  try {
    response = await auth.api.requestPasswordReset({ body: { email: credentials.email }, asResponse: true })
    // The send is fire-and-forget, so its rejection settles on a later
    // microtask/macrotask than the response itself — give it one tick.
    await new Promise(resolve => setImmediate(resolve))
  } finally {
    console.error = originalError
  }

  assert.equal(response.status, 200)
  assert.ok(
    logged.some(args => args[0] === 'reset password email send failed:'),
    `expected the fail-soft log line; saw: ${JSON.stringify(logged)}`,
  )
})

test('reset-request timing does not leak account existence (fire-and-forget send, not an awaited one)', async () => {
  // A mailer slow enough that an accidentally-awaited send is unmistakable.
  const SLOW_MS = 200
  const mailer = {
    kind: 'test',
    sender: 'x',
    send: async () => { await new Promise(resolve => setTimeout(resolve, SLOW_MS)) },
  }
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env, mailer })
  await (await getMigrations(auth.options)).runMigrations()
  const credentials = { email: 'timing-known@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'T' }, asResponse: true })

  const start = performance.now()
  const response = await auth.api.requestPasswordReset({ body: { email: credentials.email }, asResponse: true })
  const elapsedMs = performance.now() - start

  assert.equal(response.status, 200)
  // If sendResetPassword awaited the send (the pre-fix shape), this request
  // could not return in under SLOW_MS — it would block on the mailer round
  // trip that an unknown address's early-return never pays, distinguishing
  // known from unknown addresses by response time. Budget well under
  // SLOW_MS so the assertion is unambiguous against normal jitter.
  assert.ok(
    elapsedMs < SLOW_MS / 2,
    `known-address reset request took ${elapsedMs}ms against a ${SLOW_MS}ms mailer — ` +
    'looks like the send is being awaited, which leaks account existence via timing',
  )
})

// ── HTTP-layer coverage: origin-check middleware ─────────────────────────
// auth.api.* calls the endpoint's handler function directly, with no
// Request object — Better Auth's originCheck middleware
// (better-auth/dist/api/middlewares/origin-check.mjs) starts with
// `if (!ctx.request) return`, so calling auth.api.* alone never exercises
// it. auth.handler(request) is the real dispatcher (what toNodeHandler /
// the compose stack / production actually serve through) and does apply it.

test('the HTTP handler rejects an untrusted redirectTo; auth.api.* alone does not exercise that check', async () => {
  const { auth } = await testAuthWithMailer()
  const credentials = { email: 'origin-check@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'O' }, asResponse: true })

  const untrustedBody = { email: credentials.email, redirectTo: 'https://evil.example.com/reset-password' }

  // Direct auth.api.* call: no Request, so originCheck bails out early and
  // an untrusted redirectTo sails through — this is NOT a stand-in for the
  // real HTTP path, which is exactly finding #3's point.
  const direct = await auth.api.requestPasswordReset({ body: untrustedBody, asResponse: true })
  assert.equal(direct.status, 200)

  const post = body => auth.handler(new Request('http://localhost:3000/api/auth/request-password-reset', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  }))

  const rejected = await post(untrustedBody)
  assert.equal(rejected.status, 403)

  const accepted = await post({ email: credentials.email, redirectTo: 'http://localhost:3000/reset-password' })
  assert.equal(accepted.status, 200)
})

test('enforcement flag parses strictly', async () => {
  const { emailVerificationEnforced } = await import('../auth.js')
  assert.equal(emailVerificationEnforced({}), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: 'false' }), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: '1' }), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: 'true' }), true)
})


// ── .env.example placeholder must never boot production ─────────────────────
//
// .env.example ships PLACEHOLDER_SECRET so `cp .env.example .env` gives a
// working local stack (an empty value broke every docker compose command,
// including `down`, during ${BETTER_AUTH_SECRET:?} interpolation). The value is
// public, and it signs session cookies — anyone reading the repo could forge
// them — so production must refuse it.

test('the public .env.example placeholder is refused in production', () => {
  assert.throws(
    () => createAuth({
      database: new DatabaseSync(':memory:'),
      env: { ...env, NODE_ENV: 'production', BETTER_AUTH_SECRET: PLACEHOLDER_SECRET },
    }),
    /still the public \.env\.example placeholder/,
  )
})

test('the placeholder is accepted outside production so local dev works', () => {
  assert.doesNotThrow(() => createAuth({
    database: new DatabaseSync(':memory:'),
    env: { ...env, NODE_ENV: 'development', BETTER_AUTH_SECRET: PLACEHOLDER_SECRET },
  }))
})

test('a real secret still boots in production', () => {
  assert.doesNotThrow(() => createAuth({
    database: new DatabaseSync(':memory:'),
    env: { ...env, NODE_ENV: 'production', BETTER_AUTH_SECRET: 'a-genuinely-random-production-secret-value-x7f2' },
  }))
})

test('PLACEHOLDER_SECRET is exactly what .env.example ships', async () => {
  const { readFileSync } = await import('node:fs')
  const envExample = readFileSync(new URL('../../.env.example', import.meta.url), 'utf8')
  const line = envExample.split('\n').find(l => l.startsWith('BETTER_AUTH_SECRET='))
  assert.equal(line, `BETTER_AUTH_SECRET=${PLACEHOLDER_SECRET}`)
})

test('the shipped placeholder still satisfies the 32-char floor', () => {
  assert.ok(PLACEHOLDER_SECRET.length >= 32)
})

// ── Account linking (#1420 follow-up) ────────────────────────────────────
//
// Two independent link paths, verified against the INSTALLED
// better-auth@1.6.25 source (not the changelog, not memory — see the
// comment on accountLinking in ../auth.js for the exact gate lines and
// where they live in node_modules):
//
//   1. IMPLICIT auto-link — a plain "Continue with Google/GitHub" sign-in
//      for an email that already owns a password account. Gated on BOTH
//      trustedProviders (skips re-checking the provider's own emailVerified
//      claim) AND requireLocalEmailVerified (default true, never overridden
//      here) which independently requires the EXISTING password account's
//      emailVerified already be true. Tests below prove both halves of that
//      AND — refused when the base account is unverified, allowed once it
//      isn't — with a real signed OAuth callback round trip, not a mock of
//      Better Auth's own internals.
//   2. EXPLICIT link — POST /link-social from a live session, i.e. the
//      "Link Google/GitHub" buttons in Account Settings. Does not check the
//      base account's emailVerified at all (ownership comes from the live
//      session), but DOES still enforce allowDifferentEmails: the OAuth
//      email must equal the session account's email.
//
// Every OAuth round trip below fakes ONLY the network boundary (Google's
// token endpoint) via a fetch mock — the authorization-URL construction,
// CSRF state generation/verification, the double-submit state cookie, and
// the account-linking decision are all the REAL library code running
// in-process against the in-memory sqlite adapter. Per CLAUDE.md: mock at
// the boundary, not the internals.

function base64url(value) {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

// An UNSIGNED id_token: better-auth's Google adapter decodes the callback's
// id_token with `decodeJwt` (no signature check — the token just came from
// Google's own token endpoint over TLS in the real flow) so any well-formed
// three-segment JWT is accepted. Good enough to drive the real linking
// decision logic without a live Google.
function fakeGoogleIdToken({ sub, email, emailVerified, name }) {
  const header = base64url({ alg: 'none', typ: 'JWT' })
  const payload = base64url({ sub, email, email_verified: emailVerified, name })
  return `${header}.${payload}.sig`
}

function mockGoogleTokenExchange(t, { sub = 'google-sub-1', email, emailVerified, name = 'Google User' }) {
  t.mock.method(globalThis, 'fetch', async (input) => {
    const href = typeof input === 'string' ? input : input?.url ?? String(input)
    if (!href.startsWith('https://oauth2.googleapis.com/token')) {
      throw new Error(`unexpected network call to ${href} — only the token endpoint should be reachable here`)
    }
    const body = {
      access_token: 'fake-access-token',
      id_token: fakeGoogleIdToken({ sub, email, emailVerified, name }),
      token_type: 'Bearer',
      expires_in: 3600,
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } })
  })
}

function forwardedCookies(response) {
  return response.headers.getSetCookie().map(cookie => cookie.split(';', 1)[0]).join('; ')
}

async function googleEnabledAuth(overrides = {}) {
  const database = new DatabaseSync(':memory:')
  const auth = createAuth({
    database,
    env: { ...env, GOOGLE_CLIENT_ID: 'test-google-client-id', GOOGLE_CLIENT_SECRET: 'test-google-client-secret', ...overrides },
  })
  await (await getMigrations(auth.options)).runMigrations()
  return { auth, database }
}

test('accountLinking config carries the exact verified semantics — trustedProviders + implicit linking on, verification NOT weakened', async () => {
  const { auth } = await googleEnabledAuth()
  const accountLinking = auth.options.account.accountLinking
  assert.deepEqual(accountLinking, {
    enabled: true,
    disableImplicitLinking: false,
    trustedProviders: ['google', 'github'],
    allowDifferentEmails: false,
    allowUnlinkingAll: false,
  })
  // Load-bearing: requireLocalEmailVerified must be left UNSET so the
  // library's own default (true) applies. Setting it to `false` here would
  // silently disable the guard the tests below prove is active — mutation-
  // prove by adding `requireLocalEmailVerified: false` to auth.js and
  // re-running; "implicit auto-link is refused when the existing password
  // account is unverified" (below) then fails.
  assert.equal(accountLinking.requireLocalEmailVerified, undefined)
})

test("the installed library's implicit-link gate still reads requireLocalEmailVerified + the base account's emailVerified (source-pinned)", async () => {
  const { readFileSync } = await import('node:fs')
  const src = readFileSync(
    new URL('../node_modules/better-auth/dist/oauth2/link-account.mjs', import.meta.url),
    'utf8',
  )
  // Pins the exact gate this PR's security claim depends on. If a future
  // `npm ci` bump to better-auth changes this line, this test fails loudly
  // instead of the claim silently going stale.
  assert.match(src, /requireLocalEmailVerified && !dbUser\.user\.emailVerified/)
  assert.match(src, /accountLinking\?\.disableImplicitLinking === true/)
})

test('implicit auto-link is refused when the existing password account is unverified, even though trustedProviders lists google', async (t) => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'unverified-owner@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  // Never verified — this IS the SES-sandbox-realistic state this PR
  // documents (auth.js's comment on accountLinking).

  const initiate = await auth.api.signInSocial({
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const cookie = forwardedCookies(initiate)

  mockGoogleTokenExchange(t, { email: credentials.email, emailVerified: true })

  const callback = await auth.handler(new Request(
    `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
    { headers: { cookie } },
  ))
  assert.equal(callback.status, 302)
  const location = new URL(callback.headers.get('location'))
  assert.equal(location.searchParams.get('error'), 'account_not_linked')
})

test('implicit auto-link succeeds once the existing password account is verified (trustedProviders doing its job)', async (t) => {
  const { auth, database } = await googleEnabledAuth()
  const credentials = { email: 'verified-owner@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  database.exec(`UPDATE auth_users SET emailVerified = 1 WHERE email = '${credentials.email}'`)

  const initiate = await auth.api.signInSocial({
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const cookie = forwardedCookies(initiate)

  mockGoogleTokenExchange(t, { email: credentials.email, emailVerified: true })

  const callback = await auth.handler(new Request(
    `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
    { headers: { cookie } },
  ))
  assert.equal(callback.status, 302)
  const location = new URL(callback.headers.get('location'))
  assert.equal(location.searchParams.get('error'), null)
  assert.equal(location.origin + location.pathname, 'http://localhost:3000/app')
})

test('explicit link-social (Account Settings "Link Google") requires a live session — refused with no cookie at all', async () => {
  const { auth } = await googleEnabledAuth()
  // requireHeaders: true on this endpoint means a Headers object must be
  // PRESENT (even empty) or the framework 400s on that requirement before
  // ever reaching the session check — passing an empty Headers() is what
  // actually exercises "no session", matching the real HTTP path (the auth
  // server's Node http handler always hands better-auth real, if header-
  // sparse, request headers).
  const response = await auth.api.linkSocialAccount({
    headers: new Headers(),
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true },
    asResponse: true,
  })
  assert.equal(response.status, 401)
})

test("explicit link is refused when the provider's email differs from the signed-in account's email (allowDifferentEmails stays false)", async (t) => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'owner@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const sessionCookie = cookieHeader(login)

  const initiate = await auth.api.linkSocialAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const cookie = forwardedCookies(initiate)

  mockGoogleTokenExchange(t, { email: 'someone-else@example.com', emailVerified: true })

  const callback = await auth.handler(new Request(
    `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
    { headers: { cookie } },
  ))
  assert.equal(callback.status, 302)
  const location = new URL(callback.headers.get('location'))
  assert.equal(location.searchParams.get('error'), "email_doesn't_match")

  // Nothing was attached to the account.
  const accounts = await auth.api.listUserAccounts({ headers: new Headers({ cookie: sessionCookie }) })
  assert.deepEqual(accounts.map(a => a.providerId), ['credential'])
})

test('explicit link succeeds for a matching email even while the base password account is UNVERIFIED — the working path while EMAIL_VERIFICATION_ENFORCED is off', async (t) => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'owner-unverified@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  // Deliberately left unverified — proves this path does NOT gate on it.
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const sessionCookie = cookieHeader(login)

  const initiate = await auth.api.linkSocialAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app/account?linked=google', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const cookie = forwardedCookies(initiate)

  mockGoogleTokenExchange(t, { email: credentials.email, emailVerified: true })

  const callback = await auth.handler(new Request(
    `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
    { headers: { cookie } },
  ))
  assert.equal(callback.status, 302)
  assert.equal(callback.headers.get('location'), 'http://localhost:3000/app/account?linked=google')

  const accounts = await auth.api.listUserAccounts({ headers: new Headers({ cookie: sessionCookie }) })
  assert.deepEqual(accounts.map(a => a.providerId).sort(), ['credential', 'google'])
})

test('unlinking the account\'s only remaining credential is refused (allowUnlinkingAll stays false)', async () => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'onlycred@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const cookie = cookieHeader(login)

  const response = await auth.api.unlinkAccount({
    headers: new Headers({ cookie }),
    body: { providerId: 'credential' },
    asResponse: true,
  })
  assert.equal(response.status, 400)
  const body = await response.json()
  assert.equal(body.code, 'FAILED_TO_UNLINK_LAST_ACCOUNT')

  // Mutation-prove (done by hand before this commit, transcript in the PR
  // body): flip allowUnlinkingAll to true in auth.js and rerun — this
  // assertion then fails (status becomes 200), proving the guard is what
  // makes the test pass, not an unrelated 400.
})

test('unlinking one of two linked accounts succeeds; the sole survivor is then protected the same way', async (t) => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'twoaccounts@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const sessionCookie = cookieHeader(login)

  const initiate = await auth.api.linkSocialAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const linkCookie = forwardedCookies(initiate)
  mockGoogleTokenExchange(t, { email: credentials.email, emailVerified: true })
  await auth.handler(new Request(
    `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
    { headers: { cookie: linkCookie } },
  ))

  const before = await auth.api.listUserAccounts({ headers: new Headers({ cookie: sessionCookie }) })
  assert.equal(before.length, 2)

  const unlinkCredential = await auth.api.unlinkAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { providerId: 'credential' },
    asResponse: true,
  })
  assert.equal(unlinkCredential.status, 200)

  const after = await auth.api.listUserAccounts({ headers: new Headers({ cookie: sessionCookie }) })
  assert.deepEqual(after.map(a => a.providerId), ['google'])

  // Google is now the LAST credential — the same guard must now protect it.
  const unlinkGoogle = await auth.api.unlinkAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { providerId: 'google' },
    asResponse: true,
  })
  assert.equal(unlinkGoogle.status, 400)
})
