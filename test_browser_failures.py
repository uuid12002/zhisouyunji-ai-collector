"""Failure retention and hidden-DOM regressions; no desktop input or real prompts."""

import asyncio
from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from collector.domain.errors import CollectionError
from collector.infrastructure.browser.diagnostics import (
    record_browser_failure,
    retain_failed_browser,
)


class BrowserFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_rpa_runtime_disables_default_viewport(self):
        from collector.infrastructure.browser.runtime import open_context

        pw = Mock()
        pw.chromium.launch_persistent_context = AsyncMock()
        await open_context(pw, 'fixture', desktop_rpa=True)
        options = pw.chromium.launch_persistent_context.call_args.kwargs
        self.assertIs(options['no_viewport'], True)
        self.assertNotIn('viewport', options)

    async def test_browser_runtime_keeps_existing_viewport(self):
        from collector.infrastructure.browser.runtime import open_context

        pw = Mock()
        pw.chromium.launch_persistent_context = AsyncMock()
        await open_context(pw, 'fixture')
        options = pw.chromium.launch_persistent_context.call_args.kwargs
        self.assertEqual(options['viewport'], {'width': 1440, 'height': 1000})
        self.assertNotIn('no_viewport', options)

    async def test_record_before_retention_even_if_screenshot_fails(self):
        page = Mock()
        page.url = 'https://www.doubao.com/chat/saved-session'
        page.is_closed.return_value = False
        page.screenshot = AsyncMock(side_effect=RuntimeError('screenshot unavailable'))
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            error = CollectionError('RPA_FOCUS_LOST', '失去焦点，未发送')
            await record_browser_failure(
                page, folder, error, 'doubao', 'desktop_rpa', 'opening_chat'
            )
            record = json.loads((folder / 'diagnostic.json').read_text(encoding='utf-8'))
            self.assertEqual(record['code'], 'RPA_FOCUS_LOST')
            self.assertEqual(record['stage'], 'opening_chat')
            self.assertIn('CollectionError', record['traceback'])

    async def test_retention_waits_for_user_close_and_never_inputs(self):
        page = Mock()
        context = Mock()
        context.pages = [page]
        reports = []

        async def report(stage, state=None):
            reports.append((stage, state))

        task = asyncio.create_task(
            retain_failed_browser(
                context,
                report,
                {'code': 'RPA_FOCUS_LOST', 'message': 'focus lost'},
                timeout=1,
                poll_interval=0.01,
            )
        )
        await asyncio.sleep(0.03)
        self.assertFalse(task.done())
        self.assertEqual(reports[0][0], 'browser_error')
        self.assertEqual(reports[0][1]['browser_error']['code'], 'RPA_FOCUS_LOST')
        context.close.assert_not_called()
        page.assert_not_called()
        context.pages.clear()  # User closes their own window.
        await task
        context.close.assert_not_called()

    async def test_retention_is_bounded(self):
        context = Mock()
        context.pages = [Mock()]
        await retain_failed_browser(
            context,
            AsyncMock(),
            {'code': 'test', 'message': 'test'},
            timeout=0.02,
            poll_interval=0.005,
        )
        context.close.assert_not_called()

    async def test_collect_persists_and_notifies_before_close(self):
        from collector.collection_bootstrap import collect

        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            config = folder / 'adapters.json'
            config.write_text('{"doubao":{}}', encoding='utf-8')
            # Non-empty configuration, since a missing platform is a separate error.
            config.write_text('{"doubao":{"verified":false}}', encoding='utf-8')
            page = Mock()
            page.url = 'https://www.doubao.com/chat/saved-session'
            page.is_closed.return_value = False
            page.screenshot = AsyncMock()
            context = Mock()
            context.pages = [page]
            context.close = AsyncMock()
            manager = Mock()
            manager.__aenter__ = AsyncMock(return_value=Mock())
            manager.__aexit__ = AsyncMock(return_value=False)
            stages = []

            async def report(stage, state=None):
                stages.append(stage)
                if stage == 'browser_error':
                    self.assertTrue((folder / 'diagnostic.json').exists())
                    context.close.assert_not_awaited()
                    self.assertEqual(state['browser_error']['code'], 'RPA_COORDINATES')
                    context.pages.clear()

            error = CollectionError('RPA_COORDINATES', '内容区无法校准，未点击')
            with (
                patch.dict('os.environ', {'COLLECTOR_ADAPTERS': str(config)}),
                patch('collector.collection_bootstrap.profile_lock', return_value=nullcontext()),
                patch('collector.collection_bootstrap.async_playwright', return_value=manager),
                patch(
                    'collector.collection_bootstrap.open_context', AsyncMock(return_value=context)
                ),
                patch('collector.collection_bootstrap.run_in_page', AsyncMock(side_effect=error)),
            ):
                with self.assertRaises(CollectionError) as caught:
                    await collect(
                        {'platform': 'doubao', 'request': {'interaction_mode': 'desktop_rpa'}},
                        report,
                        folder,
                        resident=False,
                    )
            self.assertIs(caught.exception, error)
            self.assertEqual(stages, ['browser_error'])
            context.close.assert_awaited_once()


async def dom_regression():
    from playwright.async_api import async_playwright
    from collector.infrastructure.browser.adapter import Adapter

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("""<div class="question" hidden>历史问题</div>
                <div class="answer" hidden>历史回答</div><div class="answer"></div>
                <input class="input" hidden><textarea class="input"></textarea>""")
            adapter = Adapter(
                page, {'question': '.question', 'answer': '.answer', 'input': '.input'}
            )
            self_check = await adapter.has_messages()
            assert not self_check, 'Hidden history and empty placeholders cannot fail a new chat'
            field = await adapter.one('input')
            assert await field.evaluate('e=>e.tagName') == 'TEXTAREA'
            await page.evaluate("()=>{document.querySelector('.question').hidden=false}")
            assert await adapter.has_messages(), 'Visible history must still prevent submission'
        finally:
            await browser.close()
    print(
        'PASS: hidden messages, empty placeholders and hidden duplicate inputs; visible history remains protected. Headless fixture only.'
    )


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(BrowserFailureTests)
    )
    if not result.wasSuccessful():
        raise SystemExit(1)
    asyncio.run(dom_regression())
