"""Build clean, versioned client/server artifacts from the layered application."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

from collector.version import VERSION

ROOT = Path(__file__).resolve().parent
RELEASE = ROOT / 'releases' / ('v' + VERSION)


def server_sources():
    files = [
        'platforms.py',
        'app.py',
        'remote_server.py',
        'requirements-server.txt',
        'start-server.ps1',
        'static/index.html',
        'static/remote.html',
        'static/client-onboarding.js',
        'REMOTE_DEPLOY.md',
        'ARCHITECTURE.md',
        'collector/__init__.py',
        'collector/version.py',
        'collector/bootstrap.py',
        'collector/application/__init__.py',
        'collector/application/contracts.py',
        'collector/application/ports.py',
        'collector/application/local_tasks.py',
        'collector/application/devices.py',
        'collector/infrastructure/__init__.py',
        'collector/infrastructure/settings.py',
        'collector/infrastructure/database.py',
        'collector/infrastructure/repositories.py',
        'collector/infrastructure/migrations.py',
        'collector/infrastructure/artifacts.py',
        'collector/presentation/__init__.py',
    ]
    for folder in ('collector/domain', 'collector/presentation/http'):
        files.extend(p.relative_to(ROOT).as_posix() for p in sorted((ROOT / folder).rglob('*.py')))
    return files


def build_executable():
    extra = []
    subprocess.run(
        [
            sys.executable,
            '-m',
            'PyInstaller',
            '--noconfirm',
            '--clean',
            '--onefile',
            '--windowed',
            '--name',
            'WebCollector',
            '--distpath',
            str(RELEASE),
            '--workpath',
            str(ROOT / 'build' / 'pyinstaller'),
            '--specpath',
            str(ROOT / 'build'),
            '--add-data',
            str(ROOT / 'adapters.json') + ';.',
            '--collect-all',
            'playwright',
            *extra,
            str(ROOT / 'desktop_client.py'),
        ],
        check=True,
        cwd=ROOT,
    )


def package_server():
    with zipfile.ZipFile(
        RELEASE / f'collector-server-v{VERSION}.zip', 'w', zipfile.ZIP_DEFLATED
    ) as archive:
        for name in server_sources():
            archive.write(ROOT / name, name)


def package_client():
    with zipfile.ZipFile(
        RELEASE / f'collector-client-v{VERSION}.zip', 'w', zipfile.ZIP_DEFLATED
    ) as archive:
        for name in (
            'WebCollector.exe',
            'WebCollectorMCP.exe',
            'install-client.ps1',
            'install-client.cmd',
        ):
            archive.write((RELEASE if name.endswith('.exe') else ROOT) / name, name)
        archive.write(ROOT / 'REMOTE_DEPLOY.md', '使用说明.md')
        archive.write(ROOT / 'AGENT_INTEGRATION.md', '智能体接入说明.md')
        archive.write(ROOT / 'PLATFORM_EXTENSION.md', '平台扩展说明.md')
        archive.write(ROOT / 'RESIDENT_BROWSERS.md', '常驻浏览器说明.md')


def build_mcp_executable():
    extra = []
    subprocess.run(
        [
            sys.executable,
            '-m',
            'PyInstaller',
            '--noconfirm',
            '--clean',
            '--onefile',
            '--console',
            '--name',
            'WebCollectorMCP',
            '--distpath',
            str(RELEASE),
            '--workpath',
            str(ROOT / 'build' / 'mcp'),
            '--specpath',
            str(ROOT / 'build'),
            '--copy-metadata',
            'mcp',
            *extra,
            str(ROOT / 'agent_mcp.py'),
        ],
        check=True,
        cwd=ROOT,
    )


def write_checksums():
    manifest = {
        path.name: {
            'size': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(RELEASE.iterdir())
        if path.suffix in ('.exe', '.zip')
    }
    (RELEASE / 'SHA256.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    RELEASE.mkdir(parents=True, exist_ok=True)
    build_executable()
    build_mcp_executable()
    package_server()
    package_client()
    print(json.dumps(write_checksums(), indent=2))


if __name__ == '__main__':
    main()
