"""SQLite statements and row materialization. Only this layer knows SQL."""


class SqliteRepository:
    def __init__(self, connection):
        self._connection = connection

    def task_by_id(self, task_id) -> dict | None:
        row = self._connection.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        return dict(row) if row is not None else None

    def jobs_for_task(self, task_id) -> list[dict]:
        rows = self._connection.execute(
            'SELECT * FROM jobs WHERE task_id=? ORDER BY platform', (task_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def begin(self) -> None:
        self._connection.execute('BEGIN IMMEDIATE', ())

    def task_by_idempotency_key(self, key) -> dict | None:
        row = self._connection.execute('SELECT * FROM tasks WHERE idem=?', (key,)).fetchone()
        return dict(row) if row is not None else None

    def insert_task(self, task_id, request_json, key, created) -> None:
        self._connection.execute(
            'INSERT INTO tasks VALUES(?,?,?,?)', (task_id, request_json, key, created)
        )

    def insert_job(self, job_id, task_id, platform, status, stage) -> None:
        self._connection.execute(
            'INSERT INTO jobs(id,task_id,platform,status,stage) VALUES(?,?,?,?,?)',
            (job_id, task_id, platform, status, stage),
        )

    def recent_task_ids(self) -> list[dict]:
        rows = self._connection.execute(
            'SELECT id FROM tasks ORDER BY created DESC LIMIT 100', ()
        ).fetchall()
        return [dict(row) for row in rows]

    def pause_expired_submissions(self, now) -> None:
        self._connection.execute(
            "UPDATE jobs SET status='waiting_human', stage='lease_expired', error='SUBMISSION_UNCERTAIN', token=NULL WHERE worker IS NULL AND status='running' AND expires<? AND json_extract(checkpoint,'$.submission') IN ('submission_uncertain','submitted')",
            (now,),
        )

    def pause_waiting_platforms(self) -> None:
        self._connection.execute(
            "UPDATE profiles SET paused=1 WHERE platform IN (SELECT platform FROM jobs WHERE status='waiting_human' AND worker IS NULL)",
            (),
        )

    def requeue_expired_local_jobs(self, now) -> None:
        self._connection.execute(
            "UPDATE jobs SET status=CASE WHEN attempt>=3 THEN 'failed' ELSE 'queued' END, stage='lease_expired', error='LEASE_EXPIRED', token=NULL WHERE worker IS NULL AND status='running' AND expires<?",
            (now,),
        )

    def next_local_job(self, mode, real_mode) -> dict | None:
        row = self._connection.execute(
            "SELECT j.*,t.request FROM jobs j JOIN tasks t ON t.id=j.task_id WHERE j.status='queued' AND json_extract(t.request,'$.mode')=? AND (?='mock' OR (NOT EXISTS(SELECT 1 FROM jobs busy WHERE busy.platform=j.platform AND busy.status='running') AND NOT EXISTS(SELECT 1 FROM profiles p WHERE p.platform=j.platform AND p.paused=1))) ORDER BY t.created LIMIT 1",
            (mode, real_mode),
        ).fetchone()
        return dict(row) if row is not None else None

    def lease_local_job(self, token, expires, job_id) -> None:
        self._connection.execute(
            "UPDATE jobs SET status='running',stage='checking_login',attempt=attempt+1,token=?,expires=?,error=NULL WHERE id=?",
            (token, expires, job_id),
        )

    def job_by_id(self, job_id) -> dict | None:
        row = self._connection.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row is not None else None

    def extend_job_lease(self, stage, expires, job_id) -> None:
        self._connection.execute(
            'UPDATE jobs SET stage=?,expires=? WHERE id=?', (stage, expires, job_id)
        )

    def save_checkpoint(self, checkpoint_json, job_id) -> None:
        self._connection.execute(
            'UPDATE jobs SET checkpoint=? WHERE id=?', (checkpoint_json, job_id)
        )

    def task_request(self, task_id) -> dict | None:
        row = self._connection.execute(
            'SELECT request FROM tasks WHERE id=?', (task_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def upload_for_attempt(self, artifact_id, job_id, attempt) -> dict | None:
        row = self._connection.execute(
            'SELECT * FROM uploads WHERE id=? AND job_id=? AND attempt=?',
            (artifact_id, job_id, attempt),
        ).fetchone()
        return dict(row) if row is not None else None

    def finish_real_job(self, status, stage, result_json, receipt, job_id) -> None:
        self._connection.execute(
            'UPDATE jobs SET status=?,stage=?,result=?,receipt=? WHERE id=?',
            (status, stage, result_json, receipt, job_id),
        )

    def finish_mock_job(self, result_json, receipt, job_id) -> None:
        self._connection.execute(
            "UPDATE jobs SET status='succeeded',stage='done',result=?,receipt=? WHERE id=?",
            (result_json, receipt, job_id),
        )

    def job_result_for_task(self, job_id, task_id) -> dict | None:
        row = self._connection.execute(
            'SELECT result FROM jobs WHERE id=? AND task_id=?', (job_id, task_id)
        ).fetchone()
        return dict(row) if row is not None else None

    def ping(self) -> list[dict]:
        rows = self._connection.execute('SELECT 1', ()).fetchall()
        return [dict(row) for row in rows]

    def insert_upload(self, artifact_id, job_id, attempt, kind, mime, sha256, size) -> None:
        self._connection.execute(
            'INSERT OR IGNORE INTO uploads VALUES(?,?,?,?,?,?,?)',
            (artifact_id, job_id, attempt, kind, mime, sha256, size),
        )

    def upload_for_task(self, artifact_id, task_id) -> dict | None:
        row = self._connection.execute(
            'SELECT u.* FROM uploads u JOIN jobs j ON j.id=u.job_id WHERE u.id=? AND j.task_id=?',
            (artifact_id, task_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def fail_job(self, status, error, job_id) -> None:
        self._connection.execute(
            'UPDATE jobs SET status=?,error=?,token=NULL WHERE id=?', (status, error, job_id)
        )

    def pause_platform(self, platform) -> None:
        self._connection.execute('UPDATE profiles SET paused=1 WHERE platform=?', (platform,))

    def waiting_job(self, job_id) -> dict | None:
        row = self._connection.execute(
            "SELECT * FROM jobs WHERE id=? AND status='waiting_human'", (job_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def resume_job(self, job_id) -> None:
        self._connection.execute("UPDATE jobs SET status='queued',error=NULL WHERE id=?", (job_id,))

    def resume_platform(self, platform) -> None:
        self._connection.execute('UPDATE profiles SET paused=0 WHERE platform=?', (platform,))

    def expired_remote_jobs(self, now) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM jobs WHERE worker IS NOT NULL AND status='running' AND expires<?", (now,)
        ).fetchall()
        return [dict(row) for row in rows]

    def expire_remote_job(self, status, stage, error, worker, job_id) -> None:
        self._connection.execute(
            'UPDATE jobs SET status=?,stage=?,error=?,token=NULL,worker=? WHERE id=?',
            (status, stage, error, worker, job_id),
        )

    def device_by_id(self, device_id) -> dict | None:
        row = self._connection.execute('SELECT * FROM devices WHERE id=?', (device_id,)).fetchone()
        return dict(row) if row is not None else None

    def insert_device(self, device_id, name, key_hash, max_slots) -> None:
        self._connection.execute(
            'INSERT INTO devices(id,name,key_hash,max_slots) VALUES(?,?,?,?)',
            (device_id, name, key_hash, max_slots),
        )

    def device_identifier(self, device_id) -> dict | None:
        row = self._connection.execute('SELECT id FROM devices WHERE id=?', (device_id,)).fetchone()
        return dict(row) if row is not None else None

    def control_device(self, enabled, max_slots, device_id) -> None:
        self._connection.execute(
            'UPDATE devices SET enabled=?,max_slots=? WHERE id=?', (enabled, max_slots, device_id)
        )

    def device_running_count(self, device_id) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM jobs WHERE worker=? AND status='running'", (device_id,)
        ).fetchone()[0]

    def all_devices(self) -> list[dict]:
        rows = self._connection.execute('SELECT * FROM devices ORDER BY name', ()).fetchall()
        return [dict(row) for row in rows]

    def device_running_ids(self, device_id) -> list[dict]:
        rows = self._connection.execute(
            "SELECT id FROM jobs WHERE worker=? AND status='running'", (device_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def rotate_job_token(self, token, job_id) -> None:
        self._connection.execute('UPDATE jobs SET token=? WHERE id=?', (token, job_id))

    def set_device_session(self, session, device_id) -> None:
        self._connection.execute('UPDATE devices SET session_id=? WHERE id=?', (session, device_id))

    def save_device_heartbeat(
        self, capacity, platforms_json, last_seen, metrics_json, device_id
    ) -> None:
        self._connection.execute(
            'UPDATE devices SET capacity=?,platforms=?,last_seen=?,metrics=? WHERE id=?',
            (capacity, platforms_json, last_seen, metrics_json, device_id),
        )

    def device_attention(self, device_id) -> list[dict]:
        rows = self._connection.execute(
            "SELECT platform,error FROM jobs WHERE worker=? AND status='waiting_human' LIMIT 4",
            (device_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def set_task_priority(self, priority, task_id) -> None:
        self._connection.execute('UPDATE jobs SET priority=? WHERE task_id=?', (priority, task_id))

    def claim_replay(self, device_id, request_id) -> dict | None:
        row = self._connection.execute(
            'SELECT j.* FROM claims c JOIN jobs j ON j.id=c.job_id WHERE c.device=? AND c.request_id=? AND c.attempt=j.attempt',
            (device_id, request_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def claim_exists(self, device_id, request_id) -> dict | None:
        row = self._connection.execute(
            'SELECT 1 FROM claims WHERE device=? AND request_id=?', (device_id, request_id)
        ).fetchone()
        return dict(row) if row is not None else None

    def running_count(self) -> int:
        return self._connection.execute(
            "SELECT count(*) FROM jobs WHERE status='running'", ()
        ).fetchone()[0]

    def next_remote_job(self, device_id, platforms_json, owner, now) -> dict | None:
        row = self._connection.execute(
            "SELECT j.* FROM jobs j JOIN tasks t ON t.id=j.task_id WHERE j.status='queued' AND json_extract(t.request,'$.mode')='real' AND (j.worker IS NULL OR j.worker=?) AND j.platform IN (SELECT value FROM json_each(?)) AND NOT EXISTS(SELECT 1 FROM jobs b WHERE b.worker=? AND b.platform=j.platform AND b.status IN ('running','waiting_human')) ORDER BY (COALESCE(json_extract(t.request,'$.priority'),0)+(?-t.created)/60.0) DESC,t.created,j.id LIMIT 1",
            (device_id, platforms_json, owner, now),
        ).fetchone()
        return dict(row) if row is not None else None

    def lease_remote_job(self, device_id, token, expires, job_id) -> None:
        self._connection.execute(
            "UPDATE jobs SET status='running',stage='checking_login',attempt=attempt+1,worker=?,token=?,expires=?,error=NULL WHERE id=?",
            (device_id, token, expires, job_id),
        )

    def insert_claim(self, device_id, request_id, job_id, attempt) -> None:
        self._connection.execute(
            'INSERT INTO claims VALUES(?,?,?,?)', (device_id, request_id, job_id, attempt)
        )

    def device_running_jobs(self, device_id) -> list[dict]:
        rows = self._connection.execute(
            "SELECT * FROM jobs WHERE worker=? AND status='running'", (device_id,)
        ).fetchall()
        return [dict(row) for row in rows]
