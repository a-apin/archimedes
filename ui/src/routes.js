import { ROADMAP_PAGES, ROADMAP_SURFACES_ENABLED } from './featureFlags.js'

const PUBLIC_PATHS = {
  '/': 'landing',
  '/architecture': 'architecture',
}

const AUTH_PATHS = {
  '/sign-in': 'sign-in',
  '/sign-up': 'sign-up',
  '/reset-password': 'reset-password',
}

const APP_PATHS = {
  '/app': 'explore',
  '/app/account': 'account',
  '/app/explore': 'explore',
  '/app/leaderboard': 'leaderboard',
  '/app/generate': 'generate',
  '/app/library': 'library',
  '/app/paper': 'paper',
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

// Pages under /app an anonymous visitor may browse (#1194 revision d, Dan's
// explicit product call): Explore, Leaderboard, Corpus and the strategy
// detail page stay login-free; auth is required only to generate, pay, or
// paper-deploy. DELIBERATELY a marker set, not a carve-out from APP_PATHS —
// PAGE_PATHS and LEGACY_PATHS are both derived from APP_PATHS, so removing
// entries there silently breaks pageToPath (corpus/leaderboard nav clicks
// would fall through to '/app' and land on Explore). Keep the paths where
// they are; mark them anonymous-OK instead. Must stay in lockstep with the
// nginx carve-outs in nginx/nginx.conf (the server-side half of this gate).
const ANON_APP_PAGES = new Set(['explore', 'leaderboard', 'corpus', 'strategy'])

export function isAnonymousAppPage(page) {
  return ANON_APP_PAGES.has(page)
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
  anonymousOk: kind === 'app' && ANON_APP_PAGES.has(page),
  ...extras,
})

export function featureEnabled(page, features = { quant: true }) {
  // Roadmap surfaces (#1266): hidden at BUILD time (VITE_ROADMAP_SURFACES,
  // default off), deliberately separate from the runtime features fetch —
  // parseFeatures() never emits `roadmapSurfaces`, so the override below is
  // reachable only from tests, never from the server.
  const roadmapOn = features.roadmapSurfaces ?? ROADMAP_SURFACES_ENABLED
  if (!roadmapOn && ROADMAP_PAGES.has(page)) return false
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
    if (value) {
      // Deep routes go through the SAME feature gate as flat routes. This loop
      // previously skipped featureEnabled(), so a feature-disabled page stayed
      // reachable via its deep link — latent today (only quant is gated, and
      // quant has no deep route), but it becomes a real page-hiding bypass the
      // moment featureEnabled() gates more pages (the UI-hide work folds its
      // gating in here precisely so this check is the single gate for both
      // nav and routing).
      return featureEnabled(deepPage, features)
        ? route('app', deepPage, { ...query, [key]: decodeURIComponent(value) })
        : route('not-found', null)
    }
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

// Nav ids an anonymous visitor should see: the browsable pages plus Generate,
// which is the conversion path (clicking it routes to sign-in). Everything
// else — portfolio, learnings, marketplace, account and friends — reads the
// signed-in user's own state and is noise on a logged-out screen.
const ANON_NAV_IDS = new Set(['landing', 'explore', 'corpus', 'architecture', 'leaderboard', 'generate'])

export function visibleNavigation(items, features, user = null) {
  return items.filter(
    (item) => featureEnabled(item.id, features) && (user != null || ANON_NAV_IDS.has(item.id)),
  )
}
