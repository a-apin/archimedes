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
