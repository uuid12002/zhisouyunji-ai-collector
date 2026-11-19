"""Batch input and expansion rules, independent of UI and storage."""

import re
from .platforms import PLATFORM_NAMES

MAX_QUESTIONS = 100


def parse_questions(text: str, mode: str = 'lines') -> list[str]:
    if mode == 'lines':
        parts = text.splitlines()
    elif mode == 'blocks':
        parts = re.split(r'\n[\t \r]*\n', text.replace('\r\n', '\n'))
    else:
        raise ValueError('未知的问题分隔方式')
    return [part.strip() for part in parts if part.strip()]


def validate_batch(platforms, questions):
    if not isinstance(platforms, (list, tuple)) or not platforms:
        raise ValueError('请至少选择一个平台')
    if any(not isinstance(p, str) or p not in PLATFORM_NAMES for p in platforms):
        raise ValueError('包含不支持的平台')
    if not isinstance(questions, (list, tuple)) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise ValueError(f'请输入 1～{MAX_QUESTIONS} 个问题')
    if any(not isinstance(q, str) or not q.strip() or len(q) > 10000 for q in questions):
        raise ValueError('每个问题必须为 1～10000 字')
    return list(dict.fromkeys(platforms)), [q.strip() for q in questions]


def batch_items(platforms, questions):
    platforms, questions = validate_batch(platforms, questions)
    return [
        {
            'id': f'{index + 1:04d}-{platform}',
            'question_index': index + 1,
            'question': question,
            'platform': platform,
            'status': 'queued',
            'stage': 'queued',
        }
        for index, question in enumerate(questions)
        for platform in platforms
    ]
