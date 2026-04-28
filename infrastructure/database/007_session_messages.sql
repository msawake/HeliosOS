-- Migration: 007_session_messages.sql
-- Description: Create a normalized table for session messages to prevent write amplification

CREATE TABLE IF NOT EXISTS session_messages (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    turn_number INT NOT NULL,
    role TEXT NOT NULL,
    content JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_session_messages_session ON session_messages(session_id, turn_number);
