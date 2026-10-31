CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS people (
  id TEXT PRIMARY KEY, display_name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0,
  consent_at TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS samples (
  id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  embedding BLOB NOT NULL, dimension INTEGER NOT NULL, perceptual_hash TEXT NOT NULL,
  quality_json TEXT NOT NULL, round_no INTEGER NOT NULL, session_id TEXT NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0,
  model_set_id TEXT NOT NULL, preprocess_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, reason TEXT NOT NULL, severity TEXT NOT NULL,
  created_at TEXT NOT NULL, source TEXT NOT NULL, details_json TEXT NOT NULL,
  media_id TEXT, acknowledged_at TEXT, UNIQUE(visit_id, reason)
);
CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY, generation INTEGER NOT NULL, payload_json TEXT NOT NULL,
  state TEXT NOT NULL, created_at TEXT NOT NULL, ack_json TEXT
);
CREATE TABLE IF NOT EXISTS receipts (
  frame_id TEXT PRIMARY KEY, boot_id TEXT NOT NULL, seq INTEGER NOT NULL,
  fingerprint TEXT NOT NULL, state TEXT NOT NULL, received_at TEXT NOT NULL,
  reason TEXT, UNIQUE(boot_id, seq)
);
CREATE TABLE IF NOT EXISTS web_sessions (
  token_hash TEXT PRIMARY KEY, csrf_hash TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS action_keys (
  key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, action TEXT NOT NULL, target TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
  reason TEXT, UNIQUE(event_id)
);
CREATE INDEX IF NOT EXISTS event_time ON events(created_at);
CREATE INDEX IF NOT EXISTS receipt_time ON receipts(received_at);
INSERT OR IGNORE INTO schema_version(version) VALUES (1);
