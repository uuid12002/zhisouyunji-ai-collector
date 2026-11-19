"""Persist browser failures before cleanup and retain a failed RPA scene briefly."""

import asyncio
from datetime import datetime, timezone
import json
import traceback
import time

from collector.version import VERSION


async def record_browser_failure(page, folder, exc, platform, mode, stage):
    record = {
        'version': VERSION,
        'platform': platform,
        'interaction_mode': mode,
        'stage': stage,
        'code': getattr(exc, 'code', type(exc).__name__),
        'message': str(exc),
        'traceback': ''.join(traceback.format_exception(exc)),
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'browser_url': page.url,
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'diagnostic.json').write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    try:
        if not page.is_closed():
            await page.screenshot(path=str(folder / 'diagnostic.png'), timeout=5000)
    except Exception:
        pass
    print(f'[{platform}] {record["code"]}: {record["message"]}', flush=True)
    return record


async def retain_failed_browser(context, report, record, timeout=300, poll_interval=0.5):
    if not context.pages:
        return
    await report(
        'browser_error',
        {
            'browser_error': {
                'code': record['code'],
                'message': record['message'],
                'file': 'diagnostic.json',
            }
        },
    )
    # No clicking, typing, navigation or retry while the failed scene is retained.
    deadline = time.monotonic() + timeout
    while context.pages and time.monotonic() < deadline:
        await asyncio.sleep(poll_interval)
