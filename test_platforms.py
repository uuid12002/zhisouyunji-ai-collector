"""Four-platform protocol and simulated DOM regression; no real questions sent."""
import asyncio
import base64
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from platforms import PLATFORM_NAMES, PLATFORM_URLS

ROOT = Path(__file__).resolve().parent


def protocol():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        key = uuid.uuid4().hex
        env = dict(os.environ, COLLECTOR_DATA=temp, COLLECTOR_ADMIN_KEY=key)
        with open(Path(temp)/'server.log', 'w+', encoding='utf-8') as log:
            proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'remote_server:app', '--port', str(port)],
                                    cwd=ROOT, env=env, stdout=log, stderr=log)
            def call(path, body=None, device=None, idem=None):
                headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer '+(device['device_key'] if device else key)}
                if device:
                    headers.update({'X-Device-ID': device['device_id'], 'X-Worker-Session': 'f'*32})
                if idem:
                    headers['Idempotency-Key'] = idem
                request = Request(f'http://127.0.0.1:{port}'+path,
                                  data=json.dumps(body).encode() if body is not None else None, headers=headers)
                try:
                    with urlopen(request, timeout=10) as response:
                        return response.status, json.load(response)
                except HTTPError as exc:
                    return exc.code, json.load(exc)
            try:
                for _ in range(100):
                    try:
                        if call('/health')[0] == 200:
                            break
                    except OSError:
                        time.sleep(.1)
                else:
                    raise RuntimeError('Test server failed to start')
                request = {'question': 'four-platform protocol fixture', 'platforms': list(PLATFORM_NAMES), 'mode': 'real'}
                code, task = call('/api/v3/tasks', request, idem='four-platform-task')
                assert code == 201, (code, task)
                assert {j['platform'] for j in task['jobs']} == set(PLATFORM_NAMES)
                assert call('/api/v3/tasks', request, idem='four-platform-task')[1]['id'] == task['id']
                assert call('/api/v3/tasks', request | {'platforms': ['unknown']}, idem='bad')[0] == 422
                for platform in PLATFORM_NAMES:
                    code, device = call('/api/v3/devices', {'name': 'fixture-'+platform})
                    assert code == 200, (code, device)
                    # All four platform names must be accepted in a single heartbeat.
                    assert call('/api/v3/worker/heartbeat', {'platforms': list(PLATFORM_NAMES), 'capacity': 1}, device)[0] == 200
                    assert call('/api/v3/worker/heartbeat', {'platforms': [platform], 'capacity': 1}, device)[0] == 200
                    code, job = call('/api/v3/worker/claim', {'request_id': uuid.uuid4().hex}, device)
                    assert code == 200 and job['platform'] == platform, (code, job)
                    token = job['lease_token']
                    prefix = '/api/v3/worker/jobs/'+job['job_id']
                    raw='完整回复正文\n第二行'.encode('utf-8')
                    attachment={'lease_token':token,'kind':'answer_text','mime':'text/plain',
                                'sha256':hashlib.sha256(raw).hexdigest(),'content_base64':base64.b64encode(raw).decode()}
                    assert call(prefix+'/artifacts',attachment,device)[0]==200
                    assert call(prefix+'/artifacts',attachment|{'mime':'text/html'},device)[0]==422
                    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j4XkAAAAASUVORK5CYII=')
                    assert call(prefix+'/artifacts',attachment|{'kind':'answer_long_image','mime':'image/png',
                                'sha256':hashlib.sha256(png).hexdigest(),'content_base64':base64.b64encode(png).decode()},device)[0]==200
                    assert call(prefix+'/fail', {'lease_token': token, 'code': 'LOGIN_REQUIRED',
                                'message': 'fixture only', 'waiting_human': True}, device)[0] == 200
                    assert call('/api/v2/jobs/'+job['job_id']+'/resume', {})[0] == 200
                    code, resumed = call('/api/v3/worker/claim', {'request_id': uuid.uuid4().hex}, device)
                    assert code == 200 and resumed['job_id'] == job['job_id']
                import sqlite3
                with closing(sqlite3.connect(Path(temp)/'collector.db')) as db:
                    assert {r[0] for r in db.execute('SELECT platform FROM profiles')} == set(PLATFORM_NAMES)
            finally:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
                proc.wait(timeout=10)
        print('PASS: four-platform creation, validation, idempotency, heartbeat, matching, pause/resume and profile migration.')


