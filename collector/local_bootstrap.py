"""Assemble the local-only collection path; no coordinator is involved."""

from collector.application.local_test import run_local_test
from collector.infrastructure.local_test import LocalTestStore
from collector.infrastructure.browser.runtime import profile_lock
from collector.collection_bootstrap import collect
from collector.presentation.report import render_report
from contextlib import nullcontext
from collector.application.local_batch import run_local_batch
from collector.infrastructure.local_batch import LocalBatchStore
from collector.presentation.batch_report import render_batch_report


async def run(request_path):
    return await run_local_test(
        LocalTestStore(request_path), profile_lock('worker-instance'), collect, render_report
    )


async def run_batch(request_path):
    async def run_item(path, on_progress):
        return await run_local_test(
            LocalTestStore(path), nullcontext(), collect, render_report, on_progress
        )

    return await run_local_batch(
        LocalBatchStore(request_path),
        profile_lock('worker-instance'),
        run_item,
        render_batch_report,
    )
