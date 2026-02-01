-- HostKit Voice Service Schema
-- SQLite database schema for voice project tracking and call history
-- Located in: /var/lib/hostkit/hostkit.db (shared HostKit database)

-- =============================================================================
-- Voice Projects
-- Tracks which projects have voice service enabled and their configuration
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_projects (
    project TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    twilio_phone_number TEXT,
    default_agent TEXT,
    enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_projects_enabled ON voice_projects(enabled);

-- =============================================================================
-- Voice Calls
-- Complete call history with outcomes, slots, and transcripts
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT UNIQUE NOT NULL,
    project TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('inbound', 'outbound')),
    from_number TEXT,
    to_number TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    turns_count INTEGER DEFAULT 0,
    outcome TEXT CHECK(outcome IN ('completed', 'failed', 'transferred', 'timeout')),
    slots TEXT,  -- JSON stored as TEXT
    transcript_summary TEXT,
    cost REAL,  -- SQLite uses REAL for decimal values
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_calls_project ON voice_calls(project);
CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_voice_calls_call_sid ON voice_calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_calls(outcome);

-- =============================================================================
-- Voice Conversation Turns
-- Individual turns within a call (agent and human utterances)
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    turn_number INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,  -- Seconds from call start
    speaker TEXT NOT NULL CHECK(speaker IN ('agent', 'human')),
    text TEXT NOT NULL,
    confidence REAL,  -- 0.0 - 1.0 for STT confidence
    duration_ms INTEGER,  -- Duration of this utterance
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_turns_call ON voice_conversation_turns(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_turns_speaker ON voice_conversation_turns(speaker);

-- =============================================================================
-- Voice Action Results
-- Results from webhook actions executed during calls
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_action_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    action_name TEXT NOT NULL,
    timestamp_seconds REAL NOT NULL,  -- Seconds from call start
    success INTEGER NOT NULL,  -- 0 or 1 (boolean)
    request_params TEXT,  -- JSON stored as TEXT
    response_data TEXT,  -- JSON stored as TEXT
    error_message TEXT,
    latency_ms INTEGER,  -- Time taken for webhook to respond
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_actions_call ON voice_action_results(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_actions_name ON voice_action_results(action_name);
CREATE INDEX IF NOT EXISTS idx_voice_actions_success ON voice_action_results(success);

-- =============================================================================
-- Voice Slot Extractions
-- Track slot filling progress during calls (for flows/structured conversations)
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_slot_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    slot_name TEXT NOT NULL,
    slot_value TEXT NOT NULL,
    slot_type TEXT,  -- string, integer, date, phone, etc.
    extracted_at_turn INTEGER,  -- Turn number when slot was filled
    confirmed INTEGER DEFAULT 0,  -- 0 or 1 (whether user confirmed)
    confidence REAL,  -- LLM confidence in extraction
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_slots_call ON voice_slot_extractions(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_slots_name ON voice_slot_extractions(slot_name);

-- =============================================================================
-- Voice Call Events
-- Detailed event log for debugging and monitoring
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_call_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,
    event_type TEXT NOT NULL,  -- state_change, error, provider_failure, etc.
    event_data TEXT,  -- JSON stored as TEXT
    severity TEXT CHECK(severity IN ('debug', 'info', 'warning', 'error')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_events_call ON voice_call_events(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_events_type ON voice_call_events(event_type);
CREATE INDEX IF NOT EXISTS idx_voice_events_severity ON voice_call_events(severity);

-- =============================================================================
-- Voice Sentiment Analysis
-- Track caller emotional state changes throughout the call
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_sentiment_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,
    sentiment TEXT CHECK(sentiment IN ('positive', 'neutral', 'negative', 'frustrated', 'confused', 'angry', 'satisfied')),
    score REAL NOT NULL,  -- 0.0 - 1.0
    turn_id INTEGER,  -- Optional reference to specific turn
    action_taken TEXT,  -- What the system did in response (e.g., 'transferred_human')
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE,
    FOREIGN KEY (turn_id) REFERENCES voice_conversation_turns(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_voice_sentiment_call ON voice_sentiment_analysis(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_sentiment_type ON voice_sentiment_analysis(sentiment);

-- =============================================================================
-- Voice DNC List (Do Not Call)
-- Project-specific do-not-call registry for compliance
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_dnc_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,  -- 'customer_request', 'compliance', 'bounce', etc.
    notes TEXT,
    UNIQUE(project, phone_number),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_dnc_project ON voice_dnc_list(project);
CREATE INDEX IF NOT EXISTS idx_voice_dnc_phone ON voice_dnc_list(phone_number);

-- =============================================================================
-- Voice Usage Tracking
-- Aggregate usage metrics for billing and analytics
-- =============================================================================

CREATE TABLE IF NOT EXISTS voice_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    date TEXT NOT NULL,  -- YYYY-MM-DD
    calls_total INTEGER DEFAULT 0,
    calls_completed INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    calls_transferred INTEGER DEFAULT 0,
    total_duration_seconds INTEGER DEFAULT 0,
    total_cost REAL DEFAULT 0.0,
    actions_executed INTEGER DEFAULT 0,
    actions_failed INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project, date),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_voice_usage_project_date ON voice_usage(project, date);
CREATE INDEX IF NOT EXISTS idx_voice_usage_date ON voice_usage(date);
