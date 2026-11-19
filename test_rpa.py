"""Desktop RPA guards and optional actual SendInput against an isolated fixture.

Run --live only on an unlocked Windows desktop. It moves the physical cursor and
types into its own temporary Edge profile; no real platform question is sent.
"""

import argparse
import asyncio
import ctypes
from pathlib import Path
import tempfile
import subprocess
import sys
import os
import unittest
from unittest.mock import Mock, patch, AsyncMock

from collector.domain.errors import CollectionError
from collector.domain.interaction import validate_interaction_mode
from collector.infrastructure.browser.native_windows import (
    WindowsDesktop,
    Input,
    unicode_inputs,
)


class NativeGuardTests(unittest.TestCase):
    def test_native_mutex_excludes_other_collectors_and_releases(self):
        first, second = WindowsDesktop(), WindowsDesktop()
        try:
            first.acquire_desktop()
            script = """from collector.infrastructure.browser.native_windows import WindowsDesktop
from collector.domain.errors import CollectionError
try:
    WindowsDesktop().acquire_desktop()
except CollectionError as exc:
    assert exc.code == 'RPA_DESKTOP_BUSY'
else:
    raise AssertionError('Cross-process desktop lock was not held')
"""
            process = subprocess.run(
                [sys.executable, '-c', script],
                cwd=Path(__file__).parent,
                capture_output=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            with self.assertRaises(CollectionError) as caught:
                second.acquire_desktop()
            self.assertEqual(caught.exception.code, 'RPA_DESKTOP_BUSY')
            first.close()
            second.acquire_desktop()
        finally:
            first.close()
            second.close()

    def test_partial_input_releases_without_retry(self):
        desktop = WindowsDesktop.__new__(WindowsDesktop)
        desktop.api = Mock()
        desktop.ensure_target = Mock()
        desktop.sent_events = 0
        desktop.api.SendInput.side_effect = [1, 1]
        with self.assertRaises(CollectionError) as caught:
            desktop.chord(0x11, 0x41)
        self.assertEqual(caught.exception.code, 'RPA_INPUT_BLOCKED')
        self.assertEqual(desktop.api.SendInput.call_count, 2)
        cleanup = desktop.api.SendInput.call_args.args[1]
        self.assertTrue(all(event.ki.dwFlags & 2 for event in cleanup))

    def test_unicode_and_native_struct_size(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        events = unicode_inputs('中A😀')
        self.assertEqual([v.ki.wScan for v in events[::2]], [0x4E2D, 65, 0xD83D, 0xDE00])
        self.assertEqual([v.ki.dwFlags for v in events], [4, 6] * 4)

    def test_foreground_loss_sends_nothing(self):
        desktop = WindowsDesktop.__new__(WindowsDesktop)
        desktop.api = Mock()
        desktop.hwnd, desktop.pid = 123, 456
        desktop.ensure_desktop = Mock()
        desktop.process_id = Mock(return_value=456)
        desktop.api.IsWindow.return_value = True
        desktop.api.GetForegroundWindow.return_value = 999
        with self.assertRaises(CollectionError) as caught:
            desktop.type_text('must never reach another window')
        self.assertEqual(caught.exception.code, 'RPA_FOCUS_LOST')
        desktop.api.SendInput.assert_not_called()

    def test_escape_sends_nothing(self):
        desktop = WindowsDesktop.__new__(WindowsDesktop)
        desktop.api = Mock()
        desktop.hwnd, desktop.pid = 123, 456
        desktop.ensure_desktop = Mock()
        desktop.process_id = Mock(return_value=456)
        desktop.api.IsWindow.return_value = True
        desktop.api.GetForegroundWindow.return_value = 123
        desktop.api.IsIconic.return_value = False
        desktop.api.GetAsyncKeyState.side_effect = lambda key: 0x8000 if key == 0x1B else 0
        with self.assertRaises(CollectionError) as caught:
            desktop.chord(0x11, 0x41)
        self.assertEqual(caught.exception.code, 'RPA_CANCELLED')
        desktop.api.SendInput.assert_not_called()

    def test_mode_validation(self):
        self.assertEqual(validate_interaction_mode('desktop_rpa'), 'desktop_rpa')
        with self.assertRaises(ValueError):
            validate_interaction_mode('silently-fallback')


class DesktopInteractionTests(unittest.IsolatedAsyncioTestCase):
    def actions(self, hit=True):
        from collector.infrastructure.browser.interaction import DesktopActions

        desktop = Mock()
        desktop.renderer_rect.return_value = (-1920, 40, 1600, 1200)
        gate = Mock()
        gate.wait_if_needed = AsyncMock(return_value=0)
        actions = DesktopActions(Mock(), {}, gate, desktop)
        actions.bind = AsyncMock()
        locator = Mock()
        locator.count = AsyncMock(return_value=1)
        locator.is_visible = AsyncMock(return_value=True)
        locator.is_enabled = AsyncMock(return_value=True)
        locator.scroll_into_view_if_needed = AsyncMock()
        locator.evaluate = AsyncMock(
            return_value={
                'x': 100,
                'y': 80,
                'w': 800,
                'h': 600,
                'scale': 1,
                'hit': hit,
            }
        )
        return actions, locator, desktop

    async def test_scaling_and_negative_monitor_origin(self):
        actions, locator, desktop = self.actions()
        await actions.click(locator)
        desktop.click.assert_called_once_with(-1720, 200)
        locator.click.assert_not_called()

    async def test_page_overlay_sends_no_click(self):
        actions, locator, desktop = self.actions(hit=False)
        with self.assertRaises(CollectionError) as caught:
            await actions.click(locator)
        self.assertEqual(caught.exception.code, 'RPA_TARGET_CHANGED')
        desktop.click.assert_not_called()

    async def test_failed_send_preflight_sends_no_native_click(self):
        actions, locator, desktop = self.actions()
        check = AsyncMock(side_effect=CollectionError('THINKING_MODE_UNVERIFIED', '模式已改变'))
        with self.assertRaises(CollectionError):
            await actions.click(locator, preflight=check)
        check.assert_awaited_once()
        desktop.click.assert_not_called()

    async def test_editor_focus_loss_sends_no_keys(self):
        actions, locator, desktop = self.actions()
        locator.evaluate.return_value = False
        with self.assertRaises(CollectionError) as caught:
            await actions.ensure_editor(locator)
        self.assertEqual(caught.exception.code, 'RPA_EDITOR_FOCUS_LOST')
        desktop.chord.assert_not_called()
        desktop.type_text.assert_not_called()

    async def test_control_characters_stop_before_input(self):
        actions, locator, desktop = self.actions()
        with self.assertRaises(CollectionError) as caught:
            await actions.fill(locator, 'unsafe\x00text')
        self.assertEqual(caught.exception.code, 'RPA_UNSUPPORTED_TEXT')
        desktop.click.assert_not_called()


async def live_fixture():
    from playwright.async_api import async_playwright, Locator
    from collector.collection_bootstrap import run_in_page
    from collector.infrastructure.browser.runtime import open_context

    stages = []

    async def report(stage, checkpoint=None):
        stages.append(stage)
        print('RPA fixture stage: ' + stage, flush=True)

    question = '桌面 RPA 测试 English 123 😀\n第二行：只发一次'
    async with async_playwright() as pw:
        with tempfile.TemporaryDirectory(prefix='collector-rpa-fixture-') as temp:
            with (
                patch('collector.infrastructure.browser.runtime.DATA', Path(temp)),
                patch.dict(os.environ, {'COLLECTOR_BROWSER': 'msedge'}),
            ):
                context = await open_context(pw, 'rpa-fixture', desktop_rpa=True)
            page = context.pages[0]
            try:
                # Preserve the clipboard if it still contains our own fixture URL.
                baseline = None
                last_share = None
                for platform, host, path in (
                    ('doubao', 'www.doubao.com', 'thread/rpa-fixture'),
                    ('deepseek', 'chat.deepseek.com', 'share/rpa-fixture'),
                ):
                    share_url = f'https://{host}/{path}'
                    document = """<!doctype html><meta charset="utf-8"><title>本地 RPA 测试</title>
                    <style>body{margin:30px;font:18px sans-serif}textarea{width:70%;height:100px}button{padding:12px;margin:8px}#answer{height:260px;background:#e2ffe9}section{position:fixed;top:100px;left:100px;background:white;padding:20px;border:2px solid black}</style>
                    <button id="new">新对话</button><br><textarea id="input"></textarea><button id="send">发送</button>
                    <pre id="asked" hidden></pre><article id="answer" hidden></article><button id="finished" hidden>完成</button><button id="share" hidden>分享</button>
                    <script>window.sends=0;window.trustedClicks=0;
                    document.addEventListener('click', e=>{if(e.isTrusted)window.trustedClicks++});
                    document.querySelector('#send').onclick=()=>{window.sends++;
                        document.querySelector('#asked').textContent=document.querySelector('#input').value;
                        document.querySelector('#answer').textContent='本地测试回答，包含中文、English 和完整截图。';
                        for(const id of ['asked','answer','finished','share'])document.getElementById(id).hidden=false;};
                    document.querySelector('#share').onclick=()=>{
                        const dialog=document.createElement('section');
                        dialog.innerHTML='<button id="create">创建分享链接</button>';
                        document.body.append(dialog);
                        document.querySelector('#create').onclick=()=>{dialog.innerHTML='<button id="copy">复制链接</button>';
                        document.querySelector('#copy').onclick=()=>navigator.clipboard.writeText(SHARE_URL);};};</script>""".replace(
                        'SHARE_URL', repr(share_url)
                    )

                    async def serve(route):
                        await route.fulfill(body=document, content_type='text/html; charset=utf-8')

                    await page.unroute_all()
                    await page.route('**/*', serve)
                    await context.grant_permissions(
                        ['clipboard-read', 'clipboard-write'], origin='http://127.0.0.1:19371'
                    )
                    await page.goto(f'http://127.0.0.1:19371/{platform}')
                    if baseline is None:
                        baseline = await page.evaluate('navigator.clipboard.readText()')
                    folder = Path(temp) / platform
                    folder.mkdir()
                    config = {
                        'new_chat': '#new',
                        'input': '#input',
                        'send': '#send',
                        'question': '#asked:visible',
                        'answer': '#answer:visible',
                        'finished': '#finished',
                        'share_button': '#share',
                        'share_create': '#create',
                        'share_copy': '#copy',
                        'share_hosts': [host],
                        'verified': False,
                    }
                    job = {
                        'job_id': 'local-rpa-fixture',
                        'task_id': 'local-rpa-fixture',
                        'platform': platform,
                        'request': {'question': question, 'interaction_mode': 'desktop_rpa'},
                    }
                    # All core clicks and input must use the native backend. A fallback
                    # to locator click/fill/press should fail this end-to-end fixture.
                    with (
                        patch.dict(
                            'collector.collection_bootstrap.URLS',
                            {platform: f'http://127.0.0.1:19371/{platform}'},
                        ),
                        patch.object(
                            Locator, 'click', side_effect=AssertionError('Browser click fallback')
                        ),
                        patch.object(
                            Locator, 'fill', side_effect=AssertionError('Browser fill fallback')
                        ),
                        patch.object(
                            Locator, 'press', side_effect=AssertionError('Browser press fallback')
                        ),
                        patch.object(
                            Locator,
                            'press_sequentially',
                            side_effect=AssertionError('Browser keyboard fallback'),
                        ),
                    ):
                        result = await run_in_page(page, job, report, folder, config)
                    assert result['interaction_backend'] == 'windows_sendinput', result
                    assert result['share_url'] == share_url, result
                    assert result['long_image_complete'], result
                    assert await page.evaluate('window.sends') == 1
                    assert await page.locator('#asked').inner_text() == question
                    assert await page.evaluate('window.trustedClicks') >= 6
                    last_share = share_url
                    print(
                        f'PASS: {platform} native click/type/send/share-copy, Chinese/multiline/emoji, exactly one Send, full long image. LOCAL FIXTURE ONLY.',
                        flush=True,
                    )
                if (
                    baseline is not None
                    and await page.evaluate('navigator.clipboard.readText()') == last_share
                ):
                    await page.evaluate('(v)=>navigator.clipboard.writeText(v)', baseline)
            finally:
                await context.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    suite = unittest.TestSuite(
        [
            unittest.defaultTestLoader.loadTestsFromTestCase(NativeGuardTests),
            unittest.defaultTestLoader.loadTestsFromTestCase(DesktopInteractionTests),
        ]
    )
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
        raise SystemExit(1)
    if args.live:
        asyncio.run(live_fixture())
