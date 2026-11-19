"""Batch planning, durable execution, cancellation and Tk selection; no real questions."""

from contextlib import nullcontext, contextmanager
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.application.desktop import DesktopController
from collector.application.local_batch import run_local_batch
from collector.application.local_test import run_local_test
from collector.domain.batches import batch_items, parse_questions, validate_batch
from collector.domain.errors import CollectionError
from collector.infrastructure.local_batch import LocalBatchStore
from collector.infrastructure.local_test import LocalTestStore
from collector.presentation.batch_report import render_batch_report
from collector.presentation.report import render_report


class BatchPlanningTests(unittest.TestCase):
    def test_lines_blocks_and_limits(self):
        self.assertEqual(parse_questions('第一题\r\n\n第二题\n'), ['第一题', '第二题'])
        self.assertEqual(
            parse_questions('第一题\n补充说明\n\n第二题', 'blocks'), ['第一题\n补充说明', '第二题']
        )
        self.assertEqual(
            len(batch_items(['doubao', 'deepseek', 'yuanbao', 'qianwen'], ['一', '二'])), 8
        )
        self.assertEqual(
            validate_batch(['doubao', 'doubao'], ['重复', '重复']), (['doubao'], ['重复', '重复'])
        )
        for platforms, questions in [
            ([], ['一']),
            (['unknown'], ['一']),
            (['doubao'], []),
            (['doubao'], ['一'] * 101),
            (['doubao'], [' ']),
            (['doubao'], ['一' * 10001]),
        ]:
            with self.subTest(platforms=platforms, count=len(questions)):
                with self.assertRaises(ValueError):
                    validate_batch(platforms, questions)


class BatchExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.calls = []

    async def asyncTearDown(self):
        self.temp.cleanup()

    def store(self, platforms=None, questions=None):
        request = self.folder / 'request.json'
        request.write_text(
            json.dumps(
                {
                    'platforms': platforms or ['doubao', 'deepseek'],
                    'questions': questions or ['第一题', '第二题'],
                }
            ),
            encoding='utf-8',
        )
        return LocalBatchStore(request)

    async def collect(self, job, report, folder):
        self.calls.append((job['request']['question'], job['platform']))
        await report('submitting', {'submission': 'submission_uncertain'})
        await report('generating', {'submission': 'submitted'})
        return {
            'answer': '<script>fixture only</script>',
            'answer_complete': True,
            'exports_complete': True,
            'files': [],
            'references': [],
        }

    async def execute_batch(self, store, collect=None):
        async def item(path, progress):
            return await run_local_test(
                LocalTestStore(path),
                nullcontext(),
                collect or self.collect,
                render_report,
                progress,
            )

        return await run_local_batch(store, nullcontext(), item, render_batch_report)

    async def test_browser_failure_is_visible_before_collector_exits(self):
        store = self.store(['doubao'], ['question'])

        async def collect(job, report, folder):
            await report(
                'browser_error',
                {'browser_error': {'code': 'RPA_FOCUS_LOST', 'message': '请检查浏览器焦点'}},
            )
            current = json.loads((self.folder / 'status.json').read_text(encoding='utf-8'))
            self.assertEqual(current['items'][0]['stage'], 'browser_error')
            self.assertEqual(current['items'][0]['code'], 'RPA_FOCUS_LOST')
            self.assertEqual(current['items'][0]['error'], '请检查浏览器焦点')
            self.assertEqual(current['items'][0]['status'], 'running')
            self.assertTrue((self.folder / 'report.html').exists())
            raise CollectionError('RPA_FOCUS_LOST', '请检查浏览器焦点')

        state = await self.execute_batch(store, collect)
        self.assertEqual(state['status'], 'failed')
        self.assertEqual(state['items'][0]['code'], 'RPA_FOCUS_LOST')

    async def test_rpa_mode_propagates_only_to_supported_platforms(self):
        store = self.store(['doubao', 'deepseek', 'yuanbao', 'qianwen'], ['question'])
        store.request['interaction_mode'] = 'desktop_rpa'
        store.request['thinking_mode'] = False
        modes = {}

        async def collect(job, report, folder):
            self.assertIs(job['request']['thinking_mode'], False)
            modes[job['platform']] = job['request']['interaction_mode']
            return await self.collect(job, report, folder)

        state = await self.execute_batch(store, collect)
        self.assertEqual(
            modes,
            {
                'doubao': 'desktop_rpa',
                'deepseek': 'desktop_rpa',
                'yuanbao': 'browser',
                'qianwen': 'browser',
            },
        )
        self.assertEqual(state['status'], 'completed')

    async def test_four_platform_batch_continues_after_failure_and_partial_export(self):
        store = self.store(['doubao', 'deepseek', 'yuanbao', 'qianwen'], ['同一题'])

        async def collect(job, report, folder):
            platform = job['platform']
            self.calls.append(platform)
            if platform == 'deepseek':
                raise CollectionError('UI_CHANGED', '发送按钮不可见')
            if platform == 'qianwen':
                await report('waiting_human', {'human_verification': {'selector': '#challenge'}})
                current = json.loads((self.folder / 'status.json').read_text(encoding='utf-8'))
                item = current['items'][-1]
                self.assertEqual(item['stage'], 'waiting_human')
                self.assertIsInstance(item['stage_started_at'], float)
                await report('resuming')
            return {
                'answer': '本地测试全文', 'answer_complete': True,
                'exports_complete': platform != 'yuanbao', 'files': [],
                'limitations': ['截图不完整'] if platform == 'yuanbao' else [],
            }

        state = await self.execute_batch(store, collect)
        self.assertEqual(self.calls, ['doubao', 'deepseek', 'yuanbao', 'qianwen'])
        self.assertEqual([i['status'] for i in state['items']], ['completed', 'failed', 'partial', 'completed'])
        self.assertEqual(state['finished'], 4)
        self.assertEqual(state['items'][2]['code'], 'EXPORT_INCOMPLETE')
        self.assertTrue((self.folder / 'items/0001-yuanbao/result.json').exists())

    async def test_cross_product_separate_reports_and_no_rerun(self):
        store = self.store(['doubao', 'deepseek', 'yuanbao', 'qianwen'])
        state = await self.execute_batch(store)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['finished'], 8)
        self.assertEqual(
            self.calls[:4], [('第一题', p) for p in ['doubao', 'deepseek', 'yuanbao', 'qianwen']]
        )
        for item in state['items']:
            folder = self.folder / 'items' / item['id']
            self.assertTrue((folder / 'checkpoint.json').exists())
            self.assertTrue((folder / 'result.json').exists())
            self.assertIn('&lt;script&gt;', (folder / 'report.html').read_text(encoding='utf-8'))
        with self.assertRaises(RuntimeError):
            await self.execute_batch(store)
        self.assertEqual(len(self.calls), 8)

    async def test_human_failure_only_blocks_same_platform(self):
        async def collect(job, report, folder):
            if job['platform'] == 'doubao':
                self.calls.append((job['request']['question'], job['platform']))
                await report('submitting', {'submission': 'submission_uncertain'})
                raise CollectionError('SUBMISSION_UNCERTAIN', '人工核对原会话')
            return await self.collect(job, report, folder)

        state = await self.execute_batch(self.store(), collect)
        self.assertEqual(
            [item['status'] for item in state['items']],
            ['failed', 'completed', 'skipped', 'completed'],
        )
        self.assertEqual(len(self.calls), 3)
        checkpoint = json.loads((self.folder / 'items/0001-doubao/checkpoint.json').read_text())
        self.assertEqual(checkpoint['submission'], 'submission_uncertain')

    async def test_nonhuman_failure_allows_next_question(self):
        async def collect(job, report, folder):
            if job['request']['question'] == '第一题':
                raise CollectionError('EMPTY_ANSWER', 'fixture', human=False)
            return await self.collect(job, report, folder)

        state = await self.execute_batch(self.store(['doubao']), collect)
        self.assertEqual([item['status'] for item in state['items']], ['failed', 'completed'])

    async def test_stop_finishes_current_then_cancels_remaining(self):
        async def collect(job, report, folder):
            result = await self.collect(job, report, folder)
            (self.folder / 'stop.request').write_text('stop')
            return result

        state = await self.execute_batch(self.store(), collect)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            [item['status'] for item in state['items']],
            ['completed', 'cancelled', 'cancelled', 'cancelled'],
        )
        self.assertEqual(state['status'], 'stopped')

    async def test_partial_exports_are_not_claimed_complete(self):
        async def collect(job, report, folder):
            return (await self.collect(job, report, folder)) | {'exports_complete': False}

        state = await self.execute_batch(self.store(['doubao'], ['一题']), collect)
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['items'][0]['status'], 'partial')

    async def test_bootstrap_uses_one_global_lock(self):
        import collector.local_bootstrap as bootstrap

        locked = []

        @contextmanager
        def lock(name):
            self.assertEqual(name, 'worker-instance')
            locked.append(True)
            try:
                yield
            finally:
                locked.pop()

        async def collect(job, report, folder):
            self.assertEqual(locked, [True])
            return await self.collect(job, report, folder)

        self.store()
        with (
            patch.object(bootstrap, 'profile_lock', lock),
            patch.object(bootstrap, 'collect', collect),
        ):
            state = await bootstrap.run_batch(self.folder / 'request.json')
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(locked, [])


