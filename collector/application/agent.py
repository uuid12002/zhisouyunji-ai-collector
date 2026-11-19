"""Agent-facing use cases; execution and persistence stay behind a port."""

from typing import Protocol
from collector.domain.batches import validate_batch
from collector.domain.interaction import RPA_PLATFORMS, validate_interaction_mode
from collector.domain.platforms import PLATFORM_NAMES
from collector.domain.errors import ApplicationError, ErrorKind
from collector.domain.thinking import validate_thinking_mode


class AgentQueue(Protocol):
    def accept(self, request: dict, key: str) -> dict: ...
    def list_batches(self) -> list[dict]: ...
    def batch(self, batch_id: str) -> dict: ...
    def results(self, batch_id: str, item_id: str | None = None) -> dict: ...
    def stop(self, batch_id: str) -> dict: ...
    def asset(self, batch_id: str, item_id: str, name: str): ...


class AgentService:
    def __init__(self, queue: AgentQueue):
        self.queue = queue

    def capabilities(self):
        return {
            'platforms': PLATFORM_NAMES,
            'rpa_platforms': list(RPA_PLATFORMS),
            'execution': 'serial',
            'browser_lifecycle': 'resident',
            'max_questions': 100,
            'human_verification': 'pause_for_user',
            'automatic_resubmit': False,
            'thinking_mode': {'values': [None, True, False], 'verification': 'before_submission'},
            'stop_policy': 'finish_current_then_cancel_pending',
        }

    def create(self, platforms, questions, interaction_mode, key, thinking_mode=None):
        try:
            platforms, questions = validate_batch(platforms, questions)
            mode = validate_interaction_mode(interaction_mode)
            thinking_mode = validate_thinking_mode(thinking_mode)
            if not isinstance(key, str) or not 1 <= len(key) <= 200:
                raise ValueError('必须提供 1～200 字的 Idempotency-Key；重试使用同一个值')
        except ValueError as exc:
            raise ApplicationError(ErrorKind.INVALID_REQUEST, str(exc)) from exc
        payload = {'platforms': platforms, 'questions': questions, 'interaction_mode': mode}
        if thinking_mode is not None:
            payload['thinking_mode'] = thinking_mode
        return self.queue.accept(payload, key)

    def list_batches(self):
        return self.queue.list_batches()

    def batch(self, batch_id):
        return self.queue.batch(batch_id)

    def results(self, batch_id, item_id=None):
        return self.queue.results(batch_id, item_id)

    def stop(self, batch_id):
        return self.queue.stop(batch_id)

    def asset(self, batch_id, item_id, name):
        return self.queue.asset(batch_id, item_id, name)
