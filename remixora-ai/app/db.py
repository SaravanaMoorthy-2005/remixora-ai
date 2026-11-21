import os
import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "./data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_FILE = DATA_DIR / "remixora.sqlite3"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, filename TEXT NOT NULL, media_path TEXT NOT NULL,
 media_kind TEXT NOT NULL, duration REAL NOT NULL, status TEXT NOT NULL,
 error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS segments (
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT NOT NULL REFERENCES assets(id),
 start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
 start REAL NOT NULL, end REAL NOT NULL, title TEXT NOT NULL,
 reason TEXT NOT NULL, quote TEXT NOT NULL, score REAL NOT NULL,
 source TEXT NOT NULL, suggested_angle TEXT NOT NULL DEFAULT 'Teach It', output_path TEXT, render_status TEXT DEFAULT 'not_rendered'
);
CREATE TABLE IF NOT EXISTS copies (
 id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), platform TEXT NOT NULL,
 angle TEXT NOT NULL, content TEXT NOT NULL, source TEXT NOT NULL,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS edits (
 id TEXT PRIMARY KEY, copy_id TEXT NOT NULL REFERENCES copies(id),
 original TEXT NOT NULL, revised TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS brand_rules (
 id TEXT PRIMARY KEY, text TEXT NOT NULL, evidence_edit_id TEXT REFERENCES edits(id),
 status TEXT NOT NULL DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS calendar (
 id TEXT PRIMARY KEY, copy_id TEXT NOT NULL REFERENCES copies(id),
 scheduled_at TEXT NOT NULL, timezone TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled_in_app'
);
CREATE TABLE IF NOT EXISTS metrics (
 id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL REFERENCES clips(id),
 platform TEXT NOT NULL, angle TEXT NOT NULL, impressions INTEGER, likes INTEGER NOT NULL,
 comments INTEGER NOT NULL, shares INTEGER NOT NULL, saves INTEGER NOT NULL, source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id TEXT, agent TEXT NOT NULL,
 action TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_segments_asset ON segments(asset_id);
CREATE INDEX IF NOT EXISTS ix_clips_asset ON clips(asset_id);
CREATE INDEX IF NOT EXISTS ix_metrics_platform_angle ON metrics(platform, angle);
"""


def init_db():
    with closing(sqlite3.connect(DB_FILE)) as conn:
        with conn:
            conn.executescript(SCHEMA)
            migrate(conn)


def migrate(conn):
    """Additive migrations preserve existing starter records and exports."""
    additions = {
        'assets': {'checksum': 'TEXT', 'probe_json': "TEXT DEFAULT '{}'", 'source_url':'TEXT', 'transcript_source':"TEXT DEFAULT 'imported_or_local'"},
        'clips': {'preset_id':'TEXT', 'evidence': "TEXT DEFAULT '[]'", 'rubric': "TEXT DEFAULT '{}'", 'base_score': 'REAL DEFAULT 0', 'adjustment': 'REAL DEFAULT 0', 'performance_revision': 'INTEGER DEFAULT 0'},
        'copies': {'approved': 'INTEGER DEFAULT 0', 'rules_used': "TEXT DEFAULT '[]'", 'brand_revision': 'INTEGER DEFAULT 0'},
        'edits': {'diff': "TEXT DEFAULT ''"},
        'calendar': {'caption': "TEXT DEFAULT ''", 'utc_at': "TEXT DEFAULT ''"},
        'metrics': {'views': 'INTEGER DEFAULT 0', 'published_at': 'TEXT', 'period': "TEXT DEFAULT 'lifetime'", 'fingerprint': 'TEXT', 'watch_time': 'REAL'},
    }
    for table, columns in additions.items():
        existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS revisions (name TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0);
    INSERT OR IGNORE INTO revisions VALUES ('brand',0),('performance',0);
    CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY CHECK(id=1), content TEXT NOT NULL);
    INSERT OR IGNORE INTO profile VALUES (1,'{}');
    CREATE TABLE IF NOT EXISTS jobs (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL, payload TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
      error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE UNIQUE INDEX IF NOT EXISTS ix_jobs_active ON jobs(kind,target_id) WHERE status IN ('queued','running');
    CREATE TABLE IF NOT EXISTS variants (
      id TEXT PRIMARY KEY, clip_id TEXT NOT NULL REFERENCES clips(id), angle TEXT NOT NULL,
      headline TEXT NOT NULL, subtitles INTEGER NOT NULL, status TEXT DEFAULT 'queued', output_path TEXT,
      error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(clip_id,angle,headline,subtitles));
    CREATE TABLE IF NOT EXISTS provider_cache (key TEXT PRIMARY KEY, content TEXT NOT NULL, provider TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS workflows (
      id TEXT PRIMARY KEY, asset_id TEXT REFERENCES assets(id), source_url TEXT,
      options TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
      message TEXT NOT NULL DEFAULT 'Waiting for the media worker', error TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE UNIQUE INDEX IF NOT EXISTS ix_workflow_asset ON workflows(asset_id) WHERE asset_id IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS ix_metrics_fingerprint ON metrics(fingerprint);
    PRAGMA user_version=3;
    ''')
    from .presets import migrate as migrate_presets
    migrate_presets(conn)


@contextmanager
def db():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(cursor):
    return [dict(item) for item in cursor.fetchall()]
