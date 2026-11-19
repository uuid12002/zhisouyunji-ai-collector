"""Browser profiles and cross-process locks, independent of business database."""

import os
from pathlib import Path
from contextlib import contextmanager
from playwright.async_api import async_playwright as async_playwright

ROOT = Path(getattr(__import__('sys'), '_MEIPASS', Path(__file__).resolve().parents[3]))
DATA = Path(os.environ.get('COLLECTOR_DATA', str(ROOT / 'data')))
from collector.domain.platforms import PLATFORM_URLS as URLS
from collector.domain.errors import CollectionError


@contextmanager
def profile_lock(platform):
    folder = DATA / 'profiles'
    folder.mkdir(parents=True, exist_ok=True)
    handle = (folder / (platform + '.lock')).open('a+b')
    handle.seek(0)
    handle.write(b'0')
    handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        if platform.startswith('session-task-'):
            raise CollectionError(
                'SESSION_BUSY', '该平台正在执行任务，请等待任务完成后再打开或关闭浏览器。'
            )
        if platform.startswith('session-start-'):
            raise CollectionError(
                'SESSION_STARTING', '该平台浏览器正在启动，请稍后再试，不会重复启动。'
            )
        raise CollectionError('PROFILE_BUSY', '该平台浏览器正在登录或采集，请先关闭登录窗口。')
    try:
        yield
    finally:
        handle.close()


async def open_context(pw, platform, desktop_rpa=False, debug_port=False):
    channel = os.environ.get('COLLECTOR_BROWSER', 'msedge')
    viewport_options = (
        {'no_viewport': True} if desktop_rpa else {'viewport': {'width': 1440, 'height': 1000}}
    )
    return await pw.chromium.launch_persistent_context(
        str(DATA / 'profiles' / platform),
        channel=channel if channel != 'chromium' else None,
        headless=False,
        chromium_sandbox=True,
        **(
            {'args': ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1']}
            if debug_port
            else {}
        ),
        **viewport_options,
        accept_downloads=False,
    )


async def login(platform):
    from .sessions import open_platform

    await open_platform(platform)


if __name__ == '__main__':
    import argparse, asyncio

    p = argparse.ArgumentParser()
    p.add_argument('platform', choices=URLS)
    asyncio.run(login(p.parse_args().platform))
