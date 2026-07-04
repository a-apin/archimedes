// Dogfood-validated example briefs for the Generate page (issue #872).
//
// Each entry is a brief that has been verified to:
//   a) pass brief validation in the backend
//   b) steer concrete assets/mechanisms so the universe is thesis-true
//   c) reliably produce a machine-readable spec + real backtest
//
// The first entry is the dogfood-PROVEN winner (real data, thesis-true universe,
// DSR pass). The remaining two are shape-derived placeholders following the same
// pattern: assets/classes + mechanism + goal. Replace placeholders with new
// dogfood-verified winners as they are validated.
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
    // Status: DOGFOOD PROVEN — real data, thesis-true universe, DSR pass.
  },
  {
    id: 'trend-vol-target-bonds',
    label: 'Trend-following equities + bonds with vol target',
    brief:
      'trend-following across major equity and bond ETFs with a volatility target that shrinks position size when realized vol spikes',
    suggestedAssets: ['SPY', 'TLT', 'IEF', 'QQQ'],
    // Status: shape-derived placeholder — same pattern: assets + mechanism + goal.
  },
  {
    id: 'mean-reversion-sector-hedge',
    label: 'Sector mean-reversion with momentum filter + cash hedge',
    brief:
      'mean-reversion entry on lagging US sectors filtered by 3-month momentum, with a cash hedge when the broad market is below its 200-day average',
    suggestedAssets: ['XLK', 'XLE', 'XLF', 'XLV', 'XLI'],
    // Status: shape-derived placeholder — same pattern: assets + mechanism + goal.
  },
]

export default EXAMPLE_BRIEFS
