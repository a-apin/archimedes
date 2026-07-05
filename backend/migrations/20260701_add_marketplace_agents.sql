CREATE TABLE IF NOT EXISTS marketplace_agents (
    id SERIAL PRIMARY KEY,
    role VARCHAR(16) NOT NULL,
    strategy_id VARCHAR(128) NOT NULL,
    creator_wallet VARCHAR(42) NOT NULL DEFAULT '',
    subscriber_wallet VARCHAR(42) NOT NULL DEFAULT '',
    sub_id VARCHAR(66) NOT NULL DEFAULT '',
    pool_id VARCHAR(66) NOT NULL DEFAULT '',
    vault_address VARCHAR(42) NOT NULL DEFAULT '',
    ephemeral_wallet VARCHAR(42) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'running',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stopped_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_marketplace_agents_strategy ON marketplace_agents (strategy_id);
CREATE INDEX IF NOT EXISTS ix_marketplace_agents_role_status ON marketplace_agents (role, status);
