"""Run the actual packaged executable against an isolated coordinator."""
import json
import base64
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen
import uuid

ROOT=Path(__file__).resolve().parent
from collector.version import VERSION
EXE=ROOT/'releases'/('v'+VERSION)/'WebCollector.exe'


def main():
    with tempfile.TemporaryDirectory() as temp:
        folder=Path(temp)
        home=folder/'client'
        home.mkdir()
        with socket.socket() as s:
            s.bind(('127.0.0.1',0))
            port=s.getsockname()[1]
        key=uuid.uuid4().hex
        base=f'http://127.0.0.1:{port}'
        env=dict(os.environ,COLLECTOR_DATA=str(folder/'server'),COLLECTOR_ADMIN_KEY=key,COLLECTOR_CLIENT_HOME=str(home))
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        log=(folder/'server.log').open('w+',encoding='utf-8')
        server=subprocess.Popen([sys.executable,'-m','uvicorn','remote_server:app','--port',str(port)],env=env,
                                stdout=log,stderr=log,creationflags=flags)
        process=None
        def call(path,body=None,headers=None):
            request=Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json',**(headers or {})})
            with urlopen(request,timeout=5) as result:
                return json.load(result)
        try:
            for _ in range(100):
                try:
                    call('/health')
                    break
                except OSError:
                    time.sleep(.1)
            device=call('/api/v3/devices',{'name':'packaged-exe-test'})
            (home/'client.json').write_text(json.dumps({'server_url':base,**device,'platforms':['doubao','deepseek','yuanbao','qianwen'],'slots':1}),encoding='utf-8')
            subprocess.run([str(EXE),'--self-test'],env=env,check=True,timeout=90,creationflags=flags)
            assert json.loads((home/'self-test.json').read_text())=={'ok':True,'frozen':True}
            batch=home/'batch-fixture'
            batch.mkdir()
            (batch/'request.json').write_text(json.dumps({'platforms':['doubao','deepseek','yuanbao','qianwen'],
                'questions':['batch fixture one','batch fixture two']}),encoding='utf-8')
            # Cancel before execution: validates the frozen batch path without opening a platform.
            (batch/'stop.request').write_text('cancel fixture')
            subprocess.run([str(EXE),'--local-batch',str(batch/'request.json')],env=env,check=True,timeout=60,creationflags=flags)
            batch_state=json.loads((batch/'status.json').read_text(encoding='utf-8'))
            assert batch_state['status']=='stopped' and batch_state['total']==8
            assert all(item['status']=='cancelled' for item in batch_state['items'])
            assert (batch/'report.html').exists()
            payload={'question':'packaged upload fixture, not a platform answer','platforms':['doubao'],
                     'mode':'real','web_search':None,'priority':0}
            task=call('/api/v3/tasks',payload,{'Idempotency-Key':uuid.uuid4().hex})
            spool=home/'data'/'spool'/task['jobs'][0]['id']
            spool.mkdir(parents=True)
            png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4XkAAAAASUVORK5CYII=')
            files=[]
            for name,kind,mime,data in [('fixture.png','screenshot','image/png',png),('answer.html','answer_html','text/html',b'<p>transport fixture</p>')]:
                (spool/name).write_bytes(data)
                files.append({'name':name,'kind':kind,'mime':mime,'sha256':hashlib.sha256(data).hexdigest()})
            (spool/'capture.json').write_text(json.dumps({'answer':'transport fixture','mock':False,'references':[],
                'request_hash':hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),'files':files}),encoding='utf-8')
            process=subprocess.Popen([str(EXE),'--run'],env=env,creationflags=flags)
            for _ in range(120):
                if call('/api/v3/devices')[0]['online']:
                    break
                if process.poll() is not None:
                    raise AssertionError('EXE worker exited early')
                time.sleep(.25)
            else:
                raise AssertionError('EXE heartbeat never arrived')
            for _ in range(120):
                result=call('/api/v2/tasks/'+task['id'])
                if result['status']=='partial':
                    break
                time.sleep(.25)
            else:
                raise AssertionError('EXE did not complete its upload fixture: '+str(result))
            assert result['jobs'][0]['attempt']==1
            assert len(result['jobs'][0]['result']['artifacts'])==2
            assert (spool/'receipt.json').exists()
            second=subprocess.run([str(EXE),'--run'],env=env,timeout=30,creationflags=flags)
            assert second.returncode!=0,'duplicate worker must fail its local lock'
            (home/'stop.request').write_text('drain')
            assert process.wait(timeout=30)==0
            assert call('/api/v3/devices')[0]['capacity']==0
            # Stop must not require a live server or leave the client in an error state.
            (home/'stop.request').unlink()
            process=subprocess.Popen([str(EXE),'--run'],env=env,creationflags=flags)
            for _ in range(120):
                if call('/api/v3/devices')[0]['capacity']==1:
                    break
                time.sleep(.25)
            else:
                raise AssertionError('EXE did not reconnect after graceful restart')
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                server.terminate()
            server.wait(timeout=10)
            (home/'stop.request').write_text('drain')
            assert process.wait(timeout=90)==0,'offline drain should exit normally'
            print('PASS: actual EXE contains Tcl/Tk and Playwright; TLS context; Edge DOM check; server authentication; heartbeat; task claim; saved fixture upload and receipt; duplicate-process lock; graceful drain. Not live-platform acceptance.')
        except Exception:
            log.flush()
            log.seek(0)
            print(log.read()[-10000:])
            if (home/'client.log').exists():
                print((home/'client.log').read_text(encoding='utf-8')[-6000:])
            raise
        finally:
            if process and process.poll() is None:
                (home/'stop.request').write_text('drain')
                process.wait(timeout=40)
            if server.poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(server.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                else:
                    server.terminate()
            server.wait(timeout=10)
            log.close()


if __name__=='__main__':
    main()

