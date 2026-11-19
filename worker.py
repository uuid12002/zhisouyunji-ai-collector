"""Separate HTTP-only mock worker. Never submits questions to websites."""
import argparse
import json
import time
from urllib.request import Request, urlopen
from pathlib import Path
import os


def main():
    p = argparse.ArgumentParser(description='本地模拟采集 Worker')
    p.add_argument('--url', default='http://127.0.0.1:8765')
    p.add_argument('--once', action='store_true')
    args = p.parse_args()
    key = os.environ.get('COLLECTOR_WORKER_KEY') or (Path(__file__).parent/'data'/'worker.key').read_text().strip()

    def post(path, payload=None):
        req = Request(args.url+path, data=json.dumps(payload or {}).encode(),
                      headers={'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'})
        with urlopen(req, timeout=20) as res:
            return json.load(res) if res.status != 204 else None

    print('模拟 Worker 已启动；不会访问真实平台。', flush=True)
    while True:
        try:
            job = post('/api/v2/worker/claim')
            if job:
                if job['request'].get('mode')!='mock':
                    raise RuntimeError('旧后台分配了真实任务给模拟 Worker，停止。请升级后台并恢复任务。')
                prefix = '/api/v2/worker/jobs/'+job['job_id']
                for stage in ['checking_login','opening_chat','configuring','submitting','generating','extracting','capturing','uploading']:
                    post(prefix+'/heartbeat', {'lease_token': job['lease_token'], 'stage': stage})
                    time.sleep(0.35)
                answer = ('【模拟结果 · 非平台真实回答】\n\n平台：'+job['platform']+
                          '\n问题：'+job['request']['question']+
                          '\n\n本结果用于验证任务创建、HTTP 领取、状态更新和附件下载。'
                          '\n真实浏览器适配器尚未接入，没有真实截图或引用。')
                post(prefix+'/complete', {'lease_token': job['lease_token'], 'answer': answer})
                print('完成模拟子任务 '+job['job_id'], flush=True)
            if args.once:
                return
            if not job:
                time.sleep(2)
        except Exception as exc:
            print('Worker 错误：'+str(exc), flush=True)
            if args.once:
                raise
            time.sleep(5)


if __name__ == '__main__':
    main()
