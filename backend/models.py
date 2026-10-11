"""SQLite schema definitions for the SPS data model."""


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    unit_code TEXT NOT NULL UNIQUE,
    quota_car INTEGER NOT NULL DEFAULT 1 CHECK (quota_car >= 0),
    quota_moto INTEGER NOT NULL DEFAULT 1 CHECK (quota_moto >= 0)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('manager', 'resident')),
    unit_id INTEGER REFERENCES units(id),
    telegram_chat_id TEXT UNIQUE,
    link_code TEXT UNIQUE,
    link_code_expires_at TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
    id INTEGER PRIMARY KEY,
    plate TEXT NOT NULL UNIQUE,
    vehicle_type TEXT NOT NULL CHECK (vehicle_type IN ('car', 'moto')),
    unit_id INTEGER NOT NULL REFERENCES units(id),
    subject_type TEXT NOT NULL DEFAULT 'resident'
        CHECK (subject_type IN ('resident', 'guest')),
    plan_expires_at TEXT,
    in_lot INTEGER NOT NULL DEFAULT 0 CHECK (in_lot IN (0, 1))
);

CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY,
    cred_type TEXT NOT NULL CHECK (cred_type IN ('rfid', 'qr')),
    value TEXT NOT NULL UNIQUE,
    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id),
    status TEXT NOT NULL CHECK (status IN ('active', 'locked', 'lost')),
    issued_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS registration_requests (
    id INTEGER PRIMARY KEY,
    unit_id INTEGER NOT NULL REFERENCES units(id),
    plate TEXT NOT NULL,
    vehicle_type TEXT NOT NULL CHECK (vehicle_type IN ('car', 'moto')),
    plan_type TEXT NOT NULL,
    doc_image_path TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved_waiting_card', 'active', 'rejected')
    ),
    reject_reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    gate TEXT NOT NULL CHECK (gate IN ('in', 'out')),
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    credential_id INTEGER REFERENCES credentials(id),
    vehicle_id INTEGER REFERENCES vehicles(id),
    result TEXT NOT NULL CHECK (result IN ('allow', 'deny')),
    reason_code TEXT NOT NULL,
    image_path TEXT,
    ocr_plate TEXT,
    ocr_confidence REAL,
    plate_mismatch INTEGER NOT NULL DEFAULT 0 CHECK (plate_mismatch IN (0, 1)),
    t_tap TEXT,
    t_sent TEXT,
    t_received TEXT,
    t_servo_cmd TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY,
    recipient_user_id INTEGER NOT NULL REFERENCES users(id),
    level TEXT NOT NULL CHECK (level IN ('alert', 'info')),
    content TEXT NOT NULL,
    image_path TEXT,
    created_at TEXT NOT NULL,
    sent_telegram INTEGER NOT NULL DEFAULT 0 CHECK (sent_telegram IN (0, 1)),
    event_id INTEGER REFERENCES events(id),
    resolved_at TEXT,
    resolved_by_user_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gate_commands (
    id INTEGER PRIMARY KEY,
    gate TEXT NOT NULL CHECK (gate IN ('in', 'out')),
    command TEXT NOT NULL CHECK (command IN ('open_command')),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_credentials_value ON credentials(value);
CREATE INDEX IF NOT EXISTS idx_vehicles_in_lot ON vehicles(in_lot);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_gate_commands_pending ON gate_commands(sent_at, id);
"""
