-- P2: Add per-publisher Circle wallet fields for per-creator Gateway seller.
-- gateway_seller_address is the creator's agent Circle wallet 0x address
-- that receives x402 Gateway settlement. agent_wallet_id is the Circle
-- wallet UUID that controls it.

ALTER TABLE marketplace_agents
    ADD COLUMN IF NOT EXISTS gateway_seller_address VARCHAR(42) DEFAULT NULL;

ALTER TABLE marketplace_agents
    ADD COLUMN IF NOT EXISTS agent_wallet_id VARCHAR(128) DEFAULT NULL;

