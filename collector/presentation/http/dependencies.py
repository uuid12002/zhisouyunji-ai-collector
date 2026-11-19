from fastapi import Request


def local_service(request: Request):
    return request.app.state.local_service


def device_service(request: Request):
    return request.app.state.device_service


def worker_token(authorization: str | None):
    return (
        authorization.removeprefix('Bearer ')
        if authorization and authorization.startswith('Bearer ')
        else ''
    )
