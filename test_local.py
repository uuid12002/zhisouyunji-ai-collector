"""Real HTTP smoke checks against temporary data; no platform requests."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import base64
import hashlib
from urllib.request import Request, urlopen
from urllib.error import HTTPError


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, COLLECTOR_DATA=temp)
        proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f'http://127.0.0.1:{port}'
        def call(path, data=None, headers=None):
            req = Request(base+path, data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type': 'application/json', **(headers or {})})
            try:
                with urlopen(req, timeout=10) as r:
                    raw = r.read()
                    return r.status, json.loads(raw) if raw else None
            except HTTPError as e:
                return e.code, json.load(e)
        try:
            for _ in range(100):
                try:
                    if call('/health')[0] == 200:
                        break
                except OSError:
                    time.sleep(.1)
            payload = {'question': '本地测试 <script>alert(1)</script>', 'platforms': ['doubao','deepseek'], 'mode':'mock'}
            code, task = call('/api/v2/tasks', payload, {'Idempotency-Key':'test'})
            assert code == 201 and len(task['jobs']) == 2
            assert call('/api/v2/tasks', payload, {'Idempotency-Key':'test'})[0] == 200
            assert call('/api/v2/tasks', payload | {'question':'changed'}, {'Idempotency-Key':'test'})[0] == 409
            assert call('/api/v2/tasks', payload | {'mode':'real'})[0] == 201
            assert call('/api/v2/worker/claim', {})[0] == 401
            key = (Path(temp)/'worker.key').read_text()
            auth = {'Authorization':'Bearer '+key}
            _, job = call('/api/v2/worker/claim', {}, auth)
            jid = job['job_id']
            with sqlite3.connect(Path(temp)/'collector.db') as db:
                db.execute('UPDATE jobs SET expires=0 WHERE id=?', (jid,))
            db.close()
            assert call(f'/api/v2/worker/jobs/{jid}/heartbeat', {'lease_token':job['lease_token'],'stage':'generating'}, auth)[0] == 409
            worker_env = dict(env, COLLECTOR_WORKER_KEY=key)
            for _ in range(2):
                subprocess.run([sys.executable,'worker.py','--once','--url',base], env=worker_env, check=True)
            _, final = call('/api/v2/tasks/'+task['id'])
            assert final['status'] == 'succeeded'
            for j in final['jobs']:
                assert j['result']['mock'] and not j['result']['screenshots']
                assert call(j['result']['artifact_url'])[0] == 200
                with sqlite3.connect(Path(temp)/'collector.db') as db:
                    token = db.execute('SELECT token FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]
                db.close()
                assert call('/api/v2/worker/jobs/'+j['id']+'/complete', {'lease_token':token,'answer':j['result']['answer']},auth)[1]['replayed']
            artifact = Path(temp)/(final['jobs'][0]['id']+'.json')
            artifact.write_text('tampered')
            assert call(final['jobs'][0]['result']['artifact_url'])[0] == 409
            # Real job: lease + checkpoint survive recovery; an old instance cannot write.
            _, real = call('/api/v2/worker/claim', {'mode':'real'}, auth)
            prefix='/api/v2/worker/jobs/'+real['job_id']
            pulse={'lease_token':real['lease_token'],'stage':'submitting','checkpoint':{'submission':'submission_uncertain','conversation_url':'https://www.doubao.com/chat/test'}}
            assert call(prefix+'/heartbeat',pulse,auth)[0]==200
            assert call(prefix+'/heartbeat',pulse|{'checkpoint':{'submission':'not_submitted'}},auth)[0]==409
            assert call(prefix+'/complete',{'lease_token':real['lease_token'],'answer':'fake'},auth)[0]==422
            with sqlite3.connect(Path(temp)/'collector.db') as db:
                db.execute('UPDATE jobs SET expires=0 WHERE id=?',(real['job_id'],))
            db.close()
            call('/api/v2/worker/claim',{'mode':'real'},auth)
            _, blocked=call('/api/v2/tasks/'+real['task_id'])
            target=next(j for j in blocked['jobs'] if j['id']==real['job_id'])
            assert target['status']=='waiting_human'
            assert call(prefix+'/heartbeat',pulse,auth)[0]==409
            assert call('/api/v2/jobs/'+real['job_id']+'/resume',{})[0]==200
            _, resumed=call('/api/v2/worker/claim',{'mode':'real'},auth)
            assert resumed['job_id']==real['job_id'] and resumed['checkpoint']['submission']=='submission_uncertain'
            token=resumed['lease_token']
            png=b'\x89PNG\r\n\x1a\nTEST_ONLY'
            up={'lease_token':token,'kind':'screenshot','mime':'image/png','sha256':hashlib.sha256(png).hexdigest(),'content_base64':base64.b64encode(png).decode()}
            assert call(prefix+'/artifacts',up|{'sha256':'0'*64},auth)[0]==422
            assert call(prefix+'/artifacts',up|{'lease_token':real['lease_token']},auth)[0]==409
            code, uploaded=call(prefix+'/artifacts',up,auth)
            assert code==200
            assert call(prefix+'/artifacts',up,auth)[1]['id']==uploaded['id']
            assert call('/api/v2/tasks/other/files/'+uploaded['id'])[0]==404
            print('PASS: HTTP lifecycle, mode isolation, idempotency, worker auth, expired lease, monotonic checkpoint, uncertain submission pause, safe resume, upload integrity and ownership')
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == '__main__':
    main()
