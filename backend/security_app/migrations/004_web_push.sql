CREATE TABLE IF NOT EXISTS push_subscriptions (
    id TEXT PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_outbox (
    event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS push_outbox_state ON push_outbox(state);
