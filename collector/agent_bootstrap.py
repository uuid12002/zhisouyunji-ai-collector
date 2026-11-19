"""Composition roots for the local agent API and the stdio MCP bridge."""

import asyncio
from contextlib import asynccontextmanager
import secrets
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from collector.application.agent import AgentService
from collector.domain.errors import ApplicationError
from collector.infrastructure.agent_queue import AgentFileQueue, supervise_queue
from collector.infrastructure.spool import write_json
from collector.presentation.agent_http import router
from collector.presentation.http.middleware import local_origin_guard
from collector.presentation.http.responses import error_response
from collector.version import VERSION


def create_agent_app(home, token, spawn):
    queue = AgentFileQueue(home / 'data' / 'agent-batches')

    @asynccontextmanager
    async def lifespan(app):
        queue.recover()
        task = asyncio.create_task(supervise_queue(queue, spawn))
        try:
            yield
        finally:
            queue.stop_pending()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title='WebCollector Agent API', version=VERSION, lifespan=lifespan)
    app.state.agent = AgentService(queue)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]'])
    app.middleware('http')(local_origin_guard)

    @app.middleware('http')
    async def authorize(request: Request, call_next):
        if not secrets.compare_digest(
            request.headers.get('authorization', '').encode(), ('Bearer ' + token).encode()
        ):
            return JSONResponse({'detail': '需要智能体接口密钥'}, status_code=401)
        return await call_next(request)

    @app.exception_handler(ApplicationError)
    async def application_error(request, error):
        return error_response(error)

    app.include_router(router)
    return app


def run_server(runtime, port=18765):
    import uvicorn
    from collector.infrastructure.browser.runtime import profile_lock

    # Prevent another service from recovering or dispatching the same queue.
    with profile_lock('agent-service'):
        token_path = runtime.home / 'agent.key'
        if not token_path.exists():
            token_path.write_text(secrets.token_urlsafe(32), encoding='utf-8')
        token = token_path.read_text(encoding='utf-8').strip()
        if len(token) < 24:
            raise ValueError('agent.key 密钥过短，请删除后重新启动接口')
        app = create_agent_app(runtime.home, token, runtime.spawn)
        connection = {'base_url': f'http://127.0.0.1:{port}', 'token': token}
        write_json(runtime.home / 'agent-connection.json', connection)
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, access_log=False))
        app.state.shutdown = lambda: setattr(server, 'should_exit', True)
        server.run()


def run_mcp():
    from collector.infrastructure.agent_gateway import AgentGateway, read_connection
    from collector.presentation.agent_mcp import build_mcp, serve_stdio
    import anyio

    gateway = AgentGateway(**read_connection())
    anyio.run(serve_stdio, build_mcp(gateway))


if __name__ == '__main__':
    import sys

    if '--mcp' in sys.argv:
        run_mcp()
    else:
        from collector.infrastructure.client_runtime import ClientRuntime

        run_server(ClientRuntime())
