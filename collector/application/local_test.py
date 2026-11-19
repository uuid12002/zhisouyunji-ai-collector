"""Local test use case; collection, persistence and presentation are injected."""

from .runtime_ports import LocalTestStorage, Collector
import asyncio
from collector.domain.platforms import PLATFORM_NAMES
from collector.domain.interaction import validate_interaction_mode
from collector.domain.thinking import validate_thinking_mode


async def run_local_test(
    store: LocalTestStorage, lock, collect: Collector, render_report, on_progress=None
):
    request = store.request
    platform = request.get('platform')
    question = request.get('question', '')
    interaction_mode = validate_interaction_mode(request.get('interaction_mode', 'browser'))
    thinking_mode = validate_thinking_mode(request.get('thinking_mode'))
    if (
        platform not in PLATFORM_NAMES
        or not isinstance(question, str)
        or not question.strip()
        or len(question) > 10000
    ):
        raise ValueError('请选择平台并输入 1～10000 字的问题')
    with lock:
        if store.started():
            raise RuntimeError('此测试已经执行过，请先核对结果，不会自动重复发送。')
        checkpoint = {}

        async def report(stage, state=None):
            if state:
                checkpoint.update(state)
            store.checkpoint(checkpoint)
            status = {'status': 'running', 'stage': stage}
            if stage == 'browser_error' and state:
                details = state.get('browser_error', {})
                status.update(code=details.get('code'), error=details.get('message'))
            store.status(status)
            if on_progress:
                await on_progress(stage, state)

        await report('checking_login')
        job = {
            'job_id': store.job_id,
            'task_id': store.job_id,
            'platform': platform,
            'attempt': 1,
            'checkpoint': {},
            'request': {
                'question': question,
                'mode': 'real',
                'web_search': None,
                'interaction_mode': interaction_mode,
                'thinking_mode': thinking_mode,
            },
        }
        try:
            result = await asyncio.wait_for(collect(job, report, store.folder), timeout=2400)
            store.result(result, render_report(question, result))
            store.status(
                {
                    'status': 'completed',
                    'stage': 'done',
                    'answer_complete': result.get('answer_complete', False),
                }
            )
            return result
        except Exception as exc:
            store.status(
                {
                    'status': 'failed',
                    'stage': 'stopped',
                    'error': str(exc),
                    'code': getattr(exc, 'code', type(exc).__name__),
                    'submission': checkpoint.get('submission', 'not_submitted'),
                }
            )
            raise