class BatchDialogTests(unittest.TestCase):
    def test_multi_selection_creates_one_batch_and_stop_signal(self):
        import tkinter as tk
        from tkinter import ttk
        from collector.infrastructure.client_runtime import ClientRuntime
        from collector.presentation.batch_dialog import show_batch_dialog

        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {'COLLECTOR_CLIENT_HOME': temp}),
        ):
            controller = DesktopController(ClientRuntime())
            root = tk.Tk()
            root.withdraw()
            launches = []

            class Process:
                def poll(self):
                    return None

            def launch(folder):
                launches.append(folder)
                return Process()

            try:
                dialog = show_batch_dialog(root, controller, lambda: False, launch)
                dialog.withdraw()

                def children(widget):
                    return [
                        child
                        for item in widget.winfo_children()
                        for child in [item, *children(item)]
                    ]

                widgets = children(dialog)
                buttons = {str(w.cget('text')): w for w in widgets if isinstance(w, ttk.Button)}
                operation_selector = next(
                    w
                    for w in widgets
                    if isinstance(w, ttk.Combobox)
                    and '桌面 RPA（豆包 / DeepSeek）' in w.cget('values')
                )
                operation_selector.set('桌面 RPA（豆包 / DeepSeek）')
                buttons['全选平台'].invoke()
                next(w for w in widgets if isinstance(w, tk.Text)).insert('1.0', '第一题\n第二题')
                buttons['开始批量采集'].invoke()
                self.assertEqual(len(launches), 1)
                request = json.loads((launches[0] / 'request.json').read_text(encoding='utf-8'))
                self.assertEqual(len(request['platforms']), 4)
                self.assertEqual(request['questions'], ['第一题', '第二题'])
                self.assertEqual(request['interaction_mode'], 'desktop_rpa')
                buttons['开始批量采集'].invoke()
                self.assertEqual(len(launches), 1)
                buttons['停止后续任务'].invoke()
                self.assertTrue((launches[0] / 'stop.request').exists())
            finally:
                root.destroy()


if __name__ == '__main__':
    unittest.main(verbosity=2)
