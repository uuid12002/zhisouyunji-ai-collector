"""Worker composition and CLI compatibility functions."""

import argparse
import asyncio
import os
from collector.application.worker import WorkerExecutor
from collector.infrastructure.http_client import Client, JobGateway
from collector.infrastructure.spool import JobSpool
from collector.infrastructure.spool import write_json as save

__all__ = ['DATA', 'Client', 'collect', 'save', 'execute', 'main']
from collector.infrastructure.browser.runtime import DATA
from collector.collection_bootstrap import collect


async def execute(client, job):
    return await WorkerExecutor(
        JobGateway(client, job), JobSpool(DATA, job['job_id']), collect
    ).execute(job)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    key = os.environ.get('COLLECTOR_WORKER_KEY') or (DATA / 'worker.key').read_text().strip()
    client = Client(args.url, key)
    print('真实浏览器 Worker 已启动。登录、验证码需要人工处理。', flush=True)
    while True:
        try:
            job = await client.post('/api/v2/worker/claim', {'mode': 'real'})
            if job:
                await execute(client, job)
            if args.once:
                return
            if not job:
                await asyncio.sleep(2)
        except Exception as exc:
            print('Worker: ' + str(exc), flush=True)
            if args.once:
                raise
            await asyncio.sleep(5)
