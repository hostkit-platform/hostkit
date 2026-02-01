"""SQLite database layer for HostKit."""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from hostkit.config import get_config

# Schema version for migrations
SCHEMA_VERSION = 24

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

-- Projects table
CREATE TABLE IF NOT EXISTS projects (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    runtime TEXT NOT NULL DEFAULT 'python',
    port INTEGER NOT NULL,
    redis_db INTEGER,
    status TEXT NOT NULL DEFAULT 'stopped',
    description TEXT,
    created_by TEXT DEFAULT 'root'
);

-- Domains table
CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    ssl_provisioned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Backups table
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    type TEXT NOT NULL,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    r2_synced INTEGER NOT NULL DEFAULT 0,
    r2_key TEXT,
    r2_synced_at TEXT,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Auth services table (per-project auth configuration)
CREATE TABLE IF NOT EXISTS auth_services (
    project TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    auth_port INTEGER NOT NULL,
    auth_db_name TEXT NOT NULL,
    auth_db_user TEXT NOT NULL,
    google_client_id TEXT,
    google_web_client_id TEXT,
    google_client_secret TEXT,
    apple_client_id TEXT,
    apple_team_id TEXT,
    apple_key_id TEXT,
    email_enabled INTEGER NOT NULL DEFAULT 1,
    magic_link_enabled INTEGER NOT NULL DEFAULT 1,
    anonymous_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- SSH configuration table
CREATE TABLE IF NOT EXISTS ssh_config (
    project TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    disabled_at TEXT,
    disabled_by TEXT,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Operators table (AI agent users)
CREATE TABLE IF NOT EXISTS operators (
    username TEXT PRIMARY KEY,
    ssh_keys TEXT,
    created_at TEXT NOT NULL,
    last_login TEXT
);

-- Releases table (deployment versions)
CREATE TABLE IF NOT EXISTS releases (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    release_name TEXT NOT NULL,
    release_path TEXT NOT NULL,
    deployed_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 0,
    files_synced INTEGER,
    deployed_by TEXT,
    checkpoint_id INTEGER,
    env_snapshot TEXT,
    git_commit TEXT,
    git_branch TEXT,
    git_tag TEXT,
    git_repo TEXT,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE,
    FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(id) ON DELETE SET NULL
);

-- Config table (key-value settings)
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- SSL rate limiting table
CREATE TABLE IF NOT EXISTS ssl_rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    domain TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- SSH key audit log
CREATE TABLE IF NOT EXISTS ssh_key_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    action TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    key_comment TEXT,
    added_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Scheduled tasks (cron jobs via systemd timers)
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    name TEXT NOT NULL,
    schedule TEXT NOT NULL,
    schedule_cron TEXT,
    command TEXT NOT NULL,
    description TEXT,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    last_run_at TEXT,
    last_run_status TEXT,
    last_run_exit_code INTEGER,
    UNIQUE(project, name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Celery workers table
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    worker_name TEXT NOT NULL DEFAULT 'default',
    concurrency INTEGER DEFAULT 2,
    queues TEXT,
    app_module TEXT DEFAULT 'app',
    loglevel TEXT DEFAULT 'info',
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT,
    UNIQUE(project, worker_name),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Celery beat scheduler table
CREATE TABLE IF NOT EXISTS celery_beat (
    project TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 1,
    schedule_file TEXT DEFAULT 'celerybeat-schedule',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Vector service projects table
CREATE TABLE IF NOT EXISTS vector_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL UNIQUE,
    enabled INTEGER DEFAULT 1,
    api_key TEXT,
    api_key_hash TEXT NOT NULL,
    api_key_prefix TEXT NOT NULL,
    database_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_activity_at TEXT,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Database checkpoints table
CREATE TABLE IF NOT EXISTS checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    label TEXT,
    checkpoint_type TEXT NOT NULL,
    trigger_source TEXT,
    database_name TEXT NOT NULL,
    backup_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    expires_at TEXT,
    metadata TEXT,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Alert notification channels
CREATE TABLE IF NOT EXISTS alert_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    name TEXT NOT NULL,
    channel_type TEXT NOT NULL,
    config TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    muted_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_name, name),
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Alert history
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_status TEXT NOT NULL,
    channel_name TEXT,
    notification_sent INTEGER DEFAULT 0,
    notification_error TEXT,
    payload TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Deploy history (every deploy attempt for rate limiting)
CREATE TABLE IF NOT EXISTS deploy_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    deployed_at TEXT NOT NULL,
    deployed_by TEXT NOT NULL,
    success INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER,
    source_type TEXT,
    files_synced INTEGER,
    override_used INTEGER DEFAULT 0,
    error_message TEXT,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Rate limit configuration per project
CREATE TABLE IF NOT EXISTS rate_limits (
    project_name TEXT PRIMARY KEY,
    max_deploys INTEGER NOT NULL DEFAULT 10,
    window_minutes INTEGER NOT NULL DEFAULT 60,
    failure_cooldown_minutes INTEGER NOT NULL DEFAULT 5,
    consecutive_failure_limit INTEGER NOT NULL DEFAULT 3,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Auto-pause configuration and state per project
CREATE TABLE IF NOT EXISTS auto_pause (
    project_name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    failure_threshold INTEGER DEFAULT 5,
    window_minutes INTEGER DEFAULT 10,
    paused INTEGER DEFAULT 0,
    paused_at TEXT,
    paused_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Sandboxes table (temporary isolated project clones)
CREATE TABLE IF NOT EXISTS sandboxes (
    id TEXT PRIMARY KEY,
    sandbox_name TEXT NOT NULL UNIQUE,
    source_project TEXT NOT NULL,
    source_release TEXT,
    port INTEGER NOT NULL,
    domain TEXT,
    db_name TEXT,
    status TEXT DEFAULT 'creating',
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    metadata TEXT,
    FOREIGN KEY (source_project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Resource limits table (cgroups configuration per project)
CREATE TABLE IF NOT EXISTS resource_limits (
    project_name TEXT PRIMARY KEY,
    cpu_quota INTEGER,
    memory_max_mb INTEGER,
    memory_high_mb INTEGER,
    tasks_max INTEGER,
    disk_quota_mb INTEGER,
    enabled INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Git configuration per project
CREATE TABLE IF NOT EXISTS git_config (
    project_name TEXT PRIMARY KEY,
    repo_url TEXT NOT NULL,
    default_branch TEXT DEFAULT 'main',
    ssh_key_path TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Environments table (staging/production per project)
CREATE TABLE IF NOT EXISTS environments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    env_name TEXT NOT NULL,
    linux_user TEXT NOT NULL,
    port INTEGER NOT NULL,
    db_name TEXT,
    share_parent_db INTEGER DEFAULT 0,
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    UNIQUE(project_name, env_name),
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Events table (structured HostKit operation events)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    category TEXT NOT NULL,
    event_type TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'INFO',
    message TEXT NOT NULL,
    data TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Metrics samples table (time-series data)
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    cpu_percent REAL,
    memory_rss_bytes INTEGER,
    memory_percent REAL,
    disk_used_bytes INTEGER,
    process_count INTEGER,
    requests_total INTEGER,
    requests_2xx INTEGER,
    requests_4xx INTEGER,
    requests_5xx INTEGER,
    avg_response_ms REAL,
    p95_response_ms REAL,
    db_size_bytes INTEGER,
    db_connections INTEGER,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Metrics configuration per project
CREATE TABLE IF NOT EXISTS metrics_config (
    project_name TEXT PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    collection_interval INTEGER DEFAULT 60,
    retention_days INTEGER DEFAULT 7,
    alert_on_threshold INTEGER DEFAULT 1,
    cpu_warning_percent REAL,
    cpu_critical_percent REAL,
    memory_warning_percent REAL,
    memory_critical_percent REAL,
    error_rate_warning_percent REAL,
    error_rate_critical_percent REAL,
    last_collected_at TEXT,
    nginx_log_position INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
);

-- Image generation tracking
CREATE TABLE IF NOT EXISTS image_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    image_url TEXT NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_domains_project ON domains(project);
CREATE INDEX IF NOT EXISTS idx_backups_project ON backups(project);
CREATE INDEX IF NOT EXISTS idx_backups_created ON backups(created_at);
CREATE INDEX IF NOT EXISTS idx_releases_project ON releases(project);
CREATE INDEX IF NOT EXISTS idx_releases_current ON releases(project, is_current);
CREATE INDEX IF NOT EXISTS idx_ssl_rate_limits_project ON ssl_rate_limits(project);
CREATE INDEX IF NOT EXISTS idx_ssl_rate_limits_attempted ON ssl_rate_limits(attempted_at);
CREATE INDEX IF NOT EXISTS idx_ssh_key_audit_project ON ssh_key_audit(project);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project ON scheduled_tasks(project);
CREATE INDEX IF NOT EXISTS idx_workers_project ON workers(project);
CREATE INDEX IF NOT EXISTS idx_vector_projects_project ON vector_projects(project_name);
CREATE INDEX IF NOT EXISTS idx_checkpoints_project ON checkpoints(project_name);
CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON checkpoints(created_at);
CREATE INDEX IF NOT EXISTS idx_checkpoints_expires ON checkpoints(expires_at);
CREATE INDEX IF NOT EXISTS idx_alert_channels_project ON alert_channels(project_name);
CREATE INDEX IF NOT EXISTS idx_alert_history_project ON alert_history(project_name);
CREATE INDEX IF NOT EXISTS idx_alert_history_created ON alert_history(created_at);
CREATE INDEX IF NOT EXISTS idx_deploy_history_project ON deploy_history(project_name);
CREATE INDEX IF NOT EXISTS idx_deploy_history_deployed ON deploy_history(deployed_at);
CREATE INDEX IF NOT EXISTS idx_sandboxes_source ON sandboxes(source_project);
CREATE INDEX IF NOT EXISTS idx_sandboxes_expires ON sandboxes(expires_at);
CREATE INDEX IF NOT EXISTS idx_sandboxes_status ON sandboxes(status);
CREATE INDEX IF NOT EXISTS idx_resource_limits_project ON resource_limits(project_name);
CREATE INDEX IF NOT EXISTS idx_git_config_project ON git_config(project_name);
CREATE INDEX IF NOT EXISTS idx_environments_project ON environments(project_name);
CREATE INDEX IF NOT EXISTS idx_environments_user ON environments(linux_user);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_name);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_level ON events(level);
CREATE INDEX IF NOT EXISTS idx_metrics_project_time ON metrics(project_name, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_metrics_config_project ON metrics_config(project_name);
CREATE INDEX IF NOT EXISTS idx_image_generations_project ON image_generations(project);
CREATE INDEX IF NOT EXISTS idx_image_generations_created ON image_generations(created_at);

-- Voice service tables
CREATE TABLE IF NOT EXISTS voice_projects (
    project TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    twilio_phone_number TEXT,
    default_agent TEXT,
    enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

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
    slots TEXT,
    transcript_summary TEXT,
    cost REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_conversation_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    turn_number INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,
    speaker TEXT NOT NULL CHECK(speaker IN ('agent', 'human')),
    text TEXT NOT NULL,
    confidence REAL,
    duration_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_action_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    action_name TEXT NOT NULL,
    timestamp_seconds REAL NOT NULL,
    success INTEGER NOT NULL,
    request_params TEXT,
    response_data TEXT,
    error_message TEXT,
    latency_ms INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_slot_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    slot_name TEXT NOT NULL,
    slot_value TEXT NOT NULL,
    slot_type TEXT,
    extracted_at_turn INTEGER,
    confirmed INTEGER DEFAULT 0,
    confidence REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_call_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,
    event_type TEXT NOT NULL,
    event_data TEXT,
    severity TEXT CHECK(severity IN ('debug', 'info', 'warning', 'error')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_sentiment_analysis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    timestamp_seconds REAL NOT NULL,
    sentiment TEXT CHECK(sentiment IN (
        'positive', 'neutral', 'negative',
        'frustrated', 'confused', 'angry', 'satisfied'
    )),
    score REAL NOT NULL,
    turn_id INTEGER,
    action_taken TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE,
    FOREIGN KEY (turn_id) REFERENCES voice_conversation_turns(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS voice_dnc_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reason TEXT,
    notes TEXT,
    UNIQUE(project, phone_number),
    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voice_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    date TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_voice_projects_enabled ON voice_projects(enabled);
CREATE INDEX IF NOT EXISTS idx_voice_calls_project ON voice_calls(project);
CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
CREATE INDEX IF NOT EXISTS idx_voice_calls_call_sid ON voice_calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_calls(outcome);
CREATE INDEX IF NOT EXISTS idx_voice_turns_call ON voice_conversation_turns(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_turns_speaker ON voice_conversation_turns(speaker);
CREATE INDEX IF NOT EXISTS idx_voice_actions_call ON voice_action_results(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_actions_name ON voice_action_results(action_name);
CREATE INDEX IF NOT EXISTS idx_voice_actions_success ON voice_action_results(success);
CREATE INDEX IF NOT EXISTS idx_voice_slots_call ON voice_slot_extractions(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_slots_name ON voice_slot_extractions(slot_name);
CREATE INDEX IF NOT EXISTS idx_voice_events_call ON voice_call_events(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_events_type ON voice_call_events(event_type);
CREATE INDEX IF NOT EXISTS idx_voice_events_severity ON voice_call_events(severity);
CREATE INDEX IF NOT EXISTS idx_voice_sentiment_call ON voice_sentiment_analysis(call_id);
CREATE INDEX IF NOT EXISTS idx_voice_sentiment_type ON voice_sentiment_analysis(sentiment);
CREATE INDEX IF NOT EXISTS idx_voice_dnc_project ON voice_dnc_list(project);
CREATE INDEX IF NOT EXISTS idx_voice_dnc_phone ON voice_dnc_list(phone_number);
CREATE INDEX IF NOT EXISTS idx_voice_usage_project_date ON voice_usage(project, date);
CREATE INDEX IF NOT EXISTS idx_voice_usage_date ON voice_usage(date);
"""


class Database:
    """SQLite database manager for HostKit."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize database connection."""
        if db_path is None:
            db_path = get_config().db_path
        self.db_path = db_path
        self._ensure_parent_dir()

    def _ensure_parent_dir(self) -> None:
        """Ensure the parent directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _secure_db_permissions(self) -> None:
        """Set secure permissions on the database file (readable by owner only)."""
        import os

        if self.db_path.exists():
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass  # May fail if not owner

    @contextmanager
    def connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with automatic cleanup."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with transaction support."""
        with self.connection() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize(self) -> None:
        """Initialize the database schema."""
        with self.transaction() as conn:
            # Check current schema version
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            if cursor.fetchone() is None:
                # Fresh database - create schema
                conn.executescript(SCHEMA_SQL)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, datetime.utcnow().isoformat()),
                )
            else:
                # Check if migration is needed
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                current_version = cursor.fetchone()[0] or 0
                if current_version < SCHEMA_VERSION:
                    self._migrate(conn, current_version)

        # Ensure database file has secure permissions (root/owner only)
        self._secure_db_permissions()

    def _migrate(self, conn: sqlite3.Connection, from_version: int) -> None:
        """Run migrations to update schema."""
        if from_version < 2:
            # Add auth_services table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auth_services (
                    project TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    auth_port INTEGER NOT NULL,
                    auth_db_name TEXT NOT NULL,
                    auth_db_user TEXT NOT NULL,
                    google_client_id TEXT,
                    google_client_secret TEXT,
                    apple_client_id TEXT,
                    apple_team_id TEXT,
                    apple_key_id TEXT,
                    email_enabled INTEGER NOT NULL DEFAULT 1,
                    magic_link_enabled INTEGER NOT NULL DEFAULT 1,
                    anonymous_enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (2, datetime.utcnow().isoformat()),
            )

        if from_version < 3:
            # Add ssh_config table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ssh_config (
                    project TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    disabled_at TEXT,
                    disabled_by TEXT,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (3, datetime.utcnow().isoformat()),
            )

        if from_version < 4:
            # Add operators table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS operators (
                    username TEXT PRIMARY KEY,
                    ssh_keys TEXT,
                    created_at TEXT NOT NULL,
                    last_login TEXT
                )
            """)

            # Add releases table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS releases (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    release_name TEXT NOT NULL,
                    release_path TEXT NOT NULL,
                    deployed_at TEXT NOT NULL,
                    is_current INTEGER NOT NULL DEFAULT 0,
                    files_synced INTEGER,
                    deployed_by TEXT,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_releases_project ON releases(project)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_releases_current ON releases(project, is_current)"
            )

            # Add config table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

            # Add created_by column to existing projects table
            conn.execute("ALTER TABLE projects ADD COLUMN created_by TEXT DEFAULT 'root'")

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (4, datetime.utcnow().isoformat()),
            )

        if from_version < 5:
            # Add ssl_rate_limits table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ssl_rate_limits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    attempted_at TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ssl_rate_limits_project ON ssl_rate_limits(project)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_ssl_rate_limits_attempted "
                "ON ssl_rate_limits(attempted_at)"
            )

            # Add ssh_key_audit table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ssh_key_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    action TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    key_comment TEXT,
                    added_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ssh_key_audit_project ON ssh_key_audit(project)"
            )

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (5, datetime.utcnow().isoformat()),
            )

        if from_version < 6:
            # Add scheduled_tasks table for cron jobs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    name TEXT NOT NULL,
                    schedule TEXT NOT NULL,
                    schedule_cron TEXT,
                    command TEXT NOT NULL,
                    description TEXT,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT,
                    last_run_at TEXT,
                    last_run_status TEXT,
                    last_run_exit_code INTEGER,
                    UNIQUE(project, name),
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_project ON scheduled_tasks(project)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (6, datetime.utcnow().isoformat()),
            )

        if from_version < 7:
            # Add workers table for Celery background workers
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workers (
                    id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    worker_name TEXT NOT NULL DEFAULT 'default',
                    concurrency INTEGER DEFAULT 2,
                    queues TEXT,
                    app_module TEXT DEFAULT 'app',
                    loglevel TEXT DEFAULT 'info',
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by TEXT,
                    UNIQUE(project, worker_name),
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workers_project ON workers(project)")

            # Add celery_beat table for beat scheduler
            conn.execute("""
                CREATE TABLE IF NOT EXISTS celery_beat (
                    project TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    schedule_file TEXT DEFAULT 'celerybeat-schedule',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (7, datetime.utcnow().isoformat()),
            )

        if from_version < 8:
            # Add vector_projects table for vector service
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vector_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL UNIQUE,
                    enabled INTEGER DEFAULT 1,
                    api_key_hash TEXT NOT NULL,
                    api_key_prefix TEXT NOT NULL,
                    database_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_activity_at TEXT,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_vector_projects_project "
                "ON vector_projects(project_name)"
            )

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (8, datetime.utcnow().isoformat()),
            )

        if from_version < 9:
            # Add api_key column to vector_projects for CLI access
            conn.execute("ALTER TABLE vector_projects ADD COLUMN api_key TEXT")

            # Add checkpoints table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    label TEXT,
                    checkpoint_type TEXT NOT NULL,
                    trigger_source TEXT,
                    database_name TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    expires_at TEXT,
                    metadata TEXT,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_project ON checkpoints(project_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_created ON checkpoints(created_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_checkpoints_expires ON checkpoints(expires_at)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (9, datetime.utcnow().isoformat()),
            )

        if from_version < 10:
            # Add alert_channels table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    channel_type TEXT NOT NULL,
                    config TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(project_name, name),
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_alert_channels_project "
                "ON alert_channels(project_name)"
            )

            # Add alert_history table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    event_status TEXT NOT NULL,
                    channel_name TEXT,
                    notification_sent INTEGER DEFAULT 0,
                    notification_error TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_alert_history_project "
                "ON alert_history(project_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alert_history_created ON alert_history(created_at)"
            )

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (10, datetime.utcnow().isoformat()),
            )

        if from_version < 11:
            # Add muted_until column to alert_channels for alert muting
            conn.execute("ALTER TABLE alert_channels ADD COLUMN muted_until TEXT")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (11, datetime.utcnow().isoformat()),
            )

        if from_version < 12:
            # Add checkpoint_id and env_snapshot columns to releases for multi-layer rollback
            conn.execute(
                "ALTER TABLE releases ADD COLUMN checkpoint_id "
                "INTEGER REFERENCES checkpoints(id) "
                "ON DELETE SET NULL"
            )
            conn.execute("ALTER TABLE releases ADD COLUMN env_snapshot TEXT")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (12, datetime.utcnow().isoformat()),
            )

        if from_version < 13:
            # Add deploy_history table for rate limiting
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deploy_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    deployed_at TEXT NOT NULL,
                    deployed_by TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1,
                    duration_ms INTEGER,
                    source_type TEXT,
                    files_synced INTEGER,
                    override_used INTEGER DEFAULT 0,
                    error_message TEXT,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_deploy_history_project "
                "ON deploy_history(project_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_deploy_history_deployed "
                "ON deploy_history(deployed_at)"
            )

            # Add rate_limits table for per-project configuration
            conn.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    project_name TEXT PRIMARY KEY,
                    max_deploys INTEGER NOT NULL DEFAULT 10,
                    window_minutes INTEGER NOT NULL DEFAULT 60,
                    failure_cooldown_minutes INTEGER NOT NULL DEFAULT 5,
                    consecutive_failure_limit INTEGER NOT NULL DEFAULT 3,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (13, datetime.utcnow().isoformat()),
            )

        if from_version < 14:
            # Add auto_pause table for auto-pause on failures
            conn.execute("""
                CREATE TABLE IF NOT EXISTS auto_pause (
                    project_name TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 0,
                    failure_threshold INTEGER DEFAULT 5,
                    window_minutes INTEGER DEFAULT 10,
                    paused INTEGER DEFAULT 0,
                    paused_at TEXT,
                    paused_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (14, datetime.utcnow().isoformat()),
            )

        if from_version < 15:
            # Add sandboxes table for temporary isolated project clones
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sandboxes (
                    id TEXT PRIMARY KEY,
                    sandbox_name TEXT NOT NULL UNIQUE,
                    source_project TEXT NOT NULL,
                    source_release TEXT,
                    port INTEGER NOT NULL,
                    domain TEXT,
                    db_name TEXT,
                    status TEXT DEFAULT 'creating',
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (source_project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sandboxes_source ON sandboxes(source_project)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sandboxes_expires ON sandboxes(expires_at)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sandboxes_status ON sandboxes(status)")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (15, datetime.utcnow().isoformat()),
            )

        if from_version < 16:
            # Add resource_limits table for cgroups configuration per project
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resource_limits (
                    project_name TEXT PRIMARY KEY,
                    cpu_quota INTEGER,
                    memory_max_mb INTEGER,
                    memory_high_mb INTEGER,
                    tasks_max INTEGER,
                    disk_quota_mb INTEGER,
                    enabled INTEGER DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_resource_limits_project "
                "ON resource_limits(project_name)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (16, datetime.utcnow().isoformat()),
            )

        if from_version < 17:
            # Add git_config table for git-based deployments
            conn.execute("""
                CREATE TABLE IF NOT EXISTS git_config (
                    project_name TEXT PRIMARY KEY,
                    repo_url TEXT NOT NULL,
                    default_branch TEXT DEFAULT 'main',
                    ssh_key_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_git_config_project ON git_config(project_name)"
            )

            # Add git columns to releases table
            conn.execute("ALTER TABLE releases ADD COLUMN git_commit TEXT")
            conn.execute("ALTER TABLE releases ADD COLUMN git_branch TEXT")
            conn.execute("ALTER TABLE releases ADD COLUMN git_tag TEXT")
            conn.execute("ALTER TABLE releases ADD COLUMN git_repo TEXT")

            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (17, datetime.utcnow().isoformat()),
            )

        if from_version < 18:
            # Add environments table for multi-environment support
            conn.execute("""
                CREATE TABLE IF NOT EXISTS environments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    env_name TEXT NOT NULL,
                    linux_user TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    db_name TEXT,
                    share_parent_db INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'stopped',
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    UNIQUE(project_name, env_name),
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_environments_project ON environments(project_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_environments_user ON environments(linux_user)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (18, datetime.utcnow().isoformat()),
            )

        if from_version < 19:
            # Add events table for structured operation logging
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    level TEXT NOT NULL DEFAULT 'INFO',
                    message TEXT NOT NULL,
                    data TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_category ON events(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_level ON events(level)")
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (19, datetime.utcnow().isoformat()),
            )

        if from_version < 20:
            # Add metrics tables for time-series data collection
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    cpu_percent REAL,
                    memory_rss_bytes INTEGER,
                    memory_percent REAL,
                    disk_used_bytes INTEGER,
                    process_count INTEGER,
                    requests_total INTEGER,
                    requests_2xx INTEGER,
                    requests_4xx INTEGER,
                    requests_5xx INTEGER,
                    avg_response_ms REAL,
                    p95_response_ms REAL,
                    db_size_bytes INTEGER,
                    db_connections INTEGER,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics_config (
                    project_name TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 0,
                    collection_interval INTEGER DEFAULT 60,
                    retention_days INTEGER DEFAULT 7,
                    alert_on_threshold INTEGER DEFAULT 1,
                    cpu_warning_percent REAL,
                    cpu_critical_percent REAL,
                    memory_warning_percent REAL,
                    memory_critical_percent REAL,
                    error_rate_warning_percent REAL,
                    error_rate_critical_percent REAL,
                    last_collected_at TEXT,
                    nginx_log_position INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (project_name) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_metrics_project_time "
                "ON metrics(project_name, collected_at DESC)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metrics_type ON metrics(metric_type)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_metrics_config_project "
                "ON metrics_config(project_name)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (20, datetime.utcnow().isoformat()),
            )

        if from_version < 21:
            # Add image_generations table for AI image generation tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS image_generations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    image_url TEXT NOT NULL,
                    cost REAL NOT NULL DEFAULT 0,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_image_generations_project "
                "ON image_generations(project)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_image_generations_created "
                "ON image_generations(created_at)"
            )
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (21, datetime.utcnow().isoformat()),
            )

        if from_version < 22:
            # Add google_web_client_id column to auth_services for web OAuth
            try:
                conn.execute("ALTER TABLE auth_services ADD COLUMN google_web_client_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (22, datetime.utcnow().isoformat()),
            )

        if from_version < 23:
            # Add voice service tables
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS voice_projects (
                    project TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    twilio_phone_number TEXT,
                    default_agent TEXT,
                    enabled_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                );

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
                    outcome TEXT CHECK(outcome IN (
                        'completed', 'failed',
                        'transferred', 'timeout')),
                    slots TEXT,
                    transcript_summary TEXT,
                    cost REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_conversation_turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL,
                    turn_number INTEGER NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    speaker TEXT NOT NULL CHECK(speaker IN ('agent', 'human')),
                    text TEXT NOT NULL,
                    confidence REAL,
                    duration_ms INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_action_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL,
                    action_name TEXT NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    success INTEGER NOT NULL,
                    request_params TEXT,
                    response_data TEXT,
                    error_message TEXT,
                    latency_ms INTEGER,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_slot_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL,
                    slot_name TEXT NOT NULL,
                    slot_value TEXT NOT NULL,
                    slot_type TEXT,
                    extracted_at_turn INTEGER,
                    confirmed INTEGER DEFAULT 0,
                    confidence REAL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_call_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT,
                    severity TEXT CHECK(severity IN ('debug', 'info', 'warning', 'error')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_sentiment_analysis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_id INTEGER NOT NULL,
                    timestamp_seconds REAL NOT NULL,
                    sentiment TEXT CHECK(sentiment IN (
                        'positive', 'neutral', 'negative',
                        'frustrated', 'confused',
                        'angry', 'satisfied')),
                    score REAL NOT NULL,
                    turn_id INTEGER,
                    action_taken TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (call_id) REFERENCES voice_calls(id) ON DELETE CASCADE,
                    FOREIGN KEY (turn_id) REFERENCES voice_conversation_turns(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS voice_dnc_list (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    phone_number TEXT NOT NULL,
                    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT,
                    notes TEXT,
                    UNIQUE(project, phone_number),
                    FOREIGN KEY (project) REFERENCES projects(name) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS voice_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project TEXT NOT NULL,
                    date TEXT NOT NULL,
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

                CREATE INDEX IF NOT EXISTS idx_voice_projects_enabled ON voice_projects(enabled);
                CREATE INDEX IF NOT EXISTS idx_voice_calls_project ON voice_calls(project);
                CREATE INDEX IF NOT EXISTS idx_voice_calls_started ON voice_calls(started_at);
                CREATE INDEX IF NOT EXISTS idx_voice_calls_call_sid ON voice_calls(call_sid);
                CREATE INDEX IF NOT EXISTS idx_voice_calls_outcome ON voice_calls(outcome);
                CREATE INDEX IF NOT EXISTS idx_voice_turns_call
                    ON voice_conversation_turns(call_id);
                CREATE INDEX IF NOT EXISTS idx_voice_turns_speaker
                    ON voice_conversation_turns(speaker);
                CREATE INDEX IF NOT EXISTS idx_voice_actions_call
                    ON voice_action_results(call_id);
                CREATE INDEX IF NOT EXISTS idx_voice_actions_name
                    ON voice_action_results(action_name);
                CREATE INDEX IF NOT EXISTS idx_voice_actions_success
                    ON voice_action_results(success);
                CREATE INDEX IF NOT EXISTS idx_voice_slots_call
                    ON voice_slot_extractions(call_id);
                CREATE INDEX IF NOT EXISTS idx_voice_slots_name
                    ON voice_slot_extractions(slot_name);
                CREATE INDEX IF NOT EXISTS idx_voice_events_call
                    ON voice_call_events(call_id);
                CREATE INDEX IF NOT EXISTS idx_voice_events_type
                    ON voice_call_events(event_type);
                CREATE INDEX IF NOT EXISTS idx_voice_events_severity
                    ON voice_call_events(severity);
                CREATE INDEX IF NOT EXISTS idx_voice_sentiment_call
                    ON voice_sentiment_analysis(call_id);
                CREATE INDEX IF NOT EXISTS idx_voice_sentiment_type
                    ON voice_sentiment_analysis(sentiment);
                CREATE INDEX IF NOT EXISTS idx_voice_dnc_project ON voice_dnc_list(project);
                CREATE INDEX IF NOT EXISTS idx_voice_dnc_phone ON voice_dnc_list(phone_number);
                CREATE INDEX IF NOT EXISTS idx_voice_usage_project_date
                    ON voice_usage(project, date);
                CREATE INDEX IF NOT EXISTS idx_voice_usage_date ON voice_usage(date);
            """)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (23, datetime.utcnow().isoformat()),
            )

        if from_version < 24:
            # Add R2 cloud backup columns to backups table
            try:
                conn.execute("ALTER TABLE backups ADD COLUMN r2_synced INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE backups ADD COLUMN r2_key TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE backups ADD COLUMN r2_synced_at TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (24, datetime.utcnow().isoformat()),
            )

    def get_schema_version(self) -> int:
        """Get the current schema version."""
        try:
            with self.connection() as conn:
                cursor = conn.execute("SELECT MAX(version) FROM schema_version")
                result = cursor.fetchone()
                return result[0] if result and result[0] else 0
        except sqlite3.OperationalError:
            return 0

    # Project operations
    def create_project(
        self,
        name: str,
        runtime: str = "python",
        port: int = 8000,
        redis_db: int | None = None,
        description: str | None = None,
        created_by: str = "root",
    ) -> dict[str, Any]:
        """Create a new project."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO projects (
                    name, created_at, runtime, port,
                    redis_db, status, description, created_by
                )
                VALUES (?, ?, ?, ?, ?, 'stopped', ?, ?)
                """,
                (
                    name,
                    datetime.utcnow().isoformat(),
                    runtime,
                    port,
                    redis_db,
                    description,
                    created_by,
                ),
            )
        return self.get_project(name)  # type: ignore

    def get_project(self, name: str) -> dict[str, Any] | None:
        """Get a project by name."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM projects WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_projects(self) -> list[dict[str, Any]]:
        """List all projects."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM projects ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def update_project_status(self, name: str, status: str) -> None:
        """Update a project's status."""
        with self.transaction() as conn:
            conn.execute("UPDATE projects SET status = ? WHERE name = ?", (status, name))

    def delete_project(self, name: str) -> bool:
        """Delete a project. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def get_next_port(self) -> int:
        """Get the next available port number."""
        config = get_config()
        with self.connection() as conn:
            cursor = conn.execute("SELECT MAX(port) FROM projects")
            result = cursor.fetchone()
            max_port = result[0] if result and result[0] else config.base_port - 1
            return max_port + 1

    def get_next_redis_db(self) -> int:
        """Get the next available Redis database number (0-49)."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT redis_db FROM projects WHERE redis_db IS NOT NULL")
            used_dbs = {row[0] for row in cursor.fetchall()}
            for db in range(50):
                if db not in used_dbs:
                    return db
            raise RuntimeError("All Redis databases (0-49) are in use")

    # Domain operations
    def add_domain(self, domain: str, project: str) -> dict[str, Any]:
        """Add a domain to a project."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO domains (domain, project, ssl_provisioned, created_at)
                VALUES (?, ?, 0, ?)
                """,
                (domain, project, datetime.utcnow().isoformat()),
            )
        return self.get_domain(domain)  # type: ignore

    def get_domain(self, domain: str) -> dict[str, Any] | None:
        """Get a domain by name."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM domains WHERE domain = ?", (domain,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_domains(self, project: str | None = None) -> list[dict[str, Any]]:
        """List domains, optionally filtered by project."""
        with self.connection() as conn:
            if project:
                cursor = conn.execute(
                    "SELECT * FROM domains WHERE project = ? ORDER BY created_at", (project,)
                )
            else:
                cursor = conn.execute("SELECT * FROM domains ORDER BY project, created_at")
            return [dict(row) for row in cursor.fetchall()]

    def update_domain_ssl(self, domain: str, ssl_provisioned: bool) -> None:
        """Update a domain's SSL status."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE domains SET ssl_provisioned = ? WHERE domain = ?",
                (1 if ssl_provisioned else 0, domain),
            )

    def delete_domain(self, domain: str) -> bool:
        """Delete a domain. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM domains WHERE domain = ?", (domain,))
            return cursor.rowcount > 0

    # Backup operations
    def create_backup_record(
        self, backup_id: str, project: str, backup_type: str, path: str, size_bytes: int = 0
    ) -> dict[str, Any]:
        """Create a backup record."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO backups (id, project, type, path, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (backup_id, project, backup_type, path, size_bytes, datetime.utcnow().isoformat()),
            )
        return self.get_backup(backup_id)  # type: ignore

    def get_backup(self, backup_id: str) -> dict[str, Any] | None:
        """Get a backup by ID."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM backups WHERE id = ?", (backup_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_backups(self, project: str | None = None) -> list[dict[str, Any]]:
        """List backups, optionally filtered by project."""
        with self.connection() as conn:
            if project:
                cursor = conn.execute(
                    "SELECT * FROM backups WHERE project = ? ORDER BY created_at DESC", (project,)
                )
            else:
                cursor = conn.execute("SELECT * FROM backups ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def delete_backup_record(self, backup_id: str) -> bool:
        """Delete a backup record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
            return cursor.rowcount > 0

    def update_backup_r2_status(
        self,
        backup_id: str,
        r2_synced: bool,
        r2_key: str | None = None,
        r2_synced_at: str | None = None,
    ) -> bool:
        """Update R2 sync status for a backup. Returns True if updated."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE backups
                SET r2_synced = ?, r2_key = ?, r2_synced_at = ?
                WHERE id = ?
                """,
                (1 if r2_synced else 0, r2_key, r2_synced_at, backup_id),
            )
            return cursor.rowcount > 0

    def list_backups_by_r2_status(
        self, project: str | None = None, r2_synced: bool | None = None
    ) -> list[dict[str, Any]]:
        """List backups filtered by R2 sync status."""
        with self.connection() as conn:
            conditions = []
            params: list[Any] = []

            if project:
                conditions.append("project = ?")
                params.append(project)

            if r2_synced is not None:
                conditions.append("r2_synced = ?")
                params.append(1 if r2_synced else 0)

            query = "SELECT * FROM backups"
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    # Auth service operations
    def create_auth_service(
        self,
        project: str,
        auth_port: int,
        auth_db_name: str,
        auth_db_user: str,
        google_client_id: str | None = None,
        google_web_client_id: str | None = None,
        google_client_secret: str | None = None,
        apple_client_id: str | None = None,
        apple_team_id: str | None = None,
        apple_key_id: str | None = None,
        email_enabled: bool = True,
        magic_link_enabled: bool = True,
        anonymous_enabled: bool = True,
    ) -> dict[str, Any]:
        """Create an auth service record for a project."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO auth_services (
                    project, enabled, auth_port, auth_db_name, auth_db_user,
                    google_client_id, google_web_client_id, google_client_secret,
                    apple_client_id, apple_team_id, apple_key_id,
                    email_enabled, magic_link_enabled, anonymous_enabled,
                    created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project,
                    auth_port,
                    auth_db_name,
                    auth_db_user,
                    google_client_id,
                    google_web_client_id,
                    google_client_secret,
                    apple_client_id,
                    apple_team_id,
                    apple_key_id,
                    1 if email_enabled else 0,
                    1 if magic_link_enabled else 0,
                    1 if anonymous_enabled else 0,
                    now,
                    now,
                ),
            )
        return self.get_auth_service(project)  # type: ignore

    def get_auth_service(self, project: str) -> dict[str, Any] | None:
        """Get auth service record for a project."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM auth_services WHERE project = ?", (project,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_auth_services(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        """List all auth services, optionally filtered by enabled status."""
        with self.connection() as conn:
            if enabled_only:
                cursor = conn.execute(
                    "SELECT * FROM auth_services WHERE enabled = 1 ORDER BY created_at"
                )
            else:
                cursor = conn.execute("SELECT * FROM auth_services ORDER BY created_at")
            return [dict(row) for row in cursor.fetchall()]

    def update_auth_service(
        self,
        project: str,
        enabled: bool | None = None,
        google_client_id: str | None = None,
        google_web_client_id: str | None = None,
        google_client_secret: str | None = None,
        apple_client_id: str | None = None,
        apple_team_id: str | None = None,
        apple_key_id: str | None = None,
        email_enabled: bool | None = None,
        magic_link_enabled: bool | None = None,
        anonymous_enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update an auth service record."""
        updates = []
        params = []

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if google_client_id is not None:
            updates.append("google_client_id = ?")
            params.append(google_client_id)
        if google_web_client_id is not None:
            updates.append("google_web_client_id = ?")
            params.append(google_web_client_id)
        if google_client_secret is not None:
            updates.append("google_client_secret = ?")
            params.append(google_client_secret)
        if apple_client_id is not None:
            updates.append("apple_client_id = ?")
            params.append(apple_client_id)
        if apple_team_id is not None:
            updates.append("apple_team_id = ?")
            params.append(apple_team_id)
        if apple_key_id is not None:
            updates.append("apple_key_id = ?")
            params.append(apple_key_id)
        if email_enabled is not None:
            updates.append("email_enabled = ?")
            params.append(1 if email_enabled else 0)
        if magic_link_enabled is not None:
            updates.append("magic_link_enabled = ?")
            params.append(1 if magic_link_enabled else 0)
        if anonymous_enabled is not None:
            updates.append("anonymous_enabled = ?")
            params.append(1 if anonymous_enabled else 0)

        if not updates:
            return self.get_auth_service(project)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE auth_services SET {', '.join(updates)} WHERE project = ?",
                params,
            )
        return self.get_auth_service(project)

    def delete_auth_service(self, project: str) -> bool:
        """Delete an auth service record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM auth_services WHERE project = ?", (project,))
            return cursor.rowcount > 0

    # SSH config operations
    def get_ssh_enabled(self, project: str) -> bool:
        """Check if SSH is enabled for a project.

        Returns True if no record exists (enabled by default).
        """
        with self.connection() as conn:
            cursor = conn.execute("SELECT enabled FROM ssh_config WHERE project = ?", (project,))
            row = cursor.fetchone()
            return bool(row["enabled"]) if row else True

    def set_ssh_enabled(self, project: str, enabled: bool, changed_by: str | None = None) -> None:
        """Set SSH enabled status for a project."""
        with self.transaction() as conn:
            if enabled:
                # When enabling, just remove the disabled record
                conn.execute("DELETE FROM ssh_config WHERE project = ?", (project,))
            else:
                # When disabling, create/update a disabled record
                conn.execute(
                    """
                    INSERT INTO ssh_config (project, enabled, disabled_at, disabled_by)
                    VALUES (?, 0, ?, ?)
                    ON CONFLICT(project) DO UPDATE SET
                        enabled = 0,
                        disabled_at = excluded.disabled_at,
                        disabled_by = excluded.disabled_by
                    """,
                    (project, datetime.utcnow().isoformat(), changed_by),
                )

    def list_ssh_config(self) -> list[dict[str, Any]]:
        """List all SSH config records (disabled projects)."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT project, enabled, disabled_at, disabled_by FROM ssh_config"
            )
            return [dict(row) for row in cursor.fetchall()]

    # Operator operations
    def create_operator(self, username: str) -> dict[str, Any]:
        """Create a new operator user."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO operators (username, created_at)
                VALUES (?, ?)
                """,
                (username, datetime.utcnow().isoformat()),
            )
        return self.get_operator(username)  # type: ignore

    def get_operator(self, username: str) -> dict[str, Any] | None:
        """Get an operator by username."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM operators WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_operators(self) -> list[dict[str, Any]]:
        """List all operators."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM operators ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def update_operator(
        self,
        username: str,
        ssh_keys: str | None = None,
        last_login: str | None = None,
    ) -> dict[str, Any] | None:
        """Update an operator record."""
        updates = []
        params = []

        if ssh_keys is not None:
            updates.append("ssh_keys = ?")
            params.append(ssh_keys)
        if last_login is not None:
            updates.append("last_login = ?")
            params.append(last_login)

        if not updates:
            return self.get_operator(username)

        params.append(username)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE operators SET {', '.join(updates)} WHERE username = ?",
                params,
            )
        return self.get_operator(username)

    def delete_operator(self, username: str) -> bool:
        """Delete an operator. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM operators WHERE username = ?", (username,))
            return cursor.rowcount > 0

    # Release operations
    def create_release(
        self,
        release_id: str,
        project: str,
        release_name: str,
        release_path: str,
        is_current: bool = False,
        files_synced: int | None = None,
        deployed_by: str | None = None,
    ) -> dict[str, Any]:
        """Create a new release record."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO releases (
                    id, project, release_name, release_path,
                    deployed_at, is_current, files_synced,
                    deployed_by
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release_id,
                    project,
                    release_name,
                    release_path,
                    datetime.utcnow().isoformat(),
                    1 if is_current else 0,
                    files_synced,
                    deployed_by,
                ),
            )
        return self.get_release(project, release_name)  # type: ignore

    def get_release(self, project: str, release_name: str) -> dict[str, Any] | None:
        """Get a release by project and release name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM releases WHERE project = ? AND release_name = ?",
                (project, release_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_release_by_id(self, release_id: str) -> dict[str, Any] | None:
        """Get a release by its ID."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM releases WHERE id = ?", (release_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_current_release(self, project: str) -> dict[str, Any] | None:
        """Get the current active release for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM releases WHERE project = ? AND is_current = 1",
                (project,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_releases(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        """List releases for a project, most recent first."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM releases WHERE project = ? ORDER BY deployed_at DESC LIMIT ?",
                (project, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def set_current_release(self, project: str, release_name: str) -> bool:
        """Set a release as the current active release for a project.

        Clears is_current on all other releases for the project.
        Returns True if the release was found and set.
        """
        with self.transaction() as conn:
            # Clear current flag on all releases for this project
            conn.execute(
                "UPDATE releases SET is_current = 0 WHERE project = ?",
                (project,),
            )
            # Set new current release
            cursor = conn.execute(
                "UPDATE releases SET is_current = 1 WHERE project = ? AND release_name = ?",
                (project, release_name),
            )
            return cursor.rowcount > 0

    def update_release_files(self, release_id: str, files_synced: int) -> bool:
        """Update the files_synced count for a release."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE releases SET files_synced = ? WHERE id = ?",
                (files_synced, release_id),
            )
            return cursor.rowcount > 0

    def update_release_snapshot(
        self,
        release_id: str,
        checkpoint_id: int | None = None,
        env_snapshot: str | None = None,
    ) -> bool:
        """Update the checkpoint and/or env snapshot for a release.

        Args:
            release_id: The release ID
            checkpoint_id: The checkpoint ID to link (for database rollback)
            env_snapshot: JSON string of environment variables at deploy time

        Returns:
            True if updated successfully
        """
        updates = []
        params = []

        if checkpoint_id is not None:
            updates.append("checkpoint_id = ?")
            params.append(checkpoint_id)
        if env_snapshot is not None:
            updates.append("env_snapshot = ?")
            params.append(env_snapshot)

        if not updates:
            return False

        params.append(release_id)

        with self.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE releases SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    def update_release_git_info(
        self,
        release_id: str,
        git_commit: str | None = None,
        git_branch: str | None = None,
        git_tag: str | None = None,
        git_repo: str | None = None,
    ) -> bool:
        """Update the git information for a release.

        Args:
            release_id: The release ID
            git_commit: Git commit hash
            git_branch: Git branch name
            git_tag: Git tag (if deployed from tag)
            git_repo: Git repository URL

        Returns:
            True if updated successfully
        """
        updates = []
        params = []

        if git_commit is not None:
            updates.append("git_commit = ?")
            params.append(git_commit)
        if git_branch is not None:
            updates.append("git_branch = ?")
            params.append(git_branch)
        if git_tag is not None:
            updates.append("git_tag = ?")
            params.append(git_tag)
        if git_repo is not None:
            updates.append("git_repo = ?")
            params.append(git_repo)

        if not updates:
            return False

        params.append(release_id)

        with self.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE releases SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            return cursor.rowcount > 0

    def delete_release(self, release_id: str) -> bool:
        """Delete a release record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM releases WHERE id = ?", (release_id,))
            return cursor.rowcount > 0

    def delete_releases_for_project(self, project: str) -> int:
        """Delete all releases for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM releases WHERE project = ?", (project,))
            return cursor.rowcount

    # Config operations
    def get_config(self, key: str) -> str | None:
        """Get a config value by key."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT value FROM config WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else None

    def set_config(self, key: str, value: str) -> bool:
        """Set a config value. Returns True on success."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, datetime.utcnow().isoformat()),
            )
            return True

    def delete_config(self, key: str) -> bool:
        """Delete a config value. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM config WHERE key = ?", (key,))
            return cursor.rowcount > 0

    def list_config(self) -> list[dict[str, Any]]:
        """List all config values."""
        with self.connection() as conn:
            cursor = conn.execute("SELECT * FROM config ORDER BY key")
            return [dict(row) for row in cursor.fetchall()]

    # SSL rate limiting operations
    def record_ssl_attempt(
        self,
        project: str,
        domain: str,
        success: bool,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Record an SSL provisioning attempt for rate limiting."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ssl_rate_limits (project, domain, attempted_at, success, error_message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project,
                    domain,
                    datetime.utcnow().isoformat(),
                    1 if success else 0,
                    error_message,
                ),
            )
        return {
            "project": project,
            "domain": domain,
            "success": success,
            "attempted_at": datetime.utcnow().isoformat(),
        }

    def get_ssl_attempts_count(self, project: str, hours: int = 24) -> int:
        """Get the number of SSL attempts for a project in the last N hours."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM ssl_rate_limits
                WHERE project = ? AND attempted_at > datetime('now', ?)
                """,
                (project, f"-{hours} hours"),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_last_ssl_failure(self, project: str) -> dict[str, Any] | None:
        """Get the most recent failed SSL attempt for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ssl_rate_limits
                WHERE project = ? AND success = 0
                ORDER BY attempted_at DESC LIMIT 1
                """,
                (project,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_ssl_attempts(self, project: str, limit: int = 10) -> list[dict[str, Any]]:
        """List SSL provisioning attempts for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ssl_rate_limits
                WHERE project = ?
                ORDER BY attempted_at DESC LIMIT ?
                """,
                (project, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    # SSH key audit operations
    def record_ssh_key_action(
        self,
        project: str,
        action: str,
        fingerprint: str,
        key_comment: str | None = None,
        added_by: str | None = None,
    ) -> dict[str, Any]:
        """Record an SSH key action for audit logging."""
        if added_by is None:
            import os

            added_by = os.environ.get("SUDO_USER") or os.environ.get("USER", "unknown")

        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ssh_key_audit (
                    project, action, fingerprint,
                    key_comment, added_by, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project,
                    action,
                    fingerprint,
                    key_comment,
                    added_by,
                    datetime.utcnow().isoformat(),
                ),
            )
        return {
            "project": project,
            "action": action,
            "fingerprint": fingerprint,
            "added_by": added_by,
            "created_at": datetime.utcnow().isoformat(),
        }

    def list_ssh_key_audit(self, project: str, limit: int = 50) -> list[dict[str, Any]]:
        """List SSH key audit entries for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM ssh_key_audit
                WHERE project = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (project, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_ssh_key_count(self, project: str) -> int:
        """Get the number of SSH keys for a project based on audit log.

        Note: This counts unique fingerprints where add > remove actions.
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(DISTINCT fingerprint) FROM (
                    SELECT fingerprint,
                           SUM(CASE WHEN action = 'add' THEN 1 ELSE 0 END) -
                           SUM(CASE WHEN action = 'remove' THEN 1 ELSE 0 END) as net_adds
                    FROM ssh_key_audit
                    WHERE project = ?
                    GROUP BY fingerprint
                    HAVING net_adds > 0
                )
                """,
                (project,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    # Scheduled tasks (cron jobs) operations
    def create_scheduled_task(
        self,
        task_id: str,
        project: str,
        name: str,
        schedule: str,
        command: str,
        schedule_cron: str | None = None,
        description: str | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """Create a new scheduled task."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO scheduled_tasks (
                    id, project, name, schedule, schedule_cron, command,
                    description, enabled, created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    task_id,
                    project,
                    name,
                    schedule,
                    schedule_cron,
                    command,
                    description,
                    now,
                    now,
                    created_by,
                ),
            )
        return self.get_scheduled_task(project, name)  # type: ignore

    def get_scheduled_task(self, project: str, name: str) -> dict[str, Any] | None:
        """Get a scheduled task by project and name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE project = ? AND name = ?",
                (project, name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_scheduled_task_by_id(self, task_id: str) -> dict[str, Any] | None:
        """Get a scheduled task by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_scheduled_tasks(self, project: str | None = None) -> list[dict[str, Any]]:
        """List scheduled tasks, optionally filtered by project."""
        with self.connection() as conn:
            if project:
                cursor = conn.execute(
                    "SELECT * FROM scheduled_tasks WHERE project = ? ORDER BY name",
                    (project,),
                )
            else:
                cursor = conn.execute("SELECT * FROM scheduled_tasks ORDER BY project, name")
            return [dict(row) for row in cursor.fetchall()]

    def update_scheduled_task(
        self,
        project: str,
        name: str,
        schedule: str | None = None,
        schedule_cron: str | None = None,
        command: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update a scheduled task."""
        updates = []
        params = []

        if schedule is not None:
            updates.append("schedule = ?")
            params.append(schedule)
        if schedule_cron is not None:
            updates.append("schedule_cron = ?")
            params.append(schedule_cron)
        if command is not None:
            updates.append("command = ?")
            params.append(command)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not updates:
            return self.get_scheduled_task(project, name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.extend([project, name])

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE scheduled_tasks SET {', '.join(updates)} WHERE project = ? AND name = ?",
                params,
            )
        return self.get_scheduled_task(project, name)

    def update_task_last_run(
        self,
        project: str,
        name: str,
        status: str,
        exit_code: int | None = None,
    ) -> bool:
        """Update the last run information for a task."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE scheduled_tasks SET
                    last_run_at = ?,
                    last_run_status = ?,
                    last_run_exit_code = ?
                WHERE project = ? AND name = ?
                """,
                (datetime.utcnow().isoformat(), status, exit_code, project, name),
            )
            return cursor.rowcount > 0

    def delete_scheduled_task(self, project: str, name: str) -> bool:
        """Delete a scheduled task. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM scheduled_tasks WHERE project = ? AND name = ?",
                (project, name),
            )
            return cursor.rowcount > 0

    # Worker (Celery) operations
    def create_worker(
        self,
        worker_id: str,
        project: str,
        worker_name: str = "default",
        concurrency: int = 2,
        queues: str | None = None,
        app_module: str = "app",
        loglevel: str = "info",
        created_by: str | None = None,
    ) -> dict[str, Any]:
        """Create a new Celery worker record."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO workers (
                    id, project, worker_name, concurrency, queues,
                    app_module, loglevel, enabled, created_at, updated_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    worker_id,
                    project,
                    worker_name,
                    concurrency,
                    queues,
                    app_module,
                    loglevel,
                    now,
                    now,
                    created_by,
                ),
            )
        return self.get_worker(project, worker_name)  # type: ignore

    def get_worker(self, project: str, worker_name: str = "default") -> dict[str, Any] | None:
        """Get a worker by project and name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM workers WHERE project = ? AND worker_name = ?",
                (project, worker_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_worker_by_id(self, worker_id: str) -> dict[str, Any] | None:
        """Get a worker by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM workers WHERE id = ?",
                (worker_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_workers(self, project: str | None = None) -> list[dict[str, Any]]:
        """List workers, optionally filtered by project."""
        with self.connection() as conn:
            if project:
                cursor = conn.execute(
                    "SELECT * FROM workers WHERE project = ? ORDER BY worker_name",
                    (project,),
                )
            else:
                cursor = conn.execute("SELECT * FROM workers ORDER BY project, worker_name")
            return [dict(row) for row in cursor.fetchall()]

    def update_worker(
        self,
        project: str,
        worker_name: str = "default",
        concurrency: int | None = None,
        queues: str | None = None,
        app_module: str | None = None,
        loglevel: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any] | None:
        """Update a worker."""
        updates = []
        params = []

        if concurrency is not None:
            updates.append("concurrency = ?")
            params.append(concurrency)
        if queues is not None:
            updates.append("queues = ?")
            params.append(queues)
        if app_module is not None:
            updates.append("app_module = ?")
            params.append(app_module)
        if loglevel is not None:
            updates.append("loglevel = ?")
            params.append(loglevel)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not updates:
            return self.get_worker(project, worker_name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.extend([project, worker_name])

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE workers SET {', '.join(updates)} WHERE project = ? AND worker_name = ?",
                params,
            )
        return self.get_worker(project, worker_name)

    def delete_worker(self, project: str, worker_name: str = "default") -> bool:
        """Delete a worker. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM workers WHERE project = ? AND worker_name = ?",
                (project, worker_name),
            )
            return cursor.rowcount > 0

    def delete_workers_for_project(self, project: str) -> int:
        """Delete all workers for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute("DELETE FROM workers WHERE project = ?", (project,))
            return cursor.rowcount

    # Celery Beat operations
    def create_celery_beat(
        self,
        project: str,
        schedule_file: str = "celerybeat-schedule",
    ) -> dict[str, Any]:
        """Create a Celery beat record for a project."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO celery_beat (project, enabled, schedule_file, created_at, updated_at)
                VALUES (?, 1, ?, ?, ?)
                """,
                (project, schedule_file, now, now),
            )
        return self.get_celery_beat(project)  # type: ignore

    def get_celery_beat(self, project: str) -> dict[str, Any] | None:
        """Get Celery beat record for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM celery_beat WHERE project = ?",
                (project,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_celery_beat(
        self,
        project: str,
        enabled: bool | None = None,
        schedule_file: str | None = None,
    ) -> dict[str, Any] | None:
        """Update Celery beat settings."""
        updates = []
        params = []

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if schedule_file is not None:
            updates.append("schedule_file = ?")
            params.append(schedule_file)

        if not updates:
            return self.get_celery_beat(project)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE celery_beat SET {', '.join(updates)} WHERE project = ?",
                params,
            )
        return self.get_celery_beat(project)

    def delete_celery_beat(self, project: str) -> bool:
        """Delete Celery beat record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM celery_beat WHERE project = ?",
                (project,),
            )
            return cursor.rowcount > 0

    # Checkpoint operations
    def create_checkpoint(
        self,
        project_name: str,
        checkpoint_type: str,
        database_name: str,
        backup_path: str,
        size_bytes: int,
        created_by: str,
        label: str | None = None,
        trigger_source: str | None = None,
        expires_at: str | None = None,
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Create a new checkpoint record."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO checkpoints (
                    project_name, label, checkpoint_type, trigger_source,
                    database_name, backup_path, size_bytes,
                    created_at, created_by, expires_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_name,
                    label,
                    checkpoint_type,
                    trigger_source,
                    database_name,
                    backup_path,
                    size_bytes,
                    datetime.utcnow().isoformat(),
                    created_by,
                    expires_at,
                    metadata,
                ),
            )
            checkpoint_id = cursor.lastrowid
        return self.get_checkpoint(checkpoint_id)  # type: ignore

    def get_checkpoint(self, checkpoint_id: int) -> dict[str, Any] | None:
        """Get a checkpoint by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM checkpoints WHERE id = ?",
                (checkpoint_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_checkpoints(
        self,
        project_name: str | None = None,
        checkpoint_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List checkpoints, optionally filtered by project and/or type."""
        with self.connection() as conn:
            query = "SELECT * FROM checkpoints WHERE 1=1"
            params: list[Any] = []

            if project_name:
                query += " AND project_name = ?"
                params.append(project_name)
            if checkpoint_type:
                query += " AND checkpoint_type = ?"
                params.append(checkpoint_type)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_latest_checkpoint(
        self,
        project_name: str,
        checkpoint_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Get the most recent checkpoint for a project."""
        with self.connection() as conn:
            if checkpoint_type:
                cursor = conn.execute(
                    """
                    SELECT * FROM checkpoints
                    WHERE project_name = ? AND checkpoint_type = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_name, checkpoint_type),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM checkpoints
                    WHERE project_name = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_name,),
                )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_checkpoint(self, checkpoint_id: int) -> bool:
        """Delete a checkpoint record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM checkpoints WHERE id = ?",
                (checkpoint_id,),
            )
            return cursor.rowcount > 0

    def delete_checkpoints_for_project(self, project_name: str) -> int:
        """Delete all checkpoints for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM checkpoints WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount

    def get_expired_checkpoints(self) -> list[dict[str, Any]]:
        """Get all checkpoints that have expired."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM checkpoints
                WHERE expires_at IS NOT NULL AND expires_at < ?
                """,
                (datetime.utcnow().isoformat(),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_checkpoints(self, project_name: str) -> int:
        """Count checkpoints for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM checkpoints WHERE project_name = ?",
                (project_name,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    # Alert channel operations
    def create_alert_channel(
        self,
        project_name: str,
        name: str,
        channel_type: str,
        config: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a new alert channel."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO alert_channels (
                    project_name, name, channel_type, config, enabled,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (project_name, name, channel_type, config, 1 if enabled else 0, now, now),
            )
        return self.get_alert_channel(project_name, name)  # type: ignore

    def get_alert_channel(self, project_name: str, name: str) -> dict[str, Any] | None:
        """Get an alert channel by project and name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM alert_channels WHERE project_name = ? AND name = ?",
                (project_name, name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_alert_channel_by_id(self, channel_id: int) -> dict[str, Any] | None:
        """Get an alert channel by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM alert_channels WHERE id = ?",
                (channel_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_alert_channels(
        self,
        project_name: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List alert channels, optionally filtered by project."""
        with self.connection() as conn:
            query = "SELECT * FROM alert_channels WHERE 1=1"
            params: list[Any] = []

            if project_name:
                query += " AND project_name = ?"
                params.append(project_name)
            if enabled_only:
                query += " AND enabled = 1"

            query += " ORDER BY project_name, name"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_alert_channel(
        self,
        project_name: str,
        name: str,
        config: str | None = None,
        enabled: bool | None = None,
        muted_until: str | None = None,
        clear_mute: bool = False,
    ) -> dict[str, Any] | None:
        """Update an alert channel.

        Args:
            project_name: Project name
            name: Channel name
            config: New config JSON string
            enabled: Enable/disable channel
            muted_until: ISO timestamp to mute until
            clear_mute: If True, clear the muted_until field
        """
        updates = []
        params = []

        if config is not None:
            updates.append("config = ?")
            params.append(config)
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if muted_until is not None:
            updates.append("muted_until = ?")
            params.append(muted_until)
        if clear_mute:
            updates.append("muted_until = NULL")

        if not updates:
            return self.get_alert_channel(project_name, name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.extend([project_name, name])

        with self.transaction() as conn:
            conn.execute(
                "UPDATE alert_channels SET "
                f"{', '.join(updates)} "
                "WHERE project_name = ? AND name = ?",
                params,
            )
        return self.get_alert_channel(project_name, name)

    def delete_alert_channel(self, project_name: str, name: str) -> bool:
        """Delete an alert channel. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM alert_channels WHERE project_name = ? AND name = ?",
                (project_name, name),
            )
            return cursor.rowcount > 0

    def count_alert_channels(self, project_name: str) -> int:
        """Count alert channels for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM alert_channels WHERE project_name = ?",
                (project_name,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    # Alert history operations
    def create_alert_history(
        self,
        project_name: str,
        event_type: str,
        event_status: str,
        channel_name: str | None = None,
        notification_sent: bool = False,
        notification_error: str | None = None,
        payload: str | None = None,
    ) -> dict[str, Any]:
        """Create a new alert history entry."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO alert_history (
                    project_name, event_type, event_status, channel_name,
                    notification_sent, notification_error, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_name,
                    event_type,
                    event_status,
                    channel_name,
                    1 if notification_sent else 0,
                    notification_error,
                    payload,
                    datetime.utcnow().isoformat(),
                ),
            )
            history_id = cursor.lastrowid
        return self.get_alert_history_by_id(history_id)  # type: ignore

    def get_alert_history_by_id(self, history_id: int) -> dict[str, Any] | None:
        """Get an alert history entry by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM alert_history WHERE id = ?",
                (history_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_alert_history(
        self,
        project_name: str,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List alert history for a project."""
        with self.connection() as conn:
            query = "SELECT * FROM alert_history WHERE project_name = ?"
            params: list[Any] = [project_name]

            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)

            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def delete_alert_history_for_project(self, project_name: str) -> int:
        """Delete all alert history for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM alert_history WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount

    # Deploy history operations (for rate limiting)
    def record_deploy(
        self,
        project_name: str,
        deployed_by: str,
        success: bool = True,
        duration_ms: int | None = None,
        source_type: str | None = None,
        files_synced: int | None = None,
        override_used: bool = False,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Record a deploy attempt for rate limiting."""
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO deploy_history (
                    project_name, deployed_at, deployed_by, success,
                    duration_ms, source_type, files_synced,
                    override_used, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_name,
                    datetime.utcnow().isoformat(),
                    deployed_by,
                    1 if success else 0,
                    duration_ms,
                    source_type,
                    files_synced,
                    1 if override_used else 0,
                    error_message,
                ),
            )
            deploy_id = cursor.lastrowid
        return self.get_deploy_by_id(deploy_id)  # type: ignore

    def get_deploy_by_id(self, deploy_id: int) -> dict[str, Any] | None:
        """Get a deploy history entry by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM deploy_history WHERE id = ?",
                (deploy_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_deploys(
        self,
        project_name: str,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List deploy history for a project.

        Args:
            project_name: Project name
            since: ISO timestamp to filter from
            limit: Maximum number of results
        """
        with self.connection() as conn:
            query = "SELECT * FROM deploy_history WHERE project_name = ?"
            params: list[Any] = [project_name]

            if since:
                query += " AND deployed_at >= ?"
                params.append(since)

            query += " ORDER BY deployed_at DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_deploys_since(
        self,
        project_name: str,
        since: str,
        exclude_overrides: bool = True,
    ) -> int:
        """Count deploys since a timestamp.

        Args:
            project_name: Project name
            since: ISO timestamp to count from
            exclude_overrides: If True, exclude deploys that used override
        """
        with self.connection() as conn:
            query = """
                SELECT COUNT(*) FROM deploy_history
                WHERE project_name = ? AND deployed_at >= ?
            """
            params: list[Any] = [project_name, since]

            if exclude_overrides:
                query += " AND override_used = 0"

            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_consecutive_failures(self, project_name: str) -> int:
        """Get the count of consecutive recent failures.

        Returns the number of failed deploys in a row from most recent.
        """
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT success FROM deploy_history
                WHERE project_name = ?
                ORDER BY deployed_at DESC
                LIMIT 10
                """,
                (project_name,),
            )
            count = 0
            for row in cursor.fetchall():
                if row["success"] == 0:
                    count += 1
                else:
                    break
            return count

    def get_last_successful_deploy(self, project_name: str) -> dict[str, Any] | None:
        """Get the most recent successful deploy."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM deploy_history
                WHERE project_name = ? AND success = 1
                ORDER BY deployed_at DESC LIMIT 1
                """,
                (project_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_last_failed_deploy(self, project_name: str) -> dict[str, Any] | None:
        """Get the most recent failed deploy."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM deploy_history
                WHERE project_name = ? AND success = 0
                ORDER BY deployed_at DESC LIMIT 1
                """,
                (project_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_deploy_history_for_project(self, project_name: str) -> int:
        """Delete all deploy history for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM deploy_history WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount

    # Rate limit configuration operations
    def get_rate_limit_config(self, project_name: str) -> dict[str, Any] | None:
        """Get rate limit configuration for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM rate_limits WHERE project_name = ?",
                (project_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_rate_limit_config(
        self,
        project_name: str,
        max_deploys: int = 10,
        window_minutes: int = 60,
        failure_cooldown_minutes: int = 5,
        consecutive_failure_limit: int = 3,
    ) -> dict[str, Any]:
        """Create rate limit configuration for a project."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (
                    project_name, max_deploys, window_minutes,
                    failure_cooldown_minutes, consecutive_failure_limit, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    project_name,
                    max_deploys,
                    window_minutes,
                    failure_cooldown_minutes,
                    consecutive_failure_limit,
                    datetime.utcnow().isoformat(),
                ),
            )
        return self.get_rate_limit_config(project_name)  # type: ignore

    def update_rate_limit_config(
        self,
        project_name: str,
        max_deploys: int | None = None,
        window_minutes: int | None = None,
        failure_cooldown_minutes: int | None = None,
        consecutive_failure_limit: int | None = None,
    ) -> dict[str, Any] | None:
        """Update rate limit configuration for a project."""
        updates = []
        params = []

        if max_deploys is not None:
            updates.append("max_deploys = ?")
            params.append(max_deploys)
        if window_minutes is not None:
            updates.append("window_minutes = ?")
            params.append(window_minutes)
        if failure_cooldown_minutes is not None:
            updates.append("failure_cooldown_minutes = ?")
            params.append(failure_cooldown_minutes)
        if consecutive_failure_limit is not None:
            updates.append("consecutive_failure_limit = ?")
            params.append(consecutive_failure_limit)

        if not updates:
            return self.get_rate_limit_config(project_name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project_name)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE rate_limits SET {', '.join(updates)} WHERE project_name = ?",
                params,
            )
        return self.get_rate_limit_config(project_name)

    def delete_rate_limit_config(self, project_name: str) -> bool:
        """Delete rate limit configuration. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM rate_limits WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount > 0

    # Auto-pause operations
    def get_auto_pause_config(self, project_name: str) -> dict[str, Any] | None:
        """Get auto-pause configuration for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM auto_pause WHERE project_name = ?",
                (project_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_auto_pause_config(
        self,
        project_name: str,
        enabled: bool = False,
        failure_threshold: int = 5,
        window_minutes: int = 10,
    ) -> dict[str, Any]:
        """Create auto-pause configuration for a project."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO auto_pause (
                    project_name, enabled, failure_threshold, window_minutes,
                    paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    project_name,
                    1 if enabled else 0,
                    failure_threshold,
                    window_minutes,
                    now,
                    now,
                ),
            )
        return self.get_auto_pause_config(project_name)  # type: ignore

    def update_auto_pause_config(
        self,
        project_name: str,
        enabled: bool | None = None,
        failure_threshold: int | None = None,
        window_minutes: int | None = None,
        paused: bool | None = None,
        paused_at: str | None = None,
        paused_reason: str | None = None,
        clear_pause: bool = False,
    ) -> dict[str, Any] | None:
        """Update auto-pause configuration for a project.

        Args:
            project_name: Project name
            enabled: Enable/disable auto-pause
            failure_threshold: Number of failures to trigger pause
            window_minutes: Time window to count failures
            paused: Set paused state
            paused_at: When paused (set automatically if paused=True)
            paused_reason: Why paused
            clear_pause: If True, clear paused state and reason
        """
        updates = []
        params = []

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)
        if failure_threshold is not None:
            updates.append("failure_threshold = ?")
            params.append(failure_threshold)
        if window_minutes is not None:
            updates.append("window_minutes = ?")
            params.append(window_minutes)
        if paused is not None:
            updates.append("paused = ?")
            params.append(1 if paused else 0)
            if paused and paused_at is None:
                paused_at = datetime.utcnow().isoformat()
        if paused_at is not None:
            updates.append("paused_at = ?")
            params.append(paused_at)
        if paused_reason is not None:
            updates.append("paused_reason = ?")
            params.append(paused_reason)
        if clear_pause:
            updates.append("paused = 0")
            updates.append("paused_at = NULL")
            updates.append("paused_reason = NULL")

        if not updates:
            return self.get_auto_pause_config(project_name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project_name)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE auto_pause SET {', '.join(updates)} WHERE project_name = ?",
                params,
            )
        return self.get_auto_pause_config(project_name)

    def is_project_paused(self, project_name: str) -> bool:
        """Check if a project is currently paused."""
        config = self.get_auto_pause_config(project_name)
        if not config:
            return False
        return bool(config.get("paused", 0))

    def delete_auto_pause_config(self, project_name: str) -> bool:
        """Delete auto-pause configuration. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM auto_pause WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount > 0

    # Resource limits operations
    def get_resource_limits(self, project_name: str) -> dict[str, Any] | None:
        """Get resource limits configuration for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM resource_limits WHERE project_name = ?",
                (project_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_resource_limits(
        self,
        project_name: str,
        cpu_quota: int | None = None,
        memory_max_mb: int | None = None,
        memory_high_mb: int | None = None,
        tasks_max: int | None = None,
        disk_quota_mb: int | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create resource limits configuration for a project."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO resource_limits (
                    project_name, cpu_quota, memory_max_mb, memory_high_mb,
                    tasks_max, disk_quota_mb, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_name,
                    cpu_quota,
                    memory_max_mb,
                    memory_high_mb,
                    tasks_max,
                    disk_quota_mb,
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
        return self.get_resource_limits(project_name)  # type: ignore

    def update_resource_limits(
        self,
        project_name: str,
        cpu_quota: int | None = None,
        memory_max_mb: int | None = None,
        memory_high_mb: int | None = None,
        tasks_max: int | None = None,
        disk_quota_mb: int | None = None,
        enabled: bool | None = None,
        clear_limits: bool = False,
    ) -> dict[str, Any] | None:
        """Update resource limits configuration for a project.

        Args:
            project_name: Project name
            cpu_quota: CPU quota as percentage (100 = 1 core)
            memory_max_mb: Hard memory limit in MB
            memory_high_mb: Soft memory limit in MB (throttle above)
            tasks_max: Maximum processes/threads
            disk_quota_mb: Disk quota in MB
            enabled: Enable/disable limits
            clear_limits: If True, set all limits to NULL (unlimited)
        """
        updates = []
        params: list[Any] = []

        if clear_limits:
            updates.extend(
                [
                    "cpu_quota = NULL",
                    "memory_max_mb = NULL",
                    "memory_high_mb = NULL",
                    "tasks_max = NULL",
                    "disk_quota_mb = NULL",
                ]
            )
        else:
            if cpu_quota is not None:
                updates.append("cpu_quota = ?")
                params.append(cpu_quota)
            if memory_max_mb is not None:
                updates.append("memory_max_mb = ?")
                params.append(memory_max_mb)
            if memory_high_mb is not None:
                updates.append("memory_high_mb = ?")
                params.append(memory_high_mb)
            if tasks_max is not None:
                updates.append("tasks_max = ?")
                params.append(tasks_max)
            if disk_quota_mb is not None:
                updates.append("disk_quota_mb = ?")
                params.append(disk_quota_mb)

        if enabled is not None:
            updates.append("enabled = ?")
            params.append(1 if enabled else 0)

        if not updates:
            return self.get_resource_limits(project_name)

        updates.append("updated_at = ?")
        params.append(datetime.utcnow().isoformat())
        params.append(project_name)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE resource_limits SET {', '.join(updates)} WHERE project_name = ?",
                params,
            )
        return self.get_resource_limits(project_name)

    def delete_resource_limits(self, project_name: str) -> bool:
        """Delete resource limits configuration. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM resource_limits WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount > 0

    # Sandbox operations
    def create_sandbox(
        self,
        sandbox_id: str,
        sandbox_name: str,
        source_project: str,
        port: int,
        expires_at: str,
        created_by: str,
        source_release: str | None = None,
        domain: str | None = None,
        db_name: str | None = None,
        status: str = "creating",
        metadata: str | None = None,
    ) -> dict[str, Any]:
        """Create a new sandbox record."""
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandboxes (
                    id, sandbox_name, source_project, source_release, port,
                    domain, db_name, status, expires_at, created_at, created_by, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sandbox_id,
                    sandbox_name,
                    source_project,
                    source_release,
                    port,
                    domain,
                    db_name,
                    status,
                    expires_at,
                    datetime.utcnow().isoformat(),
                    created_by,
                    metadata,
                ),
            )
        return self.get_sandbox(sandbox_name)  # type: ignore

    def get_sandbox(self, sandbox_name: str) -> dict[str, Any] | None:
        """Get a sandbox by name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sandboxes WHERE sandbox_name = ?",
                (sandbox_name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_sandbox_by_id(self, sandbox_id: str) -> dict[str, Any] | None:
        """Get a sandbox by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM sandboxes WHERE id = ?",
                (sandbox_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_sandboxes(
        self,
        source_project: str | None = None,
        status: str | None = None,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        """List sandboxes, optionally filtered by source project and/or status."""
        with self.connection() as conn:
            query = "SELECT * FROM sandboxes WHERE 1=1"
            params: list[Any] = []

            if source_project:
                query += " AND source_project = ?"
                params.append(source_project)
            if status:
                query += " AND status = ?"
                params.append(status)
            if not include_expired:
                query += " AND (expires_at > ? OR status = 'promoting')"
                params.append(datetime.utcnow().isoformat())

            query += " ORDER BY created_at DESC"
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def update_sandbox(
        self,
        sandbox_name: str,
        status: str | None = None,
        domain: str | None = None,
        db_name: str | None = None,
        expires_at: str | None = None,
        metadata: str | None = None,
    ) -> dict[str, Any] | None:
        """Update a sandbox record."""
        updates = []
        params = []

        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if domain is not None:
            updates.append("domain = ?")
            params.append(domain)
        if db_name is not None:
            updates.append("db_name = ?")
            params.append(db_name)
        if expires_at is not None:
            updates.append("expires_at = ?")
            params.append(expires_at)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(metadata)

        if not updates:
            return self.get_sandbox(sandbox_name)

        params.append(sandbox_name)

        with self.transaction() as conn:
            conn.execute(
                f"UPDATE sandboxes SET {', '.join(updates)} WHERE sandbox_name = ?",
                params,
            )
        return self.get_sandbox(sandbox_name)

    def delete_sandbox(self, sandbox_name: str) -> bool:
        """Delete a sandbox record. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM sandboxes WHERE sandbox_name = ?",
                (sandbox_name,),
            )
            return cursor.rowcount > 0

    def count_sandboxes_for_project(self, source_project: str) -> int:
        """Count active (non-expired) sandboxes for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM sandboxes
                WHERE source_project = ?
                AND status NOT IN ('expired', 'failed')
                AND expires_at > ?
                """,
                (source_project, datetime.utcnow().isoformat()),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def get_expired_sandboxes(self) -> list[dict[str, Any]]:
        """Get all sandboxes that have expired."""
        with self.connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM sandboxes
                WHERE expires_at < ? AND status NOT IN ('expired', 'promoting')
                """,
                (datetime.utcnow().isoformat(),),
            )
            return [dict(row) for row in cursor.fetchall()]

    def delete_sandboxes_for_project(self, source_project: str) -> int:
        """Delete all sandboxes for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM sandboxes WHERE source_project = ?",
                (source_project,),
            )
            return cursor.rowcount

    # Environment operations
    def create_environment(
        self,
        project_name: str,
        env_name: str,
        linux_user: str,
        port: int,
        db_name: str | None = None,
        share_parent_db: bool = False,
        created_by: str = "root",
    ) -> dict[str, Any]:
        """Create a new environment for a project."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO environments (
                    project_name, env_name, linux_user, port, db_name,
                    share_parent_db, status, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 'stopped', ?, ?)
                """,
                (
                    project_name,
                    env_name,
                    linux_user,
                    port,
                    db_name,
                    1 if share_parent_db else 0,
                    now,
                    created_by,
                ),
            )
        return self.get_environment(project_name, env_name)  # type: ignore

    def get_environment(self, project_name: str, env_name: str) -> dict[str, Any] | None:
        """Get an environment by project and name."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM environments WHERE project_name = ? AND env_name = ?",
                (project_name, env_name),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_environment_by_user(self, linux_user: str) -> dict[str, Any] | None:
        """Get an environment by its Linux user."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM environments WHERE linux_user = ?",
                (linux_user,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_environments(self, project_name: str | None = None) -> list[dict[str, Any]]:
        """List environments, optionally filtered by project."""
        with self.connection() as conn:
            if project_name:
                cursor = conn.execute(
                    "SELECT * FROM environments WHERE project_name = ? ORDER BY env_name",
                    (project_name,),
                )
            else:
                cursor = conn.execute("SELECT * FROM environments ORDER BY project_name, env_name")
            return [dict(row) for row in cursor.fetchall()]

    def update_environment_status(self, project_name: str, env_name: str, status: str) -> None:
        """Update an environment's status."""
        with self.transaction() as conn:
            conn.execute(
                "UPDATE environments SET status = ? WHERE project_name = ? AND env_name = ?",
                (status, project_name, env_name),
            )

    def delete_environment(self, project_name: str, env_name: str) -> bool:
        """Delete an environment. Returns True if deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM environments WHERE project_name = ? AND env_name = ?",
                (project_name, env_name),
            )
            return cursor.rowcount > 0

    def count_environments_for_project(self, project_name: str) -> int:
        """Count environments for a project."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM environments WHERE project_name = ?",
                (project_name,),
            )
            result = cursor.fetchone()
            return result[0] if result else 0

    def delete_environments_for_project(self, project_name: str) -> int:
        """Delete all environments for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM environments WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount

    # Event operations
    def create_event(
        self,
        project_name: str,
        category: str,
        event_type: str,
        message: str,
        level: str = "INFO",
        data: str | None = None,
        created_by: str | None = None,
    ) -> int:
        """Create a new event and return its ID."""
        now = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events (
                    project_name, category, event_type, level, message, data, created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (project_name, category, event_type, level, message, data, now, created_by),
            )
            return cursor.lastrowid or 0

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        """Get an event by ID."""
        with self.connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM events WHERE id = ?",
                (event_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_events(
        self,
        project_name: str,
        category: str | None = None,
        level: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List events with optional filters."""
        query = "SELECT * FROM events WHERE project_name = ?"
        params: list[Any] = [project_name]

        if category:
            # Support comma-separated categories
            categories = [c.strip() for c in category.split(",")]
            placeholders = ",".join("?" * len(categories))
            query += f" AND category IN ({placeholders})"
            params.extend(categories)

        if level:
            # Filter by level and above
            level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
            min_level = level_order.get(level.upper(), 1)
            levels = [line for line, v in level_order.items() if v >= min_level]
            placeholders = ",".join("?" * len(levels))
            query += f" AND level IN ({placeholders})"
            params.extend(levels)

        if since:
            query += " AND created_at >= ?"
            params.append(since)

        if until:
            query += " AND created_at <= ?"
            params.append(until)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self.connection() as conn:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def count_events(
        self,
        project_name: str,
        category: str | None = None,
        level: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> int:
        """Count events with optional filters."""
        query = "SELECT COUNT(*) FROM events WHERE project_name = ?"
        params: list[Any] = [project_name]

        if category:
            categories = [c.strip() for c in category.split(",")]
            placeholders = ",".join("?" * len(categories))
            query += f" AND category IN ({placeholders})"
            params.extend(categories)

        if level:
            level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
            min_level = level_order.get(level.upper(), 1)
            levels = [line for line, v in level_order.items() if v >= min_level]
            placeholders = ",".join("?" * len(levels))
            query += f" AND level IN ({placeholders})"
            params.extend(levels)

        if since:
            query += " AND created_at >= ?"
            params.append(since)

        if until:
            query += " AND created_at <= ?"
            params.append(until)

        with self.connection() as conn:
            cursor = conn.execute(query, params)
            result = cursor.fetchone()
            return result[0] if result else 0

    def delete_old_events(self, older_than_days: int = 30) -> int:
        """Delete events older than the specified number of days."""
        from datetime import timedelta

        cutoff = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE created_at < ?",
                (cutoff,),
            )
            return cursor.rowcount

    def delete_events_for_project(self, project_name: str) -> int:
        """Delete all events for a project. Returns count deleted."""
        with self.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE project_name = ?",
                (project_name,),
            )
            return cursor.rowcount


# Global database instance (loaded lazily)
_db: Database | None = None


def get_db() -> Database:
    """Get the global database instance."""
    global _db
    if _db is None:
        _db = Database()
        _db.initialize()
    return _db
