import asyncio
import base64
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from collector.domain.errors import CollectionError, TransportError


class Client:
    def __init__(self, url, key):
        self.url, self.key = (url, key)

    def sync_post(self, path, payload):
        req = Request(
            self.url + path,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key},
        )
        with urlopen(req, timeout=30) as res:
            return json.load(res) if res.status != 204 else None

    async def post(self, path, payload):
        last_error = None
        for attempt in range(3):
            try:
                return await asyncio.to_thread(self.sync_post, path, payload)
            except HTTPError as exc:
                last_error = exc
                if exc.code < 500:
                    raise TransportError(exc.code, str(exc)) from exc
            except OSError as exc:
                last_error = exc
            if attempt == 2:
                import traceback

                traceback.print_exception(last_error)
                raise CollectionError(
                    'NETWORK_ERROR', '后台连接失败，采集现场已保留。' + str(last_error)
                ) from last_error
            await asyncio.sleep(1 + attempt)


class DeviceClient(Client):
    def __init__(self, url, key, device_id, session):
        super().__init__(url, key)
        self.device_id, self.session = device_id, session

    def sync_post(self, path, payload):
        path = path.replace('/api/v2/worker/', '/api/v3/worker/', 1)
        request = Request(
            self.url + path,
            data=json.dumps(payload).encode(),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + self.key,
                'X-Device-ID': self.device_id,
                'X-Worker-Session': self.session,
            },
        )
        with urlopen(request, timeout=20) as result:
            return json.load(result) if result.status != 204 else None


class JobGateway:
    def __init__(self, client, job):
        self.client, self.job = client, job
        self.persistent = getattr(client,'persistent',False)
        self.prefix = '/api/v2/worker/jobs/' + job['job_id']
        self.token = job['lease_token']

    async def progress(self, stage, checkpoint=None):
        payload = {'lease_token': self.token, 'stage': stage}
        if checkpoint is not None:
            payload['checkpoint'] = checkpoint
        return await self.client.post(self.prefix + '/heartbeat', payload)

    async def upload(self, item, raw):
        return await self.client.post(
            self.prefix + '/artifacts',
            {
                'lease_token': self.token,
                'kind': item['kind'],
                'mime': item['mime'],
                'sha256': item['sha256'],
                'content_base64': base64.b64encode(raw).decode(),
            },
        )

    async def complete(self, answer, evidence):
        return await self.client.post(
            self.prefix + '/complete',
            {'lease_token': self.token, 'answer': answer, 'evidence': evidence},
        )

    async def fail(self, code, message, waiting_human):
        return await self.client.post(
            self.prefix + '/fail',
            {
                'lease_token': self.token,
                'code': code,
                'message': message,
                'waiting_human': waiting_human,
            },
        )


class DeviceGateway:
    def __init__(self, client):
        self.client = client

    async def heartbeat(self, payload):
        return await self.client.post('/api/v3/worker/heartbeat', payload)

    async def sync(self):
        return await self.client.post('/api/v3/worker/sync', {})

    async def claim(self, request_id):
        return await self.client.post('/api/v3/worker/claim', {'request_id': request_id})
