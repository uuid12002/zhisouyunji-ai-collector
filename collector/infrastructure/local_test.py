import json
from pathlib import Path
from .spool import write_json


class LocalTestStore:
    def __init__(self, request_path):
        path = Path(request_path)
        self.request = json.loads(path.read_text(encoding='utf-8'))
        self.folder = path.parent
        self.job_id = self.folder.name

    def started(self):
        return (self.folder / 'status.json').exists()

    def checkpoint(self, value):
        write_json(self.folder / 'checkpoint.json', value)

    def status(self, value):
        write_json(self.folder / 'status.json', value)

    def result(self, value, html):
        write_json(self.folder / 'result.json', value)
        (self.folder / 'report.html').write_text(html, encoding='utf-8')
