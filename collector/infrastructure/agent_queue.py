"""Durable, serial local agent queue. Never redispatch an uncertain execution."""

import asyncio
import hashlib
import json
import mimetypes
import re
import threading
import time
from pathlib import Path
from collector.application.contracts import FileAsset
from collector.domain.batches import batch_items
from collector.domain.errors import ApplicationError, ErrorKind
from .spool import write_json


class AgentFileQueue:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def folder(self, batch_id):
        if not re.fullmatch(r'[0-9a-f]{32}', batch_id):
            raise ApplicationError(ErrorKind.NOT_FOUND, '批次不存在')
        folder = self.root / batch_id
        if not (folder / 'agent.json').is_file():
            raise ApplicationError(ErrorKind.NOT_FOUND, '批次不存在')
        return folder

    def accept(self, request, key):
        batch_id = hashlib.sha256(key.encode()).hexdigest()[:32]
        with self.lock:
            folder = self.root / batch_id
            if (folder / 'agent.json').exists():
                if json.loads((folder / 'request.json').read_text(encoding='utf-8')) != request:
                    raise ApplicationError(ErrorKind.CONFLICT, '同一个幂等键不能用于不同任务')
                return self.batch(batch_id) | {'replayed': True}
            folder.mkdir(exist_ok=True)
            write_json(folder / 'request.json', request)
            write_json(folder / 'agent.json', {'status': 'queued', 'created_at': time.time()})
            return self.batch(batch_id) | {'replayed': False}

    def list_batches(self):
        with self.lock:
            return [
                self.batch(path.parent.name)
                for path in sorted(self.root.glob('*/agent.json'), key=lambda p: p.stat().st_mtime)
            ][-100:]

    def batch(self, batch_id):
        with self.lock:
            folder = self.folder(batch_id)
            meta = json.loads((folder / 'agent.json').read_text(encoding='utf-8'))
            state_path = folder / 'status.json'
            state = (
                json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            )
            # An interrupted dispatch may have submitted a question. Never imply it is retryable.
            if meta['status'] == 'interrupted':
                state.update(status='interrupted', stage='stopped')
            elif state.get('status') == 'running' and meta['status'] == 'failed':
                state.update(status='failed', stage='stopped')
            return {
                'id': batch_id,
                'status': meta['status'],
                **state,
                'created_at': meta['created_at'],
                'error': meta.get('error') or state.get('error'),
                'stop_requested': (folder / 'stop.request').exists(),
            }

    def stop(self, batch_id):
        with self.lock:
            folder = self.folder(batch_id)
            (folder / 'stop.request').write_text('stop-after-current', encoding='utf-8')
            meta = json.loads((folder / 'agent.json').read_text(encoding='utf-8'))
            if meta['status'] == 'queued':
                write_json(folder / 'agent.json', meta | {'status': 'stopped'})
            return self.batch(batch_id)

    def results(self, batch_id, item_id=None):
        with self.lock:
            folder = self.folder(batch_id)
            request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
            results = []
            items = batch_items(request['platforms'], request['questions'])
            if item_id is not None:
                items = [item for item in items if item['id'] == item_id]
                if not items:
                    raise ApplicationError(ErrorKind.NOT_FOUND, '子任务不存在')
            for item in items:
                item_folder = folder / 'items' / item['id']
                if not item_folder.resolve().is_relative_to(folder.resolve()):
                    raise ApplicationError(ErrorKind.FORBIDDEN, '结果目录越界')
                path = item_folder / 'result.json'
                result = (
                    json.loads(path.read_text(encoding='utf-8')) if self.exportable(path) else None
                )
                files = []
                if item_folder.exists():
                    for file in sorted(item_folder.iterdir()):
                        if self.exportable(file):
                            files.append({'name': file.name, 'size': file.stat().st_size})
                results.append(
                    {
                        'id': item['id'],
                        'platform': item['platform'],
                        'result': result,
                        'files': files,
                    }
                )
            return {'batch': self.batch(batch_id), 'items': results}

    @staticmethod
    def exportable(path):
        return (
            path.is_file()
            and not path.is_symlink()
            and (
                path.name
                in ('answer.txt', 'answer.html', 'result.json', 'manifest.json', 'report.html')
                or bool(re.fullmatch(r'(answer[^/\\]*|sources[^/\\]*|diagnostic)\.png', path.name))
            )
        )

    def asset(self, batch_id, item_id, name):
        folder = self.folder(batch_id)
        request = json.loads((folder / 'request.json').read_text(encoding='utf-8'))
        valid_items = {
            item['id'] for item in batch_items(request['platforms'], request['questions'])
        }
        if item_id not in valid_items or name != Path(name).name or '/' in name or '\\' in name:
            raise ApplicationError(ErrorKind.NOT_FOUND, '文件不存在')
        path = folder / 'items' / item_id / name
        if not path.resolve().is_relative_to(folder.resolve()) or not self.exportable(path):
            raise ApplicationError(ErrorKind.NOT_FOUND, '文件不存在或不可导出')
        return FileAsset(path, mimetypes.guess_type(name)[0] or 'application/octet-stream', name)

    def recover(self):
        with self.lock:
            for path in self.root.glob('*/agent.json'):
                meta = json.loads(path.read_text(encoding='utf-8'))
                if meta['status'] == 'running':
                    state_path = path.parent / 'status.json'
                    state = (
                        json.loads(state_path.read_text(encoding='utf-8'))
                        if state_path.exists()
                        else {}
                    )
                    status = state.get('status')
                    finished = status in ('completed', 'partial', 'failed', 'stopped')
                    write_json(
                        path,
                        meta
                        | {
                            'status': status if finished else 'interrupted',
                            'error': None
                            if finished
                            else '接口中断；请核对原会话，不会自动重新提交',
                        },
                    )

    def stop_pending(self):
        with self.lock:
            for path in self.root.glob('*/agent.json'):
                meta = json.loads(path.read_text(encoding='utf-8'))
                if meta['status'] in ('queued', 'running'):
                    self.stop(path.parent.name)

    def take(self):
        with self.lock:
            for path in sorted(self.root.glob('*/agent.json'), key=lambda p: p.stat().st_mtime):
                meta = json.loads(path.read_text(encoding='utf-8'))
                if meta['status'] == 'queued':
                    # Persist before spawning: even a crash between these steps must not resend.
                    write_json(path, meta | {'status': 'running'})
                    return path.parent

    def finish(self, folder, error=None):
        with self.lock:
            path = folder / 'agent.json'
            meta = json.loads(path.read_text(encoding='utf-8'))
            state_path = folder / 'status.json'
            state = (
                json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            )
            status = state.get('status')
            if status not in ('completed', 'partial', 'failed', 'stopped'):
                status = 'failed'
                error = error or '采集进程未写入最终状态，请检查 client.log 和原会话'
            write_json(path, meta | {'status': status, 'error': error})


async def supervise_queue(queue, spawn):
    """One child at a time. Shutdown drains the active batch, never kills a send."""
    while True:
        folder = queue.take()
        if folder is None:
            await asyncio.sleep(0.25)
            continue
        process = None
        try:
            process, _ = spawn('--local-batch', str(folder / 'request.json'))
            while process.poll() is None:
                await asyncio.sleep(0.25)
            queue.finish(
                folder, None if process.returncode == 0 else f'采集进程退出：{process.returncode}'
            )
        except asyncio.CancelledError:
            queue.stop(folder.name)
            if process is not None:
                while process.poll() is None:
                    await asyncio.sleep(0.25)
                queue.finish(folder)
            raise
        except Exception as exc:
            queue.finish(folder, str(exc))
