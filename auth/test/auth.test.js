import assert from 'node:assert/strict'
import { DatabaseSync } from 'node:sqlite'
import test from 'node:test'

import { getMigrations } from 'better-auth/db/migration'

import { createAuth, enabledProviders } from '../auth.js'

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

test('a failing reset mailer never 500s the request (fail-soft, and preserves anti-enumeration)', async () => {
  const mailer = { kind: 'test', sender: 'x', send: async () => { throw new Error('SES sandbox: address not verified') } }
  const auth = createAuth({ database: new DatabaseSync(':memory:'), env, mailer })
  await (await getMigrations(auth.options)).runMigrations()
  const credentials = { email: 'sandboxed-reset@example.com', password: 'correct horse battery staple' }
  await auth.api.signUpEmail({ body: { ...credentials, name: 'S' }, asResponse: true })

  const response = await auth.api.requestPasswordReset({ body: { email: credentials.email }, asResponse: true })
  assert.equal(response.status, 200)
})

test('enforcement flag parses strictly', async () => {
  const { emailVerificationEnforced } = await import('../auth.js')
  assert.equal(emailVerificationEnforced({}), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: 'false' }), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: '1' }), false)
  assert.equal(emailVerificationEnforced({ EMAIL_VERIFICATION_ENFORCED: 'true' }), true)
})
