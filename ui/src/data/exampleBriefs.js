// Dogfood-validated example briefs for the Generate page (issue #872).
//
// Each entry is a brief that has been verified to:
//   a) pass brief validation in the backend
//   b) steer concrete assets/mechanisms so the universe is thesis-true
//   c) reliably produce a machine-readable spec + real backtest
//
// All three entries are dogfood-PROVEN on the live debate pipeline
// (5-brief bake-off, 2026-07-04 — see PR #875): each produced a real-data,
// thesis-true backtest. Replace entries only with new dogfood-verified
// winners as they are validated.
//
// Shape of a good brief: name concrete assets or classes, a mechanism
// (momentum / vol-managed / hedge / mean-reversion), and a goal.
//
// `suggestedAssets` (optional) are display symbols from the supported universe
// (see assetUniverse.js) the brief pairs well with — the UI pre-selects them.
// Keep the list short: 3–5 symbols that are directly named in the brief.

export const EXAMPLE_BRIEFS = [
  {
    id: 'momentum-quality-gold-usdc',
    label: 'Momentum + quality + gold hedge, vol-managed sizing',
    brief:
      'blend momentum, quality and a gold hedge across major ETFs with volatility-managed sizing for idle USDC',
    suggestedAssets: ['SPY', 'QQQ', 'GLD', 'IWM', 'VTV'],
    // Status: DOGFOOD PROVEN — dsr_p 0.999; strongest alpha signal of the bake-off.
  },
  {
    id: 'crypto-trend-treasury-rotation',
    label: 'BTC/ETH trend with defensive treasury rotation',
    brief:
      'trend-following on BTC and ETH with a defensive rotation into treasuries when volatility spikes',
    suggestedAssets: ['BTC', 'ETH', 'IEF', 'SHY'],
    // Status: DOGFOOD PROVEN — dsr_p 0.938, PBO 0.19; best-balanced of the bake-off.
  },
  {
    id: 'low-vol-income-preservation',
    label: 'Low-volatility income, capital preservation',
    brief:
      'low-volatility income portfolio from SPY TLT and SCHD focused on capital preservation',
    suggestedAssets: ['SPY', 'TLT', 'SCHD'],
    // Status: DOGFOOD PROVEN — cleanest overfitting profile (PBO 0.27) of the bake-off.
  },
]

export default EXAMPLE_BRIEFS
