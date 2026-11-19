"""Local unified-fleet adapter implementing the existing WorkerExecutor port."""
import asyncio
import base64
import json
import urllib.request
from collector.domain.errors import TransportError


class LocalFleetClient:
    def __init__(self, token):
        self.token = token

    async def post(self, action, body):
        def send():
            request=urllib.request.Request('http://127.0.0.1:4321/local/web/'+action,
                data=json.dumps(body,ensure_ascii=False).encode(),
                headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            try:
                with urllib.request.urlopen(request,timeout=20) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                raise TransportError(exc.code, 'Local fleet rejected request') from exc
        return await asyncio.to_thread(send)


class LocalJobGateway:
    def __init__(self, client, job):
        self.client=client
        self.identity={'taskId':job['job_id'],'token':job['lease_token']}

    async def progress(self, stage, checkpoint=None):
        return await self.client.post('progress',self.identity|{'stage':stage,'checkpoint':checkpoint})

    async def upload(self, item, raw):
        return await self.client.post('artifacts',self.identity|{'base64':base64.b64encode(raw).decode(),'name':item['name']})

    async def complete(self, answer, evidence):
        return await self.client.post('complete',self.identity|{'answer':answer,'evidence':evidence})

    async def fail(self, code, message, waiting_human):
        return await self.client.post('fail',self.identity|{'code':code,'message':message,'waiting_human':waiting_human})
