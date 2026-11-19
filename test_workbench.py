"""Workspace behavior and planned platform isolation; no real browser or input."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from collector.application.desktop import DesktopController
from collector.infrastructure.client_runtime import ClientRuntime
from collector.presentation.workbench import Workbench


class WorkbenchTests(unittest.TestCase):
    def test_active_platform_and_selected_error_do_not_get_mixed(self):
        from collector.presentation.batch_dialog import batch_progress, batch_error

        state = {
            'status': 'running',
            'finished': 3,
            'total': 4,
            'items': [
                {
                    'id': '0001-yuanbao',
                    'platform': 'yuanbao',
                    'status': 'failed',
                    'stage': 'stopped',
                    'code': 'EVIDENCE_INCOMPLETE',
                    'error': '截图失败',
                },
                {
                    'id': '0001-qianwen',
                    'platform': 'qianwen',
                    'status': 'running',
                    'stage': 'waiting_human',
                    'stage_started_at': 100,
                },
            ],
        }
        text = batch_progress(state, now=110)
        self.assertIn('当前：千问', text)
        self.assertIn('09:50', text)
        self.assertNotIn('元宝', text)
        self.assertNotIn('EVIDENCE_INCOMPLETE', batch_error(state))
        self.assertIn('选中项：元宝', batch_error(state, '0001-yuanbao'))
        self.assertNotIn('截图失败', batch_error(state, '0001-qianwen'))
        state['items'][-1].update(platform='deepseek', stage='waiting_login')
        self.assertIn('请在浏览器登录', batch_progress(state, now=110))
        self.assertIn('09:50', batch_progress(state, now=110))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'COLLECTOR_CLIENT_HOME': self.temp.name})
        self.env.start()
        self.runtime = ClientRuntime()
        self.controller = DesktopController(self.runtime)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_reserved_platform_persists_but_is_not_executable(self):
        value = {'id': 'future_ai', 'name': '未来平台', 'url': 'https://example.com/'}
        self.controller.reserve_platform(value | {'available': True, 'modes': ['desktop_rpa']})
        restored = DesktopController(ClientRuntime()).platform_catalog()
        entry = next(item for item in restored if item['id'] == 'future_ai')
        self.assertFalse(entry['available'])
        self.assertEqual(entry['modes'], [])
        with self.assertRaises(ValueError):
            self.controller.preview_local_batch(['future_ai'], '不得发送')
        with self.assertRaises(ValueError):
            self.controller.reserve_platform(value)
        for change in [
            {'id': '../bad'},
            {'url': 'javascript:alert(1)'},
            {'id': 'doubao'},
            {'url': 'https://user:password@example.com'},
        ]:
            with self.assertRaises(ValueError):
                self.controller.reserve_platform(value | change)
        with self.assertRaises(ValueError):
            self.controller.remove_reserved_platform('doubao')
        self.controller.remove_reserved_platform('future_ai')
        self.assertEqual(len(self.controller.platform_catalog()), 4)

    def test_saved_results_do_not_fall_back_to_navigation_url(self):
        folder = self.runtime.create_local_batch(
            {'platforms': ['doubao'], 'questions': ['fixture']}
        )
        item = folder / 'items' / '0001-doubao'
        item.mkdir(parents=True)
        result = item / 'result.json'
        result.write_text(
            json.dumps({'conversation_url': 'https://example.com/chat/1'}), encoding='utf-8'
        )
        with self.assertRaises(ValueError):
            self.controller.saved_output(folder, '0001-doubao', 'share')
        result.write_text(
            json.dumps({'share_url': 'https://example.com/share/1'}), encoding='utf-8'
        )
        self.assertEqual(
            self.controller.saved_output(folder, '0001-doubao', 'share'),
            'https://example.com/share/1',
        )
        with self.assertRaises(ValueError):
            self.controller.saved_output(Path(self.temp.name), '0001-doubao', 'share')
        with self.assertRaises(ValueError):
            self.controller.saved_output(folder, '../result', 'share')

    def test_navigation_preview_reserve_disabled_and_single_launch(self):
        from tkinter import ttk
        import tkinter as tk

        self.controller.reserve_platform(
            {'id': 'future_ai', 'name': '未来平台', 'url': 'https://example.com/'}
        )
        view = Workbench(self.controller)
        view.window.withdraw()

        def children(widget):
            return [child for item in widget.winfo_children() for child in [item, *children(item)]]

        launches = []

        class Process:
            done = False

            def poll(self):
                return 0 if self.done else None

        process = Process()

        def spawn(*args):
            launches.append(args)
            return process, 'fixture'

        try:
            view.show('platforms')
            view.platform_table.selection_set('future_ai')
            view.select_platform()
            self.assertTrue(view.platform_login.instate(['disabled']))
            view.show('history')
            self.assertEqual(view.history.get_children(), ())
            view.show('agent')
            view.show('connection')
            self.assertEqual(view.connection_values['device_key'].get(), '')
            view.show('collect')
            widgets = children(view.pages['collect'])
            editor = next(widget for widget in widgets if isinstance(widget, tk.Text))
            editor.insert('1.0', '第一题\n第二题')
            view.window.update()
            table = next(widget for widget in widgets if isinstance(widget, ttk.Treeview))
            self.assertEqual(len(table.get_children()), 4)
            buttons = {
                str(widget.cget('text')): widget
                for widget in widgets
                if isinstance(widget, ttk.Button)
            }
            thinking = next(
                w
                for w in widgets
                if isinstance(w, ttk.Combobox)
                and tuple(w.cget('values')) == ('平台默认', '开启', '关闭')
            )
            thinking.set('开启')
            with patch.object(self.controller, 'spawn', spawn):
                buttons['开始批量采集'].invoke()
                buttons['开始批量采集'].invoke()
                self.assertEqual(len(launches), 1)
                self.assertTrue(view.busy())
                buttons['停止后续任务'].invoke()
                request_path = Path(launches[0][1])
                self.assertTrue((request_path.parent / 'stop.request').exists())
                request = json.loads(request_path.read_text(encoding='utf-8'))
                self.assertEqual(request['platforms'], ['doubao', 'deepseek'])
                self.assertIs(request['thinking_mode'], True)
                self.assertTrue(thinking.instate(['disabled']))
                self.assertNotIn('future_ai', request['platforms'])
                process.done = True
                buttons['新建下一批次'].invoke()
                self.assertFalse(buttons['开始批量采集'].instate(['disabled']))
                self.assertTrue(thinking.instate(['readonly']))
        finally:
            view.window.destroy()

    def test_primary_actions_fit_minimum_window(self):
        from tkinter import ttk
        import tkinter as tk

        view = Workbench(self.controller)
        try:
            view.window.geometry('1180x800')
            view.window.update()

            def children(widget):
                return [
                    child for item in widget.winfo_children() for child in [item, *children(item)]
                ]

            widgets = children(view.pages['collect'])
            for widget in widgets:
                if isinstance(widget, ttk.Button) and str(widget.cget('text')) in (
                    '开始批量采集',
                    '清空问题',
                    '停止后续任务',
                    '新建下一批次',
                ):
                    self.assertTrue(widget.winfo_ismapped(), widget.cget('text'))
                    self.assertLessEqual(
                        widget.winfo_rooty() + widget.winfo_height(),
                        view.window.winfo_rooty() + view.window.winfo_height() - 24,
                        widget.cget('text'),
                    )
            editor = next(w for w in widgets if isinstance(w, tk.Text))
            self.assertGreater(editor.winfo_height(), 60)
        finally:
            view.window.destroy()


if __name__ == '__main__':
    import sys

    if '--progress-preview' in sys.argv:
        import time
        import tkinter as tk
        from tkinter import ttk
        from collector.domain.batches import batch_items

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {'COLLECTOR_CLIENT_HOME': tmp}),
        ):
            runtime = ClientRuntime()
            items = batch_items(
                ['doubao', 'deepseek', 'yuanbao', 'qianwen'], ['仅用于本地界面验收']
            )
            for item, status in zip(items, ['completed', 'failed', 'partial', 'running']):
                item.update(
                    status=status,
                    stage='waiting_human' if status == 'running' else 'done',
                    stage_started_at=time.time(),
                )
            items[1].update(code='UI_CHANGED', error='模拟发送按钮变化，未操作真实平台')
            items[2].update(code='EXPORT_INCOMPLETE', error='模拟截图不完整，全文已保留')
            state = {'status': 'running', 'finished': 3, 'total': 4, 'items': items}

            class PreviewProcess:
                def poll(self):
                    return None

            with (
                patch.object(runtime, 'spawn', return_value=(PreviewProcess(), 'preview')),
                patch.object(runtime, 'local_status', return_value=state),
            ):
                view = Workbench(DesktopController(runtime))
                view.window.title('网页采集工作台 · 进度界面验收（模拟，不执行平台任务）')

                def children(widget):
                    return [
                        child
                        for item in widget.winfo_children()
                        for child in [item, *children(item)]
                    ]

                widgets = children(view.pages['collect'])
                buttons = {str(w.cget('text')): w for w in widgets if isinstance(w, ttk.Button)}
                buttons['全选平台'].invoke()
                next(w for w in widgets if isinstance(w, tk.Text)).insert(
                    '1.0', '仅用于本地界面验收'
                )
                buttons['开始批量采集'].invoke()
                view.window.protocol('WM_DELETE_WINDOW', view.window.destroy)

                def verify_visible_rows():
                    table = next(w for w in widgets if isinstance(w, ttk.Treeview))
                    for item in items:
                        box = table.bbox(item['id'])
                        assert box and box[1] + box[3] <= table.winfo_height(), item['id']
                    print(
                        'PASS: all four platform rows fit the local progress preview.', flush=True
                    )

                view.window.after(1000, verify_visible_rows)
                view.window.after(180000, view.window.destroy)
                view.window.mainloop()
    elif '--preview' in sys.argv:
        # Isolated visual QA, not a second collector. No login or execution allowed.
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {'COLLECTOR_CLIENT_HOME': tmp}),
        ):
            runtime = ClientRuntime()
            with patch.object(
                runtime, 'spawn', side_effect=ValueError('界面验收模式不执行采集或登录')
            ):
                view = Workbench(DesktopController(runtime))
                view.window.title('网页采集工作台 v0.10.0 · 隔离界面验收')
                view.window.after(180000, view.window.destroy)
                view.window.mainloop()
    else:
        unittest.main(verbosity=2)
