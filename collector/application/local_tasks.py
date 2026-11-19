import base64
import hashlib
import json
import secrets
import time
import uuid
from collector.domain.errors import ApplicationError, ErrorKind
from collector.domain.policies import task_status, merge_checkpoint
from collector.domain.interaction import RPA_PLATFORMS
from .contracts import CreatedTask
from .ports import UnitOfWorkFactory, ArtifactStorage

ONLINE_SECONDS = 60
LEASE_SECONDS = 120


class LocalTaskService:
    def __init__(self, store: UnitOfWorkFactory, files: ArtifactStorage, key):
        self.store, self.files, self.key = store, files, key

    def authorize(self, worker_key):
        if not secrets.compare_digest(worker_key or '', self.key):
            raise ApplicationError(ErrorKind.UNAUTHORIZED, 'Worker 身份验证失败')

    def get_task(self, db, task_id):
        row = db.task_by_id(task_id)
        if not row:
            raise ApplicationError(ErrorKind.NOT_FOUND, '任务不存在')
        jobs = []
        for j in db.jobs_for_task(task_id):
            jobs.append(
                {k: j[k] for k in ('id', 'platform', 'status', 'stage', 'attempt', 'error')}
                | {'result': json.loads(j['result']) if j['result'] else None}
            )
        status = task_status(jobs)
        return dict(
            id=row['id'],
            created=row['created'],
            **json.loads(row['request']),
            status=status,
            jobs=jobs,
        )

    def create_task(self, body, idempotency_key):
        if not body.question.strip():
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '问题不能为空')
        if idempotency_key and len(idempotency_key) > 128:
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '幂等键过长')
        if body.interaction_mode == 'desktop_rpa' and any(
            p not in RPA_PLATFORMS for p in body.platforms
        ):
            raise ApplicationError(
                ErrorKind.INVALID_REQUEST, '桌面 RPA 试用仅支持豆包和 DeepSeek。'
            )
        payload = body.model_dump()
        if payload.get('thinking_mode') is None:
            payload.pop('thinking_mode')  # Keep old default requests idempotent.
        if payload.get('interaction_mode') == 'browser':
            payload.pop('interaction_mode')  # Preserve existing idempotency payloads.
        payload['platforms'] = sorted(set(body.platforms))
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.store.transaction() as db:
            db.begin()
            old = db.task_by_idempotency_key(idempotency_key)
            if old:
                if old['request'] != raw:
                    raise ApplicationError(ErrorKind.CONFLICT, '幂等键对应不同请求')

                return CreatedTask(self.get_task(db, old['id']), replayed=True)
            task_id = str(uuid.uuid4())
            db.insert_task(task_id, raw, idempotency_key, time.time())
            for platform in payload['platforms']:
                db.insert_job(str(uuid.uuid4()), task_id, platform, 'queued', 'queued')
            return CreatedTask(self.get_task(db, task_id))

    def list_tasks(self):
        with self.store.transaction() as db:
            return [self.get_task(db, r['id']) for r in db.recent_task_ids()]

    def task(self, task_id):
        with self.store.transaction() as db:
            return self.get_task(db, task_id)

    def recover(self, db):
        db.pause_expired_submissions(time.time())
        db.pause_waiting_platforms()
        db.requeue_expired_local_jobs(time.time())

    def claim(self, body, worker_key):
        self.authorize(worker_key)
        with self.store.transaction() as db:
            db.begin()
            self.recover(db)
            row = db.next_local_job(body.mode, body.mode)
            if not row:
                return None
            token = secrets.token_urlsafe(32)
            db.lease_local_job(token, time.time() + 120, row['id'])
            return {
                'job_id': row['id'],
                'task_id': row['task_id'],
                'platform': row['platform'],
                'lease_token': token,
                'attempt': row['attempt'] + 1,
                'request': json.loads(row['request']),
                'checkpoint': json.loads(row['checkpoint']),
            }

    def lease(self, db, job_id, token):
        row = db.job_by_id(job_id)
        if (
            not row
            or row['status'] != 'running'
            or row['token'] != token
            or (row['expires'] < time.time())
        ):
            raise ApplicationError(ErrorKind.CONFLICT, 'LEASE_LOST')
        return row

    def heartbeat(self, job_id, body, worker_key):
        self.authorize(worker_key)
        with self.store.transaction() as db:
            db.begin()
            row = self.lease(db, job_id, body.lease_token)
            db.extend_job_lease(body.stage, time.time() + 120, job_id)
            if body.checkpoint is not None:
                state = json.loads(row['checkpoint'])
                try:
                    state = merge_checkpoint(state, body.checkpoint)
                except ValueError as exc:
                    raise ApplicationError(ErrorKind.CONFLICT, str(exc)) from exc
                db.save_checkpoint(json.dumps(state), job_id)
        return {'ok': True}

    def complete(self, job_id, body, worker_key):
        self.authorize(worker_key)
        digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        with self.store.transaction() as db:
            db.begin()
            old = db.job_by_id(job_id)
            if old and old['receipt'] == digest:
                return {'ok': True, 'replayed': True}
            row = self.lease(db, job_id, body.lease_token)
            request = json.loads(db.task_request(row['task_id'])['request'])
            if request['mode'] == 'real':
                if (
                    not body.evidence
                    or body.evidence.get('mock') is not False
                    or (not body.answer.strip())
                ):
                    raise ApplicationError(
                        ErrorKind.INVALID_REQUEST, '真实任务必须提供真实正文和证据'
                    )
                artifact_ids = body.evidence.get('artifact_ids', [])
                attachments = []
                for aid in artifact_ids:
                    item = db.upload_for_attempt(aid, job_id, row['attempt'])
                    if not item:
                        raise ApplicationError(ErrorKind.INVALID_REQUEST, '附件不属于本次执行')
                    if not self.files.verify('uploads/' + aid, item['sha256']):
                        raise ApplicationError(ErrorKind.CONFLICT, '附件校验失败')
                    attachments.append(dict(item))
                if not any((a['kind'] == 'screenshot' for a in attachments)) or not any(
                    (a['kind'] == 'answer_html' for a in attachments)
                ):
                    raise ApplicationError(ErrorKind.INVALID_REQUEST, '缺少真实截图或原始 HTML')
                result = body.evidence | {
                    'answer': body.answer,
                    'mock': False,
                    'artifacts': attachments,
                }
                status = 'succeeded' if result.get('real_evidence_complete') else 'partial'
                db.finish_real_job(
                    status, 'done', json.dumps(result, ensure_ascii=False), digest, job_id
                )
                return {'ok': True, 'replayed': False}
            artifact = {
                'mock': True,
                'job_id': job_id,
                'attempt': row['attempt'],
                'platform': row['platform'],
                'answer': body.answer,
                'captured_at': time.time(),
                'references': [],
                'screenshots': [],
                'real_evidence_complete': False,
            }
            raw = json.dumps(artifact, ensure_ascii=False, indent=2).encode('utf-8')
            self.files.write(job_id + '.json', raw)
            result = artifact | {'artifact_sha256': hashlib.sha256(raw).hexdigest()}
            db.finish_mock_job(json.dumps(result, ensure_ascii=False), digest, job_id)
        return {'ok': True, 'replayed': False}

    def download(self, task_id, job_id):
        with self.store.transaction() as db:
            row = db.job_result_for_task(job_id, task_id)
        if not row or not row['result']:
            raise ApplicationError(ErrorKind.NOT_FOUND)
        result = json.loads(row['result'])
        return self.files.asset(
            job_id + '.json', result['artifact_sha256'], 'application/json', job_id + '.json'
        )

    def health(self):
        with self.store.transaction() as db:
            db.ping()
        return {
            'ok': True,
            'mode': 'local_browser',
            'real_adapters': 'requires_login_and_validation',
        }

    def upload(self, job_id, body, worker_key):
        self.authorize(worker_key)
        try:
            raw = base64.b64decode(body.content_base64, validate=True)
        except Exception:
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '无效附件编码')
        if len(raw) > 20 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != body.sha256:
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '附件大小或哈希不正确')
        if body.kind in ('screenshot', 'answer_long_image', 'sources_screenshot') and (
            body.mime != 'image/png' or not raw.startswith(b'\x89PNG\r\n\x1a\n')
        ):
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '截图必须为 PNG')
        if body.kind == 'answer_text' and body.mime != 'text/plain':
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '回复正文必须为文本')
        with self.store.transaction() as db:
            db.begin()
            row = self.lease(db, job_id, body.lease_token)
            aid = hashlib.sha256(
                f'{job_id}:{row["attempt"]}:{body.kind}:{body.sha256}'.encode()
            ).hexdigest()
            self.files.write('uploads/' + aid, raw)
            db.insert_upload(
                aid, job_id, row['attempt'], body.kind, body.mime, body.sha256, len(raw)
            )
        return {'id': aid}

    def file_download(self, task_id, artifact_id):
        with self.store.transaction() as db:
            item = db.upload_for_task(artifact_id, task_id)
        if not item:
            raise ApplicationError(ErrorKind.NOT_FOUND)
        return self.files.asset(
            'uploads/' + item['id'],
            item['sha256'],
            item['mime'],
            artifact_id + '.html' if item['mime'] == 'text/html' else None,
        )

    def fail(self, job_id, body, worker_key):
        self.authorize(worker_key)
        with self.store.transaction() as db:
            db.begin()
            row = self.lease(db, job_id, body.lease_token)
            db.fail_job(
                'waiting_human' if body.waiting_human else 'failed',
                body.code + ': ' + body.message,
                job_id,
            )
            if body.waiting_human and (not row['worker']):
                db.pause_platform(row['platform'])
        return {'ok': True}

    def resume(self, job_id):
        with self.store.transaction() as db:
            db.begin()
            row = db.waiting_job(job_id)
            if not row:
                raise ApplicationError(ErrorKind.CONFLICT, '任务不在等待人工状态')
            db.resume_job(job_id)
            if not row['worker']:
                db.resume_platform(row['platform'])
        return {'ok': True}
