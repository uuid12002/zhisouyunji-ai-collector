"""Check distributable server isolation, completeness and source agreement."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from build_release import RELEASE, ROOT, server_sources
from collector.version import VERSION


def main():
    manifest = json.loads((RELEASE / 'SHA256.json').read_text(encoding='utf-8'))
    for name, metadata in manifest.items():
        assert hashlib.sha256((RELEASE / name).read_bytes()).hexdigest() == metadata['sha256'], name
    with zipfile.ZipFile(RELEASE / f'collector-server-v{VERSION}.zip') as archive:
        assert set(archive.namelist()) == set(server_sources())
        for name in archive.namelist():
            assert archive.read(name) == (ROOT / name).read_bytes(), name
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in archive.namelist():
                assert (folder / name).resolve().is_relative_to(folder.resolve())
            archive.extractall(folder)
            script = """
import sys
import remote_server
from collector.application.contracts import TaskCreate
app=remote_server.app
assert not any(name.startswith(('playwright','tkinter','PIL')) for name in sys.modules)
routes=app.openapi()['paths']
assert '/api/v3/worker/jobs/{job_id}/complete' in routes
assert '/api/v3/worker/heartbeat' in routes
created=app.state.device_service.create_task(TaskCreate(question='isolated package fixture',platforms=['doubao','deepseek','yuanbao','qianwen']),'fixture')
assert len(created.task['jobs'])==4
assert app.state.local_service.health()['ok']
print('PASS: extracted server starts without source checkout or browser/UI dependencies; four-platform task creation works')
"""
            result = subprocess.run(
                [sys.executable, '-c', script],
                cwd=folder,
                env=dict(
                    os.environ,
                    PYTHONPATH='',
                    COLLECTOR_DATA=str(folder / 'test-data'),
                    COLLECTOR_ADMIN_KEY='test-only-admin-' + 'x' * 32,
                ),
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            print(result.stdout.strip())
    print('PASS: release hashes and server files match current source')


if __name__ == '__main__':
    main()
