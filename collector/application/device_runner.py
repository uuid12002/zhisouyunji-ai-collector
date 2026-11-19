from .runtime_ports import DeviceGateway, DeviceState
import asyncio
import random
import time
import uuid


async def run_device_worker(
    config, gateway: DeviceGateway, state: DeviceState, memory_load, execute_job, lock
):
    tasks = {}
    failures = 0
    connected = False
    claim_id = None
    last_contact = 0
    attention = []

    def publish(message):
        if attention and (not state.stop_requested()):
            platforms_text = '、'.join(sorted({item['platform'] for item in attention}))
            message = (
                '需要人工处理 '
                + platforms_text
                + '：请停止采集后检查登录或验证码，并由后台恢复任务。'
            )
        if last_contact and time.time() - last_contact > 45 and (not state.stop_requested()):
            message = '与服务器心跳中断，等待重连'
        state.publish(
            {
                'message': message,
                'running': len(tasks),
                'updated': time.time(),
                'pid': state.process_id,
                'run_id': state.run_id,
            }
        )

    async def beat():
        nonlocal last_contact, attention
        response = await gateway.heartbeat(
            {
                'platforms': config['platforms'],
                'capacity': 0 if memory_load() >= 85 else config['slots'],
                'memory_load': memory_load(),
                'draining': state.stop_requested(),
            }
        )
        attention = response.get('attention', [])
        last_contact = time.time()

    async def pulse():
        while True:
            try:
                await beat()
            except Exception as exc:
                print('设备心跳失败：' + str(exc), flush=True)
            await asyncio.sleep(15)

    def start(job):
        if job['job_id'] not in tasks:
            tasks[job['job_id']] = asyncio.create_task(execute_job(job))

    with lock:
        heartbeat = asyncio.create_task(pulse())
        try:
            while True:
                for job_id, task in list(tasks.items()):
                    if task.done():
                        await task
                        del tasks[job_id]
                if state.stop_requested():
                    publish('停止领取任务，等待当前任务完成' if tasks else '已停止')
                    if not tasks:
                        try:
                            await asyncio.wait_for(beat(), timeout=3)
                        except Exception:
                            print(
                                '停止时服务器不可达；设备将由服务端心跳超时标记离线。', flush=True
                            )
                        break
                    await asyncio.sleep(1)
                    continue
                try:
                    if not connected:
                        await beat()
                        for job in await gateway.sync():
                            start(job)
                        connected = True
                    if len(tasks) < config['slots'] and memory_load() < 85:
                        claim_id = claim_id or str(uuid.uuid4())
                        job = await gateway.claim(claim_id)
                        claim_id = None
                        if job:
                            start(job)
                    failures = 0
                    publish(
                        '执行采集并回传'
                        if tasks
                        else '内存占用较高，暂停领取'
                        if memory_load() >= 85
                        else '在线，等待任务'
                    )
                    await asyncio.sleep(2 + random.random())
                except Exception as exc:
                    failures += 1
                    connected = False
                    publish('连接异常，自动重连：' + str(exc))
                    delay = min(30, 2 ** min(failures, 5)) + random.random()
                    deadline = time.monotonic() + delay
                    while not state.stop_requested() and time.monotonic() < deadline:
                        await asyncio.sleep(0.2)
        finally:
            heartbeat.cancel()
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(heartbeat, *tasks.values(), return_exceptions=True)
