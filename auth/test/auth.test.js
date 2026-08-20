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
//      for an email that already owns a password account. Stays OFF
//      (disableImplicitLinking: true, unchanged from before #1420) — round-2
//      review (major finding) reverted an earlier revision that flipped this
//      on with trustedProviders: ['google', 'github']. trustedProviders only
//      ever skipped re-checking the PROVIDER's own emailVerified claim; it
//      did nothing for the explicit flow below (the half that actually
//      ships), and arming implicit auto-link is its own security decision —
//      see the long comment on accountLinking in ../auth.js for why. Tests
//      below prove the refusal holds unconditionally, including once the
//      base account IS verified (the one case that used to succeed) — with
//      a real signed OAuth callback round trip, not a mock of Better Auth's
//      own internals.
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

async function googleEnabledAuth(overrides = {}, mailer = undefined) {
  const database = new DatabaseSync(':memory:')
  const auth = createAuth({
    database,
    env: { ...env, GOOGLE_CLIENT_ID: 'test-google-client-id', GOOGLE_CLIENT_SECRET: 'test-google-client-secret', ...overrides },
    ...(mailer ? { mailer } : {}),
  })
  await (await getMigrations(auth.options)).runMigrations()
  return { auth, database }
}

test('accountLinking config keeps implicit auto-link OFF (round-2 review: trustedProviders reverted)', async () => {
  const { auth } = await googleEnabledAuth()
  const accountLinking = auth.options.account.accountLinking
  assert.deepEqual(accountLinking, {
    enabled: true,
    disableImplicitLinking: true,
    allowDifferentEmails: false,
    allowUnlinkingAll: false,
  })
  // No trustedProviders key at all — an earlier revision of this PR set
  // trustedProviders: ['google', 'github'] alongside disableImplicitLinking:
  // false; review found that pairing armed implicit auto-link for no
  // present-day benefit (the explicit flow below never reads either
  // setting) while quietly discarding the provider's own emailVerified
  // signal for the day EMAIL_VERIFICATION_ENFORCED flips on. Mutation-prove
  // by setting disableImplicitLinking: false and re-running — "implicit
  // auto-link stays refused even once the existing password account is
  // verified" (below) then fails (302 to /app instead of an
  // account_not_linked error).
  assert.equal(accountLinking.trustedProviders, undefined)
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

test('implicit auto-link is refused when the existing password account is unverified', async (t) => {
  const { auth } = await googleEnabledAuth()
  const credentials = { email: 'unverified-owner@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  // Never verified — this IS the SES-sandbox-realistic state this PR
  // documents (auth.js's comment on accountLinking). Refused twice over now:
  // disableImplicitLinking: true refuses unconditionally regardless of this,
  // and requireLocalEmailVerified (library default, still left unset) would
  // refuse on its own even if disableImplicitLinking were ever flipped back.

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

test('implicit auto-link stays refused even once the existing password account is verified (round-2 review: disableImplicitLinking reverted to true)', async (t) => {
  const { auth, database } = await googleEnabledAuth()
  const credentials = { email: 'verified-owner@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  database.exec(`UPDATE auth_users SET emailVerified = 1 WHERE email = '${credentials.email}'`)
  // This is the ONE case where an earlier revision of this PR (before
  // review) let implicit auto-link through: a verified base account +
  // trustedProviders. With trustedProviders reverted and
  // disableImplicitLinking back to true, it must now stay refused
  // regardless of emailVerified — proving the revert actually took, not
  // just that the config object looks right in isolation.

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

  // Nothing was attached — the base account still has only its password
  // credential.
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const accounts = await auth.api.listUserAccounts({ headers: new Headers({ cookie: cookieHeader(login) }) })
  assert.deepEqual(accounts.map(a => a.providerId), ['credential'])
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

// ── Session freshness parity (round-2 review, blocker) ───────────────────
//
// better-auth gates /unlink-account behind its OWN freshSessionMiddleware
// (node_modules/better-auth/dist/api/routes/account.mjs:229 `use:
// [freshSessionMiddleware]`) but /link-social carries only the plain
// sessionMiddleware (same file, :117) — no freshness check. Left alone, a
// session older than freshAge could permanently ATTACH a new sign-in
// credential while the same stale session could not remove one: a one-way
// door a password reset does not close either (resetPassword revokes
// sessions, it does not touch already-linked accounts). auth.js's `hooks.
// before` now applies the identical freshAge check to /link-social. These
// tests drive the REAL HTTP handler (auth.handler, what toNodeHandler /
// production actually serve — see the origin-check tests above for why
// auth.api.* alone is not a stand-in) and age a session by writing
// auth_sessions.createdAt directly, the same technique the accountLinking
// tests above use for emailVerified.

function ageSession(database, cookie, hoursAgo) {
  const token = cookie.split('=')[1].split('.')[0]
  const agedIso = new Date(Date.now() - hoursAgo * 60 * 60 * 1000).toISOString()
  database.exec(`UPDATE auth_sessions SET createdAt = '${agedIso}' WHERE token = '${token}'`)
}

function postJson(auth, path, body, cookie) {
  return auth.handler(new Request(`http://localhost:3000${path}`, {
    method: 'POST',
    // origin is required — Better Auth's originCheck middleware 403s
    // "Missing or null Origin" first otherwise, which would make this test
    // pass for the wrong reason (any 403 looks like a win if you don't
    // check the code).
    headers: { 'content-type': 'application/json', cookie, origin: 'http://localhost:3000' },
    body: JSON.stringify(body),
  }))
}

test('session.freshAge is pinned explicitly in auth.js, not the library\'s inherited 24h default', async () => {
  const { auth } = await googleEnabledAuth()
  // Left unset, better-auth silently defaults freshAge to 24h regardless of
  // expiresIn (node_modules/better-auth/dist/context/create-context.mjs:148
  // `freshAge: options.session?.freshAge === void 0 ? 3600 * 24 :
  // options.session.freshAge`) — a 7-day session (expiresIn above) carrying
  // a 1-day "fresh" window nobody actually chose. Asserting the literal
  // here means a future edit to auth.js has to touch this number on
  // purpose; it can no longer drift back to "whatever the library
  // defaults to" silently.
  assert.equal(auth.options.session.freshAge, 60 * 60 * 24)
})

test('/link-social now requires the same session freshness as /unlink-account', async () => {
  const { auth, database } = await googleEnabledAuth()
  const credentials = { email: 'stale-session@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const cookie = cookieHeader(login)

  // Fresh session: /link-social still works (no false-positive block).
  const freshLink = await postJson(auth, '/api/auth/link-social', {
    provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true,
  }, cookie)
  assert.equal(freshLink.status, 200)

  ageSession(database, cookie, 25)

  const agedLink = await postJson(auth, '/api/auth/link-social', {
    provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true,
  }, cookie)
  assert.equal(agedLink.status, 403)
  assert.equal((await agedLink.json()).code, 'SESSION_NOT_FRESH')

  const agedUnlink = await postJson(auth, '/api/auth/unlink-account', { providerId: 'credential' }, cookie)
  assert.equal(agedUnlink.status, 403)
  assert.equal((await agedUnlink.json()).code, 'SESSION_NOT_FRESH')

  // A request with no session at all still gets the library's own 401, not
  // our hook's freshness 403 — freshness is moot with nothing to be fresh.
  const noSession = await postJson(auth, '/api/auth/link-social', {
    provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true,
  }, '')
  assert.equal(noSession.status, 401)

  // Mutation-proof (done by hand before this commit, transcript in the PR
  // body): replace the hook's `if (ctx.path !== '/link-social') return`
  // guard with an unconditional `return` and rerun — agedLink.status goes
  // back to 200 (asymmetric again) while agedUnlink.status stays 403,
  // proving this test actually exercises the hook and not some other gate.
})

// ── Account-change notification email (round-2 review, minor) ────────────
//
// Linking or unlinking a sign-in credential used to be completely silent to
// the account owner — no email at all — despite being exactly the kind of
// change an account-takeover attempt would make. auth.js's databaseHooks.
// account.{create,delete}.after now emails the owner either way, via the
// same real OAuth round trip the accountLinking tests above drive.

async function linkGoogleForReal(auth, sessionCookie, email) {
  const initiate = await auth.api.linkSocialAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { provider: 'google', callbackURL: 'http://localhost:3000/app/account', disableRedirect: true },
    asResponse: true,
  })
  const { url } = await initiate.json()
  const state = new URL(url).searchParams.get('state')
  const linkCookie = forwardedCookies(initiate)
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (input) => {
    const href = typeof input === 'string' ? input : input?.url ?? String(input)
    if (!href.startsWith('https://oauth2.googleapis.com/token')) throw new Error(`unexpected network call to ${href}`)
    return new Response(JSON.stringify({
      access_token: 'fake-access-token',
      id_token: fakeGoogleIdToken({ sub: 'g1', email, emailVerified: true, name: 'Owner' }),
      token_type: 'Bearer',
      expires_in: 3600,
    }), { status: 200, headers: { 'content-type': 'application/json' } })
  }
  try {
    return await auth.handler(new Request(
      `http://localhost:3000/api/auth/callback/google?code=fake-code&state=${encodeURIComponent(state)}`,
      { headers: { cookie: linkCookie } },
    ))
  } finally {
    globalThis.fetch = originalFetch
  }
}

test('signup sends only the verification mail — no redundant "sign-in method added" mail for the credential account it also creates', async () => {
  const mailer = capturingMailer()
  const { auth } = await googleEnabledAuth({}, mailer)
  await auth.api.signUpEmail({
    body: { email: 'signup-quiet@example.com', password: 'correct horse battery staple', name: 'Q' },
    asResponse: true,
  })
  assert.deepEqual(mailer.sent.map(m => m.subject), ['Verify your Archimedes account'])
})

test('linking a Google account emails the account owner (account-takeover signal, round-2 review)', async () => {
  const mailer = capturingMailer()
  const { auth } = await googleEnabledAuth({}, mailer)
  const credentials = { email: 'notify-link@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  mailer.sent.length = 0 // drop the signup verification mail; only the link notification matters here

  const callback = await linkGoogleForReal(auth, cookieHeader(login), credentials.email)
  assert.equal(callback.status, 302)
  // databaseHooks.account.create.after is queued to run after the write
  // commits (@better-auth/core/dist/context/transaction.mjs), not
  // necessarily before the response headers are constructed — give it a
  // tick, same reasoning as the reset-mailer fire-and-forget test above.
  await new Promise(resolve => setImmediate(resolve))

  assert.equal(mailer.sent.length, 1)
  assert.equal(mailer.sent[0].to, credentials.email)
  assert.equal(mailer.sent[0].subject, 'A sign-in method was added to your Archimedes account')
  assert.match(mailer.sent[0].text, /Google/)
})

test('unlinking a sign-in credential also emails the account owner, naming the credential that was removed', async () => {
  const mailer = capturingMailer()
  const { auth } = await googleEnabledAuth({}, mailer)
  const credentials = { email: 'notify-unlink@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })
  const sessionCookie = cookieHeader(login)
  await linkGoogleForReal(auth, sessionCookie, credentials.email)
  await new Promise(resolve => setImmediate(resolve))
  mailer.sent.length = 0 // drop signup + link notification mails

  const unlink = await auth.api.unlinkAccount({
    headers: new Headers({ cookie: sessionCookie }),
    body: { providerId: 'credential' },
    asResponse: true,
  })
  assert.equal(unlink.status, 200)
  await new Promise(resolve => setImmediate(resolve))

  assert.equal(mailer.sent.length, 1)
  assert.equal(mailer.sent[0].to, credentials.email)
  assert.equal(mailer.sent[0].subject, 'A sign-in method was removed from your Archimedes account')
  assert.match(mailer.sent[0].text, /Email & password/)
})

test('a failing account-change mailer never breaks the link/unlink it is reporting on (fail-soft)', async () => {
  const mailer = { kind: 'test', sender: 'x', send: async () => { throw new Error('SES sandbox: address not verified') } }
  const { auth } = await googleEnabledAuth({}, mailer)
  const credentials = { email: 'notify-failsoft@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'Owner' }, asResponse: true })
  const login = await auth.api.signInEmail({ body: credentials, asResponse: true })

  // Mutation-proof (done by hand before this commit, transcript in the PR
  // body): remove notifyAccountChange's try/catch in auth.js and rerun —
  // this callback then rejects with the mailer's own thrown Error instead
  // of resolving 302, because databaseHooks' create.after is awaited by the
  // library itself (@better-auth/core/dist/context/transaction.mjs: `for
  // (const hook of pendingHooks) await hook();` then `if (hasError) throw
  // error;`) — unlike sendResetPassword's hand-rolled, genuinely
  // not-awaited fire-and-forget above, an uncaught throw HERE fails the
  // request that triggered it.
  const callback = await linkGoogleForReal(auth, cookieHeader(login), credentials.email)
  assert.equal(callback.status, 302)
  assert.equal(new URL(callback.headers.get('location')).searchParams.get('error'), null)

  const accounts = await auth.api.listUserAccounts({ headers: new Headers({ cookie: cookieHeader(login) }) })
  assert.deepEqual(accounts.map(a => a.providerId).sort(), ['credential', 'google'])
})
