"""Process-owned browser sessions, reusable across short-lived task workers.

Only the owner holds the profile lock. Task clients hold a separate exclusive
lease and disconnect CDP without closing the owner's context or pages.
"""

import asyncio
from contextlib import asynccontextmanager
import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import time
from urllib.parse import urlparse, quote
import uuid
from collector.domain.errors import CollectionError
from collector.domain.platforms import PLATFORM_NAMES
from collector.infrastructure.spool import write_json
from . import runtime


def process_stamp(pid):
    """Return a live Windows process creation stamp, without sending a signal."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name != 'nt':
        try:
            os.kill(pid, 0)
            return str(pid)
        except OSError:
            return None
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        times = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return None
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        api.CloseHandle(handle)


def session_file(platform, data=None):
    if platform not in PLATFORM_NAMES:
        raise CollectionError('UNKNOWN_PLATFORM', '未适配的平台不能启动浏览器。')
    folder = (data or runtime.DATA) / 'browser-sessions'
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{platform}.json'


def read_session(platform, data=None):
    path = session_file(platform, data)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        endpoint = urlparse(value['endpoint'])
        stamp = process_stamp(value['owner_pid'])
        valid = (
            value['platform'] == platform
            and endpoint.scheme == 'ws'
            and endpoint.hostname == '127.0.0.1'
            and endpoint.port
            and endpoint.path.startswith('/devtools/browser/')
            and not endpoint.username
            and not endpoint.password
            and stamp is not None
            and stamp == value['owner_stamp']
        )
        return value if valid else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def browser_statuses(data=None):
    return {key: ('常驻已打开' if read_session(key, data) else '未打开') for key in PLATFORM_NAMES}


def label_script(platform, session_id):
    colors = {
        'doubao': '#3264ED',
        'deepseek': '#1670CA',
        'yuanbao': '#16835D',
        'qianwen': '#7C4CDB',
    }
    name = PLATFORM_NAMES[platform]
    glyph = {'doubao': '豆', 'deepseek': 'D', 'yuanbao': '元', 'qianwen': '千'}.get(
        platform, name[:1]
    )
    color = colors.get(platform, '#3264ED')
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32"><rect width="32" height="32" rx="7" fill="{color}"/><text x="16" y="23" text-anchor="middle" font-family="sans-serif" font-size="23" fill="white">{glyph}</text></svg>'
    return (
        """(() => {
      const prefix=PREFIX, id=SESSION, icon=ICON;
      if(window.__collectorLabelInstalled)return;
      window.__collectorLabelInstalled=true;
      window.__collectorSessionId=id;
      const update=()=>{
        if(!document.head)return;
        if(!document.title.startsWith(prefix))document.title=prefix+document.title;
        let link=document.querySelector('link[data-collector-icon]');
        if(!link){link=document.createElement('link');link.rel='icon';link.dataset.collectorIcon='1';document.head.append(link);}
        if(link.getAttribute('href')!==icon)link.setAttribute('href',icon);
      };
      const start=()=>{
        update();
        const titleObserver=new MutationObserver(update);
        let watched=null;
        const bindTitle=()=>{const title=document.querySelector('title');
          if(title!==watched){titleObserver.disconnect();watched=title;
            if(title)titleObserver.observe(title,{childList:true,subtree:true,characterData:true});}
        };
        bindTitle();
        new MutationObserver(()=>{bindTitle();update();}).observe(document.head,{childList:true});
      };
      if(document.head)start();else document.addEventListener('DOMContentLoaded',start,{once:true});
    })();""".replace('PREFIX', json.dumps(f'【采集专用 · {name}】'))
        .replace('SESSION', json.dumps(session_id))
        .replace('ICON', json.dumps('data:image/svg+xml,' + quote(svg)))
    )


def landing_url(platform):
    import html

    name = html.escape(PLATFORM_NAMES[platform])
    url = html.escape(runtime.URLS[platform], quote=True)
    content = f'''<!doctype html><meta charset="utf-8"><title>{name} · 专用浏览器</title>
    <style>body{{font:17px/1.8 "Microsoft YaHei UI",sans-serif;background:#f4f6fa;color:#192333;margin:10vh auto;max-width:700px;padding:32px}}
    article{{background:white;padding:40px;border-radius:16px}}a{{color:#3264ed}}h1{{font-size:30px}}</style>
    <article><h1>{name} · 常驻专用浏览器</h1><p>此窗口会持续保留，登录和采集任务共用当前标签页。</p>
    <p>在工作台点击“打开 / 唤起浏览器”即可前往平台登录。请保留此主标签页。</p>
    <a href="{url}">前往 {name} 官网</a></article><script>window.__collectorLanding=true</script>'''
    return 'data:text/html;charset=utf-8,' + quote(content)


async def serve_session(platform, launch_context=None, start_url=None):
    """Own the browser until its windows are closed; no task or input loop."""
    from playwright.async_api import async_playwright, Error as BrowserError

    path = session_file(platform)
    session_id = uuid.uuid4().hex
    with runtime.profile_lock(platform):
        async with async_playwright() as pw:
            factory = launch_context or runtime.open_context
            context = await factory(pw, platform, desktop_rpa=True, debug_port=True)
            try:
                script = label_script(platform, session_id)
                await context.add_init_script(script)
                page = context.pages[0] if context.pages else await context.new_page()
                if page.url == 'about:blank':
                    try:
                        await page.goto(
                            start_url or landing_url(platform), wait_until='domcontentloaded'
                        )
                    except BrowserError as exc:
                        print(f'平台首页暂未加载，保留窗口供手动检查：{exc}', flush=True)
                await page.evaluate(script)
                cdp = await context.new_cdp_session(page)
                target = (await cdp.send('Target.getTargetInfo'))['targetInfo']['targetId']
                await cdp.detach()
                active = runtime.DATA / 'profiles' / platform / 'DevToolsActivePort'
                lines = active.read_text(encoding='utf-8').splitlines()
                port = int(lines[0])
                endpoint = f'ws://127.0.0.1:{port}{lines[1]}'
                write_json(
                    path,
                    {
                        'platform': platform,
                        'endpoint': endpoint,
                        'session_id': session_id,
                        'target_id': target,
                        'owner_pid': os.getpid(),
                        'owner_stamp': process_stamp(os.getpid()),
                        'opened_at': time.time(),
                    },
                )
                print(
                    f'{PLATFORM_NAMES[platform]} 常驻浏览器已准备好；登录后无需关闭窗口。',
                    flush=True,
                )
                while context.pages:
                    await asyncio.sleep(0.5)
            finally:
                try:
                    current = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                    if current.get('session_id') == session_id:
                        path.unlink(missing_ok=True)
                finally:
                    try:
                        await context.close()
                    except BrowserError:
                        if context.pages and context.browser and context.browser.is_connected():
                            raise


async def connect_session(pw, platform):
    state = read_session(platform)
    if not state:
        raise CollectionError('SESSION_NOT_OPEN', '专用浏览器尚未打开。')
    browser = await pw.chromium.connect_over_cdp(state['endpoint'], timeout=5000)
    try:
        if len(browser.contexts) != 1:
            raise CollectionError('SESSION_AMBIGUOUS', '专用浏览器上下文不唯一，已停止。')
        context = browser.contexts[0]
        for page in context.pages:
            cdp = await context.new_cdp_session(page)
            try:
                target = (await cdp.send('Target.getTargetInfo'))['targetInfo']['targetId']
            finally:
                await cdp.detach()
            if target == state['target_id']:
                if await page.evaluate('window.__collectorSessionId') != state['session_id']:
                    raise CollectionError('SESSION_CHANGED', '专用标签页标识不符，未操作。')
                return browser, context, page
        raise CollectionError(
            'SESSION_TAB_CLOSED',
            '专用主标签已关闭；请关闭该平台浏览器后重新打开，不会自动选择其他标签。',
        )
    except BaseException:
        await browser.close()  # Disconnect this client, never context.close().
        raise


def spawn_owner(platform):
    from collector.infrastructure.client_runtime import command

    log_path = session_file(platform).with_suffix('.log')
    with log_path.open('a', encoding='utf-8') as log:
        return subprocess.Popen(
            command('--browser-host', platform),
            stdout=log,
            stderr=log,
            env=dict(
                os.environ,
                COLLECTOR_SESSION_DATA=str(runtime.DATA),
                PYINSTALLER_RESET_ENVIRONMENT='1',
            ),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )


async def ensure_session(pw, platform):
    if read_session(platform):
        return await connect_session(pw, platform)
    with runtime.profile_lock('session-start-' + platform):
        if read_session(platform):
            return await connect_session(pw, platform)
        process = spawn_owner(platform)
        for _ in range(300):
            if read_session(platform):
                return await connect_session(pw, platform)
            if process.poll() is not None:
                raise CollectionError(
                    'SESSION_START_FAILED',
                    '常驻浏览器启动失败。请先关闭旧版专用浏览器，再查看 browser-sessions 中的平台日志。',
                )
            await asyncio.sleep(0.1)
        raise CollectionError(
            'SESSION_START_TIMEOUT', '常驻浏览器启动尚未完成，请检查平台窗口和日志后再操作。'
        )


@asynccontextmanager
async def session_page(pw, platform):
    with runtime.profile_lock('session-task-' + platform):
        browser, context, page = await ensure_session(pw, platform)
        try:
            yield context, page
        finally:
            await browser.close()


async def open_platform(platform):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        async with session_page(pw, platform) as (_, page):
            if await page.evaluate('window.__collectorLanding === true'):
                await page.goto(runtime.URLS[platform], wait_until='domcontentloaded')
            await page.bring_to_front()


async def close_platform(platform):
    from playwright.async_api import async_playwright

    with runtime.profile_lock('session-task-' + platform):
        state = read_session(platform)
        if not state:
            return
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(state['endpoint'], timeout=5000)
            cdp = await browser.new_browser_cdp_session()
            try:
                await cdp.send('Browser.close')
            finally:
                await browser.close()
