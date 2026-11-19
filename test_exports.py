"""Long-image pixel coverage and share UI semantics; synthetic pages only."""
import asyncio
from pathlib import Path
import tempfile
from PIL import Image
from playwright.async_api import async_playwright
from capture import capture_segments, stitch_answer
from sharing import extract_share_link


async def main():
    async with async_playwright() as pw:
        browser=await pw.chromium.launch(channel='msedge',headless=True)
        try:
            page=await browser.new_page(viewport={'width':900,'height':700})
            with tempfile.TemporaryDirectory() as temp:
                folder=Path(temp)
                await page.set_content('''<style>body{margin:0;padding:100px 0}article{width:500px;height:2500px;background:repeating-linear-gradient(to bottom,rgb(255,0,0) 0px,rgb(255,0,0) 100px,rgb(0,0,255) 100px,rgb(0,0,255) 200px)}header,footer{position:fixed;width:100%;background:black;height:50px;left:0}header{top:0}footer{bottom:0}</style><article></article><header></header><footer></footer>''')
                files,complete=await capture_segments(page,page.locator('article'),folder,'answer')
                image,error=stitch_answer(folder,files)
                assert complete and image and not error,(complete,error,files)
                with Image.open(folder/image['name']) as result:
                    assert result.size==(500,2500),result.size
                    for y in range(2500):
                        assert result.getpixel((250,y))==((255,0,0) if y%200<100 else (0,0,255)),y
                # Never conceal missing rows with blank fill or stretch an image.
                assert stitch_answer(folder,files[1:])[0] is None
                changed=[dict(f,coverage=f['coverage']|{'horizontal_complete':False}) for f in files]
                assert stitch_answer(folder,changed)[0] is None

            html='''<button id="share">分享</button><script>
            document.querySelector('#share').onclick=()=>{const d=document.createElement('div');d.setAttribute('role','dialog');d.innerHTML='<input readonly value="https://chat.deepseek.com/share/decoy"><button id="copy">复制链接</button>';d.querySelector('#copy').onclick=()=>navigator.clipboard.writeText('https://chat.deepseek.com/share/test-token');document.body.append(d)};
            </script>'''
            await page.route('https://chat.deepseek.com/**',lambda route:route.fulfill(body=html,content_type='text/html'))
            await page.goto('https://chat.deepseek.com/a/chat-id')
            config={'share_button':'#share','share_link':'[role="dialog"] input','share_copy':'#copy','share_hosts':['chat.deepseek.com']}
            result=await extract_share_link(page,config)
            assert result['share_url']=='https://chat.deepseek.com/share/test-token'
            assert result['share_url']!=page.url and result['share_method']=='share_copy_button'
            # Repeated copying the same URL must still succeed.
            await page.goto('https://chat.deepseek.com/a/chat-id')
            result=await extract_share_link(page,config)
            assert result['share_url']=='https://chat.deepseek.com/share/test-token'
            # Missing share controls must not fall back to the conversation address.
            result=await extract_share_link(page,config|{'share_button':'#missing'})
            assert result['share_url'] is None and result['share_status']=='unavailable'
            # A stale clipboard URL is never accepted when no fresh link is returned.
            await page.goto('https://chat.deepseek.com/a/chat-id')
            await page.evaluate("navigator.clipboard.writeText('https://chat.deepseek.com/share/stale')")
            result=await extract_share_link(page,{'share_button':'#share','share_hosts':['chat.deepseek.com']})
            assert result['share_url'] is None
            # A visible dialog URL is not proof that Copy Link was clicked.
            result=await extract_share_link(page,config|{'share_copy':'#missing'})
            assert result['share_url'] is None
            html='''<button id="share">分享</button><script>document.querySelector('#share').onclick=()=>{const copy=document.createElement('button');copy.id='copy';copy.textContent='复制链接';copy.onclick=()=>navigator.clipboard.writeText('https://chat.deepseek.com/share/fresh');document.body.append(copy)};</script>'''
            await page.goto('https://chat.deepseek.com/a/chat-id')
            result=await extract_share_link(page,{'share_button':'#share','share_copy':'#copy','share_hosts':['chat.deepseek.com']})
            assert result['share_url']=='https://chat.deepseek.com/share/fresh',result
            await page.set_content('<div id="result"></div>')
            await page.add_script_tag(content=(Path(__file__).parent/'static/client-onboarding.js').read_text(encoding='utf-8'))
            await page.evaluate("data=>renderExtra(data,document.getElementById('result'))",{
                'answer':'完整回复\n第二段','share_url':'https://chat.deepseek.com/share/fresh',
                'long_image_complete':True,'artifacts':[{'kind':'answer_long_image','url':'/long.png'}]})
            await page.get_by_role('button',name='复制全部回复').click()
            await page.wait_for_function("()=>document.querySelector('button').textContent==='已复制' || [...document.querySelectorAll('button')].some(b=>b.textContent==='已复制')")
            copied=await page.evaluate('navigator.clipboard.readText()')
            assert copied.replace('\r\n','\n')=='完整回复\n第二段',repr(copied)
            await page.get_by_role('button',name='复制分享链接').click()
            assert await page.evaluate('navigator.clipboard.readText()')=='https://chat.deepseek.com/share/fresh'
            assert await page.get_by_role('link',name='下载完整回答长图').get_attribute('download')=='answer-long.png'
        finally:
            await browser.close()
    print('PASS: single long PNG, exact 2500-row pixel coverage, overlay exclusion, gap/crop rejection; explicit copy button, decoy dialog rejected, repeated copy, no address-bar or stale-clipboard substitution. Synthetic only.')


asyncio.run(main())
