"""MCP-to-HTTP transport. No retries of commands and no redirects of credentials."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse
import httpx


def read_connection():
    home = Path(
        os.environ.get(
            'COLLECTOR_CLIENT_HOME',
            str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'WebCollector'),
        )
    )
    path = Path(os.environ.get('COLLECTOR_AGENT_CONNECTION', str(home / 'agent-connection.json')))
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise RuntimeError('请先在客户端启动智能体接口，再连接 MCP') from exc
    return {'base_url': value['base_url'], 'token': value['token']}


class AgentGateway:
    def __init__(self, base_url, token):
        url = urlparse(base_url)
        if (
            url.scheme != 'http'
            or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or url.username
            or url.password
            or url.path not in ('', '/')
            or url.query
            or url.fragment
        ):
            raise ValueError('智能体接口只允许本机 HTTP 地址')
        self.base_url, self.token = base_url.rstrip('/'), token

    async def request(self, method, path, body=None, key=None, binary=False):
        headers = {'Authorization': 'Bearer ' + self.token}
        if key is not None:
            headers['Idempotency-Key'] = key
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
            response = await client.request(
                method, self.base_url + '/api/agent' + path, json=body, headers=headers
            )
            if response.status_code >= 300:
                raise RuntimeError(
                    f'采集接口拒绝请求 ({response.status_code}): {response.text[:1000]}'
                )
            if binary:
                return response.content, response.headers.get('content-type', '')
            return response.json()
