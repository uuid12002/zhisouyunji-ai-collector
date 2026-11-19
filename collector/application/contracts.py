"""Input validation and transport-neutral use-case results."""

from dataclasses import dataclass
from pathlib import Path
from pydantic import BaseModel, Field, StrictBool
from typing import Literal
from collector.domain.platforms import Platform


class CreateTask(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    platforms: list[Platform] = Field(min_length=1, max_length=4)
    web_search: bool | None = None
    thinking_mode: StrictBool | None = None
    mode: Literal['mock', 'real'] = 'real'
    interaction_mode: Literal['browser', 'desktop_rpa'] = 'browser'


class LocalClaim(BaseModel):
    mode: Literal['mock', 'real'] = 'mock'


class Progress(BaseModel):
    lease_token: str
    stage: Literal[
        'checking_login',
        'waiting_login',
        'waiting_human',
        'waiting_overlay',
        'waiting_window',
        'browser_error',
        'resuming',
        'opening_chat',
        'configuring',
        'submitting',
        'generating',
        'extracting',
        'capturing',
        'uploading',
    ]
    checkpoint: dict | None = None


class Complete(BaseModel):
    lease_token: str
    answer: str = Field(max_length=200000)
    evidence: dict | None = None


class Upload(BaseModel):
    lease_token: str
    kind: Literal[
        'screenshot',
        'answer_long_image',
        'answer_text',
        'answer_html',
        'manifest',
        'sources_screenshot',
        'diagnostic',
    ]
    mime: Literal['image/png', 'text/html', 'text/plain', 'application/json']
    sha256: str = Field(pattern='^[0-9a-f]{64}$')
    content_base64: str = Field(max_length=28000000)


class Failure(BaseModel):
    lease_token: str
    code: str = Field(max_length=100)
    message: str = Field(max_length=2000)
    waiting_human: bool = True


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    max_slots: int = Field(default=1, ge=1, le=2)


class DeviceControl(BaseModel):
    enabled: bool = True
    max_slots: int = Field(default=1, ge=0, le=2)


class Beat(BaseModel):
    platforms: list[Platform] = Field(max_length=4)
    capacity: int = Field(ge=0, le=2)
    memory_load: int = Field(default=0, ge=0, le=100)
    draining: bool = False


class TaskCreate(CreateTask):
    priority: int = Field(default=0, ge=0, le=10)


class RemoteClaim(BaseModel):
    request_id: str = Field(min_length=16, max_length=80)


@dataclass(frozen=True)
class CreatedTask:
    task: dict
    replayed: bool = False


@dataclass(frozen=True)
class FileAsset:
    path: Path
    mime: str
    filename: str | None = None
    headers: dict | None = None
