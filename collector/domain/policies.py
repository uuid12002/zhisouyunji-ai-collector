"""Pure task-state rules. No HTTP, SQL, filesystem or browser imports."""


def task_status(jobs):
    states = [job['status'] for job in jobs]
    if 'running' in states:
        return 'running'
    if 'queued' in states:
        return 'queued'
    if 'waiting_human' in states:
        return 'waiting_human'
    if all(state == 'succeeded' for state in states):
        return 'succeeded'
    return 'partial' if any(job['result'] for job in jobs) else 'failed'


def merge_checkpoint(previous, update):
    rank = {'not_submitted': 0, 'submission_uncertain': 1, 'submitted': 2}
    before = rank.get(previous.get('submission', 'not_submitted'), 0)
    after = rank.get(update.get('submission', previous.get('submission', 'not_submitted')), -1)
    if after < before:
        raise ValueError('提交检查点不能回退')
    return previous | update


def expired_remote_lease(checkpoint, attempt, worker):
    submitted = checkpoint.get('submission') in ('submitted', 'submission_uncertain')
    captured = checkpoint.get('capture_ready') is True
    pinned = submitted or captured
    status = (
        ('queued' if captured and attempt < 3 else 'waiting_human')
        if pinned
        else ('failed' if attempt >= 3 else 'queued')
    )
    return (
        status,
        ('SUBMISSION_UNCERTAIN' if submitted else 'LEASE_EXPIRED'),
        worker if pinned else None,
    )
