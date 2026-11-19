"""Agent API/queue and real MCP stdio transport tests; never opens platform pages."""

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient
import uvicorn
from collector.agent_bootstrap import create_agent_app
from collector.application.agent import AgentService
from collector.domain.errors import ApplicationError
from collector.infrastructure.agent_queue import AgentFileQueue
from collector.infrastructure.spool import write_json

ROOT = Path(__file__).resolve().parent
TOKEN = 'local-test-token-' + 'x' * 32
PAYLOAD = {
    'platforms': ['doubao'],
    'questions': ['本地模拟问题'],
    'interaction_mode': 'desktop_rpa',
}


class FixtureSpawner:
    def __init__(self):
        self.calls, self.active, self.max_active = [], 0, 0

    def __call__(self, *args):
        owner = self
        folder = Path(args[1]).parent
        owner.calls.append(args)
        owner.active += 1
        owner.max_active = max(owner.max_active, owner.active)
        write_json(folder / 'status.json', {'status': 'running', 'stage': 'waiting_human'})

        class Process:
            returncode = None
            deadline = time.monotonic() + 0.35

            def poll(self):
                if self.returncode is None and time.monotonic() >= self.deadline:
                    item = folder / 'items' / '0001-doubao'
                    item.mkdir(parents=True)
                    (item / 'answer.txt').write_text('模拟回答，不是平台回答。', encoding='utf-8')
                    from PIL import Image

                    Image.new('RGB', (10, 20), 'blue').save(item / 'answer-long.png')
                    write_json(
                        item / 'result.json',
                        {
                            'answer': '模拟回答，不是平台回答。',
                            'share_url': 'https://example.com/shared/fixture',
                            'answer_complete': True,
                            'exports_complete': True,
                        },
                    )
                    write_json(folder / 'status.json', {'status': 'completed', 'stage': 'done'})
                    self.returncode = 0
                    owner.active -= 1
                return self.returncode

        return Process(), 'fixture'


def headers(key=None):
    return {'Authorization': 'Bearer ' + TOKEN, **({'Idempotency-Key': key} if key else {})}


def wait_completed(client, batch_id):
    for _ in range(100):
        value = client.get('/api/agent/batches/' + batch_id, headers=headers()).json()
        if value['status'] == 'completed':
            return value
        time.sleep(0.03)
    raise AssertionError(value)


