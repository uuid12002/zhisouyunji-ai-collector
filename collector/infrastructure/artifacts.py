"""Checked filesystem evidence storage; application services never open files."""

import hashlib
from pathlib import Path
import uuid
from collector.application.contracts import FileAsset
from collector.domain.errors import ApplicationError, ErrorKind


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise ApplicationError(ErrorKind.INVALID_REQUEST, '非法附件路径')
        return path

    def write(self, name, raw):
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_bytes(raw)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def verify(self, name, digest):
        path = self._path(name)
        return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest

    def asset(self, name, digest, mime, filename=None):
        if not self.verify(name, digest):
            raise ApplicationError(ErrorKind.CONFLICT, '附件缺失或校验失败')
        headers = {
            'X-Content-Type-Options': 'nosniff',
            'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'none'; sandbox",
        }
        return FileAsset(self._path(name), mime, filename, headers)
