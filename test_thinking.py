"""Thinking controls and send safeguards on local HTML; no live platform prompts."""

import json
from pathlib import Path
import tempfile
import unittest
from playwright.async_api import async_playwright
from collector.collection_bootstrap import run_in_page
from collector.domain.errors import CollectionError
from collector.domain.thinking import validate_thinking_mode
from collector.infrastructure.browser.adapter import Adapter
from test_browser import HTML

ROOT = Path(__file__).resolve().parent
CONFIGS = json.loads((ROOT / 'adapters.json').read_text(encoding='utf-8'))


def control_html(platform):
    if platform == 'deepseek':
        return """<div class="ds-toggle-button" aria-pressed="false" onclick="window.modeClicks++;this.setAttribute('aria-pressed',this.getAttribute('aria-pressed')==='true'?'false':'true')">深度思考</div><script>window.modeClicks=0</script>"""
    labels = {'doubao': '快速', 'yuanbao': '快速回答', 'qianwen': '快速'}
    attrs = {
        'doubao': 'data-testid="chat_input_action_model"',
        'yuanbao': 'data-thinking-mode-switcher-trigger="true"',
        'qianwen': 'aria-label="快速" aria-haspopup="menu"',
    }
    return f"""<div data-new-input-control="thinking-mode-switcher"><button id="mode" {attrs[platform]}>{labels[platform]}</button></div>
      <script>(()=>{{window.modeClicks=0;const mode=document.querySelector('#mode');mode.onclick=()=>{{
        window.modeClicks++;const panel=document.createElement('div');panel.setAttribute('role','menu');
        for(const label of ['深度思考','{labels[platform]}']){{const option=document.createElement('button');option.setAttribute('role','menuitemradio');
          const text=document.createElement('span');text.textContent=label;option.append(text);
          option.onclick=()=>{{window.modeClicks++;mode.textContent=label;mode.setAttribute('aria-label',label);panel.remove();}};panel.append(option);}}
        mode.parentElement.append(panel);
      }};}})();</script>"""


class ThinkingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='msedge', headless=True)
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_all_four_controls_on_off_default_and_no_duplicate_toggle(self):
        for platform in CONFIGS:
            with self.subTest(platform=platform):
                await self.page.set_content(control_html(platform))
                adapter = Adapter(self.page, CONFIGS[platform])
                default = await adapter.configure_thinking(None)
                self.assertIs(default['thinking_mode'], False)
                self.assertEqual(await self.page.evaluate('window.modeClicks'), 0)
                enabled = await adapter.configure_thinking(True)
                self.assertIs(enabled['thinking_mode'], True)
                self.assertEqual(enabled['thinking_mode_status'], 'verified')
                clicks = await self.page.evaluate('window.modeClicks')
                await adapter.configure_thinking(True)
                self.assertEqual(await self.page.evaluate('window.modeClicks'), clicks)
                disabled = await adapter.configure_thinking(False)
                self.assertIs(disabled['thinking_mode'], False)

    async def test_missing_controls_preserve_default_but_explicit_mode_stops(self):
        await self.page.set_content('<p>普通页面</p>')
        adapter = Adapter(self.page, {})
        self.assertEqual(
            (await adapter.configure_thinking(None))['thinking_mode_status'],
            'platform_default_unverified',
        )
        with self.assertRaises(CollectionError) as error:
            await adapter.configure_thinking(True)
        self.assertEqual(error.exception.code, 'THINKING_MODE_UNAVAILABLE')
        for bad in ['on', 1, 0, {}, []]:
            with self.assertRaises(ValueError):
                validate_thinking_mode(bad)

    async def test_ambiguous_or_disabled_menu_never_selects_an_option(self):
        for bad in ['duplicate', 'disabled']:
            await self.page.set_content(control_html('yuanbao'))
            await self.page.evaluate(
                """bad=>{const mode=document.querySelector('#mode');const open=mode.onclick;mode.onclick=()=>{
                open();const option=document.querySelector('[role=menuitemradio]');
                if(bad==='duplicate')option.parentElement.append(option.cloneNode(true));else option.setAttribute('aria-disabled','true');
            }}""",
                bad,
            )
            adapter = Adapter(self.page, CONFIGS['yuanbao'])
            with self.assertRaises(CollectionError):
                await adapter.configure_thinking(True)
            self.assertEqual(await self.page.locator('#mode').inner_text(), '快速回答')
            self.assertEqual(await self.page.evaluate('window.modeClicks'), 1)

    async def test_mode_drifting_during_input_stops_before_submission(self):
        html = HTML.replace('<div id="promotion"', control_html('deepseek') + '<div id="promotion"')
        html += """<script>document.querySelector('#input').addEventListener('input',()=>document.querySelector('.ds-toggle-button').setAttribute('aria-pressed','false'));</script>"""
        await self.page.route(
            'https://www.doubao.com/**',
            lambda route: route.fulfill(body=html, content_type='text/html'),
        )
        config = {
            key: '#' + key for key in ['input', 'send', 'question', 'answer', 'finished', 'busy']
        }
        config.update(
            new_chat='#new',
            dismiss_promotion_texts=['下次提醒我'],
            thinking=CONFIGS['deepseek']['thinking'],
        )
        stages = []

        async def report(stage, state=None):
            stages.append(stage)

        job = {'platform': 'doubao', 'request': {'question': '模拟测试', 'thinking_mode': True}}
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(CollectionError) as error:
                await run_in_page(self.page, job, report, Path(temp), config)
        self.assertEqual(error.exception.code, 'THINKING_MODE_UNVERIFIED')
        self.assertNotIn('submitting', stages)
        self.assertEqual(await self.page.locator('#question').count(), 0)

    async def test_last_click_rechecks_mode_after_an_earlier_confirmation(self):
        await self.page.set_content(
            control_html('deepseek')
            + '<button id="send" onclick="window.sends++">发送</button><script>window.sends=0</script>'
        )
        adapter = Adapter(
            self.page,
            {
                'thinking': CONFIGS['deepseek']['thinking'],
                'requested_thinking_mode': True,
                'send': '#send',
            },
        )
        await adapter.configure_thinking(True)
        await adapter.confirm_thinking(True)
        await self.page.locator('.ds-toggle-button').evaluate(
            'e=>e.setAttribute("aria-pressed","false")'
        )
        with self.assertRaises(CollectionError):
            await adapter.send()
        self.assertEqual(await self.page.evaluate('window.sends'), 0)

    async def test_successful_answer_keeps_verified_mode_in_checkpoint_and_result(self):
        html = HTML.replace('<div id="promotion"', control_html('deepseek') + '<div id="promotion"')
        await self.page.route(
            'https://www.doubao.com/**',
            lambda route: route.fulfill(body=html, content_type='text/html'),
        )
        config = {
            key: '#' + key for key in ['input', 'send', 'question', 'answer', 'finished', 'busy']
        }
        config.update(
            new_chat='#new',
            dismiss_promotion_texts=['下次提醒我'],
            thinking=CONFIGS['deepseek']['thinking'],
        )
        checkpoints = []

        async def report(stage, state=None):
            if state:
                checkpoints.append(state)

        job = {
            'job_id': 'local-thinking',
            'task_id': 'fixture',
            'platform': 'doubao',
            'request': {'question': '本地思考模式测试', 'thinking_mode': True},
        }
        with tempfile.TemporaryDirectory() as temp:
            result = await run_in_page(self.page, job, report, Path(temp), config)
        self.assertTrue(result['answer_complete'])
        self.assertIs(result['actual_options']['thinking_mode'], True)
        self.assertEqual(result['actual_options']['thinking_mode_status'], 'verified')
        self.assertIs(
            next(s for s in checkpoints if s.get('submission') == 'submission_uncertain')[
                'actual_options'
            ]['thinking_mode'],
            True,
        )
        self.assertEqual(await self.page.locator('#question').count(), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
