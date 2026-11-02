-- Retry each device independently; never resend to a device that already accepted an event.
CREATE TABLE IF NOT EXISTS push_delivery (
    event_id TEXT NOT NULL REFERENCES push_outbox(event_id) ON DELETE CASCADE,
    subscription_id TEXT NOT NULL,
    state TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    PRIMARY KEY(event_id, subscription_id)
);
