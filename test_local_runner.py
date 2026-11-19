import asyncio
import json
import os
from pathlib import Path
import tempfile


async def main():
    with tempfile.TemporaryDirectory() as temp:
        os.environ['COLLECTOR_DATA']=str(Path(temp)/'profiles-data')
        import local_runner as runner
        calls=[]
        async def fixture(job,report,folder):
            calls.append(job)
            await report('submitting',{'submission':'submission_uncertain'})
            await report('generating',{'submission':'submitted'})
            return {'answer':'<script>do not execute</script>','answer_complete':True,'files':[],
                    'conversation_url':'https://www.doubao.com/chat/fixture','references':[
                        {'title':'source','displayed_url':'https://example.com','relation':'cited'},
                        {'title':'unsafe','displayed_url':'javascript:alert(1)','relation':'unknown'}]}
        runner.collect=fixture
        request=Path(temp)/'request.json'
        request.write_text(json.dumps({'platform':'doubao','question':'test question'}))
        await runner.run(request)
        assert len(calls)==1
        output=(Path(temp)/'report.html').read_text(encoding='utf-8')
        assert '&lt;script&gt;' in output and 'href="javascript:' not in output
        assert 'https://example.com' in output
        assert json.loads((Path(temp)/'status.json').read_text())['status']=='completed'
        try:
            await runner.run(request)
            raise AssertionError('duplicate test was executed')
        except RuntimeError:pass
        assert len(calls)==1
    print('PASS: local task without server config; durable checkpoints; result HTML escaping; URL filtering; duplicate execution prevented. Fixture only.')


asyncio.run(main())
