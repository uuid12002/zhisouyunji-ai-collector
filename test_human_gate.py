"""Human handoff regression on local DOM fixtures, never solve real challenges."""

import asyncio
from playwright.async_api import async_playwright

from collector.domain.errors import CollectionError
from collector.infrastructure.browser.adapter import Adapter
from collector.infrastructure.browser.human_gate import HumanGate


async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            stages = []

            async def report(stage, state=None):
                stages.append(stage)

            await page.set_content("""<article>回答正文：请完成人机验证、安全验证、captcha</article>
                <div id="captcha_container" hidden>hidden placeholder</div>""")
            gate = HumanGate(page, {}, report, timeout=1, poll_interval=0.02)
            assert not await gate.detected(), (
                'Answer text and hidden placeholders are not challenges'
            )
            # Verification SDKs keep transparent/off-screen frames mounted even
            # when the user sees no challenge. Playwright is_visible ignores opacity.
            for markup in [
                '<div style="opacity:0"><div id="captcha_container">验证</div></div>',
                '<div id="captcha_container" style="position:fixed;left:-9999px;width:200px;height:100px">验证</div>',
                '<iframe src="about:blank#captcha" style="width:300px;height:200px;opacity:0"></iframe>',
                '<div id="captcha_container" style="width:200px;height:100px">验证</div><div style="position:fixed;inset:0;background:white;z-index:100">正常页面</div>',
                '<div id="captcha_container" aria-hidden="true">后台占位</div>',
            ]:
                await page.set_content(markup)
                assert not await gate.detected(), markup
            await page.set_content("""<div id="captcha_container" style="width:200px;height:100px">验证</div>
                <input id="input"><button id="send">发送</button>
                <script>window.sends=0;document.querySelector('#send').onclick=()=>window.sends++;</script>""")
            adapter = Adapter(page, {'input': '#input', 'send': '#send'}, report)
            adapter.human_gate = HumanGate(page, {}, report, timeout=1, poll_interval=0.02)
            pending = asyncio.create_task(adapter.fill_question('中文问题\n第二行'))
            await asyncio.sleep(0.12)
            assert not pending.done() and await page.locator('#input').input_value() == ''
            assert await page.evaluate('window.sends') == 0
            # Simulate the user completing verification, not an automated solver.
            await page.locator('#captcha_container').evaluate('e=>e.remove()')
            await pending
            assert stages == ['waiting_human', 'resuming'], stages
            assert await page.evaluate('window.sends') == 0, 'Verification must not cause a send'
            assert adapter.human_gate.paused_seconds >= 0.1
            # Keyboard mode preserves Chinese and multiline text; Enter is not sent alone.
            for element in [
                '<textarea id="input"></textarea>',
                '<div id="input" contenteditable="true"></div>',
            ]:
                await page.set_content(element + '<button id="send">发送</button>')
                await page.evaluate("""()=>{window.enterSends=0;window.keys=0;
                    document.querySelector('#input').addEventListener('keydown',e=>{
                    window.keys++;if(e.key==='Enter'&&!e.shiftKey)window.enterSends++});}""")
                keyboard_adapter = Adapter(
                    page, {'input': '#input', 'send': '#send', 'input_method': 'keyboard'}
                )
                await keyboard_adapter.fill_question('中文 ABC\n第二行')
                text = await page.locator('#input').evaluate(
                    '(e)=>e.value === undefined ? e.innerText : e.value'
                )
                assert ' '.join(text.split()) == '中文 ABC 第二行', repr(text)
                assert await page.evaluate('window.keys > 0 && window.enterSends === 0')
            # A challenge appearing after Send must resume confirmation, not Send again.
            await page.set_content("""<div id="captcha_container" hidden>验证</div>
                <div id="question" hidden>同一个问题</div><button id="send">发送</button>
                <script>window.sends=0;document.querySelector('#send').onclick=()=>{
                window.sends++;document.querySelector('#captcha_container').hidden=false;
                document.querySelector('#question').hidden=false;};</script>""")
            adapter = Adapter(page, {'send': '#send', 'question': '#question'}, report)
            adapter.human_gate = HumanGate(page, {}, report, timeout=1, poll_interval=0.02)
            await adapter.send()
            pending = asyncio.create_task(adapter.confirm_question('同一个问题'))
            await asyncio.sleep(0.12)
            assert not pending.done() and await page.evaluate('window.sends') == 1
            await page.locator('#captcha_container').evaluate('e=>e.remove()')
            await pending
            assert await page.evaluate('window.sends') == 1
            # A challenge never cleared by the user stops; do not refresh or click it.
            await page.set_content(
                '<div id="captcha_container">验证</div><button onclick="window.clicked=true">验证</button>'
            )
            gate = HumanGate(page, {}, report, timeout=0.08, poll_interval=0.01)
            try:
                await gate.wait_if_needed()
                raise AssertionError('Expected human verification error')
            except CollectionError as exc:
                assert exc.code == 'HUMAN_VERIFICATION' and exc.human
            assert not await page.evaluate('Boolean(window.clicked)')
            assert not page.is_closed()
            await page.close()
            try:
                await gate.wait_if_needed()
                raise AssertionError('Closed browser must stop')
            except CollectionError as exc:
                assert exc.code == 'BROWSER_CLOSED'
        finally:
            await browser.close()
    print(
        'PASS: visible challenge detection; paused input; manual clearance; no send, refresh or challenge clicks; timeout and browser closure. Synthetic only.'
    )


if __name__ == '__main__':
    asyncio.run(main())
