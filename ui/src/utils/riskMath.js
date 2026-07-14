/**
 * riskMath.js — pure, dependency-free quant helpers for the risk + backtest
 * visualization components (RiskAnalysis, BacktestVisualizer, PortfolioAdvisorPanels).
 *
 * Everything here is intentionally small and standalone so the components can
 * render with mock data offline and so the math is unit-testable without React.
 * All functions guard against empty / degenerate input and return finite-safe
 * values (never NaN/Infinity leaking to the page — see fmt helpers in the
 * components).
 */

const TRADING_DAYS = 252

/**
 * Historical Value-at-Risk at a confidence `level` (e.g. 0.95).
 * Returns a POSITIVE magnitude of the loss (e.g. 0.031 == "you can lose 3.1%").
 * Uses the empirical quantile of the loss distribution (-returns).
 * @param {number[]} returns — periodic simple returns (e.g. daily)
 * @param {number} level — confidence level in (0,1), default 0.95
 * @returns {number} VaR as a positive fraction; 0 for empty input
 */
export function computeHistoricalVaR(returns, level = 0.95) {
  if (!returns || returns.length === 0) return 0
  const losses = returns.map((r) => -r).sort((a, b) => a - b)
  // Index of the (level) quantile of the loss distribution.
  const idx = Math.min(losses.length - 1, Math.max(0, Math.floor(level * (losses.length - 1))))
  return Math.max(0, losses[idx])
}

/**
 * Conditional VaR (a.k.a. Expected Shortfall): the mean loss in the tail beyond
 * the VaR threshold. Always >= the VaR at the same level.
 * @param {number[]} returns — periodic simple returns
 * @param {number} level — confidence level in (0,1), default 0.95
 * @returns {number} CVaR as a positive fraction; 0 for empty input
 */
export function computeCVaR(returns, level = 0.95) {
  if (!returns || returns.length === 0) return 0
  const losses = returns.map((r) => -r).sort((a, b) => a - b)
  const cutoff = Math.floor(level * (losses.length - 1))
  const tail = losses.slice(cutoff)
  if (tail.length === 0) return Math.max(0, losses[losses.length - 1])
  const mean = tail.reduce((s, v) => s + v, 0) / tail.length
  return Math.max(0, mean)
}

/**
 * Annualized Sharpe ratio over a window of periodic returns.
 * @param {number[]} window — periodic returns
 * @param {number} rf — periodic risk-free rate (default 0)
 * @param {number} periodsPerYear — annualization factor (default 252)
 * @returns {number} annualized Sharpe; 0 if std is ~0 or input too short
 */
export function annualizedSharpe(window, rf = 0, periodsPerYear = TRADING_DAYS) {
  if (!window || window.length < 2) return 0
  const excess = window.map((r) => r - rf)
  const mean = excess.reduce((s, v) => s + v, 0) / excess.length
  const variance = excess.reduce((s, v) => s + (v - mean) ** 2, 0) / (excess.length - 1)
  const std = Math.sqrt(variance)
  if (std < 1e-12) return 0
  return (mean / std) * Math.sqrt(periodsPerYear)
}

/**
 * Chronological walk-forward folds over a returns series. Splits the series
 * into `k` contiguous segments; inside each, the first `trainFrac` is the
 * in-sample window and the remainder is out-of-sample. Mirrors the shape the
 * BacktestVisualizer fold band expects ({isSharpe, oosSharpe} per fold).
 * @param {number[]} returns — periodic returns, chronological
 * @param {number} k — number of folds (default 6)
 * @param {number} trainFrac — in-sample share of each fold (default 0.7)
 * @returns {{isSharpe: number, oosSharpe: number}[]} empty when the series is
 *   too short for each OOS slice to hold at least 20 observations
 */
export function walkForwardFolds(returns, k = 6, trainFrac = 0.7) {
  if (!returns || returns.length === 0) return []
  const segLen = Math.floor(returns.length / k)
  const oosLen = Math.floor(segLen * (1 - trainFrac))
  if (oosLen < 20) return []
  const folds = []
  for (let i = 0; i < k; i++) {
    const seg = returns.slice(i * segLen, (i + 1) * segLen)
    const split = Math.floor(seg.length * trainFrac)
    folds.push({
      isSharpe: annualizedSharpe(seg.slice(0, split)),
      oosSharpe: annualizedSharpe(seg.slice(split)),
    })
  }
  return folds
}

