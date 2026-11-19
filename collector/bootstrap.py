"""Composition root: concrete implementations are assembled only here."""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from collector.version import VERSION
from starlette.middleware.trustedhost import TrustedHostMiddleware
from collector.application.local_tasks import LocalTaskService
from collector.application.devices import DeviceService
from collector.domain.errors import ApplicationError
from collector.infrastructure.artifacts import ArtifactStore
from collector.infrastructure.database import SqliteStore
from collector.infrastructure.settings import ServerSettings
from collector.presentation.http import local, remote as remote_routes
from collector.presentation.http.middleware import authentication, local_origin_guard
from collector.presentation.http.responses import error_response


def create_app(remote=False, settings=None):
    settings = settings or ServerSettings.from_environment(remote=remote)
    store = SqliteStore(settings.data)
    store.initialize(remote=remote)
    tasks = LocalTaskService(store, ArtifactStore(settings.data), settings.worker_key)
    devices = DeviceService(store, tasks, settings.global_limit) if remote else None

    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                with store.transaction() as repository:
                    repository.begin()
                    (devices if remote else tasks).recover(repository)
                await asyncio.sleep(5 if remote else 10)

        sweeper = asyncio.create_task(sweep())
        try:
            yield
        finally:
            sweeper.cancel()
            await asyncio.gather(sweeper, return_exceptions=True)

    app = FastAPI(title='云电脑采集系统', version=VERSION, lifespan=lifespan)
    app.state.settings = settings
    app.state.local_service = tasks
    app.state.device_service = devices
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.middleware('http')(local_origin_guard)
    if remote:
        app.middleware('http')(authentication)

    @app.exception_handler(ApplicationError)
    async def application_error(request, error):
        return error_response(error)

    app.include_router(local.router)
    if remote:
        app.include_router(remote_routes.router)
    return app
