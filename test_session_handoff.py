"""Regression tests for consecutive jobs and reconnect recovery; isolated fixtures."""
import asyncio
from contextlib import nullcontext
import unittest
from unittest.mock import AsyncMock, Mock, patch
from playwright.async_api import async_playwright
from collector.infrastructure.browser.adapter import Adapter
from collector.infrastructure.socket_worker import run_socket_worker


class NewChatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(channel='msedge', headless=True)
        self.page = await self.browser.new_page()
        self.adapter = Adapter(self.page, {'input': 'textarea', 'question': '.question',
                                          'answer': '.answer', 'new_chat': '#new'})

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def test_empty_landing_without_button(self):
        await self.page.set_content('<textarea></textarea>')
        await self.adapter.new_chat()

    async def test_two_jobs_clear_previous_answer_after_delayed_transition(self):
        await self.page.set_content('''<textarea></textarea><div class="answer">old</div>
          <button id="new" onclick="setTimeout(()=>document.querySelector('.answer').remove(),1800)">New</button>''')
        await self.adapter.new_chat()
        self.assertFalse(await self.adapter.has_messages())
        await self.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div class=answer>second</div>')")
        await self.adapter.new_chat()
        self.assertFalse(await self.adapter.has_messages())

    async def test_history_or_draft_never_passes_empty_check(self):
        await self.page.set_content('<textarea></textarea><div class="answer">old</div>')
        self.assertFalse(await self.adapter.wait_empty_chat(.2))
        await self.page.set_content('<textarea>unsent draft</textarea>')
        self.assertFalse(await self.adapter.wait_empty_chat(.2))


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_lease_executes_then_next_job_runs(self):
        state = Mock(process_id=1, run_id='test')
        stopped = False
        state.stop_requested.side_effect = lambda: stopped
        client = Mock(jobs=asyncio.Queue())
        client.receive = AsyncMock(side_effect=lambda: None)
        async def receive():
            await asyncio.Future()
        client.receive = receive
        first = {'job_id': 'first', 'checkpoint': {'submission': 'submitted'}}
        second = {'job_id': 'second'}
        calls = []
        async def request(op, body):
            if not calls:
                return {'jobs': [first]}
            if calls == ['first']:
                await client.jobs.put({'type': 'job', 'job': second})
            return {'jobs': []}
        client.request = request
        async def execute(connection, job):
            nonlocal stopped
            calls.append(job['job_id'])
            if job is second:
                stopped = True
        class Connection:
            async def __aenter__(self): return object()
            async def __aexit__(self, *args): pass
        with patch('collector.infrastructure.socket_worker.connect', return_value=Connection()), \
             patch('collector.infrastructure.socket_worker.SocketClient', return_value=client):
            await asyncio.wait_for(run_socket_worker(
                {'server_url': 'https://fixture.invalid', 'device_id': 'test', 'device_key': 'test', 'platforms': ['deepseek']},
                state, lambda: 20, execute, nullcontext()), 5)
        self.assertEqual(calls, ['first', 'second'])


if __name__ == '__main__':
    unittest.main()
