// Grouped-asset metadata for the /explore card view (#464).
//
// The grouping key IS the existing `asset_class` field returned by
// /api/explore/assets (see backend/archimedes/data/synthetic_universe.json
// and explore_schemas.py) — the same field Explore.jsx already uses to build
// its filter pills. There is no separate "category" concept in the backend
// today, so a card == one asset_class bucket. If/when the strategy engine
// grows a richer sector/theme taxonomy, this map is the place to extend.
//
// Each entry is a plain-English description of what the bucket represents,
// plus a simple inline SVG icon (no new icon-library dependency — see PR
// #464 report for why: @iconify-json/* packages are already declared in
// ui/package.json but nothing wires up @iconify/react to consume them, so
// pulling that thread in would be a genuinely new dependency).
export const ASSET_GROUP_META = {
  crypto: {
    label: 'Crypto',
    description:
      'Digital-native assets — major coins and tokens traded 24/7 across on-chain and centralized venues. ' +
      'No market close, so prices move on weekends and holidays too.',
    icon: 'crypto',
  },
  fx: {
    label: 'FX',
    description:
      'Currency pairs. Used as a diversifier against USD-denominated risk and as a macro regime signal ' +
      '(risk-on/risk-off shows up here first for a lot of strategies).',
    icon: 'fx',
  },
  us_thematic_etf: {
    label: 'US Thematic ETFs',
    description:
      'US-listed funds built around a theme (AI, clean energy, robotics, etc.) rather than a sector or index. ' +
      'Higher concentration risk than a broad index fund in exchange for a more targeted bet.',
    icon: 'theme',
  },
  asia_equity_etf: {
    label: 'Asia Equity ETFs',
    description: 'Funds tracking equity markets across Asia — a regional diversifier outside US and EU exposure.',
    icon: 'globe',
  },
  us_equity_etf: {
    label: 'US Equity ETFs',
    description: 'Broad US equity index funds (S&P 500, total market, etc.) — the core beta building block for most portfolios.',
    icon: 'chart',
  },
  factor_etf: {
    label: 'Factor ETFs',
    description:
      'Funds that tilt toward a specific academic risk factor — value, momentum, quality, low-volatility — ' +
      'instead of market-cap weighting.',
    icon: 'factor',
  },
  eu_equity_etf: {
    label: 'EU Equity ETFs',
    description: 'Funds tracking equity markets across Europe — a regional diversifier outside US and Asia exposure.',
    icon: 'globe',
  },
  us_sector_etf: {
    label: 'US Sector ETFs',
    description: 'US equity funds sliced by sector (tech, energy, financials, etc.) for sector rotation strategies.',
    icon: 'sector',
  },
  commodity_etf: {
    label: 'Commodity ETFs',
    description: 'Funds tracking broad commodity baskets or indices — an inflation hedge and a diversifier against equities.',
    icon: 'commodity',
  },
  intl_equity_etf: {
    label: 'International Equity ETFs',
    description: 'Developed-market equity funds outside the US — ex-US diversification without single-country concentration.',
    icon: 'globe',
  },
  latam_equity_etf: {
    label: 'LatAm Equity ETFs',
    description: 'Funds tracking equity markets across Latin America.',
    icon: 'globe',
  },
  us_bond_agg: {
    label: 'US Aggregate Bonds',
    description: 'Broad US fixed-income funds spanning maturities and issuers — the classic ballast against equity drawdowns.',
    icon: 'bond',
  },
  metal_eq_etf: {
    label: 'Metals & Mining Equities',
    description: 'Equity funds holding metals and mining companies — leveraged exposure to metal prices via producers rather than the spot metal.',
    icon: 'metal',
  },
  credit_ig: {
    label: 'Investment-Grade Credit',
    description: 'Corporate bond funds rated investment-grade — more yield than treasuries, more safety than high-yield.',
    icon: 'bond',
  },
  metal_etf: {
    label: 'Metals ETFs',
    description: 'Funds tracking physical or futures-based precious-metal prices (gold, silver, etc.).',
    icon: 'metal',
  },
  metal_spot: {
    label: 'Metal Spot',
    description: 'Direct spot-price exposure to precious metals, without a fund wrapper.',
    icon: 'metal',
  },
  credit_hy: {
    label: 'High-Yield Credit',
    description: 'Corporate bond funds rated below investment-grade — more yield, more default and rate-cycle risk.',
    icon: 'bond',
  },
  agri_etf: {
    label: 'Agriculture ETFs',
    description: 'Funds tracking agricultural commodities (grains, softs) — a different macro driver than metals or energy.',
    icon: 'commodity',
  },
  reit_etf: {
    label: 'REIT ETFs',
    description: 'Real-estate investment trust funds — equity-like exposure to property income and valuations.',
    icon: 'reit',
  },
  us_bond_tbill: {
    label: 'US T-Bills',
    description: 'Short-dated US Treasury bills — the closest thing to a risk-free rate proxy in the universe.',
    icon: 'bond',
  },
  energy_etf: {
    label: 'Energy ETFs',
    description: 'Funds tracking oil, gas, and broader energy-sector prices.',
    icon: 'commodity',
  },
  em_equity_etf: {
    label: 'Emerging Market Equity ETFs',
    description: 'Equity funds across emerging markets — higher growth potential, higher volatility and currency risk.',
    icon: 'globe',
  },
  volatility_etf: {
    label: 'Volatility ETFs',
    description: 'Funds tracking implied or realized volatility indices — used as a hedge or a tail-risk signal, not a buy-and-hold position.',
    icon: 'volatility',
  },
  intl_bond: {
    label: 'International Bonds',
    description: 'Fixed-income exposure outside the US — diversifies rate-cycle and currency risk away from USD bonds.',
    icon: 'bond',
  },
  em_bond: {
    label: 'Emerging Market Bonds',
    description: 'Fixed-income exposure to emerging-market sovereign and corporate debt — higher yield, higher credit and currency risk.',
    icon: 'bond',
  },
  us_bond_mid: {
    label: 'US Mid-Duration Bonds',
    description: 'US Treasury exposure in the middle of the curve — a balance between rate sensitivity and yield.',
    icon: 'bond',
  },
  us_muni: {
    label: 'US Municipal Bonds',
    description: 'US state and local government debt — often tax-advantaged, lower yield in exchange for that treatment.',
    icon: 'bond',
  },
  us_bond_short: {
    label: 'US Short-Duration Bonds',
    description: 'US Treasury exposure at the short end of the curve — low rate sensitivity, closer to a cash-equivalent.',
    icon: 'bond',
  },
  us_bond_tips: {
    label: 'US TIPS',
    description: 'Treasury Inflation-Protected Securities — principal adjusts with CPI, an explicit inflation hedge inside fixed income.',
    icon: 'bond',
  },
  us_bond_long: {
    label: 'US Long-Duration Bonds',
    description: 'US Treasury exposure at the long end of the curve — highest rate sensitivity, biggest swings when yields move.',
    icon: 'bond',
  },
  tr_equity_etf: {
    label: 'Turkey Equity ETFs',
    description: 'Funds tracking Turkish equity markets.',
    icon: 'globe',
  },
}

const FALLBACK_META = {
  label: null, // filled in with the raw class name at call site
  description: 'A grouping of related assets from the tradable universe.',
  icon: 'default',
}

export function groupMeta(assetClass) {
  const meta = ASSET_GROUP_META[assetClass]
  if (meta) return meta
  return { ...FALLBACK_META, label: (assetClass || 'Other').replace(/_/g, ' ') }
}
