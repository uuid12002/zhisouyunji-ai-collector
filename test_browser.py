"""Simulated webpage checks, explicitly NOT real platform acceptance."""
import asyncio
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright
from real_collector import run_in_page, CollectionError, Adapter

HTML='''<!doctype html><meta charset="utf-8"><button id="new">新对话</button>
<style>#finished{display:block;margin-top:4px}</style>
<textarea id="input"></textarea><button id="send" style="display:none">发送</button><main></main>
<div id="promotion" style="position:fixed;inset:0;background:white;z-index:100"><button id="later">下次提醒我</button></div>
<script>
document.querySelector('#later').onclick=()=>document.querySelector('#promotion').remove();
document.querySelector('#input').oninput=()=>setTimeout(()=>document.querySelector('#send').style.display='block',400);
document.querySelector('#new').onclick=()=>document.querySelector('main').replaceChildren();
document.querySelector('#send').onclick=()=>{
 const main=document.querySelector('main');const q=document.createElement('p');q.id='question';q.textContent=document.querySelector('#input').value;main.append(q);
 const a=document.createElement('article');a.id='answer';a.textContent='模拟页面流式内容';main.append(a);
 const busy=document.createElement('div');busy.id='busy';busy.textContent='生成中';main.append(busy);
 setTimeout(()=>{a.textContent='仅用于测试的模拟页面回答。';const link=document.createElement('a');link.href='https://example.invalid/source?x=1';link.textContent='测试来源';a.append(link);busy.remove();const end=document.createElement('button');end.id='finished';end.textContent='复制';main.append(end)},700);
};
</script>'''


async def main():
    with tempfile.TemporaryDirectory() as temp:
        async with async_playwright() as pw:
            browser=await pw.chromium.launch(channel='msedge',headless=True)
            page=await browser.new_page()
            await page.route('https://www.doubao.com/**',lambda route:route.fulfill(body=HTML,content_type='text/html'))
            job={'job_id':'fixture','task_id':'fixture','platform':'doubao','request':{'question':'仅为测试问题','web_search':None}}
            config={key:'#'+key for key in ['input','send','question','answer','finished','busy']}
            config['new_chat']='#new'
            config['dismiss_promotion_texts']=['下次提醒我']
            events=[]
            async def report(stage,checkpoint=None):
                if stage=='submitting':
                    assert await page.locator('#question').count()==0
                    assert checkpoint['submission']=='submission_uncertain'
                events.append((stage,checkpoint))
            result=await run_in_page(page,job,report,Path(temp),config)
            assert result['mock'] is False and result['answer_complete'] is True
            assert result['real_evidence_complete'] is False
            assert result['references'][0]['displayed_url']=='https://example.invalid/source?x=1'
            assert (Path(temp)/'answer-001.png').read_bytes().startswith(b'\x89PNG')
            assert '<article' in (Path(temp)/'answer.html').read_text(encoding='utf-8')
            assert (Path(temp)/'answer.txt').read_text(encoding='utf-8')==result['answer']
            assert result['long_image_complete'] and (Path(temp)/'answer-long.png').exists(),result
            assert result['share_url'] is None
            assert any(c and c.get('submission')=='submitted' for _,c in events)
            # Unsupported requested option must stop BEFORE sending.
            job['request']['web_search']=True
            try:
                await run_in_page(page,job,report,Path(temp),config)
                raise AssertionError('unsupported option accepted')
            except CollectionError as exc:
                assert exc.code=='UNSUPPORTED_OPTION'
                assert await page.locator('#question').count()==0
            job['request']['web_search']=None
            events.clear()
            original_wait=Adapter.wait_finished
            async def unfinished(self,question,timeout=300):
                return False
            Adapter.wait_finished=unfinished
            try:
                await run_in_page(page,job,report,Path(temp),config)
                raise AssertionError('Unfinished answer exported')
            except CollectionError as exc:
                assert exc.code=='GENERATION_TIMEOUT'
                assert not any(stage=='capturing' for stage,_ in events)
            finally:
                Adapter.wait_finished=original_wait
            await browser.close()
    print('PASS: simulated browser fixture; send intent, exact question, completion signals, DOM/PNG capture, unsupported option. NOT real-platform acceptance.')


if __name__=='__main__':
    asyncio.run(main())
