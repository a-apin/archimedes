import test from 'node:test'
import assert from 'node:assert/strict'

import { parseFeatures } from '../src/features.js'
import {
  pageToPath,
  postAuthPath,
  resolveRoute,
  safeNextPath,
  visibleNavigation,
} from '../src/routes.js'

test('landing and architecture remain public', () => {
  assert.deepEqual(resolveRoute('/').kind, 'public')
  assert.deepEqual(resolveRoute('/architecture').kind, 'public')
})

test('account routes remain public', () => {
  assert.equal(resolveRoute('/sign-in').kind, 'auth')
  assert.equal(resolveRoute('/sign-up').kind, 'auth')
})

test('application routes use /app boundary', () => {
  const route = resolveRoute('/app/library', '?tab=examples')
  assert.equal(route.kind, 'app')
  assert.equal(route.page, 'library')
  assert.equal(route.tab, 'examples')
  assert.equal(pageToPath('library', { tab: 'examples' }), '/app/library?tab=examples')
})

test('deep application routes retain identifiers', () => {
  assert.equal(resolveRoute('/app/portfolio/vaults/0x123').vaultAddress, '0x123')
  assert.equal(resolveRoute('/app/strategy/alpha').strategyId, 'alpha')
  assert.equal(pageToPath('strategy', { strategyId: 'alpha beta' }), '/app/strategy/alpha%20beta')
})

test('legacy private path redirects under app boundary', () => {
  assert.deepEqual(resolveRoute('/generate').redirect, '/app/generate')
  assert.deepEqual(resolveRoute('/library', '?highlight=alpha').redirect, '/app/library?highlight=alpha')
  assert.deepEqual(resolveRoute('/portfolio/vaults/0x123').redirect, '/app/portfolio/vaults/0x123')
})

test('unknown route is not silently treated as landing', () => {
  assert.equal(resolveRoute('/gone').kind, 'not-found')
})

test('post-auth redirect accepts only local app paths', () => {
  assert.equal(safeNextPath('/app/portfolio?tab=mine'), '/app/portfolio?tab=mine')
  assert.equal(postAuthPath('?next=/app/library&highlight=alpha&tab=generated'), '/app/library?highlight=alpha&tab=generated')
  assert.equal(postAuthPath('?next=https://evil.example/app&tab=generated'), '/app')
  assert.equal(safeNextPath('https://evil.example/app'), '/app')
  assert.equal(safeNextPath('//evil.example/app'), '/app')
  assert.equal(safeNextPath('/architecture'), '/app')
})

test('feature payload accepts booleans only', () => {
  assert.deepEqual(parseFeatures({ quant: false }, { quant: true }), { quant: false })
  assert.deepEqual(parseFeatures({ quant: 'false' }, { quant: true }), { quant: true })
})

test('quant navigation and direct route share feature result', () => {
  const nav = [{ id: 'library' }, { id: 'quant' }]
  assert.deepEqual(visibleNavigation(nav, { quant: false }), [{ id: 'library' }])
  assert.equal(resolveRoute('/app/quant', '', { quant: false }).kind, 'not-found')
  assert.equal(resolveRoute('/app/quant', '', { quant: true }).page, 'quant')
})
