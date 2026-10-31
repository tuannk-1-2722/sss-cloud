CREATE TABLE IF NOT EXISTS cloud_outbox (
    event_id TEXT PRIMARY KEY REFERENCES events(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS cloud_outbox_state ON cloud_outbox(state);
