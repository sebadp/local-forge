from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number  TEXT UNIQUE NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    wa_message_id   TEXT UNIQUE,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_wa_id ON messages(wa_message_id);

CREATE TABLE IF NOT EXISTS summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    content         TEXT NOT NULL,
    message_count   INTEGER NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_summaries_conversation ON summaries(conversation_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    content    TEXT NOT NULL,
    category   TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_memories_active ON memories(active, id);

CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS processed_messages (
    wa_message_id TEXT PRIMARY KEY,
    processed_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_profiles (
    phone_number      TEXT PRIMARY KEY,
    onboarding_state  TEXT NOT NULL DEFAULT 'complete',
    data              TEXT NOT NULL DEFAULT '{}',
    message_count     INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS projects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT NOT NULL,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active', 'archived', 'completed')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_projects_phone ON projects(phone_number, status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_phone_name ON projects(phone_number, name);

CREATE TABLE IF NOT EXISTS project_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  INTEGER NOT NULL REFERENCES projects(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'in_progress', 'done')),
    priority    TEXT NOT NULL DEFAULT 'medium'
                CHECK (priority IN ('low', 'medium', 'high')),
    due_date    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON project_tasks(project_id, status);

CREATE TABLE IF NOT EXISTS project_activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_project ON project_activity(project_id, created_at);

CREATE TABLE IF NOT EXISTS project_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    content    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pnotes_project ON project_notes(project_id);

CREATE TABLE IF NOT EXISTS conversation_state (
    conversation_id   INTEGER PRIMARY KEY REFERENCES conversations(id),
    sticky_categories TEXT NOT NULL DEFAULT '[]',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_command_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL,
    phone_number   TEXT NOT NULL,
    command        TEXT NOT NULL,
    decision       TEXT NOT NULL
                   CHECK (decision IN ('allow', 'deny', 'ask_approved', 'ask_rejected')),
    exit_code      INTEGER,
    stdout_preview TEXT,
    stderr_preview TEXT,
    duration_ms    INTEGER,
    started_at     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at   TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cmd_log_session ON agent_command_log(session_id);
CREATE INDEX IF NOT EXISTS idx_cmd_log_phone ON agent_command_log(phone_number);

CREATE TABLE IF NOT EXISTS user_cron_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    phone_number TEXT NOT NULL,
    cron_expr    TEXT NOT NULL,
    message      TEXT NOT NULL,
    timezone     TEXT NOT NULL DEFAULT 'UTC',
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cron_phone ON user_cron_jobs(phone_number, active);
"""

TRACING_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    id            TEXT PRIMARY KEY,
    phone_number  TEXT NOT NULL,
    input_text    TEXT NOT NULL,
    output_text   TEXT,
    wa_message_id TEXT,
    message_type  TEXT NOT NULL DEFAULT 'text'
                  CHECK (message_type IN ('text', 'audio', 'image', 'agent')),
    status        TEXT NOT NULL DEFAULT 'started'
                  CHECK (status IN ('started', 'completed', 'failed')),
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at  TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_traces_phone ON traces(phone_number, started_at);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_traces_wa_msg ON traces(wa_message_id);

CREATE TABLE IF NOT EXISTS trace_spans (
    id           TEXT PRIMARY KEY,
    trace_id     TEXT NOT NULL REFERENCES traces(id),
    parent_id    TEXT REFERENCES trace_spans(id),
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'span'
                 CHECK (kind IN ('span', 'generation', 'tool', 'guardrail')),
    input        TEXT,
    output       TEXT,
    status       TEXT NOT NULL DEFAULT 'started'
                 CHECK (status IN ('started', 'completed', 'failed')),
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT,
    latency_ms   REAL,
    metadata     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON trace_spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_kind ON trace_spans(kind);

CREATE TABLE IF NOT EXISTS trace_scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id   TEXT NOT NULL REFERENCES traces(id),
    span_id    TEXT REFERENCES trace_spans(id),
    name       TEXT NOT NULL,
    value      REAL NOT NULL,
    source     TEXT NOT NULL DEFAULT 'system'
               CHECK (source IN ('system', 'user', 'llm_judge', 'human')),
    comment    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scores_trace ON trace_scores(trace_id);
CREATE INDEX IF NOT EXISTS idx_scores_name ON trace_scores(name, value);
"""

# Migration: add 'agent' to message_type CHECK constraint.
# Run when an existing DB has the old constraint without 'agent'.
# Uses PRAGMA foreign_keys=OFF so the DROP TABLE doesn't cascade-fail on
# trace_spans / trace_scores FK references.
_TRACES_MIGRATION = """
PRAGMA foreign_keys = OFF;

CREATE TABLE traces_v2 (
    id            TEXT PRIMARY KEY,
    phone_number  TEXT NOT NULL,
    input_text    TEXT NOT NULL,
    output_text   TEXT,
    wa_message_id TEXT,
    message_type  TEXT NOT NULL DEFAULT 'text'
                  CHECK (message_type IN ('text', 'audio', 'image', 'agent')),
    status        TEXT NOT NULL DEFAULT 'started'
                  CHECK (status IN ('started', 'completed', 'failed')),
    started_at    TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at  TEXT,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO traces_v2 SELECT * FROM traces;
DROP TABLE traces;
ALTER TABLE traces_v2 RENAME TO traces;

CREATE INDEX IF NOT EXISTS idx_traces_phone ON traces(phone_number, started_at);
CREATE INDEX IF NOT EXISTS idx_traces_status ON traces(status);
CREATE INDEX IF NOT EXISTS idx_traces_wa_msg ON traces(wa_message_id);

PRAGMA foreign_keys = ON;
"""

DATASET_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_dataset (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id     TEXT REFERENCES traces(id),
    entry_type   TEXT NOT NULL CHECK (entry_type IN ('golden', 'failure', 'correction')),
    input_text   TEXT NOT NULL,
    output_text  TEXT,
    expected_output TEXT,
    metadata     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dataset_type ON eval_dataset(entry_type);
CREATE INDEX IF NOT EXISTS idx_dataset_trace ON eval_dataset(trace_id);

CREATE TABLE IF NOT EXISTS eval_dataset_tags (
    dataset_id  INTEGER NOT NULL REFERENCES eval_dataset(id) ON DELETE CASCADE,
    tag         TEXT NOT NULL,
    PRIMARY KEY (dataset_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_dataset_tag_name ON eval_dataset_tags(tag);
"""

PROMPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_name TEXT NOT NULL,
    version     INTEGER NOT NULL,
    content     TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 0,
    scores      TEXT NOT NULL DEFAULT '{}',
    created_by  TEXT NOT NULL DEFAULT 'human',
    approved_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompt_version ON prompt_versions(prompt_name, version);
CREATE INDEX IF NOT EXISTS idx_prompt_active ON prompt_versions(prompt_name, is_active);
"""

VEC_SCHEMA_MEMORIES = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_memories "
    "USING vec0(memory_id INTEGER PRIMARY KEY, embedding float[{dims}])"
)
VEC_SCHEMA_NOTES = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_notes "
    "USING vec0(note_id INTEGER PRIMARY KEY, embedding float[{dims}])"
)
VEC_SCHEMA_PROJECT_NOTES = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_project_notes "
    "USING vec0(note_id INTEGER PRIMARY KEY, embedding float[{dims}])"
)
VEC_SCHEMA_TOOLS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS vec_tools "
    "USING vec0(tool_name TEXT PRIMARY KEY, embedding float[{dims}])"
)


async def init_db(db_path: str, embedding_dims: int = 768) -> tuple[aiosqlite.Connection, bool]:
    """Initialize database and optionally load sqlite-vec.

    Returns (connection, vec_available).
    """
    # check_same_thread=False allows accessing the raw connection from
    # the main thread (needed for enable_load_extension during init)
    conn = await aiosqlite.connect(db_path, check_same_thread=False)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")  # Faster, safe with WAL
    await conn.execute("PRAGMA cache_size=-32000")  # 32MB page cache in memory
    await conn.execute("PRAGMA temp_store=MEMORY")  # Temp tables in RAM
    await conn.execute("PRAGMA mmap_size=30000000")  # 30MB mmap for faster reads
    await conn.execute("PRAGMA busy_timeout=5000")  # 5s wait on lock contention
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.executescript(SCHEMA)
    await conn.executescript(TRACING_SCHEMA)

    # Migrate project_notes: add title column if missing
    cursor = await conn.execute("PRAGMA table_info(project_notes)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "title" not in cols:
        logger.info("Migrating project_notes: adding 'title' column")
        await conn.execute("ALTER TABLE project_notes ADD COLUMN title TEXT")
        await conn.commit()

    # Migrate existing `traces` table if it was created without 'agent' message_type
    cursor = await conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='traces' AND type='table'"
    )
    row = await cursor.fetchone()
    if row and "'agent'" not in row[0]:
        logger.info("Migrating traces table: adding 'agent' to message_type CHECK constraint")
        await conn.executescript(_TRACES_MIGRATION)
        await conn.commit()

    await conn.executescript(DATASET_SCHEMA)
    await conn.executescript(PROMPT_SCHEMA)
    await conn.commit()

    # Try to load sqlite-vec
    vec_available = False
    try:
        import sqlite_vec

        ext_path = sqlite_vec.loadable_path()
        # Load extension via the raw connection (safe during init — no concurrent queries)
        conn._connection.enable_load_extension(True)  # type: ignore[union-attr]
        conn._connection.load_extension(ext_path)  # type: ignore[union-attr]
        conn._connection.enable_load_extension(False)  # type: ignore[union-attr]

        # Create vector tables
        await conn.execute(VEC_SCHEMA_MEMORIES.format(dims=embedding_dims))
        await conn.execute(VEC_SCHEMA_NOTES.format(dims=embedding_dims))
        await conn.execute(VEC_SCHEMA_PROJECT_NOTES.format(dims=embedding_dims))
        await conn.execute(VEC_SCHEMA_TOOLS.format(dims=embedding_dims))
        await conn.commit()
        vec_available = True
        logger.info("sqlite-vec loaded successfully (dims=%d)", embedding_dims)
    except Exception:
        logger.warning("sqlite-vec not available, semantic search disabled", exc_info=True)

    return conn, vec_available
