"""Device/admin controllers; v3 endpoints delegate to the same task use cases."""

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import HTMLResponse
from collector.application import contracts as dto
from collector.application.devices import DeviceService
from collector.application.local_tasks import LocalTaskService
from .dependencies import device_service, local_service
from .responses import claimed_response, task_response

router = APIRouter()


@router.post('/api/v3/devices')
def add_device(body: dto.DeviceCreate, service: DeviceService = Depends(device_service)):
    return service.add_device(body)


@router.post('/api/v3/devices/{device_id}/control')
def control_device(
    device_id: str, body: dto.DeviceControl, service: DeviceService = Depends(device_service)
):
    return service.control_device(device_id, body)


@router.get('/api/v3/devices')
def devices(service: DeviceService = Depends(device_service)):
    return service.devices()


@router.post('/api/v3/tasks', status_code=201)
def create_task(
    body: dto.TaskCreate,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=128),
    service: DeviceService = Depends(device_service),
):
    created = service.create_task(body, idempotency_key)
    response.status_code = 200 if created.replayed else 201
    return task_response(created.task)


@router.post('/api/v3/worker/heartbeat')
def device_heartbeat(
    body: dto.Beat, request: Request, service: DeviceService = Depends(device_service)
):
    return service.device_heartbeat(
        body, request.state.device['id'], request.headers['x-worker-session']
    )


@router.post('/api/v3/worker/claim')
def claim(
    body: dto.RemoteClaim, request: Request, service: DeviceService = Depends(device_service)
):
    return claimed_response(service.claim(body, request.state.device['id']))


@router.post('/api/v3/worker/sync')
def sync(request: Request, service: DeviceService = Depends(device_service)):
    return service.sync(request.state.device['id'])


@router.post('/api/v3/worker/jobs/{job_id}/heartbeat')
def heartbeat(job_id: str, body: dto.Progress, service: LocalTaskService = Depends(local_service)):
    return service.heartbeat(job_id, body, service.key)


@router.post('/api/v3/worker/jobs/{job_id}/complete')
def complete(job_id: str, body: dto.Complete, service: LocalTaskService = Depends(local_service)):
    return service.complete(job_id, body, service.key)


@router.post('/api/v3/worker/jobs/{job_id}/artifacts')
def upload(job_id: str, body: dto.Upload, service: LocalTaskService = Depends(local_service)):
    return service.upload(job_id, body, service.key)


@router.post('/api/v3/worker/jobs/{job_id}/fail')
def fail(job_id: str, body: dto.Failure, service: LocalTaskService = Depends(local_service)):
    return service.fail(job_id, body, service.key)


@router.get('/console', response_class=HTMLResponse)
def console(request: Request):
    return (request.app.state.settings.root / 'static/remote.html').read_text(encoding='utf-8')
