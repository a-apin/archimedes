CREATE TABLE IF NOT EXISTS subscriber_tick_log (
    id            SERIAL PRIMARY KEY,
    sub_id        VARCHAR(66)  NOT NULL,
    strategy_id   VARCHAR(128) NOT NULL,
    tick_id       VARCHAR(128) NOT NULL,
    step_reached  VARCHAR(32)  NOT NULL,
    halted        BOOLEAN      NOT NULL DEFAULT FALSE,
    halt_source   VARCHAR(16),
    halt_reason   VARCHAR(512),
    charged       BOOLEAN      NOT NULL DEFAULT FALSE,
    action_count  INTEGER,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_subscriber_tick_log_sub_id ON subscriber_tick_log (sub_id);
CREATE INDEX IF NOT EXISTS ix_subscriber_tick_log_strategy_id ON subscriber_tick_log (strategy_id);
CREATE INDEX IF NOT EXISTS ix_subscriber_tick_log_tick_id ON subscriber_tick_log (tick_id);
