CREATE TABLE notification_delivery (
  outbox_id TEXT PRIMARY KEY REFERENCES outbox(id) ON DELETE CASCADE,
  next_attempt_at TEXT,
  last_attempt_at TEXT,
  delivered_at TEXT,
  provider_message_id INTEGER,
  media_kind TEXT,
  duplicate_possible INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX notification_due ON notification_delivery(next_attempt_at);
CREATE INDEX outbox_state_time ON outbox(state,created_at);
CREATE TABLE notification_runtime (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  not_before TEXT
);
INSERT INTO notification_runtime(singleton) VALUES(1);
