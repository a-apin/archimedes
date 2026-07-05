CREATE TABLE IF NOT EXISTS strategy_generators (
    id SERIAL PRIMARY KEY,
    strategy_id VARCHAR(64) NOT NULL,
    wallet_address VARCHAR(42) NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_strategy_generator UNIQUE (strategy_id, wallet_address)
);
CREATE INDEX IF NOT EXISTS ix_strategy_generators_strategy ON strategy_generators (strategy_id);
CREATE INDEX IF NOT EXISTS ix_strategy_generators_wallet ON strategy_generators (wallet_address);
