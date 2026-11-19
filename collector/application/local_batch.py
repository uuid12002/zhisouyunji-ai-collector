"""Serial local batch execution with per-item checkpoints and no implicit retry."""

from collector.domain.batches import batch_items
from collector.domain.interaction import validate_interaction_mode, RPA_PLATFORMS
from .runtime_ports import LocalBatchStorage
import time
from collector.domain.thinking import validate_thinking_mode


async def run_local_batch(store: LocalBatchStorage, lock, run_item, render_report):
    interaction_mode = validate_interaction_mode(store.request.get('interaction_mode', 'browser'))
    thinking_mode = validate_thinking_mode(store.request.get('thinking_mode'))
    items = batch_items(store.request.get('platforms'), store.request.get('questions'))
    for item in items:
        item['thinking_mode'] = thinking_mode
        item['interaction_mode'] = (
            interaction_mode if item['platform'] in RPA_PLATFORMS else 'browser'
        )
    with lock:
        if store.started():
            raise RuntimeError('此批次已经执行过，不会自动重新发送；请先核对原结果。')
        state = {'status': 'running', 'stage': 'queued', 'total': len(items), 'items': items}
        blocked = {}

        def publish(with_report=True):
            state['finished'] = sum(item['status'] not in ('queued', 'running') for item in items)
            store.publish(state, render_report(state) if with_report else None)

        publish()  # Durable batch marker must precede every platform submission.
        try:
            for item in items:
                if store.stop_requested():
                    item.update(status='cancelled', stage='cancelled', error='已停止后续任务')
                    publish()
                    continue
                if item['platform'] in blocked:
                    item.update(
                        status='skipped', stage='waiting_human', error=blocked[item['platform']]
                    )
                    publish()
                    continue
                item.update(status='running', stage='checking_login', stage_started_at=time.time())
                state['stage'] = 'collecting'
                publish()

                async def progress(stage, checkpoint=None):
                    if item['stage'] != stage:
                        item['stage_started_at'] = time.time()
                    item['stage'] = stage
                    if stage == 'browser_error' and checkpoint:
                        details = checkpoint.get('browser_error', {})
                        item.update(code=details.get('code'), error=details.get('message'))
                    publish(with_report=stage == 'browser_error')

                try:
                    request_path = store.prepare_item(item)
                    result = await run_item(request_path, progress)
                    item.update(
                        status='completed' if result.get('exports_complete') else 'partial',
                        stage='done',
                        answer_complete=bool(result.get('answer_complete')),
                        exports_complete=bool(result.get('exports_complete')),
                        report=f'items/{item["id"]}/report.html',
                    )
                    if not result.get('exports_complete'):
                        item.update(
                            code='EXPORT_INCOMPLETE',
                            error='；'.join(result.get('limitations', []))
                            or '部分导出未完成，请查看结果。',
                        )
                except Exception as exc:
                    item.update(
                        status='failed',
                        stage='stopped',
                        error=str(exc),
                        code=getattr(exc, 'code', type(exc).__name__),
                    )
                    if getattr(exc, 'human', True):
                        blocked[item['platform']] = '该平台需要人工处理：' + str(exc)
                publish()
            state['status'] = (
                'stopped'
                if any(i['status'] == 'cancelled' for i in items)
                else 'completed'
                if all(i['status'] == 'completed' for i in items)
                else 'partial'
                if any(i['status'] in ('completed', 'partial') for i in items)
                else 'failed'
            )
            state['stage'] = 'done'
            publish()
            return state
        except BaseException as exc:
            state.update(status='interrupted', stage='stopped', error=str(exc))
            publish()
            raise
