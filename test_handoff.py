"""Reproduce remote login/capture failures locally, with no external requests."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from playwright.async_api import async_playwright
from collector.domain.errors import CollectionError
from collector.infrastructure.browser.adapter import Adapter
from collector.infrastructure.browser.attention import request_attention
from collector.infrastructure.browser.human_gate import HumanGate
from collector.infrastructure.evidence.capture import capture_segments


class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='msedge', headless=True)
        self.page = await self.browser.new_page(viewport={'width': 900, 'height': 700})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_login_is_exposed_and_manual_completion_resumes_without_input(self):
        await self.page.route(
            'https://chat.deepseek.com/**',
            lambda route: route.fulfill(
                body='<script>window.inputs=0;document.addEventListener("input",()=>window.inputs++)</script><p>本地登录占位</p>',
                content_type='text/html',
            ),
        )
        await self.page.goto('https://chat.deepseek.com/sign_in')
        stages = []
        waiting = asyncio.Event()

        async def report(stage, state=None):
            stages.append(stage)
            if stage == 'waiting_login':
                waiting.set()

        adapter = Adapter(self.page, {}, report)
        adapter.login_poll_interval = 0.01
        with patch(
            'collector.infrastructure.browser.attention.request_attention', wraps=request_attention
        ) as attention:
            task = asyncio.create_task(adapter.check_login('deepseek'))
            await asyncio.wait_for(waiting.wait(), 2)
            await asyncio.sleep(0.1)
            self.assertFalse(task.done())
            self.assertEqual(await self.page.evaluate('window.inputs'), 0)
            # This routed local page transition simulates manual login completion.
            await self.page.goto('https://chat.deepseek.com/')
            await asyncio.wait_for(task, 2)
            attention.assert_awaited_once()
        self.assertEqual(stages, ['waiting_login', 'resuming'])

    async def test_login_timeout_keeps_page_and_does_not_submit(self):
        await self.page.set_content(
            '<button id="login">登录</button><input><script>window.inputs=0;document.addEventListener("input",()=>window.inputs++)</script>'
        )
        adapter = Adapter(self.page, {'login_required': '#login'})
        adapter.login_timeout = 0.04
        adapter.login_poll_interval = 0.01
        with self.assertRaises(CollectionError) as error:
            await adapter.check_login('yuanbao')
        self.assertEqual(error.exception.code, 'LOGIN_REQUIRED')
        self.assertFalse(self.page.is_closed())
        self.assertEqual(await self.page.evaluate('window.inputs'), 0)

    async def test_real_visible_challenge_is_exposed_but_never_solved(self):
        await self.page.set_content(
            '<div id="captcha_container" style="width:300px;height:200px">请拖动滑块完成验证</div><script>window.clicks=0;document.addEventListener("click",()=>window.clicks++)</script>'
        )
        gate = HumanGate(self.page, {}, timeout=0.04, poll_interval=0.01)
        with patch(
            'collector.infrastructure.browser.attention.request_attention', wraps=request_attention
        ) as attention:
            with self.assertRaises(CollectionError):
                await gate.wait_if_needed()
            attention.assert_awaited_once()
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)
        self.assertFalse(self.page.is_closed())

    async def test_question_hidden_by_fixed_header_is_recaptured_with_real_pixels(self):
        await self.page.set_content("""<style>body{margin:0;padding:1000px 0}
          #question{width:192px;height:24px;background:rgb(0,200,100)}
          header{position:fixed;top:0;left:0;width:100%;height:240px;background:black}</style>
          <div id="question">问题</div><header></header>""")
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            files, complete = await capture_segments(
                self.page, self.page.locator('#question'), folder, 'question'
            )
            self.assertTrue(complete)
            self.assertEqual(len(files), 1)
            with Image.open(folder / files[0]['name']) as tile:
                self.assertEqual(tile.size, (192, 24))
                self.assertEqual(tile.getpixel((180, 12))[:3], (0, 200, 100))


if __name__ == '__main__':
    unittest.main(verbosity=2)
