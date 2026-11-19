"""Windows process, registry, resource and client-state implementations."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import re
from .spool import write_json
from collector.domain.deployment import DEFAULT_MANAGEMENT_URL

ROOT = Path(__file__).resolve().parents[2]


def command(*args):
    return (
        [sys.executable, *args]
        if getattr(sys, 'frozen', False)
        else [sys.executable, str(ROOT / 'desktop_client.py'), *args]
    )


def memory_load():
    if os.name != 'nt':
        return 0

    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong)
            for name in (
                'total',
                'available',
                'page_total',
                'page_available',
                'virtual_total',
                'virtual_available',
                'extended',
            )
        ]

    value = Memory()
    value.length = ctypes.sizeof(value)
    return value.load if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)) else 0


class ClientRuntime:
    def browser_sessions(self):
        from .browser.sessions import browser_statuses

        return browser_statuses(self.home / 'data')

    def planned_platforms(self):
        path = self.home / 'platform-catalog.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []

    def save_planned_platforms(self, value):
        write_json(self.home / 'platform-catalog.json', value)

    def recent_collections(self):
        entries = []
        paths = [
            p
            for name in ('local-batches', 'agent-batches')
            for p in (self.home / 'data' / name).glob('*/status.json')
        ]
        for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)[:40]:
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
                for item in state.get('items', []):
                    entries.append(
                        item | {'folder': str(path.parent), 'updated': path.stat().st_mtime}
                    )
                    if len(entries) >= 200:
                        return entries
            except (OSError, ValueError):
                continue
        return entries

    def saved_output(self, folder, item_id, kind):
        folder = Path(folder).resolve()
        roots = [
            (self.home / 'data' / name).resolve() for name in ('local-batches', 'agent-batches')
        ]
        if not any(folder.parent == root for root in roots):
            raise ValueError('结果目录不属于本机任务')
        from collector.domain.platforms import PLATFORM_NAMES

        if (
            not re.fullmatch(r'\d{4}-[a-z][a-z0-9_]*', item_id)
            or item_id.split('-', 1)[1] not in PLATFORM_NAMES
        ):
            raise ValueError('非法子任务编号')
        item = folder / 'items' / item_id
        if not item.resolve().is_relative_to(folder):
            raise ValueError('结果目录越界')
        if kind == 'share':
            path = item / 'result.json'
            if not path.exists() or path.is_symlink():
                raise ValueError('本项尚未保存分享链接')
            value = json.loads(path.read_text(encoding='utf-8')).get('share_url')
            from urllib.parse import urlparse

            if (
                not isinstance(value, str)
                or urlparse(value).scheme != 'https'
                or not urlparse(value).hostname
            ):
                raise ValueError('没有通过页面分享操作取得的有效链接')
            return value
        path = (
            item
            / {'answer': 'answer.txt', 'image': 'answer-long.png', 'report': 'report.html'}[kind]
        )
        if not path.is_file() or path.is_symlink():
            raise ValueError('该结果尚未生成，请查看单项报告')
        os.startfile(str(path))

    def __init__(self):
        self.home = Path(
            os.environ.get(
                'COLLECTOR_CLIENT_HOME',
                str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WebCollector'),
            )
        )
        self.home.mkdir(parents=True, exist_ok=True)
        os.environ['COLLECTOR_DATA'] = str(self.home / 'data')
        if (self.home / 'adapters.json').exists():
            os.environ['COLLECTOR_ADAPTERS'] = str(self.home / 'adapters.json')
        self.config = self.home / 'client.json'
        self.stop_file = self.home / 'stop.request'
        self.status = self.home / 'status.json'

    def load_configuration(self):
        return json.loads(self.config.read_text(encoding='utf-8')) if self.config.exists() else {'server_url':DEFAULT_MANAGEMENT_URL}

    def read_connection(self, path):
        path = Path(path)
        if path.stat().st_size > 65536:
            raise ValueError('接入配置文件过大')
        return json.loads(path.read_text(encoding='utf-8-sig'))

    def save_configuration(self, value):
        write_json(self.config, value)

    def configuration_bytes(self):
        return self.config.read_bytes() if self.config.exists() else None

    def restore_configuration(self, value):
        if value is None:
            self.config.unlink(missing_ok=True)
        else:
            self.config.write_bytes(value)

    def set_startup(self, enabled):
        if os.name != 'nt':
            return
        import winreg

        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run'
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key,
                    'WebCollector',
                    0,
                    winreg.REG_SZ,
                    subprocess.list2cmdline(command('--autostart')),
                )
            else:
                try:
                    winreg.DeleteValue(key, 'WebCollector')
                except FileNotFoundError:
                    pass

    def spawn(self, *args):
        run_id = uuid.uuid4().hex
        with (self.home / 'client.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen(
                command(*args),
                stdout=log,
                stderr=log,
                env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1', COLLECTOR_RUN_ID=run_id),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        return process, run_id

    def stop_requested(self):
        return self.stop_file.exists()

    def clear_stop(self):
        self.stop_file.unlink(missing_ok=True)

    def stop(self):
        self.stop_file.write_text('drain', encoding='utf-8')

    def publish(self, value):
        write_json(self.status, value)

    @property
    def process_id(self):
        return os.getpid()

    @property
    def run_id(self):
        return os.environ.get('COLLECTOR_RUN_ID', '')

    def open_home(self):
        os.startfile(str(self.home))

    def worker_status(self):
        return json.loads(self.status.read_text(encoding='utf-8')) if self.status.exists() else {}

    def local_status(self, folder):
        path = folder / 'status.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def create_local_test(self, platform, question):
        folder = self.home / 'data' / 'local-tests' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        write_json(folder / 'request.json', {'platform': platform, 'question': question})
        return folder

    def open_result(self, folder):
        path = folder / 'report.html'
        os.startfile(str(path if path.exists() else folder))

    def create_local_batch(self, request):
        folder = self.home / 'data' / 'local-batches' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        write_json(folder / 'request.json', request)
        return folder

    def stop_local_batch(self, folder):
        (folder / 'stop.request').write_text('stop-after-current', encoding='utf-8')

    def open_batch_item(self, folder, item_id):
        if not re.fullmatch(r'\d{4}-(doubao|deepseek|yuanbao|qianwen)', item_id):
            raise ValueError('非法子任务编号')
        item_folder = folder / 'items' / item_id
        if item_folder.exists():
            self.open_result(item_folder)
