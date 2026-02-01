-- HostKit Chatbot Service Database Schema
-- Complete schema for chatbot conversations, messages, and configuration

-- Chatbot configuration per project
CREATE TABLE IF NOT EXISTS chatbot_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project TEXT UNIQUE NOT NULL DEFAULT '_default',
    enabled BOOLEAN NOT NULL DEFAULT true,

    -- Display settings
    name TEXT NOT NULL DEFAULT 'Assistant',
    greeting TEXT DEFAULT 'Hi! How can I help you today?',
    placeholder TEXT DEFAULT 'Type your message...',

    -- Widget appearance
    position TEXT NOT NULL DEFAULT 'bottom-right' CHECK (position IN ('bottom-right', 'bottom-left', 'top-right', 'top-left')),
    theme TEXT NOT NULL DEFAULT 'light' CHECK (theme IN ('light', 'dark', 'auto')),
    primary_color TEXT NOT NULL DEFAULT '#6366f1',

    -- Behavior
    system_prompt TEXT,
    suggested_questions JSONB DEFAULT '[]'::jsonb,

    -- CTA (Call-to-Action) settings
    cta_enabled BOOLEAN NOT NULL DEFAULT false,
    cta_text TEXT,
    cta_url TEXT,
    cta_after_messages INTEGER NOT NULL DEFAULT 3,

    -- LLM settings
    llm_provider TEXT NOT NULL DEFAULT 'anthropic' CHECK (llm_provider IN ('anthropic', 'openai')),
    llm_model TEXT NOT NULL DEFAULT 'claude-sonnet-4-20250514',
    max_tokens INTEGER NOT NULL DEFAULT 1024,
    temperature NUMERIC(3,2) NOT NULL DEFAULT 0.7,

    -- Limits
    max_messages_per_conversation INTEGER NOT NULL DEFAULT 50,
    session_timeout_minutes INTEGER NOT NULL DEFAULT 30,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Conversations (sessions)
CREATE TABLE IF NOT EXISTS chatbot_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project TEXT NOT NULL,

    -- Session tracking
    session_id TEXT NOT NULL,
    visitor_id TEXT,

    -- Metadata
    page_url TEXT,
    referrer TEXT,
    user_agent TEXT,
    ip_address TEXT,

    -- State
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'ended', 'timed_out')),
    message_count INTEGER NOT NULL DEFAULT 0,

    -- CTA tracking
    cta_shown BOOLEAN NOT NULL DEFAULT false,
    cta_clicked BOOLEAN NOT NULL DEFAULT false,
    cta_shown_at TIMESTAMPTZ,
    cta_clicked_at TIMESTAMPTZ,

    -- Timestamps
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

-- Messages
CREATE TABLE IF NOT EXISTS chatbot_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES chatbot_conversations(id) ON DELETE CASCADE,
    project TEXT NOT NULL,

    -- Message content
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,

    -- Metadata
    tokens_used INTEGER,
    model_used TEXT,
    latency_ms INTEGER,

    -- Error tracking
    error BOOLEAN NOT NULL DEFAULT false,
    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Rate limiting
CREATE TABLE IF NOT EXISTS chatbot_rate_limits (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project TEXT NOT NULL,
    identifier TEXT NOT NULL,  -- IP address or session_id
    window_start TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,

    UNIQUE(project, identifier, window_start)
);

-- Analytics (daily aggregates)
CREATE TABLE IF NOT EXISTS chatbot_analytics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project TEXT NOT NULL,
    date DATE NOT NULL,

    -- Counts
    conversations_started INTEGER NOT NULL DEFAULT 0,
    conversations_ended INTEGER NOT NULL DEFAULT 0,
    messages_sent INTEGER NOT NULL DEFAULT 0,
    messages_received INTEGER NOT NULL DEFAULT 0,

    -- CTA metrics
    cta_shown_count INTEGER NOT NULL DEFAULT 0,
    cta_clicked_count INTEGER NOT NULL DEFAULT 0,

    -- Averages
    avg_messages_per_conversation NUMERIC(5,2),
    avg_session_duration_seconds INTEGER,

    -- Token usage
    total_tokens_used INTEGER NOT NULL DEFAULT 0,

    UNIQUE(project, date)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_chatbot_configs_project ON chatbot_configs(project);

CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_project ON chatbot_conversations(project);
CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_session ON chatbot_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_visitor ON chatbot_conversations(visitor_id);
CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_status ON chatbot_conversations(status);
CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_active ON chatbot_conversations(project, session_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_chatbot_conversations_started ON chatbot_conversations(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_chatbot_messages_conversation ON chatbot_messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chatbot_messages_project ON chatbot_messages(project);
CREATE INDEX IF NOT EXISTS idx_chatbot_messages_created ON chatbot_messages(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_chatbot_rate_limits_lookup ON chatbot_rate_limits(project, identifier, window_start);

CREATE INDEX IF NOT EXISTS idx_chatbot_analytics_project_date ON chatbot_analytics(project, date DESC);

-- Insert default configuration
INSERT INTO chatbot_configs (project) VALUES ('_default') ON CONFLICT DO NOTHING;
