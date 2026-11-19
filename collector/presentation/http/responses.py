"""HTTP response conversion and URLs are presentation concerns."""

from fastapi.responses import FileResponse, JSONResponse, Response
from collector.domain.errors import ErrorKind

STATUS = {
    ErrorKind.UNAUTHORIZED: 401,
    ErrorKind.FORBIDDEN: 403,
    ErrorKind.NOT_FOUND: 404,
    ErrorKind.CONFLICT: 409,
    ErrorKind.INVALID_REQUEST: 422,
}


def error_response(error):
    return JSONResponse({'detail': error.message}, status_code=STATUS[error.kind])


def asset_response(asset):
    return FileResponse(
        asset.path, media_type=asset.mime, filename=asset.filename, headers=asset.headers
    )


def claimed_response(job):
    return Response(status_code=204) if job is None else job


def task_response(task):
    for job in task['jobs']:
        result = job.get('result')
        if not result:
            continue
        if result.get('mock'):
            result['artifact_url'] = f'/api/v2/tasks/{task["id"]}/artifacts/{job["id"]}'
        for artifact in result.get('artifacts', []):
            artifact['url'] = f'/api/v2/tasks/{task["id"]}/files/{artifact["id"]}'
    return task
