"""Durable local evidence and checkpoint repository."""

import hashlib
import json
from pathlib import Path
from collector.domain.errors import CollectionError


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class JobSpool:
    def __init__(self, root: Path, job_id: str):
        if Path(job_id).name != job_id or job_id in ('.', '..'):
            raise CollectionError('SPOOL_MISMATCH', '非法任务编号。')
        self.folder = root / 'spool' / job_id
        self.folder.mkdir(parents=True, exist_ok=True)

    def _file(self, name):
        if Path(name).name != name or '/' in name or '\\' in name:
            raise CollectionError('SPOOL_MISMATCH', '非法证据文件名。')
        return self.folder / name

    def capture(self, request_hash):
        path = self.folder / 'capture.json'
        if not path.exists():
            return None
        result = json.loads(path.read_text(encoding='utf-8'))
        if result.get('request_hash') != request_hash:
            raise CollectionError('SPOOL_MISMATCH', '本地证据与任务请求不一致。')
        for item in result['files']:
            file = self._file(item['name'])
            if not file.exists() or hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
                raise CollectionError('SPOOL_CORRUPTED', '本地证据校验失败，不能自动重复发问。')
        return result

    def checkpoint(self, update):
        path = self.folder / 'checkpoint.json'
        previous = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        write_json(path, previous | update)

    def restore(self, job):
        path = self.folder / 'checkpoint.json'
        if path.exists():
            state = json.loads(path.read_text(encoding='utf-8'))
            if state.get('submission') in ('submitted', 'submission_uncertain'):
                job = job | {'checkpoint': (job.get('checkpoint') or {}) | state}
        return job

    def save_capture(self, result, request_hash, attempt):
        result.update(request_hash=request_hash, capture_attempt=attempt)
        for item in result['files']:
            item['sha256'] = hashlib.sha256(self._file(item['name']).read_bytes()).hexdigest()
        manifest = self.folder / 'manifest.json'
        write_json(manifest, result)
        result['files'].append(
            {
                'name': 'manifest.json',
                'kind': 'manifest',
                'mime': 'application/json',
                'sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            }
        )
        write_json(self.folder / 'capture.json', result)

    def read_artifact(self, item):
        return self._file(item['name']).read_bytes()

    def receipt(self, value):
        write_json(self.folder / 'receipt.json', value)

    def error(self, exception):
        (self.folder / 'error.txt').write_text(str(exception), encoding='utf-8')
