-- P3: Add subscriber Circle wallet ID for Developer-Controlled Wallets signing.
-- circle_wallet_id is the Circle wallet UUID that controls the subscriber's
-- Circle Developer-Controlled Wallet, used for x402 signing and funded balance.

ALTER TABLE marketplace_agents
    ADD COLUMN IF NOT EXISTS circle_wallet_id VARCHAR(128) DEFAULT NULL;
