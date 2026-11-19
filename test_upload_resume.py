"""Proves upload retry consumes saved evidence without calling the browser."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import real_worker

async def main():
    with tempfile.TemporaryDirectory() as temp:
        real_worker.DATA=Path(temp)
        folder=Path(temp)/'spool'/'upload-test'
        folder.mkdir(parents=True)
        request={'question':'fixture','mode':'real'}
        raw=b'fixture stored HTML'
        (folder/'answer.html').write_bytes(raw)
        result={'answer':'stored answer','mock':False,'references':[],
            'request_hash':hashlib.sha256(json.dumps(request,sort_keys=True).encode()).hexdigest(),
            'files':[{'name':'answer.html','kind':'answer_html','mime':'text/html','sha256':hashlib.sha256(raw).hexdigest()}]}
        real_worker.save(folder/'capture.json',result)
        calls=[]
        class Client:
            async def post(self,path,payload):
                calls.append(path)
                return {'id':'uploaded'} if path.endswith('/artifacts') else {'ok':True}
        async def forbidden(*args):
            raise AssertionError('Browser must NOT be called on upload resume')
        real_worker.collect=forbidden
        await real_worker.execute(Client(),{'job_id':'upload-test','request':request,'lease_token':'new-lease','attempt':2})
        assert any(p.endswith('/complete') for p in calls)
        assert (folder/'receipt.json').exists()
    print('PASS: upload resume reuses hashed spool, no browser call, new attempt receipt')

asyncio.run(main())
