"""Idempotent SQLite schema; existing tasks and evidence are preserved."""

LOCAL_SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, request TEXT NOT NULL,
      idem TEXT UNIQUE, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
      platform TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
      attempt INTEGER NOT NULL DEFAULT 0, token TEXT, expires REAL,
      worker TEXT, result TEXT, error TEXT, receipt TEXT);
    CREATE INDEX IF NOT EXISTS job_queue ON jobs(status, platform);
    CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY, job_id TEXT, attempt INTEGER,
      kind TEXT, mime TEXT, sha256 TEXT, size INTEGER);
    CREATE TABLE IF NOT EXISTS profiles(platform TEXT PRIMARY KEY, paused INTEGER NOT NULL DEFAULT 0);

    """

REMOTE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS devices (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, key_hash TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, max_slots INTEGER NOT NULL DEFAULT 1,
      capacity INTEGER NOT NULL DEFAULT 0, platforms TEXT NOT NULL DEFAULT '[]',
      last_seen REAL NOT NULL DEFAULT 0, metrics TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS claims (
      device TEXT NOT NULL, request_id TEXT NOT NULL, job_id TEXT NOT NULL,
      attempt INTEGER NOT NULL, PRIMARY KEY(device,request_id));
    CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(worker,status,platform);
    """
