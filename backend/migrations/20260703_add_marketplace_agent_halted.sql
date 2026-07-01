-- C-5: Add halted column to track subscriber non-payment state
-- without conflating with status='stopped' (which means unsubscribed).
-- Subscribers halted mid-run are still active subscriptions (status='running')
-- but are not copied trades until payment resumes.

ALTER TABLE marketplace_agents
    ADD COLUMN IF NOT EXISTS halted BOOLEAN NOT NULL DEFAULT FALSE;
