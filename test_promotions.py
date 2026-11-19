"""Promotion recognition and sandbox checks on isolated pages only."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock
from playwright.async_api import async_playwright
from collector.domain.errors import CollectionError
from collector.infrastructure.browser.adapter import Adapter
from collector.infrastructure.browser import runtime

CONFIG = {'promotion_dialogs': [{'texts': ['每日签到', '豆包工作', '立即下载电脑版']}]}
HTML = """<style>.modal{position:fixed;z-index:20;top:30px;left:30px;width:500px;height:300px;background:white}
.close{position:absolute;right:10px;top:10px;width:28px;height:28px}</style>
<div class="modal" role="dialog"><div class="modal-content"><h2>每日签到，免费用豆包工作</h2>
<button id="download" onclick="window.downloads++">立即下载电脑版</button></div>
<button class="close" aria-label="关闭" onclick="window.closes++;this.closest('.modal').remove()">×</button></div>
<script>window.downloads=0;window.closes=0</script>"""


class PromotionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            channel='msedge', headless=True, chromium_sandbox=True
        )
        self.page = await self.browser.new_page()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_only_close_known_dialog_not_download(self):
        await self.page.set_content(HTML)
        adapter = Adapter(self.page, CONFIG)
        await adapter.dismiss_known_overlays()
        self.assertEqual(await self.page.evaluate('window.closes'), 1)
        self.assertEqual(await self.page.evaluate('window.downloads'), 0)
        self.assertEqual(await self.page.locator('[role=dialog]').count(), 0)

    async def test_desktop_mode_uses_injected_actions(self):
        await self.page.set_content(HTML)
        adapter = Adapter(self.page, CONFIG | {'interaction_mode': 'desktop_rpa'})
        clicks = []

        async def click(locator):
            clicks.append(await locator.get_attribute('aria-label'))
            await locator.click()

        adapter.actions.click = click
        await adapter.dismiss_known_overlays()
        self.assertEqual(clicks, ['关闭'])

    async def test_unknown_close_waits_for_manual_and_never_clicks(self):
        await self.page.set_content(
            HTML.replace('class="close" aria-label="关闭"', 'class="unknown"').replace(
                '>×</button>', '>?</button>'
            )
        )
        events = []
        waiting = asyncio.Event()

        async def report(stage, checkpoint=None):
            events.append(stage)
            if stage == 'waiting_overlay':
                waiting.set()

        adapter = Adapter(self.page, CONFIG, report)
        adapter.promotions.timeout = 2
        adapter.promotions.interval = 0.01
        adapter.actions.click = AsyncMock(side_effect=AssertionError('must not click'))

        async def manual():
            await asyncio.wait_for(waiting.wait(), timeout=3)
            await self.page.locator('.modal').evaluate('e=>e.remove()')

        task = asyncio.create_task(manual())
        await adapter.dismiss_known_overlays()
        await task
        self.assertEqual(events, ['waiting_overlay', 'resuming'])
        self.assertEqual(await self.page.evaluate('window.downloads'), 0)

    async def test_answer_text_and_nonmodal_not_dismissed(self):
        for html in [
            HTML.replace('role="dialog"', '').replace(
                'position:fixed;z-index:20;', 'position:static;'
            ),
            '<article>' + HTML + '</article>',
        ]:
            await self.page.set_content(html)
            adapter = Adapter(self.page, CONFIG)
            adapter.actions.click = AsyncMock(side_effect=AssertionError('must not click'))
            await adapter.dismiss_known_overlays()
            self.assertEqual(await self.page.evaluate('window.closes'), 0)

    async def test_download_named_like_close_never_clicked(self):
        await self.page.set_content(
            HTML.replace('id="download"', 'id="download" class="close"')
            .replace(
                '<button class="close" aria-label="关闭"', '<button class="other" aria-label="未知"'
            )
            .replace('>×</button>', '>?</button>')
        )
        adapter = Adapter(self.page, CONFIG)
        adapter.promotions.timeout = 0.05
        adapter.promotions.interval = 0.01
        adapter.actions.click = AsyncMock(side_effect=AssertionError('must not click download'))
        with self.assertRaises(CollectionError) as error:
            await adapter.dismiss_known_overlays()
        self.assertEqual(error.exception.code, 'PROMOTION_BLOCKED')
        adapter.actions.click.assert_not_called()

    async def test_captcha_pauses_before_any_promotion_click(self):
        await self.page.set_content(HTML)
        adapter = Adapter(self.page, CONFIG)
        adapter.human_gate.wait_if_needed = AsyncMock(
            side_effect=CollectionError('HUMAN_VERIFICATION', 'fixture')
        )
        adapter.actions.click = AsyncMock()
        with self.assertRaises(CollectionError):
            await adapter.dismiss_known_overlays()
        adapter.actions.click.assert_not_called()

    async def test_production_sandbox_configuration_and_real_command_line(self):
        pw = Mock()
        pw.chromium.launch_persistent_context = AsyncMock()
        await runtime.open_context(pw, 'fixture')
        self.assertTrue(pw.chromium.launch_persistent_context.call_args.kwargs['chromium_sandbox'])
        await self.page.goto('edge://version')
        command = await self.page.locator('#command_line').inner_text()
        self.assertNotIn('--no-sandbox', command)


if __name__ == '__main__':
    unittest.main(verbosity=2)
