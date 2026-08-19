import { createServer } from 'node:http'
import { pathToFileURL } from 'node:url'

import { fromNodeHeaders, toNodeHandler } from 'better-auth/node'

import { createAuth, enabledProviders } from './auth.js'

function json(res, status, body) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.setHeader('Cache-Control', 'no-store')
  res.end(JSON.stringify(body))
}

export function createRequestHandler({ auth, providers = enabledProviders(), nodeHandler = toNodeHandler(auth) }) {
  return async (req, res) => {
    try {
      if (req.url === '/health') {
        res.statusCode = 204
        res.end()
        return
      }

      if (req.url === '/api/auth/providers' && req.method === 'GET') {
        json(res, 200, {
          emailPassword: true,
          google: providers.includes('google'),
          github: providers.includes('github'),
          passkey: false,
        })
        return
      }

      if (req.url === '/_internal/auth/session' && req.method === 'GET') {
        const session = await auth.api.getSession({ headers: fromNodeHeaders(req.headers) })
        res.statusCode = session?.user?.id ? 204 : 401
        res.end()
        return
      }

      if (req.url?.startsWith('/api/auth/')) {
        await nodeHandler(req, res)
        return
      }

      json(res, 404, { error: 'Not found' })
    } catch (error) {
      console.error('auth request failed:', error instanceof Error ? error.name : 'UnknownError')
      if (!res.headersSent) json(res, 500, { error: 'Authentication service unavailable' })
      else res.end()
    }
  }
}

export function startServer(env = process.env) {
  const auth = createAuth({ env })
  const port = Number(env.PORT || 3000)
  return createServer(createRequestHandler({ auth, providers: enabledProviders(env) })).listen(port, '0.0.0.0', () => {
    console.log(`Archimedes auth listening on ${port}`)
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  startServer()
}
