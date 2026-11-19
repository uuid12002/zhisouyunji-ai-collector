"""Desktop actions; UI widgets and OS operations are injected dependencies."""

from .runtime_ports import DesktopRuntime
from .client_config import validate_config
from collector.domain.batches import parse_questions, validate_batch
from collector.domain.interaction import validate_interaction_mode
from .platform_catalog import platform_catalog, validate_planned
from collector.domain.thinking import validate_thinking_mode


class DesktopController:
    def browser_sessions(self):
        return self.runtime.browser_sessions()

    def __init__(self, runtime: DesktopRuntime):
        self.runtime = runtime

    def load_configuration(self):
        return self.runtime.load_configuration()

    def platform_catalog(self):
        return platform_catalog(self.runtime.planned_platforms())

    def reserve_platform(self, value):
        entry = validate_planned(value)
        planned = self.runtime.planned_platforms()
        if len(planned) >= 24:
            raise ValueError('最多预留 24 个平台')
        if any(item['id'] == entry['id'] for item in planned):
            raise ValueError('该平台编号已经预留')
        self.runtime.save_planned_platforms([*planned, entry])
        return entry

    def remove_reserved_platform(self, key):
        if key in [item['id'] for item in self.platform_catalog() if item['available']]:
            raise ValueError('不能移除已适配平台')
        self.runtime.save_planned_platforms(
            [item for item in self.runtime.planned_platforms() if item['id'] != key]
        )

    def recent_collections(self):
        return self.runtime.recent_collections()

    def saved_output(self, folder, item_id, kind):
        if kind not in ('answer', 'image', 'share', 'report'):
            raise ValueError('未知结果类型')
        return self.runtime.saved_output(folder, item_id, kind)

    def read_config(self):
        return validate_config(self.load_configuration())

    def import_connection(self, path):
        raw = self.runtime.read_connection(path)
        config = validate_config(
            {key: raw[key] for key in ('server_url', 'device_id', 'device_key')}
            | {'platforms': ['doubao'], 'slots': 1, 'auto_start': False}
        )
        self.runtime.save_configuration(config)
        return config

    def save_configuration(self, config):
        validated = validate_config(config)
        previous = self.runtime.configuration_bytes()
        try:
            self.runtime.save_configuration(validated)
            self.runtime.set_startup(config.get('auto_start') is True)
        except Exception:
            self.runtime.restore_configuration(previous)
            raise

    def set_startup(self, enabled):
        self.runtime.set_startup(enabled)

    def spawn(self, *args):
        return self.runtime.spawn(*args)

    def clear_stop(self):
        self.runtime.clear_stop()

    def stop(self):
        self.runtime.stop()

    def open_home(self):
        self.runtime.open_home()

    def worker_status(self):
        return self.runtime.worker_status()

    def local_status(self, folder):
        return self.runtime.local_status(folder)

    def create_local_test(self, platform, question):
        return self.runtime.create_local_test(platform, question)

    def open_result(self, folder):
        self.runtime.open_result(folder)

    def preview_local_batch(
        self, platforms, text, mode='lines', interaction_mode='browser', thinking_mode=None
    ):
        platforms, questions = validate_batch(platforms, parse_questions(text, mode))
        return {
            'platforms': platforms,
            'questions': questions,
            'interaction_mode': validate_interaction_mode(interaction_mode),
            'thinking_mode': validate_thinking_mode(thinking_mode),
        }

    def create_local_batch(
        self, platforms, text, mode='lines', interaction_mode='browser', thinking_mode=None
    ):
        return self.runtime.create_local_batch(
            self.preview_local_batch(platforms, text, mode, interaction_mode, thinking_mode)
        )

    def stop_local_batch(self, folder):
        self.runtime.stop_local_batch(folder)

    def open_batch_item(self, folder, item_id):
        self.runtime.open_batch_item(folder, item_id)
