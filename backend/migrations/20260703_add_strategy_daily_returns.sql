-- Add strategy_daily_returns: DB-backed store for the library-PBO cross-section (#774).
-- Replaces analytics-engine/strategies/daily_returns/*.json as the data source for
-- rigor_evaluator.load_daily_returns_store / compute_library_pbo.
-- Idempotent, one row per (stem, date) observation.

CREATE TABLE IF NOT EXISTS strategy_daily_returns (
    id SERIAL PRIMARY KEY,
    stem VARCHAR(128) NOT NULL,
    date DATE NOT NULL,
    daily_return DOUBLE PRECISION NOT NULL,
    data_vintage VARCHAR(32)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_strategy_daily_returns_stem_date
    ON strategy_daily_returns(stem, date);

CREATE INDEX IF NOT EXISTS ix_strategy_daily_returns_stem
    ON strategy_daily_returns(stem);
