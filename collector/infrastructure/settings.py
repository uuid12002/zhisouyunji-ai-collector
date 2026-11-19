"""Read process configuration at the composition root, never on service import."""

from dataclasses import dataclass
import os
from pathlib import Path
import secrets


@dataclass(frozen=True)
class ServerSettings:
    root: Path
    data: Path
    worker_key: str
    admin_key: str
    allowed_hosts: tuple[str, ...]
    global_limit: int = 20

    @classmethod
    def from_environment(cls, remote=False):
        root = Path(__file__).resolve().parents[2]
        data = Path(os.environ.get('COLLECTOR_DATA', str(root / 'data')))
        data.mkdir(parents=True, exist_ok=True)
        key_file = data / 'worker.key'
        if not key_file.exists():
            key_file.write_text(secrets.token_urlsafe(32), encoding='utf-8')
        admin_key = os.environ.get('COLLECTOR_ADMIN_KEY', '')
        if remote and len(admin_key) < 24:
            raise RuntimeError(
                'Set COLLECTOR_ADMIN_KEY to a random secret of at least 24 characters'
            )
        return cls(
            root,
            data,
            key_file.read_text(encoding='utf-8').strip(),
            admin_key,
            tuple(
                os.environ.get('COLLECTOR_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(',')
            ),
            int(os.environ.get('COLLECTOR_MAX_RUNNING', '20')),
        )
