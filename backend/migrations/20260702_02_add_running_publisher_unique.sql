-- C-4: Prevent duplicate running publishers for the same strategy_id.
-- Partial unique index allows a publisher to be re-published after the
-- previous row is stopped (status != 'running'), while preventing two
-- concurrent active publishers for the same strategy.

CREATE UNIQUE INDEX IF NOT EXISTS uq_marketplace_agents_running_publisher
    ON marketplace_agents (strategy_id)
    WHERE role = 'publisher' AND status = 'running';
