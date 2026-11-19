"""Real CDP owner/client lifecycle on isolated headless Edge profiles."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
from playwright.async_api import async_playwright
from collector.domain.errors import CollectionError
from collector.infrastructure.browser import runtime, sessions


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = patch.object(runtime, 'DATA', self.data)
        self.patch.start()
        self.contexts, self.owners = {}, []

    async def asyncTearDown(self):
        for key, context in self.contexts.items():
            if sessions.read_session(key):
                await sessions.close_platform(key)
            elif context.pages:
                await context.close()
        for task in self.owners:
            await asyncio.wait_for(task, timeout=10)
        self.patch.stop()
        self.temp.cleanup()

    async def start_owner(self, platform):
        async def launch(pw, key, **kwargs):
            context = await pw.chromium.launch_persistent_context(
                str(self.data / 'profiles' / key),
                channel='msedge',
                headless=True,
                no_viewport=True,
                chromium_sandbox=True,
                args=['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'],
            )
            self.contexts[key] = context
            return context

        task = asyncio.create_task(
            sessions.serve_session(
                platform, launch, 'data:text/html,<title>Local fixture</title><textarea></textarea>'
            )
        )
        self.owners.append(task)
        for _ in range(100):
            if task.done():
                await task
                raise AssertionError('owner exited before ready')
            if sessions.read_session(platform):
                return self.contexts[platform]
            await asyncio.sleep(0.05)
        raise AssertionError('owner did not become ready')

    async def test_two_clients_reuse_same_page_and_cookie_owner_survives(self):
        owner = await self.start_owner('doubao')
        identity = sessions.read_session('doubao')['session_id']
        async with async_playwright() as client:
            async with sessions.session_page(client, 'doubao') as (context, page):
                await page.goto(
                    'data:text/html,<title>Next local page</title><textarea></textarea>'
                )
                await page.evaluate("window.fixtureCount=7;document.title='新的回答'")
                await context.add_cookies(
                    [{'name': 'fixture', 'value': 'saved', 'url': 'https://example.com'}]
                )
                await asyncio.sleep(0.05)
                self.assertIn('【采集专用 · 豆包】', await page.title())
        self.assertEqual(len(owner.pages), 1)
        self.assertFalse(owner.pages[0].is_closed())
        async with async_playwright() as client:
            async with sessions.session_page(client, 'doubao') as (context, page):
                self.assertEqual(await page.evaluate('window.fixtureCount'), 7)
                cookies = await context.cookies('https://example.com')
                self.assertEqual(cookies[0]['value'], 'saved')
        self.assertEqual(sessions.read_session('doubao')['session_id'], identity)
        self.assertFalse(owner.pages[0].is_closed())

    async def test_platform_labels_and_profiles_are_independent(self):
        first = await self.start_owner('doubao')
        second = await self.start_owner('deepseek')
        self.assertIn('豆包', await first.pages[0].title())
        await first.pages[0].evaluate(
            "const title=document.createElement('title');title.textContent='替换标题';document.querySelector('title').replaceWith(title)"
        )
        await asyncio.sleep(0.05)
        self.assertIn('豆包', await first.pages[0].title())
        self.assertIn('DeepSeek', await second.pages[0].title())
        self.assertNotEqual(
            sessions.read_session('doubao')['endpoint'],
            sessions.read_session('deepseek')['endpoint'],
        )
        await first.pages[0].evaluate("document.title='collector-rpa-unique-token'")
        await asyncio.sleep(0.05)
        self.assertIn('collector-rpa-unique-token', await first.pages[0].title())
        self.assertIn('豆包', await first.pages[0].title())
        self.assertEqual(sessions.browser_statuses()['yuanbao'], '未打开')

    async def test_active_task_rejects_second_client_and_close(self):
        owner = await self.start_owner('doubao')
        with runtime.profile_lock('session-task-doubao'):
            with self.assertRaises(CollectionError) as error:
                await sessions.close_platform('doubao')
            self.assertEqual(error.exception.code, 'SESSION_BUSY')
            async with async_playwright() as client:
                with self.assertRaises(CollectionError):
                    async with sessions.session_page(client, 'doubao'):
                        self.fail('second client entered')
        self.assertFalse(owner.pages[0].is_closed())
        await sessions.close_platform('doubao')
        await asyncio.wait_for(self.owners[0], timeout=5)
        self.assertIsNone(sessions.read_session('doubao'))

    async def test_closed_primary_tab_never_selects_another_tab(self):
        owner = await self.start_owner('doubao')
        other = await owner.new_page()
        await owner.pages[0].close()
        async with async_playwright() as client:
            with self.assertRaises(CollectionError) as error:
                await sessions.connect_session(client, 'doubao')
            self.assertEqual(error.exception.code, 'SESSION_TAB_CLOSED')
        self.assertFalse(other.is_closed())
        await sessions.close_platform('doubao')
        await asyncio.wait_for(self.owners[0], timeout=5)

    async def test_discovery_rejects_stale_pid_and_nonlocal_endpoint(self):
        await self.start_owner('doubao')
        path = sessions.session_file('doubao')
        value = json.loads(path.read_text(encoding='utf-8'))
        self.assertIsNotNone(sessions.process_stamp(os.getpid()))
        for changed in [
            value | {'owner_stamp': 'stale'},
            value | {'endpoint': 'ws://example.com:9222/devtools/browser/abc'},
            value | {'owner_pid': -1, 'owner_stamp': None},
        ]:
            path.write_text(json.dumps(changed), encoding='utf-8')
            self.assertIsNone(sessions.read_session('doubao'))
        path.write_text(json.dumps(value), encoding='utf-8')

    async def test_collection_error_keeps_context_and_original_exception(self):
        from collector.collection_bootstrap import collect

        config = self.data / 'adapters.json'
        config.write_text('{"doubao":{"verified":false}}', encoding='utf-8')
        context, page = Mock(), Mock()
        context.close = AsyncMock()
        report = AsyncMock()
        error = CollectionError('FIXTURE', 'no actual input')
        disconnected = []

        @asynccontextmanager
        async def connection(*args):
            try:
                yield context, page
            finally:
                disconnected.append(True)

        with (
            patch.dict(os.environ, {'COLLECTOR_ADAPTERS': str(config)}),
            patch.object(sessions, 'session_page', connection),
            patch('collector.collection_bootstrap.run_in_page', AsyncMock(side_effect=error)),
            patch(
                'collector.collection_bootstrap.record_browser_failure',
                AsyncMock(return_value={'code': 'FIXTURE', 'message': 'no actual input'}),
            ),
        ):
            with self.assertRaises(CollectionError) as caught:
                await collect(
                    {'platform': 'doubao', 'request': {'interaction_mode': 'browser'}},
                    report,
                    self.data,
                )
        self.assertIs(caught.exception, error)
        context.close.assert_not_awaited()
        self.assertTrue(disconnected)
        self.assertEqual(report.await_args.args[1]['browser_error']['retained'], 'resident')


async def process_probe(data, expected):
    runtime.DATA = Path(data)
    async with async_playwright() as client:
        async with sessions.session_page(client, 'doubao') as (_, page):
            count = await page.evaluate('window.fixtureCount||0')
            assert count == int(expected), count
            await page.evaluate('n=>window.fixtureCount=n', count + 1)
            assert '豆包' in await page.title()


async def packaged_session_test(executable):
    import subprocess
    import sys

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        data = home / 'data'
        previous = runtime.DATA
        runtime.DATA = data
        env = dict(os.environ, COLLECTOR_CLIENT_HOME=str(home), COLLECTOR_SESSION_DATA=str(data))
        with (home / 'owner-test.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(
                [str(executable), '--browser-host', 'doubao'],
                env=env,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            try:
                for _ in range(300):
                    state = sessions.read_session('doubao')
                    if state:
                        break
                    assert process.poll() is None, 'owner exited; inspect test logs'
                    await asyncio.sleep(0.1)
                assert state, 'owner not ready'
                identity = state['session_id']
                for expected in (0, 1):
                    await asyncio.to_thread(
                        subprocess.run,
                        [
                            sys.executable,
                            str(Path(__file__).resolve()),
                            '--probe',
                            str(data),
                            str(expected),
                        ],
                        check=True,
                        timeout=30,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                    )
                    assert process.poll() is None
                    assert sessions.read_session('doubao')['session_id'] == identity
                await sessions.close_platform('doubao')
                assert await asyncio.to_thread(process.wait, timeout=20) == 0
            finally:
                try:
                    if process.poll() is None:
                        await sessions.close_platform('doubao')
                        await asyncio.to_thread(process.wait, timeout=15)
                finally:
                    if process.poll() is None:
                        subprocess.run(
                            ['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True
                        )
                    runtime.DATA = previous
        print(
            'PASS: frozen browser owner survives two separate worker processes, same page/session; explicit close. Local landing page only.'
        )


if __name__ == '__main__':
    import sys

    if '--probe' in sys.argv:
        asyncio.run(process_probe(sys.argv[2], sys.argv[3]))
    elif '--exe' in sys.argv:
        asyncio.run(packaged_session_test(Path(sys.argv[2]).resolve()))
    else:
        unittest.main(verbosity=2)
