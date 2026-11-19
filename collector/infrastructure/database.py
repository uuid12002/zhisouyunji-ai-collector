"""SQLite unit of work and idempotent schema migrations."""

from .migrations import LOCAL_SCHEMA, REMOTE_SCHEMA
import sqlite3
from contextlib import contextmanager
from collector.domain.platforms import PLATFORM_NAMES
from .repositories import SqliteRepository


class SqliteStore:
    def __init__(self, data):
        self.path = data / 'collector.db'

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self._connection() as connection:
            yield SqliteRepository(connection)

    def initialize(self, remote=False):
        with self._connection() as db:
            db.executescript(LOCAL_SCHEMA)
            db.executemany(
                'INSERT OR IGNORE INTO profiles(platform) VALUES(?)', [(p,) for p in PLATFORM_NAMES]
            )
            columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            if 'checkpoint' not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN checkpoint TEXT NOT NULL DEFAULT '{}'")
        # WAL must be enabled outside the local-schema write transaction.
        if remote:
            with self._connection() as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.executescript(REMOTE_SCHEMA)
                cols = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
                if 'priority' not in cols:
                    db.execute('ALTER TABLE jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0')
                if 'session_id' not in {
                    r['name'] for r in db.execute('PRAGMA table_info(devices)')
                }:
                    db.execute("ALTER TABLE devices ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
