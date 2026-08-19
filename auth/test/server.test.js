import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import test from 'node:test'

import { createRequestHandler } from '../server.js'

async function listen(handler) {
  const server = createServer(handler)
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    baseURL: `http://127.0.0.1:${port}`,
    close: () => new Promise(resolve => server.close(resolve)),
  }
}

test('health and provider discovery expose no provider secrets', async t => {
  const app = await listen(createRequestHandler({
    auth: { api: { getSession: async () => null }, handler: async () => new Response(null, { status: 404 }) },
    providers: ['google', 'github'],
  }))
  t.after(app.close)

  assert.equal((await fetch(`${app.baseURL}/health`)).status, 204)
  const response = await fetch(`${app.baseURL}/api/auth/providers`)
  assert.deepEqual(await response.json(), { emailPassword: true, google: true, github: true, passkey: false })
})

test('internal authorization fails closed without a valid session', async t => {
  const app = await listen(createRequestHandler({
    auth: { api: { getSession: async ({ headers }) => headers.get('cookie') === 'valid=1' ? { user: { id: 'user-1' } } : null }, handler: async () => new Response(null, { status: 404 }) },
    providers: [],
  }))
  t.after(app.close)

  assert.equal((await fetch(`${app.baseURL}/_internal/auth/session`)).status, 401)
  assert.equal((await fetch(`${app.baseURL}/_internal/auth/session`, { headers: { cookie: 'valid=1' } })).status, 204)
})
