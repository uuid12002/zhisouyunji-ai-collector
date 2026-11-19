import hashlib
import json
import secrets
import time
from collector.domain.errors import ApplicationError, ErrorKind
from collector.domain.policies import expired_remote_lease
from .ports import UnitOfWorkFactory

ONLINE_SECONDS = 60
LEASE_SECONDS = 120


class DeviceService:
    def __init__(self, store: UnitOfWorkFactory, local, global_limit=20):
        self.store, self.local, self.global_limit = store, local, global_limit

    def authenticate_device(self, device_id, token, session, heartbeat=False):
        with self.store.transaction() as db:
            row = db.device_by_id(device_id)
        if (
            not row
            or not row['enabled']
            or not secrets.compare_digest(
                row['key_hash'], hashlib.sha256(token.encode()).hexdigest()
            )
        ):
            raise ApplicationError(ErrorKind.UNAUTHORIZED, '设备凭据无效或设备已停用')
        if len(session) != 32 or any(c not in '0123456789abcdef' for c in session):
            raise ApplicationError(ErrorKind.UNAUTHORIZED, '缺少有效进程会话')
        if not heartbeat and session != row['session_id']:
            raise ApplicationError(ErrorKind.CONFLICT, 'WORKER_SESSION_LOST')
        return dict(row)

    def assert_job_owner(self, job_id, device_id):
        with self.store.transaction() as db:
            row = db.job_by_id(job_id)
        if not row or row['worker'] != device_id:
            raise ApplicationError(ErrorKind.FORBIDDEN, '任务不属于当前设备')

    def recover(self, db):
        for row in db.expired_remote_jobs(time.time()):
            status, error, owner = expired_remote_lease(
                json.loads(row['checkpoint']), row['attempt'], row['worker']
            )
            db.expire_remote_job(status, 'lease_expired', error, owner, row['id'])

    def add_device(self, body):
        device_id = secrets.token_hex(12)
        key = secrets.token_urlsafe(32)
        with self.store.transaction() as db:
            db.insert_device(
                device_id, body.name, hashlib.sha256(key.encode()).hexdigest(), body.max_slots
            )
        return {'device_id': device_id, 'device_key': key, 'name': body.name}

    def control_device(self, device_id, body):
        with self.store.transaction() as db:
            if not db.device_identifier(device_id):
                raise ApplicationError(ErrorKind.NOT_FOUND, '设备不存在')
            db.control_device(body.enabled, body.max_slots, device_id)
        return {'ok': True}

    def devices(self):
        with self.store.transaction() as db:
            self.recover(db)
            return [
                dict(
                    id=r['id'],
                    name=r['name'],
                    enabled=bool(r['enabled']),
                    max_slots=r['max_slots'],
                    online=r['last_seen'] > time.time() - ONLINE_SECONDS,
                    last_seen=r['last_seen'],
                    capacity=r['capacity'],
                    metrics=json.loads(r['metrics']),
                    running=db.device_running_count(r['id']),
                )
                for r in db.all_devices()
            ]

    def device_heartbeat(self, body, device_id, session):
        with self.store.transaction() as db:
            db.begin()
            old = db.device_by_id(device_id)
            active = db.device_running_count(device_id)
            if old['session_id'] and old['session_id'] != session:
                if old['last_seen'] > time.time() - ONLINE_SECONDS and (
                    old['capacity'] > 0 or active
                ):
                    raise ApplicationError(
                        ErrorKind.CONFLICT, 'DEVICE_ALREADY_CONNECTED：同一设备凭据已有在线进程'
                    )
                for row in db.device_running_ids(device_id):
                    db.rotate_job_token(secrets.token_urlsafe(32), row['id'])
            db.set_device_session(session, device_id)
            db.save_device_heartbeat(
                0 if body.draining else body.capacity,
                json.dumps(sorted(set(body.platforms))),
                time.time(),
                body.model_dump_json(),
                device_id,
            )
            attention = [dict(row) for row in db.device_attention(device_id)]
        return {
            'ok': True,
            'heartbeat_seconds': 15,
            'lease_seconds': LEASE_SECONDS,
            'attention': attention,
        }

    def create_task(self, body, idempotency_key):
        if body.mode != 'real':
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '远程调度仅执行真实浏览器任务')
        result = self.local.create_task(body, idempotency_key)
        with self.store.transaction() as db:
            db.set_task_priority(body.priority, result.task['id'])
        return result

    def job_payload(self, row, db):
        raw = db.task_request(row['task_id'])['request']
        return {
            'job_id': row['id'],
            'task_id': row['task_id'],
            'platform': row['platform'],
            'lease_token': row['token'],
            'attempt': row['attempt'],
            'request': json.loads(raw),
            'checkpoint': json.loads(row['checkpoint']),
        }

    def claim(self, body, device_id):
        with self.store.transaction() as db:
            db.begin()
            self.recover(db)
            replay = db.claim_replay(device_id, body.request_id)
            if replay:
                if replay['status'] == 'running' and replay['worker'] == device_id:
                    return self.job_payload(replay, db)
                return None
            if db.claim_exists(device_id, body.request_id):
                return None
            dev = db.device_by_id(device_id)
            active = db.device_running_count(device_id)
            total = db.running_count()
            if (
                not dev['enabled']
                or dev['last_seen'] < time.time() - ONLINE_SECONDS
                or active >= min(dev['capacity'], dev['max_slots'])
                or (total >= self.global_limit)
            ):
                return None
            platforms = json.loads(dev['platforms'])
            if not platforms:
                return None
            row = db.next_remote_job(device_id, json.dumps(platforms), device_id, time.time())
            if not row:
                return None
            db.lease_remote_job(
                device_id, secrets.token_urlsafe(32), time.time() + LEASE_SECONDS, row['id']
            )
            db.insert_claim(device_id, body.request_id, row['id'], row['attempt'] + 1)
            return self.job_payload(db.job_by_id(row['id']), db)

    def sync(self, device_id):
        with self.store.transaction() as db:
            db.begin()
            self.recover(db)
            return [self.job_payload(row, db) for row in db.device_running_jobs(device_id)]
