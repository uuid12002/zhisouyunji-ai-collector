"""Local batch files, child requests, progress and cancellation signal."""

import json
from pathlib import Path
from .spool import write_json
from collector.domain.interaction import RPA_PLATFORMS, validate_interaction_mode
from collector.domain.thinking import validate_thinking_mode


class LocalBatchStore:
    def __init__(self, request_path):
        path = Path(request_path)
        self.request = json.loads(path.read_text(encoding='utf-8'))
        self.folder = path.parent

    def started(self):
        return (self.folder / 'status.json').exists()

    def stop_requested(self):
        return (self.folder / 'stop.request').exists()

    def prepare_item(self, item):
        folder = self.folder / 'items' / item['id']
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'request.json'
        if path.exists():
            raise RuntimeError('子任务目录已存在，已停止，避免重复发送')
        mode = validate_interaction_mode(self.request.get('interaction_mode', 'browser'))
        if item['platform'] not in RPA_PLATFORMS:
            mode = 'browser'
        write_json(
            path,
            {
                'platform': item['platform'],
                'question': item['question'],
                'interaction_mode': mode,
                'thinking_mode': validate_thinking_mode(self.request.get('thinking_mode')),
            },
        )
        return path

    def publish(self, state, report):
        write_json(self.folder / 'status.json', state)
        if report is not None:
            temp = self.folder / 'report.html.tmp'
            temp.write_text(report, encoding='utf-8')
            temp.replace(self.folder / 'report.html')
