CREATE TABLE IF NOT EXISTS subscriber_liabilities (
    id SERIAL PRIMARY KEY,
    sub_id VARCHAR(66) NOT NULL,
    strategy_id VARCHAR(128) NOT NULL,
    tick_id VARCHAR(128) NOT NULL,
    action_count INTEGER NOT NULL,
    unit_price_usdc NUMERIC,
    amount_owed_usdc NUMERIC,
    reason VARCHAR(64) NOT NULL DEFAULT 'mirror_execution_failed',
    status VARCHAR(16) NOT NULL DEFAULT 'owed',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    resolution_note VARCHAR(512)
);
CREATE INDEX IF NOT EXISTS ix_subscriber_liabilities_sub_id ON subscriber_liabilities (sub_id);
CREATE INDEX IF NOT EXISTS ix_subscriber_liabilities_strategy_id ON subscriber_liabilities (strategy_id);
