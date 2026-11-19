"""Persistent server-push transport. No scheduled heartbeat or claim requests."""
import asyncio
import json
import time
import uuid
from urllib.parse import urlsplit,urlunsplit
from websockets.asyncio.client import connect
from collector.domain.errors import TransportError

class SocketClient:
    persistent=True
    def __init__(self,ws):
        self.ws=ws;self.pending={};self.jobs=asyncio.Queue();self.lock=asyncio.Lock()
    async def request(self,op,body,task_id=None):
        rid=str(uuid.uuid4());future=asyncio.get_running_loop().create_future();self.pending[rid]=future
        try:
            async with self.lock:await self.ws.send(json.dumps({'id':rid,'op':op,'task_id':task_id,'body':body}))
            return await asyncio.wait_for(future,60)
        finally:self.pending.pop(rid,None)
    async def post(self,path,payload):
        parts=path.strip('/').split('/')
        op={'heartbeat':'progress','artifacts':'upload','complete':'complete','fail':'fail'}[parts[-1]]
        return await self.request(op,payload,parts[-2])
    async def receive(self):
        try:
            async for raw in self.ws:
                msg=json.loads(raw)
                if msg.get('type') in ('job','blocked'):await self.jobs.put(msg);continue
                future=self.pending.get(msg.get('id'))
                if future is not None and not future.done():
                    if msg.get('ok'):future.set_result(msg.get('result'))
                    else:future.set_exception(TransportError(409,msg.get('error','UNKNOWN')))
        finally:
            for f in list(self.pending.values()):
                if not f.done():f.set_exception(ConnectionError('Long connection closed'))

async def run_socket_worker(config,state,memory_load,execute,lock):
    parsed=urlsplit(config['server_url'])
    if parsed.scheme!='https':raise ValueError('长连接生产服务必须使用 HTTPS')
    url=urlunsplit(('wss',parsed.netloc,'/api/v3/worker/connect','',''))
    session=uuid.uuid4().hex
    def publish(message,running=0):state.publish({'message':message,'running':running,'updated':time.time(),'pid':state.process_id,'run_id':state.run_id,'transport':'websocket'})
    with lock:
        while not state.stop_requested():
            task=None;reader=None
            try:
                async with connect(url,additional_headers={'Authorization':'Bearer '+config['device_key'],'X-Device-ID':config['device_id'],'X-Worker-Session':session},ping_interval=None,max_size=32*1024*1024,open_timeout=15) as ws:
                    client=SocketClient(ws);reader=asyncio.create_task(client.receive())
                    def health(draining=False):return {'platforms':config['platforms'],'capacity':0 if memory_load()>=85 else 1,'draining':draining,'memory_load':memory_load()}
                    response=await client.request('ready',health())
                    if response['jobs']:
                        if len(response['jobs']) != 1:
                            raise RuntimeError('Multiple active jobs require reconciliation')
                        # Same-session lease: the executor restores the durable spool
                        # and collection checks submitted conversations without resending.
                        task=asyncio.create_task(execute(client,response['jobs'][0]))
                        publish('长连接已恢复，核对并继续原任务',1)
                    else:
                        publish('长连接已建立，等待服务器下发任务')
                    capacity=health()['capacity']
                    while not reader.done():
                        if task is not None and task.done():
                            await task;task=None
                            response=await client.request('ready',health(state.stop_requested()))
                            if response['jobs']:
                                raise ConnectionError('任务回执未确认，重新连接核对原任务')
                            publish('长连接已建立，等待服务器下发任务')
                        if state.stop_requested():
                            if task is None:
                                await client.request('ready',health(True));publish('已停止');return
                            publish('停止接单，等待当前任务完成',1)
                        if not client.jobs.empty():
                            msg=await client.jobs.get()
                            if msg['type']=='blocked':publish('任务待核查：'+msg['error'])
                            else:
                                if task is not None:raise RuntimeError('Server assigned concurrent job')
                                task=asyncio.create_task(execute(client,msg['job']))
                                publish('长连接：执行采集并回传',1)
                        current=health()['capacity']
                        if task is None and current!=capacity:
                            capacity=current;await client.request('ready',health(state.stop_requested()))
                        # Local UI/stop-file monitoring only; sends no network traffic.
                        await asyncio.sleep(.2)
                    raise ConnectionError('Long connection disconnected')
            except Exception as exc:
                publish('长连接断开，正在重连：'+str(exc))
            finally:
                if task is not None and not task.done():task.cancel()
                if reader is not None and not reader.done():reader.cancel()
                await asyncio.gather(*(x for x in (task,reader) if x is not None),return_exceptions=True)
            for _ in range(25):
                if state.stop_requested():return
                await asyncio.sleep(.2)
