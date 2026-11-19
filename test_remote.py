"""Protocol integration tests with real HTTP/SQLite, no calls to external platforms."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import uuid


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port=sock.getsockname()[1]
        admin='test-only-admin-'+uuid.uuid4().hex
        env=dict(os.environ,COLLECTOR_DATA=temp,COLLECTOR_ADMIN_KEY=admin,COLLECTOR_MAX_RUNNING='20')
        log=open(Path(temp)/'server.log','w+',encoding='utf-8')
        proc=subprocess.Popen([sys.executable,'-m','uvicorn','remote_server:app','--port',str(port)],env=env,stdout=log,stderr=log)
        base=f'http://127.0.0.1:{port}'

        def call(path,body=None,device=None,headers=None,authenticated=True):
            h={'Content-Type':'application/json'}
            if authenticated:
                h['Authorization']='Bearer '+(device['device_key'] if device else admin)
            if device:
                h['X-Device-ID']=device['device_id']
                h['X-Worker-Session']=device.get('_session',hashlib.sha256(device['device_id'].encode()).hexdigest()[:32])
            h.update(headers or {})
            req=Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
            try:
                with urlopen(req,timeout=20) as result:
                    raw=result.read()
                    return result.status,json.loads(raw) if raw else None
            except HTTPError as exc:
                return exc.code,json.loads(exc.read())

        def submit(key=None,question='protocol fixture',platforms=None):
            return call('/api/v3/tasks',{'question':question,'platforms':platforms or ['doubao'],'mode':'real'},
                        headers={'Idempotency-Key':key or uuid.uuid4().hex})

        def beat(d,capacity=1):
            return call('/api/v3/worker/heartbeat',{'platforms':['doubao','deepseek'],'capacity':capacity},d)

        def claim(d,key=None):
            return call('/api/v3/worker/claim',{'request_id':key or uuid.uuid4().hex},d)

        def jobpost(d,j,suffix,extra=None):
            return call('/api/v3/worker/jobs/'+j['job_id']+'/'+suffix,{'lease_token':j['lease_token'],**(extra or {})},d)

        def expire(job):
            with closing(sqlite3.connect(Path(temp)/'collector.db')) as db:
                with db:
                    db.execute('UPDATE jobs SET expires=? WHERE id=?',(time.time()-1,job['job_id']))

        try:
            for _ in range(100):
                try:
                    if call('/health',authenticated=False)[0]==200:
                        break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError('server did not start')
            assert call('/api/v2/tasks',authenticated=False)[0]==401
            assert call('/api/v3/tasks',{'question':'x','platforms':['doubao']})[0]==422
            assert call('/api/v2/worker/claim',{})[0]==403
            code,t=submit('idempotency')
            assert code==201
            assert submit('idempotency')[1]['id']==t['id']
            assert submit('idempotency','different')[0]==409
            devices=[]
            for n in range(20):
                code,d=call('/api/v3/devices',{'name':f'fixture-{n}'})
                assert code==200
                devices.append(d)
                assert beat(d)[0]==200
            for n in range(19):
                assert submit()[0]==201
            with ThreadPoolExecutor(max_workers=20) as pool:
                claimed=list(pool.map(claim,devices))
            assert all(code==200 for code,_ in claimed),claimed
            jobs=[j for _,j in claimed]
            assert len({j['job_id'] for j in jobs})==20
            assert claim(devices[0])[0]==204
            assert beat(devices[0]|{'_session':uuid.uuid4().hex})[0]==409
            assert jobpost(devices[1],jobs[0],'heartbeat',{'stage':'generating'})[0]==403
            assert call('/api/v2/tasks',device=devices[0])[0]==401
            for d,j in zip(devices,jobs):
                assert jobpost(d,j,'fail',{'code':'TEST_DONE','message':'protocol only','waiting_human':False})[0]==200
            submit()
            key=uuid.uuid4().hex
            with ThreadPoolExecutor(max_workers=8) as pool:
                replay=list(pool.map(lambda _:claim(devices[0],key),range(8)))
            assert len({j['lease_token'] for code,j in replay if code==200})==1
            assert all(code==200 for code,_ in replay)
            old=replay[0][1]
            expire(old)
            code,new=claim(devices[1])
            assert code==200 and new['job_id']==old['job_id'] and new['attempt']==old['attempt']+1
            assert jobpost(devices[0],old,'heartbeat',{'stage':'submitting'})[0]==403
            assert jobpost(devices[1],new,'heartbeat',{'stage':'submitting','checkpoint':{'submission':'submission_uncertain'}})[0]==200
            expire(new)
            assert claim(devices[2])[0]==204
            task=call('/api/v2/tasks/'+new['task_id'])[1]
            assert task['jobs'][0]['status']=='waiting_human'
            assert call('/api/v2/jobs/'+new['job_id']+'/resume',{})[0]==200
            assert claim(devices[2])[0]==204
            code,resumed=claim(devices[1])
            assert code==200 and resumed['checkpoint']['submission']=='submission_uncertain'
            assert jobpost(devices[1],resumed,'heartbeat',{'stage':'submitting','checkpoint':{'submission':'not_submitted'}})[0]==409
            assert jobpost(devices[1],resumed,'fail',{'code':'TEST_DONE','message':'fixture','waiting_human':False})[0]==200
            submit()
            assert call('/api/v3/devices/'+devices[0]['device_id']+'/control',{'enabled':True,'max_slots':0})[0]==200
            assert claim(devices[0])[0]==204
            beat(devices[2],0)
            assert claim(devices[2])[0]==204
            with closing(sqlite3.connect(Path(temp)/'collector.db')) as db:
                with db:
                    db.execute('UPDATE devices SET last_seen=0 WHERE id=?',(devices[3]['device_id'],))
            assert claim(devices[3])[0]==204
            code,j=claim(devices[4])
            assert code==200
            assert jobpost(devices[4],j,'heartbeat',{'stage':'uploading','checkpoint':{'capture_ready':True}})[0]==200
            expire(j)
            assert claim(devices[5])[0]==204
            code,j=claim(devices[4])
            assert code==200 and j['attempt']==2
            ids=[]
            for kind,mime,raw in [('screenshot','image/png',b'\x89PNG\r\n\x1a\nprotocol-fixture'),('answer_html','text/html',b'<p>protocol fixture</p>')]:
                payload={'kind':kind,'mime':mime,'sha256':hashlib.sha256(raw).hexdigest(),'content_base64':base64.b64encode(raw).decode()}
                code,a=jobpost(devices[4],j,'artifacts',payload)
                assert code==200
                assert jobpost(devices[4],j,'artifacts',payload)[1]['id']==a['id']
                ids.append(a['id'])
            result={'answer':'protocol fixture, not real platform validation','evidence':{'mock':False,'artifact_ids':ids,
                    'conversation_url':'https://www.doubao.com/chat/protocol-fixture',
                    'references':[{'title':'Reference fixture','displayed_url':'https://example.com/reference','relation':'cited'}]}}
            assert jobpost(devices[4],j,'complete',result)[0]==200
            assert jobpost(devices[4],j,'complete',result)[1]['replayed'] is True
            assert call('/api/v3/devices/'+devices[4]['device_id']+'/control',{'enabled':False})[0]==200
            assert beat(devices[4])[0]==401
            # A restarted process may take over only after the old device is offline.
            # Its new lease token fences the old process even with copied credentials.
            submit()
            code,old_session_job=claim(devices[6])
            assert code==200
            assert jobpost(devices[6],old_session_job,'heartbeat',{'stage':'submitting','checkpoint':{'submission':'submission_uncertain'}})[0]==200
            replacement=devices[6]|{'_session':uuid.uuid4().hex}
            assert beat(replacement)[0]==409
            with closing(sqlite3.connect(Path(temp)/'collector.db')) as db:
                with db:
                    db.execute('UPDATE devices SET last_seen=0 WHERE id=?',(devices[6]['device_id'],))
            assert beat(replacement)[0]==200
            code,owned=call('/api/v3/worker/sync',{},replacement)
            assert code==200 and len(owned)==1
            assert owned[0]['lease_token']!=old_session_job['lease_token']
            assert owned[0]['checkpoint']['submission']=='submission_uncertain'
            assert jobpost(devices[6],old_session_job,'heartbeat',{'stage':'generating'})[0]==409
            assert jobpost(replacement,old_session_job,'heartbeat',{'stage':'generating'})[0]==409
            assert jobpost(replacement,owned[0],'heartbeat',{'stage':'generating'})[0]==200
            assert jobpost(replacement,owned[0],'fail',{'code':'FIXTURE','message':'session recovery fixture','waiting_human':True})[0]==200
            # Browser verifies console authentication, JS, device controls and real-mode UI.
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge',headless=True)
                ctx=browser.new_context(http_credentials={'username':'admin','password':admin})
                page=ctx.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base+'/console')
                page.locator('#devices').get_by_text('fixture-0',exact=False).wait_for()
                assert page.locator('#mode option').count()==1
                page.locator('#tasks button').filter(has_text='部分完成').first.click()
                page.get_by_role('link',name='打开本次对话（可能需要原账号登录）').wait_for()
                assert page.get_by_role('link',name='打开本次对话（可能需要原账号登录）').get_attribute('href')==result['evidence']['conversation_url']
                assert page.get_by_role('link',name='[正文引用] Reference fixture').get_attribute('href')=='https://example.com/reference'
                page.once('dialog',lambda dialog:dialog.accept('onboarding-download-fixture'))
                page.locator('#add-device').click()
                with page.expect_download() as downloading:
                    page.get_by_role('button',name='下载客户接入配置').click()
                config=json.loads(Path(downloading.value.path()).read_text(encoding='utf-8'))
                assert config['server_url']==base and len(config['device_key'])>=24
                assert 'admin' not in config
                assert not errors,errors
                Path('build').mkdir(exist_ok=True)
                page.screenshot(path='build/remote-console.png',full_page=True)
                browser.close()
            print('PASS: 20 concurrent devices; idempotency; claim replay; lease fencing; uncertain submission affinity; drain; offline; memory capacity; artifact/complete replay; credential revocation; console JS')
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-10000:])
            raise
        finally:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
            proc.wait(timeout=10)
            log.close()


if __name__=='__main__':
    main()
