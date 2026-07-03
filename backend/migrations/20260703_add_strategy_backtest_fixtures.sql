-- Add strategy_backtest_fixtures: DB-backed store for per-strategy summary
-- backtest metrics (Sharpe, DSR, PBO, Kelly, etc.).
-- Replaces analytics-engine/strategies/backtest_fixtures.json as the data
-- source for strategy_provider._load_fixtures_from_db.
-- Idempotent, one row per strategy stem.

CREATE TABLE IF NOT EXISTS strategy_backtest_fixtures (
    stem VARCHAR(128) PRIMARY KEY,
    n_obs_daily INTEGER NOT NULL,
    sharpe_ratio DOUBLE PRECISION NOT NULL,
    sortino_ratio DOUBLE PRECISION NOT NULL,
    max_drawdown DOUBLE PRECISION NOT NULL,
    cagr DOUBLE PRECISION NOT NULL,
    calmar_ratio DOUBLE PRECISION NOT NULL,
    win_rate DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    total_trades INTEGER NOT NULL,
    avg_holding_period_days DOUBLE PRECISION,
    correlation_to_spy DOUBLE PRECISION NOT NULL,
    correlation_to_btc DOUBLE PRECISION,
    out_of_sample_sharpe DOUBLE PRECISION NOT NULL,
    look_ahead_audit_passed BOOLEAN NOT NULL,
    backtest_engine VARCHAR(64) NOT NULL,
    transaction_cost_bps INTEGER NOT NULL,
    backtest_start VARCHAR(32) NOT NULL,
    backtest_end VARCHAR(32) NOT NULL,
    paper_claimed_sharpe DOUBLE PRECISION,
    paper_claimed_cagr DOUBLE PRECISION,
    paper_claimed_max_dd DOUBLE PRECISION,
    deflated_sharpe_ratio DOUBLE PRECISION NOT NULL,
    dsr_p_value DOUBLE PRECISION NOT NULL,
    dsr_convention VARCHAR(16) NOT NULL,
    num_trials_in_selection INTEGER NOT NULL,
    pbo_score DOUBLE PRECISION NOT NULL,
    passes_rigor_gate BOOLEAN NOT NULL,
    kelly_fraction DOUBLE PRECISION NOT NULL
);
