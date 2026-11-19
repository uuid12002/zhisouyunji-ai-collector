"""Authenticated local agent HTTP controllers; no browser or storage access."""

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from typing import Literal
from collector.presentation.http.responses import asset_response

router = APIRouter(prefix='/api/agent')


@router.post('/shutdown')
def shutdown(request: Request):
    request.app.state.shutdown()
    return {'status': 'draining', 'message': '正在停止接口，等待当前采集结束'}


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    platforms: list[str] = Field(min_length=1, max_length=4)
    questions: list[str] = Field(min_length=1, max_length=100)
    interaction_mode: Literal['browser', 'desktop_rpa'] = 'desktop_rpa'
    thinking_mode: StrictBool | None = None


@router.get('/capabilities')
def capabilities(request: Request):
    return request.app.state.agent.capabilities()


@router.post('/batches', status_code=202)
def create(body: BatchRequest, request: Request, idempotency_key: str = Header()):
    return request.app.state.agent.create(**body.model_dump(), key=idempotency_key)


@router.get('/batches')
def batches(request: Request):
    return request.app.state.agent.list_batches()


@router.get('/batches/{batch_id}')
def batch(batch_id: str, request: Request):
    return request.app.state.agent.batch(batch_id)


@router.get('/batches/{batch_id}/results')
def results(batch_id: str, request: Request, item_id: str | None = None):
    return request.app.state.agent.results(batch_id, item_id)


@router.post('/batches/{batch_id}/stop')
def stop(batch_id: str, request: Request):
    return request.app.state.agent.stop(batch_id)


@router.get('/batches/{batch_id}/items/{item_id}/files/{name}')
def file(batch_id: str, item_id: str, name: str, request: Request):
    return asset_response(request.app.state.agent.asset(batch_id, item_id, name))
