"""Browser-session factory and legacy function adapters, no task business rules."""

import hashlib
import json
import os
from pathlib import Path
from playwright.async_api import async_playwright
from collector.application.collection import run_collection
from collector.domain.errors import CollectionError
from collector.domain.interaction import validate_interaction_mode, RPA_PLATFORMS
from collector.infrastructure.browser.runtime import ROOT, URLS, profile_lock, open_context
from collector.infrastructure.browser.adapter import Adapter
from collector.infrastructure.browser.diagnostics import (
    record_browser_failure,
    retain_failed_browser,
)


async def run_in_page(page, job, report, folder, config):
    mode = validate_interaction_mode(job['request'].get('interaction_mode', 'browser'))
    if mode == 'desktop_rpa' and job['platform'] not in RPA_PLATFORMS:
        raise CollectionError('RPA_UNSUPPORTED', '本次桌面 RPA 试用仅支持豆包和 DeepSeek。')
    config = config | {
        'interaction_mode': mode,
        'requested_thinking_mode': job['request'].get('thinking_mode'),
    }
    adapter = Adapter(page, config, report)
    try:
        return await run_collection(adapter, job, report, folder, config, URLS)
    finally:
        adapter.actions.close()


async def collect(job, report, folder, *, resident=True):
    config_path = Path(os.environ.get('COLLECTOR_ADAPTERS', str(ROOT / 'adapters.json')))
    adapter_raw = config_path.read_bytes()
    config = json.loads(adapter_raw.decode('utf-8')).get(job['platform'])
    if not config:
        raise CollectionError(
            'ADAPTER_NOT_CONFIGURED', '适配配置缺少当前平台，请更新 adapters.json 后恢复任务。'
        )
    mode = validate_interaction_mode(job['request'].get('interaction_mode', 'browser'))
    if mode == 'desktop_rpa' and job['platform'] not in RPA_PLATFORMS:
        raise CollectionError('RPA_UNSUPPORTED', '本次桌面 RPA 试用仅支持豆包和 DeepSeek。')
    if resident:
        from collector.infrastructure.browser.sessions import session_page

        async with async_playwright() as pw:
            async with session_page(pw, job['platform']) as (_, page):
                last_stage = 'checking_login'

                async def resident_report(stage, state=None):
                    nonlocal last_stage
                    last_stage = stage
                    await report(stage, state)

                try:
                    result = await run_in_page(page, job, resident_report, folder, config)
                    result['adapter_sha256'] = hashlib.sha256(adapter_raw).hexdigest()
                    result['browser_lifecycle'] = 'resident'
                    return result
                except Exception as exc:
                    try:
                        record = await record_browser_failure(
                            page, folder, exc, job['platform'], mode, last_stage
                        )
                        await resident_report(
                            'browser_error',
                            {
                                'browser_error': {
                                    'code': record['code'],
                                    'message': record['message'],
                                    'file': 'diagnostic.json',
                                    'retained': 'resident',
                                }
                            },
                        )
                    except Exception as diagnostic_error:
                        print(f'Browser diagnostics: {diagnostic_error}', flush=True)
                    raise
        # The session owner remains alive; no context.close() in this branch.
    with profile_lock(job['platform']):
        async with async_playwright() as pw:
            context = await open_context(pw, job['platform'], desktop_rpa=mode == 'desktop_rpa')
            page = context.pages[0] if context.pages else await context.new_page()
            last_stage = 'checking_login'

            async def tracked_report(stage, state=None):
                nonlocal last_stage
                last_stage = stage
                await report(stage, state)

            try:
                result = await run_in_page(page, job, tracked_report, folder, config)
                result['adapter_sha256'] = hashlib.sha256(adapter_raw).hexdigest()
                return result
            except Exception as exc:
                # Persist before closing the browser: batched failures previously had
                # no traceback and an immediate close looked like a browser crash.
                try:
                    record = await record_browser_failure(
                        page, folder, exc, job['platform'], mode, last_stage
                    )
                    if mode == 'desktop_rpa':
                        await retain_failed_browser(context, tracked_report, record)
                except Exception as diagnostic_error:
                    print(f'Browser diagnostics: {diagnostic_error}', flush=True)
                raise
            finally:
                await context.close()
