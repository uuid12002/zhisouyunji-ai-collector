"""HTTP origin and authentication policies; contains no persistence access."""

import base64
import secrets
from fastapi.responses import JSONResponse, RedirectResponse, Response
from collector.domain.errors import ApplicationError
from .responses import error_response


async def local_origin_guard(request, call_next):
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and (
        request.headers.get('sec-fetch-site') == 'cross-site'
        or (origin and origin != f'{request.url.scheme}://{request.headers.get("host")}')
    ):
        return Response('Cross-origin requests are not allowed', status_code=403)
    return await call_next(request)


async def authentication(request, call_next):
    path = request.url.path
    if path == '/health':
        return await call_next(request)
    settings = request.app.state.settings
    try:
        if path.startswith('/api/v3/worker/'):
            service = request.app.state.device_service
            auth = request.headers.get('authorization', '')
            token = auth.removeprefix('Bearer ') if auth.startswith('Bearer ') else ''
            request.state.device = service.authenticate_device(
                request.headers.get('x-device-id', ''),
                token,
                request.headers.get('x-worker-session', ''),
                heartbeat=path == '/api/v3/worker/heartbeat',
            )
            if path.startswith('/api/v3/worker/jobs/'):
                parts = path.split('/')
                service.assert_job_owner(parts[5], request.state.device['id'])
        else:
            auth = request.headers.get('authorization', '')
            valid = secrets.compare_digest(auth, 'Bearer ' + settings.admin_key)
            if auth.startswith('Basic '):
                try:
                    user, password = base64.b64decode(auth[6:]).decode().split(':', 1)
                    valid = user == 'admin' and secrets.compare_digest(password, settings.admin_key)
                except (ValueError, UnicodeError):
                    valid = False
            if not valid:
                return JSONResponse(
                    {'detail': '请使用管理员凭据登录'},
                    status_code=401,
                    headers={'WWW-Authenticate': 'Basic realm="Collector", charset="UTF-8"'},
                )
            if path.startswith('/api/v2/worker/') or (
                path == '/api/v2/tasks' and request.method == 'POST'
            ):
                return JSONResponse({'detail': '远程服务仅接受 v3 任务和设备协议'}, status_code=403)
            if path == '/':
                return RedirectResponse('/console')
    except ApplicationError as error:
        return error_response(error)
    return await call_next(request)
