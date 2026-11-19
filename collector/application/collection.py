"""Collection use case; coordinates a browser port without knowing Playwright."""

from urllib.parse import urlparse
from collector.domain.errors import CollectionError
from .collection_ports import CollectionAdapter
from collector.domain.thinking import validate_thinking_mode


async def run_collection(adapter: CollectionAdapter, job, report, folder, config, urls):
    state = job.get('checkpoint') or {}
    question = job['request']['question']
    thinking_mode = validate_thinking_mode(job['request'].get('thinking_mode'))
    await report('checking_login')
    recovering = state.get('submission') in ('submitted', 'submission_uncertain')
    if recovering:
        url = state.get('conversation_url')
        if not url or urlparse(url).hostname != urlparse(urls[job['platform']]).hostname:
            raise CollectionError(
                'SUBMISSION_UNCERTAIN', '没有可恢复的对话地址。请人工检查原会话，不能自动重新发送。'
            )
        await adapter.navigate(url)
    else:
        await adapter.navigate(urls[job['platform']])
    await adapter.dismiss_known_overlays()
    await adapter.check_login(job['platform'])
    adapter.validate_rules()
    if not recovering:
        await report('opening_chat')
        await adapter.new_chat()
        await adapter.check_login(job['platform'])
        if await adapter.has_messages():
            raise CollectionError('NEW_CHAT_FAILED', '新对话未清空，停止以避免混入历史回答。')
        await report('configuring')
        actual = await adapter.configure(job['request'].get('web_search'))
        actual.update(await adapter.configure_thinking(thinking_mode))
        await adapter.fill_question(question)
        await adapter.confirm_thinking(thinking_mode)
        await report(
            'submitting',
            {
                'submission': 'submission_uncertain',
                'conversation_url': adapter.url,
                'actual_options': actual,
            },
        )
        await adapter.send()
        try:
            await adapter.confirm_question(question)
        except CollectionError as exc:
            if exc.code in ('HUMAN_VERIFICATION', 'BROWSER_CLOSED'):
                raise
            raise CollectionError(
                'SUBMISSION_UNCERTAIN', '发送后无法确认用户消息。请检查原会话，程序不会重复提问。'
            ) from exc
        except Exception:
            raise CollectionError(
                'SUBMISSION_UNCERTAIN', '发送后无法确认用户消息。请检查原会话，程序不会重复提问。'
            )
        await report('generating', {'submission': 'submitted', 'conversation_url': adapter.url})
    else:
        if thinking_mode is not None and (
            state.get('actual_options', {}).get('thinking_mode') is not thinking_mode
            or state.get('actual_options', {}).get('thinking_mode_status') != 'verified'
        ):
            raise CollectionError(
                'THINKING_MODE_UNVERIFIED',
                '原会话没有匹配的思考模式核验记录；不会切换模式或重复提问。',
            )
        await adapter.current_question(question)
        actual = state.get('actual_options', {})
        await report('generating', {'submission': 'submitted', 'conversation_url': adapter.url})
    done = await adapter.wait_finished(
        question, timeout=900 if actual.get('thinking_mode') is True else 300
    )
    if not done:
        raise CollectionError(
            'GENERATION_TIMEOUT', '回答尚未确认生成结束，已保留原会话，请稍后恢复采集。'
        )
    await report('capturing')
    result = await adapter.collect(question, folder, done, job['platform'])
    result['actual_options'] = actual
    result['job_id'] = job['job_id']
    result['task_id'] = job['task_id']
    result['platform'] = job['platform']
    result['adapter_verified'] = config.get('verified', False)
    if config.get('verified') is False:
        result['limitations'].append('该平台适配规则尚未完成登录后真实问答验收。')
    return result