/**
 * Rolling annualized Sharpe over a trailing window. Returns one value per
 * position once enough data exists (null before the window fills, so charts
 * can skip the warm-up region).
 * @param {number[]} returns — periodic returns
 * @param {number} window — lookback length in periods (e.g. 30)
 * @param {number} rf — periodic risk-free rate (default 0)
 * @returns {(number|null)[]} array aligned to `returns`
 */
export function rollingSharpe(returns, window = 30, rf = 0) {
  if (!returns || returns.length === 0) return []
  const out = []
  for (let i = 0; i < returns.length; i++) {
    if (i + 1 < window) {
      out.push(null)
      continue
    }
    const slice = returns.slice(i + 1 - window, i + 1)
    out.push(annualizedSharpe(slice, rf))
  }
  return out
}

/**
 * Drawdown series from an equity curve: at each point, the fractional decline
 * from the running peak (<= 0). 0 means a fresh high-water mark.
 * @param {number[]} equity — cumulative portfolio value series
 * @returns {number[]} drawdown fractions (<= 0)
 */
export function drawdownSeries(equity) {
  if (!equity || equity.length === 0) return []
  let peak = equity[0]
  return equity.map((v) => {
    if (v > peak) peak = v
    return peak > 0 ? v / peak - 1 : 0
  })
}

/**
 * Maximum drawdown magnitude (positive fraction) from an equity curve.
 * @param {number[]} equity
 * @returns {number} max drawdown as a positive fraction
 */
export function maxDrawdown(equity) {
  const dd = drawdownSeries(equity)
  return dd.length ? -Math.min(...dd) : 0
}

/**
 * Build an equity curve from periodic returns, starting at `start`.
 * @param {number[]} returns
 * @param {number} start — initial value (default 1)
 * @returns {number[]} equity curve of length returns.length + 1
 */
export function equityFromReturns(returns, start = 1) {
  const out = [start]
  let v = start
  for (const r of returns || []) {
    v *= 1 + r
    out.push(v)
  }
  return out
}

/**
 * Kelly fraction for a binary bet: f* = (b·p − q) / b, where q = 1 − p.
 * `b` is the net odds (payoff per unit staked, e.g. 1.5 means win 1.5x).
 * Clamped to [0,1] — never recommends leverage or a negative bet.
 * @param {number} p — win probability in [0,1]
 * @param {number} b — net payoff multiple (> 0)
 * @returns {number} optimal fraction of bankroll in [0,1]
 */
export function kellyFraction(p, b) {
  if (!Number.isFinite(p) || !Number.isFinite(b) || b <= 0) return 0
  const q = 1 - p
  const f = (b * p - q) / b
  if (!Number.isFinite(f)) return 0
  return Math.min(1, Math.max(0, f))
}

/**
 * Pearson correlation between two equal-length series. Returns 0 for
 * degenerate input.
 */
export function correlation(a, b) {
  if (!a || !b || a.length !== b.length || a.length < 2) return 0
  const n = a.length
  const ma = a.reduce((s, v) => s + v, 0) / n
  const mb = b.reduce((s, v) => s + v, 0) / n
  let cov = 0
  let va = 0
  let vb = 0
  for (let i = 0; i < n; i++) {
    const da = a[i] - ma
    const db = b[i] - mb
    cov += da * db
    va += da * da
    vb += db * db
  }
  const denom = Math.sqrt(va * vb)
  if (denom < 1e-12) return 0
  return cov / denom
}

/**
 * Deterministic pseudo-random generator (mulberry32) so mock series are
 * stable across renders — avoids flicker and makes screenshots reproducible.
 * @param {number} seed
 * @returns {() => number} a () => number in [0,1)
 */
export function seededRng(seed = 42) {
  let a = seed >>> 0
  return function () {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/**
 * Box-Muller standard-normal draw from a uniform RNG.
 * @param {() => number} rng
 * @returns {number} ~ N(0,1)
 */
export function gaussian(rng) {
  const u = Math.max(1e-12, rng())
  const v = rng()
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v)
}

/**
 * Generate a mock daily-return series with a small positive drift and fat-ish
 * tails — good enough to make the risk charts look realistic offline.
 * @param {number} n — number of returns
 * @param {object} opts — { drift, vol, seed }
 * @returns {number[]}
 */
export function mockReturns(n = 252, { drift = 0.0004, vol = 0.011, seed = 7 } = {}) {
  const rng = seededRng(seed)
  const out = []
  for (let i = 0; i < n; i++) {
    out.push(drift + vol * gaussian(rng))
  }
  return out
}
