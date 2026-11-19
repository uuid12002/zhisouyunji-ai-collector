"""Desktop composition and CLI; all concrete dependencies are assembled here."""

import argparse
import asyncio
import sys
import uuid
from collector.application.client_config import validate_config
from collector.domain.platforms import PLATFORM_NAMES
from collector.application.desktop import DesktopController
from collector.infrastructure.client_runtime import ClientRuntime, memory_load
from collector.infrastructure.spool import write_json as save

__all__ = [
    'HOME',
    'CONFIG',
    'STOP',
    'STATUS',
    'validate_config',
    'read_config',
    'import_connection',
    'set_startup',
    'save',
    'gui',
    'worker',
    'main',
    'entrypoint',
]

runtime = ClientRuntime()
controller = DesktopController(runtime)
HOME, CONFIG, STOP, STATUS = runtime.home, runtime.config, runtime.stop_file, runtime.status
read_config = controller.read_config
import_connection = controller.import_connection
set_startup = controller.set_startup


def gui(autostart=False, smoke_test=False):
    from collector.presentation.desktop import gui as show

    show(controller, autostart, smoke_test)


async def worker():
    from collector.infrastructure.socket_worker import run_socket_worker
    from collector.infrastructure.browser.runtime import profile_lock
    from collector.worker_bootstrap import execute
    await run_socket_worker(read_config(), runtime, memory_load, execute, profile_lock('worker-instance'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--login', choices=list(PLATFORM_NAMES))
    parser.add_argument('--browser-host', choices=list(PLATFORM_NAMES))
    parser.add_argument('--close-browser', choices=list(PLATFORM_NAMES))
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--fleet-check', action='store_true', help='Check unified backend authentication without claiming tasks')
    parser.add_argument('--autostart', action='store_true')
    parser.add_argument('--local-test')
    parser.add_argument('--local-batch')
    parser.add_argument('--agent-server', action='store_true')
    parser.add_argument('--agent-stop', action='store_true')
    parser.add_argument('--agent-port', type=int, default=18765)
    args = parser.parse_args()
    if args.fleet_check:
        from collector.infrastructure.http_client import DeviceClient,DeviceGateway
        config=read_config()
        async def check_fleet():
            gateway=DeviceGateway(DeviceClient(config['server_url'],config['device_key'],config['device_id'],uuid.uuid4().hex))
            response=await gateway.heartbeat({'platforms':config['platforms'],'capacity':0,'draining':True,'memory_load':memory_load()})
            if response.get('backend')!='unified-fleet' or response.get('protocol')!=3:
                raise ValueError('服务器不是统一采集管理后台，未领取任务')
            save(HOME/'fleet-check.json',{'ok':True,'server_url':config['server_url'],'backend':response['backend'],'frozen':bool(getattr(sys,'frozen',False))})
        asyncio.run(check_fleet())
    elif args.browser_host:
        from collector.infrastructure.browser import runtime as browser_runtime
        from collector.infrastructure.browser.sessions import serve_session
        import os
        from pathlib import Path

        browser_runtime.DATA = Path(
            os.environ.get('COLLECTOR_SESSION_DATA', str(browser_runtime.DATA))
        )
        asyncio.run(serve_session(args.browser_host))
    elif args.close_browser:
        from collector.infrastructure.browser.sessions import close_platform

        asyncio.run(close_platform(args.close_browser))
    elif args.agent_stop:
        from collector.infrastructure.agent_gateway import AgentGateway, read_connection

        asyncio.run(AgentGateway(**read_connection()).request('POST', '/shutdown'))
    elif args.agent_server:
        from collector.agent_bootstrap import run_server

        if not 1024 <= args.agent_port <= 65535:
            parser.error('--agent-port 必须为 1024～65535')
        run_server(runtime, args.agent_port)
    elif args.self_test:
        import ssl

        tls = ssl.create_default_context()
        assert tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname
        import tkinter as tk

        window = tk.Tk()
        window.withdraw()
        window.update()
        window.destroy()
        gui(smoke_test=True)
        from collector.infrastructure.browser.runtime import ROOT
        from playwright.async_api import async_playwright

        async def check():
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(channel='msedge', headless=True)
                page = await browser.new_page()
                await page.set_content('<h1>collector packaging check</h1>')
                assert await page.locator('h1').inner_text() == 'collector packaging check'
                await browser.close()

        asyncio.run(check())
        from collector.infrastructure.browser.native_windows import WindowsDesktop, Input
        import ctypes

        WindowsDesktop()  # Load the packaged backend without issuing desktop input.
        assert ctypes.sizeof(Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
        assert (ROOT / 'adapters.json').is_file()
        save(HOME / 'self-test.json', {'ok': True, 'frozen': bool(getattr(sys, 'frozen', False))})
    elif args.local_batch:
        from collector.local_bootstrap import run_batch

        asyncio.run(run_batch(args.local_batch))
    elif args.local_test:
        from collector.local_bootstrap import run

        asyncio.run(run(args.local_test))
    elif args.run:
        asyncio.run(worker())
    elif args.login:
        from collector.infrastructure.browser.runtime import login

        asyncio.run(login(args.login))
    else:
        from collector.infrastructure.browser.runtime import profile_lock

        with profile_lock('desktop-instance'):
            gui(args.autostart)


def entrypoint():
    if sys.stdout is None:
        sys.stdout = (HOME / 'client.log').open('a', encoding='utf-8', buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        raise SystemExit(1)
