const PUBLIC_PATHS = {
  '/': 'landing',
  '/architecture': 'architecture',
}

const AUTH_PATHS = {
  '/sign-in': 'sign-in',
  '/sign-up': 'sign-up',
}

const APP_PATHS = {
  '/app': 'explore',
  '/app/account': 'account',
  '/app/explore': 'explore',
  '/app/leaderboard': 'leaderboard',
  '/app/generate': 'generate',
  '/app/library': 'library',
  '/app/corpus': 'corpus',
  '/app/quant': 'quant',
  '/app/portfolio': 'portfolio',
  '/app/reasoning': 'reasoning',
  '/app/learnings': 'learnings',
  '/app/insights': 'insights',
  '/app/marketplace': 'marketplace',
  '/app/publish': 'publish',
  '/app/subscriptions': 'subscriptions',
}

const PAGE_PATHS = Object.fromEntries(Object.entries(APP_PATHS).map(([path, page]) => [page, path]))
const LEGACY_PATHS = Object.fromEntries(
  Object.entries(APP_PATHS)
    .filter(([path]) => path !== '/app')
    .map(([path]) => [path.replace('/app', ''), path]),
)

const route = (kind, page, extras = {}) => ({
  kind,
  page,
  vaultAddress: null,
  traceId: null,
  strategyId: null,
  highlight: null,
  tab: null,
  redirect: null,
  ...extras,
})

export function featureEnabled(page, features = { quant: true }) {
  return page !== 'quant' || features.quant === true
}

export function resolveRoute(pathname = '/', search = '', features = { quant: true }) {
  const params = new URLSearchParams(search)
  const query = {
    highlight: params.get('highlight'),
    traceId: params.get('trace_id'),
    tab: params.get('tab'),
  }

  if (PUBLIC_PATHS[pathname]) return route('public', PUBLIC_PATHS[pathname], query)
  if (AUTH_PATHS[pathname]) return route('auth', AUTH_PATHS[pathname], query)

  const page = APP_PATHS[pathname]
  if (page) return featureEnabled(page, features) ? route('app', page, query) : route('not-found', null)

  const deepRoutes = [
    ['/app/portfolio/vaults/', 'vault-detail', 'vaultAddress'],
    ['/app/reasoning/', 'reasoning', 'traceId'],
    ['/app/strategy/', 'strategy', 'strategyId'],
    ['/app/marketplace/strategy/', 'market-strategy', 'strategyId'],
  ]
  for (const [prefix, deepPage, key] of deepRoutes) {
    if (!pathname.startsWith(prefix)) continue
    const value = pathname.slice(prefix.length)
    if (value) return route('app', deepPage, { ...query, [key]: decodeURIComponent(value) })
  }

  if (LEGACY_PATHS[pathname]) return route('redirect', null, { redirect: `${LEGACY_PATHS[pathname]}${search}` })
  if (pathname.startsWith('/portfolio/vaults/')) {
    return route('redirect', null, { redirect: `/app${pathname}${search}` })
  }
  if (pathname.startsWith('/strategy/') || pathname.startsWith('/reasoning/') || pathname.startsWith('/marketplace/strategy/')) {
    return route('redirect', null, { redirect: `/app${pathname}${search}` })
  }
  return route('not-found', null)
}

export function pageToPath(page, options = {}) {
  if (page === 'landing') return '/'
  if (page === 'architecture') return '/architecture'
  if (page === 'vault-detail' && options.vaultAddress) return `/app/portfolio/vaults/${options.vaultAddress}`
  if (page === 'strategy' && options.strategyId) return `/app/strategy/${encodeURIComponent(options.strategyId)}`
  if (page === 'market-strategy' && options.strategyId) return `/app/marketplace/strategy/${encodeURIComponent(options.strategyId)}`
  const base = PAGE_PATHS[page] ?? '/app'
  const params = new URLSearchParams()
  if (options.highlight && page === 'library') params.set('highlight', options.highlight)
  if (options.traceId && page === 'reasoning') params.set('trace_id', options.traceId)
  if (options.tab && page === 'library') params.set('tab', options.tab)
  const query = params.toString()
  return query ? `${base}?${query}` : base
}

export function safeNextPath(value) {
  if (typeof value !== 'string' || !value.startsWith('/app') || value.startsWith('//')) return '/app'
  try {
    const url = new URL(value, 'https://archimedes.invalid')
    return url.origin === 'https://archimedes.invalid' && url.pathname.startsWith('/app')
      ? `${url.pathname}${url.search}${url.hash}`
      : '/app'
  } catch {
    return '/app'
  }
}

export function postAuthPath(search = '') {
  const params = new URLSearchParams(search)
  const requested = params.get('next')
  const next = safeNextPath(requested)
  if (next === '/app' && requested !== '/app') return next
  params.delete('next')
  const query = params.toString()
  return query && !next.includes('?') && !next.includes('#') ? `${next}?${query}` : next
}

export function visibleNavigation(items, features) {
  return items.filter((item) => featureEnabled(item.id, features))
}
