"""Local HTTP controllers: validation, delegation and response formatting only."""

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from collector.application import contracts as dto
from collector.application.local_tasks import LocalTaskService
from .dependencies import local_service, worker_token
from .responses import asset_response, claimed_response, task_response

router = APIRouter()


@router.get('/')
def home(request: Request):
    return FileResponse(request.app.state.settings.root / 'static/index.html')


@router.get('/static/client-onboarding.js')
def client_script(request: Request):
    return FileResponse(
        request.app.state.settings.root / 'static/client-onboarding.js',
        media_type='text/javascript',
    )


@router.post('/api/v2/tasks', status_code=201)
def create_task(
    body: dto.CreateTask,
    response: Response,
    idempotency_key: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    created = service.create_task(body, idempotency_key)
    response.status_code = 200 if created.replayed else 201
    return task_response(created.task)


@router.get('/api/v2/tasks')
def list_tasks(service: LocalTaskService = Depends(local_service)):
    return [task_response(task) for task in service.list_tasks()]


@router.get('/api/v2/tasks/{task_id}')
@router.get('/api/v2/tasks/{task_id}/result')
def task(task_id: str, service: LocalTaskService = Depends(local_service)):
    return task_response(service.task(task_id))


@router.post('/api/v2/worker/claim')
def claim(
    body: dto.LocalClaim = dto.LocalClaim(),
    authorization: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    return claimed_response(service.claim(body, worker_token(authorization)))


@router.post('/api/v2/worker/jobs/{job_id}/heartbeat')
def heartbeat(
    job_id: str,
    body: dto.Progress,
    authorization: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    return service.heartbeat(job_id, body, worker_token(authorization))


@router.post('/api/v2/worker/jobs/{job_id}/complete')
def complete(
    job_id: str,
    body: dto.Complete,
    authorization: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    return service.complete(job_id, body, worker_token(authorization))


@router.post('/api/v2/worker/jobs/{job_id}/artifacts')
def upload(
    job_id: str,
    body: dto.Upload,
    authorization: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    return service.upload(job_id, body, worker_token(authorization))


@router.post('/api/v2/worker/jobs/{job_id}/fail')
def fail(
    job_id: str,
    body: dto.Failure,
    authorization: str | None = Header(default=None),
    service: LocalTaskService = Depends(local_service),
):
    return service.fail(job_id, body, worker_token(authorization))


@router.get('/api/v2/tasks/{task_id}/artifacts/{job_id}')
def download(task_id: str, job_id: str, service: LocalTaskService = Depends(local_service)):
    return asset_response(service.download(task_id, job_id))


@router.get('/api/v2/tasks/{task_id}/files/{artifact_id}')
def file_download(
    task_id: str, artifact_id: str, service: LocalTaskService = Depends(local_service)
):
    return asset_response(service.file_download(task_id, artifact_id))


@router.post('/api/v2/jobs/{job_id}/resume')
def resume(job_id: str, service: LocalTaskService = Depends(local_service)):
    return service.resume(job_id)


@router.get('/health')
def health(service: LocalTaskService = Depends(local_service)):
    return service.health()