class AgentTests(unittest.TestCase):
    def test_thinking_request_is_preserved_and_changes_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = AgentFileQueue(tmp)
            service = AgentService(queue)
            created = service.create(**PAYLOAD, key='thinking', thinking_mode=True)
            request = json.loads(
                (queue.folder(created['id']) / 'request.json').read_text(encoding='utf-8')
            )
            self.assertIs(request['thinking_mode'], True)
            with self.assertRaises(ApplicationError):
                service.create(**PAYLOAD, key='thinking', thinking_mode=False)
            default = service.create(**PAYLOAD, key='old-default')
            self.assertTrue(
                service.create(**PAYLOAD, key='old-default', thinking_mode=None)['replayed']
            )
            self.assertIsNotNone(default['id'])
            for invalid in ['true', 1, {}]:
                with self.assertRaises(ApplicationError):
                    service.create(**PAYLOAD, key='invalid', thinking_mode=invalid)

    def test_http_auth_validation_idempotency_serial_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            spawner = FixtureSpawner()
            app = create_agent_app(Path(tmp), TOKEN, spawner)
            with TestClient(app, base_url='http://127.0.0.1') as client:
                self.assertEqual(client.get('/api/agent/capabilities').status_code, 401)
                caps = client.get('/api/agent/capabilities', headers=headers()).json()
                self.assertEqual(caps['rpa_platforms'], ['doubao', 'deepseek'])
                self.assertEqual(
                    client.post('/api/agent/batches', json=PAYLOAD, headers=headers()).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        '/api/agent/batches',
                        json=PAYLOAD | {'platforms': ['bad']},
                        headers=headers('invalid'),
                    ).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        '/api/agent/batches',
                        json=PAYLOAD,
                        headers=headers('cross') | {'Origin': 'https://example.com'},
                    ).status_code,
                    403,
                )
                response = client.post('/api/agent/batches', json=PAYLOAD, headers=headers('first'))
                self.assertEqual(response.status_code, 202, response.text)
                first = response.json()['id']
                replay = client.post(
                    '/api/agent/batches', json=PAYLOAD, headers=headers('first')
                ).json()
                self.assertEqual(replay['id'], first)
                self.assertTrue(replay['replayed'])
                self.assertEqual(
                    client.post(
                        '/api/agent/batches',
                        json=PAYLOAD | {'questions': ['changed']},
                        headers=headers('first'),
                    ).status_code,
                    409,
                )
                second = client.post(
                    '/api/agent/batches', json=PAYLOAD, headers=headers('second')
                ).json()['id']
                wait_completed(client, first)
                wait_completed(client, second)
                self.assertEqual(spawner.max_active, 1)
                self.assertEqual(len(spawner.calls), 2)
                result = client.get(f'/api/agent/batches/{first}/results', headers=headers()).json()
                self.assertEqual(
                    result['items'][0]['result']['share_url'], 'https://example.com/shared/fixture'
                )
                path = f'/api/agent/batches/{first}/items/0001-doubao/files/'
                self.assertIn('模拟回答', client.get(path + 'answer.txt', headers=headers()).text)
                self.assertEqual(client.get(path + 'agent.key', headers=headers()).status_code, 404)
                self.assertEqual(
                    client.get('/api/agent/batches/' + 'f' * 32, headers=headers()).status_code, 404
                )

    def test_queued_stop_and_restart_never_redispatch_uncertain(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = AgentFileQueue(tmp)
            service = AgentService(queue)
            first = service.create(**PAYLOAD, key='one')['id']
            service.stop(first)
            self.assertIsNone(queue.take())
            second = service.create(**PAYLOAD, key='two')['id']
            self.assertEqual(queue.take().name, second)
            restarted = AgentFileQueue(tmp)
            restarted.recover()
            self.assertEqual(restarted.batch(second)['status'], 'interrupted')
            self.assertIsNone(restarted.take())
            self.assertTrue(service.create(**PAYLOAD, key='two')['replayed'])
            self.assertEqual(service.batch(second)['status'], 'interrupted')

    def test_artifact_path_and_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = AgentFileQueue(Path(tmp) / 'queue')
            batch_id = queue.accept(PAYLOAD, 'files')['id']
            for item, name in [
                ('..', 'answer.txt'),
                ('0001-doubao', '../agent.key'),
                ('0001-doubao', '..\\agent.key'),
            ]:
                with self.assertRaises(ApplicationError):
                    queue.asset(batch_id, item, name)
            folder = queue.folder(batch_id) / 'items' / '0001-doubao'
            folder.mkdir(parents=True)
            secret = Path(tmp) / 'secret.txt'
            secret.write_text('secret')
            try:
                (folder / 'answer.txt').symlink_to(secret)
            except OSError:
                return  # Windows may not permit creating symlinks without developer mode.
            with self.assertRaises(ApplicationError):
                queue.asset(batch_id, '0001-doubao', 'answer.txt')

    def test_shutdown_drains_current_and_stops_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            spawner = FixtureSpawner()
            app = create_agent_app(Path(tmp), TOKEN, spawner)
            with TestClient(app, base_url='http://127.0.0.1') as client:
                first = client.post(
                    '/api/agent/batches', json=PAYLOAD, headers=headers('one')
                ).json()['id']
                for _ in range(50):
                    if spawner.calls:
                        break
                    time.sleep(0.02)
                self.assertEqual(len(spawner.calls), 1)
                second = client.post(
                    '/api/agent/batches', json=PAYLOAD, headers=headers('two')
                ).json()['id']
            self.assertEqual(spawner.active, 0)
            self.assertEqual(app.state.agent.batch(first)['status'], 'completed')
            self.assertEqual(app.state.agent.batch(second)['status'], 'stopped')


@contextmanager
def fixture_server(home):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    app = create_agent_app(home, TOKEN, FixtureSpawner())
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.02)
    assert server.started
    connection = home / 'connection.json'
    write_json(connection, {'base_url': f'http://127.0.0.1:{port}', 'token': TOKEN})
    try:
        yield connection
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()