def fixture(platform):
    if platform == 'yuanbao':
        new = '<button role="button" aria-label="新建对话" id="new">新建对话</button>'
        field = '<div class="ql-editor" contenteditable="true"></div>'
        send = '<button id="yuanbao-send-btn" aria-label="发送">发送</button>'
        question = '<section class="agent-chat__list__item--human"><p class="hyc-content-text"></p></section>'
        answer = '<section class="agent-chat__list__item--ai"><article class="hyc-common-markdown"></article></section>'
        login = '<button class="agent-dialogue__tool__login">登录</button>'
        end = ''
    else:
        new = '<button data-session-switch-target="new-chat" id="new">新对话</button>'
        field = '<div data-slate-editor="true" contenteditable="true"></div>'
        send = '<button data-session-switch-target="send-query">发送</button>'
        question = '<section data-chat-question-wrap="fixture"><p class="question-text-card"></p></section>'
        answer = '<section data-chat-answers-wrap="fixture"><article class="qk-markdown"></article></section>'
        login = '<button>登录</button>'
        end = '<div data-answer-feedback-toolbar>复制</div>'
    return '''<!doctype html><meta charset="utf-8">'''+new+field+send+'''<main></main><script>
    const config = CONFIG;
    const main = document.querySelector('main');
    const send = document.querySelector(config.send);
    document.querySelector('#new').onclick = () => main.replaceChildren();
    send.onclick = () => {
      main.innerHTML = QUESTION + ANSWER;
      document.querySelector(config.question).textContent = document.querySelector(config.input).innerText;
      const answer = document.querySelector(config.answer);
      answer.textContent = '模拟流式回答';
      send.setAttribute('aria-label', '停止回答');
      setTimeout(() => {
        answer.innerHTML = '模拟测试完成。<a href="https://example.invalid/source">测试来源</a>';
        send.setAttribute('aria-label', '发送');
        if (END) document.querySelector('[data-chat-answers-wrap]').insertAdjacentHTML('beforeend', END);
      }, 600);
    };
    </script>'''.replace('CONFIG', json.dumps(ADAPTERS[platform])).replace('QUESTION', json.dumps(question)).replace('ANSWER', json.dumps(answer)).replace('END', json.dumps(end)) , login


ADAPTERS = json.loads((ROOT/'adapters.json').read_text(encoding='utf-8'))


async def browser():
    from playwright.async_api import async_playwright
    from real_collector import Adapter, CollectionError, run_in_page
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        try:
            for platform in ('yuanbao', 'qianwen'):
                with tempfile.TemporaryDirectory() as temp:
                    page = await browser.new_page()
                    html, login = fixture(platform)
                    async def serve_fixture(route):
                        await route.fulfill(body=html, content_type='text/html')
                    await page.route(PLATFORM_URLS[platform]+'**', serve_fixture)
                    config = ADAPTERS[platform]
                    job = {'job_id': 'fixture', 'task_id': 'fixture', 'platform': platform,
                           'request': {'question': '测试问题', 'web_search': None}}
                    events = []
                    async def report(stage, checkpoint=None):
                        if stage == 'submitting':
                            assert await page.locator(config['question']).count() == 0
                        events.append((stage, checkpoint))
                    result = await run_in_page(page, job, report, Path(temp), config)
                    assert result['answer_complete'] and '模拟测试完成' in result['answer']
                    assert result['adapter_verified'] is False and result['references_complete'] is False
                    assert result['references'][0]['displayed_url'] == 'https://example.invalid/source'
                    assert (Path(temp)/'answer-001.png').read_bytes().startswith(b'\x89PNG')
                    job['checkpoint'] = next(c for _, c in reversed(events) if c and c.get('submission') == 'submitted')
                    # The fixture has no persistent server; only test recovery with mismatched URL rejected.
                    job['checkpoint'] = job['checkpoint'] | {'conversation_url': 'https://example.invalid/wrong'}
                    try:
                        await run_in_page(page, job, report, Path(temp), config)
                        raise AssertionError('Foreign recovery URL accepted')
                    except CollectionError as exc:
                        assert exc.code == 'SUBMISSION_UNCERTAIN'
                    job.pop('checkpoint')
                    job['request']['web_search'] = True
                    try:
                        await run_in_page(page, job, report, Path(temp), config)
                        raise AssertionError('Unverified search option accepted')
                    except CollectionError as exc:
                        assert exc.code == 'UNSUPPORTED_OPTION'
                        assert await page.locator(config['question']).count() == 0
                    await page.set_content(login)
                    try:
                        adapter = Adapter(page, config)
                        adapter.login_timeout = 0.05
                        adapter.login_poll_interval = 0.01
                        await adapter.check_login(platform)
                        raise AssertionError('Login marker not detected')
                    except CollectionError as exc:
                        assert exc.code == 'LOGIN_REQUIRED'
                    await page.close()
        finally:
            await browser.close()
    print('PASS: new-platform configured selectors on simulated DOM, contenteditable input, completion, PNG/HTML, links, login and no unsafe resend. NOT real acceptance.')


if __name__ == '__main__':
    protocol()
    asyncio.run(browser())
