"""One-shot local worker; fixture is explicit, --real uses existing browser collector."""
import argparse
import asyncio
import json
import uuid
from pathlib import Path
from collector.application.worker import WorkerExecutor
from collector.infrastructure.local_relay_gateway import LocalFleetClient, LocalJobGateway
from collector.infrastructure.spool import JobSpool
from collector.infrastructure.browser.runtime import profile_lock


async def main(args):
    folder=Path(args.data);folder.mkdir(parents=True,exist_ok=True)
    claim_file=folder/'claim-request.json'
    if claim_file.exists():
        request=json.loads(claim_file.read_text())
    else:
        request={'requestId':str(uuid.uuid4())}
        claim_file.write_text(json.dumps(request))
    client=LocalFleetClient(Path(args.token_file).read_text().strip())
    job=await client.post('claim',request)
    if not job:
        print('No local task for this claim.');return
    async def fixture(job,report,folder):
        await report('extracting',{'fixture':True})
        return {'answer':'[本地协议联调模拟结果] 面食包括面条、馒头、饺子、包子、饼等。','references':[], 'files':[], 'fixture':True}
    if args.real:
        from collector.collection_bootstrap import collect
    else:
        collect=fixture
    await WorkerExecutor(LocalJobGateway(client,job),JobSpool(folder,job['job_id']),collect).execute(job)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--token-file',required=True);p.add_argument('--data',required=True)
    p.add_argument('--real',action='store_true');a=p.parse_args()
    with profile_lock('local-relay-desktop'):
        asyncio.run(main(a))