async def mcp_roundtrip(connection, executable=None):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    parameters = StdioServerParameters(
        command=str(executable or sys.executable),
        args=[] if executable else [str(ROOT / 'agent_mcp.py')],
        env=dict(os.environ, COLLECTOR_AGENT_CONNECTION=str(connection)),
    )
    stderr = tempfile.TemporaryFile(mode='w+', encoding='utf-8')
    async with stdio_client(parameters, errlog=stderr) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert len(names) == 7, names
            caps = await session.call_tool('collector_capabilities', {})
            assert not caps.isError, caps
            first = await session.call_tool(
                'create_collection', {**PAYLOAD, 'idempotency_key': 'mcp-fixture', 'thinking_mode': True}
            )
            assert not first.isError, first
            batch_id = json.loads(first.content[0].text)['id']
            stored = connection.parent / 'data' / 'agent-batches' / batch_id / 'request.json'
            assert json.loads(stored.read_text(encoding='utf-8'))['thinking_mode'] is True
            replay = await session.call_tool(
                'create_collection', {**PAYLOAD, 'idempotency_key': 'mcp-fixture', 'thinking_mode': True}
            )
            assert json.loads(replay.content[0].text)['replayed']
            for _ in range(100):
                status = await session.call_tool('get_collection', {'batch_id': batch_id})
                if json.loads(status.content[0].text)['status'] == 'completed':
                    break
                await asyncio.sleep(0.03)
            results = await session.call_tool(
                'get_collection_results', {'batch_id': batch_id, 'item_id': '0001-doubao'}
            )
            assert not results.isError
            assert 'https://example.com/shared/fixture' in results.content[0].text
            text = await session.call_tool(
                'read_collection_file',
                {'batch_id': batch_id, 'item_id': '0001-doubao', 'name': 'answer.txt'},
            )
            assert not text.isError and '模拟回答' in text.content[0].text
            image = await session.call_tool(
                'read_collection_file',
                {'batch_id': batch_id, 'item_id': '0001-doubao', 'name': 'answer-long.png'},
            )
            assert not image.isError and image.content[0].type == 'image', image
            forbidden = await session.call_tool(
                'read_collection_file',
                {'batch_id': batch_id, 'item_id': '0001-doubao', 'name': '..\\agent.key'},
            )
            assert forbidden.isError
            stopped = await session.call_tool('stop_collection', {'batch_id': batch_id})
            assert not stopped.isError
    stderr.seek(0)
    diagnostics = stderr.read()
    stderr.close()
    assert 'Traceback' not in diagnostics, diagnostics


def packaged_api_smoke(executable):
    import hashlib
    import subprocess
    from urllib.request import Request, urlopen

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, COLLECTOR_CLIENT_HOME=str(home))
        with (home / 'api-test.log').open('w', encoding='utf-8') as log:
            process = subprocess.Popen(
                [str(executable), '--agent-server', '--agent-port', str(port)],
                env=env,
                stdout=log,
                stderr=log,
            )
            try:
                connection = None
                for _ in range(200):
                    path = home / 'agent-connection.json'
                    if path.exists():
                        connection = json.loads(path.read_text(encoding='utf-8'))
                        break
                    assert process.poll() is None, (home / 'client.log').read_text(encoding='utf-8')
                    time.sleep(0.1)
                assert connection

                def call(method, path, data=None, key=None):
                    request_headers = {'Authorization': 'Bearer ' + connection['token']}
                    if key:
                        request_headers['Idempotency-Key'] = key
                    if data is not None:
                        request_headers['Content-Type'] = 'application/json'
                    request = Request(
                        connection['base_url'] + '/api/agent' + path,
                        method=method,
                        data=json.dumps(data).encode() if data is not None else None,
                        headers=request_headers,
                    )
                    with urlopen(request, timeout=5) as response:
                        return json.load(response)

                for _ in range(100):
                    try:
                        assert call('GET', '/capabilities')['execution'] == 'serial'
                        break
                    except OSError:
                        time.sleep(0.1)
                # Pre-cancel before dispatch: exercise the actual frozen child batch path
                # without ever opening a platform or sending a question.
                key = 'packaged-agent-cancelled-fixture'
                batch_id = hashlib.sha256(key.encode()).hexdigest()[:32]
                folder = home / 'data' / 'agent-batches' / batch_id
                folder.mkdir(parents=True)
                (folder / 'stop.request').write_text('pre-cancel fixture')
                payload = PAYLOAD | {'thinking_mode': False}
                created = call('POST', '/batches', payload, key)
                assert created['id'] == batch_id
                for _ in range(200):
                    state = call('GET', '/batches/' + batch_id)
                    if state['status'] in ('stopped', 'failed'):
                        break
                    time.sleep(0.1)
                assert state['status'] == 'stopped', state
                assert all(item['status'] == 'cancelled' for item in state['items'])
                assert all(item['thinking_mode'] is False for item in state['items'])
                assert not (folder / 'items').exists()
                assert call('POST', '/batches', payload, key)['replayed']
                # Also run a second EXE to test its real stop command and shared connection file.
                subprocess.run([str(executable), '--agent-stop'], env=env, check=True, timeout=40)
                assert process.wait(timeout=40) == 0
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
        print(
            'PASS: packaged authenticated API, actual frozen cancelled batch child, idempotency and stop CLI'
        )


if __name__ == '__main__':
    if '--exe' in sys.argv:
        packaged_api_smoke(Path(sys.argv[sys.argv.index('--exe') + 1]).resolve())
    elif '--mcp' in sys.argv:
        executable = (
            sys.argv[sys.argv.index('--mcp') + 1]
            if len(sys.argv) > sys.argv.index('--mcp') + 1
            else None
        )
        with tempfile.TemporaryDirectory() as tmp:
            with fixture_server(Path(tmp)) as connection:
                asyncio.run(mcp_roundtrip(connection, executable))
        print(
            'PASS: real MCP stdio -> authenticated HTTP -> serial fixture queue -> text/image/share result'
        )
    else:
        unittest.main(verbosity=2)
