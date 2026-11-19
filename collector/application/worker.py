"""Execute one leased job using injected transport, collection and spool ports."""

from .runtime_ports import JobGateway, EvidenceSpool, Collector
import asyncio
import hashlib
import json
import time
from collector.domain.errors import CollectionError, TransportError


class WorkerExecutor:
    def __init__(self, gateway: JobGateway, spool: EvidenceSpool, collect: Collector):
        self.gateway, self.spool, self.collect = gateway, spool, collect

    async def execute(self, job):
        current = {'stage': 'checking_login'}
        stage_started = time.monotonic()
        durations = {}
        progress_lock = asyncio.Lock()
        request_hash = hashlib.sha256(
            json.dumps(job['request'], sort_keys=True).encode()
        ).hexdigest()

        async def report(stage, checkpoint=None):
            nonlocal stage_started
            async with progress_lock:
                stage = stage or current['stage']
                now = time.monotonic()
                if stage != current['stage']:
                    previous = current['stage']
                    durations[previous] = durations.get(previous, 0) + now - stage_started
                    stage_started = now
                    current['stage'] = stage
                timing = {'stage_seconds': round(now-stage_started, 3),
                          'stage_timings': {k: round(v, 3) for k,v in durations.items()}}
                await self.gateway.progress(stage, {**(checkpoint or {}), **timing})
                if checkpoint is not None: self.spool.checkpoint(checkpoint)

        async def heartbeat():
            if getattr(self.gateway,'persistent',False):
                await asyncio.Future()
            while True:
                await asyncio.sleep(10)
                await report(None)

        async def work():
            result = self.spool.capture(request_hash)
            if result is None:
                result = await self.collect(self.spool.restore(job), report, self.spool.folder)
                self.spool.save_capture(result, request_hash, job['attempt'])
            await report('uploading', {'capture_ready': True})
            artifacts = []
            artifact_map = {}
            limit = asyncio.Semaphore(3)
            async def upload_one(item):
                async with limit:
                    return item, await self.gateway.upload(item, self.spool.read_artifact(item))
            uploads = [asyncio.create_task(upload_one(item)) for item in result['files']]
            try:
                for item, uploaded in await asyncio.gather(*uploads):
                    artifacts.append(uploaded['id'])
                    artifact_map[item['name']] = uploaded['id']
            finally:
                for upload in uploads:
                    if not upload.done(): upload.cancel()
                await asyncio.gather(*uploads, return_exceptions=True)
            for reference in result.get('references', []):
                reference['evidence_artifact_ids'] = [
                    artifact_map[name]
                    for name in reference.get('evidence_files', [])
                    if name in artifact_map
                ]
            result['capture_files'] = [
                item | {'artifact_id': artifact_map[item['name']]} for item in result['files']
            ]
            evidence = {
                key: value for key, value in result.items() if key not in ('files', 'answer')
            } | {'artifact_ids': artifacts}
            await report('returning')
            evidence['stage_timings'] = {k: round(v, 3) for k,v in durations.items()}
            await self.gateway.complete(result['answer'], evidence)
            self.spool.receipt({'completed_at': time.time(), 'attempt': job['attempt']})

        pulse = asyncio.create_task(heartbeat())
        task = asyncio.create_task(asyncio.wait_for(work(), timeout=2400))
        try:
            done, _ = await asyncio.wait([pulse, task], return_when=asyncio.FIRST_COMPLETED)
            if pulse in done:
                task.cancel()
                await pulse
            await task
            print('采集结果已回传：' + job['job_id'], flush=True)
        except Exception as exc:
            code = getattr(exc, 'code', 'BROWSER_ERROR')
            message = (
                str(exc)
                if isinstance(exc, CollectionError)
                else '浏览器或网络操作失败。请检查专用浏览器和原会话后恢复任务；详情已保存在本机诊断文件。'
            )
            self.spool.error(exc)
            if isinstance(exc, TransportError) and exc.status == 409:
                code = 'LEASE_LOST'
            print(str(code) + ': ' + str(exc), flush=True)
            if code not in ('LEASE_LOST', 'NETWORK_ERROR'):
                try:
                    await self.gateway.fail(code, message[:1800], getattr(exc, 'human', True))
                except Exception:
                    pass
        finally:
            pulse.cancel()
            task.cancel()
            await asyncio.gather(pulse, task, return_exceptions=True)
