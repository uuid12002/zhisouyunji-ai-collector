"""Enforce dependency direction and critical use-case behavior without a browser."""

import ast
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from collector.application.collection import run_collection
from collector.application.contracts import CreateTask, Progress
from collector.domain.errors import CollectionError
from collector.domain.policies import expired_remote_lease, merge_checkpoint

ROOT = Path(__file__).resolve().parent


class ArchitectureTests(unittest.TestCase):
    def test_human_handoff_progress_contract(self):
        for stage in (
            'waiting_login',
            'waiting_human',
            'waiting_overlay',
            'waiting_window',
            'browser_error',
            'resuming',
        ):
            self.assertEqual(Progress(lease_token='lease', stage=stage).stage, stage)

    def test_layer_dependencies(self):
        forbidden = {
            'domain': (
                'collector.application',
                'collector.infrastructure',
                'collector.presentation',
                'fastapi',
                'playwright',
                'sqlite3',
                'pydantic',
            ),
            'application': (
                'collector.infrastructure',
                'collector.presentation',
                'fastapi',
                'starlette',
                'playwright',
                'sqlite3',
                'urllib.request',
            ),
            'presentation': ('collector.infrastructure', 'sqlite3', 'playwright', 'urllib.request'),
            'infrastructure': ('collector.presentation',),
        }
        old_modules = {
            'app',
            'remote_server',
            'desktop_client',
            'real_collector',
            'real_worker',
            'browser_runtime',
            'capture',
            'sharing',
            'sources',
            'platforms',
            'local_runner',
        }
        for layer, prefixes in forbidden.items():
            for path in (ROOT / 'collector' / layer).rglob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [item.name for item in node.names]
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ''
                        if node.level:
                            package = path.parent.relative_to(ROOT).parts
                            module = '.'.join(
                                (*package[: len(package) - node.level + 1], module)
                            ).rstrip('.')
                        names = [module]
                    for name in names:
                        with self.subTest(file=str(path.relative_to(ROOT)), dependency=name):
                            self.assertFalse(
                                any(
                                    name == prefix or name.startswith(prefix + '.')
                                    for prefix in prefixes
                                )
                            )
                            self.assertNotIn(
                                name,
                                old_modules,
                                'Production packages must not depend on compatibility entry points',
                            )
                if layer in ('application', 'presentation', 'domain'):
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                            self.assertNotIn(
                                node.func.attr,
                                ('execute', 'executemany', 'executescript')
                                if layer != 'application'
                                else ('executemany', 'executescript'),
                                str(path),
                            )
                            if layer in ('application', 'domain'):
                                self.assertNotIn(
                                    node.func.attr,
                                    (
                                        'read_text',
                                        'write_text',
                                        'read_bytes',
                                        'write_bytes',
                                        'mkdir',
                                    ),
                                    str(path),
                                )

    def test_checkpoint_is_monotonic(self):
        with self.assertRaises(ValueError):
            merge_checkpoint({'submission': 'submitted'}, {'submission': 'not_submitted'})
        self.assertEqual(
            merge_checkpoint({'submission': 'submitted'}, {'capture_ready': True}),
            {'submission': 'submitted', 'capture_ready': True},
        )

    def test_expired_submission_never_migrates(self):
        self.assertEqual(
            expired_remote_lease({'submission': 'submission_uncertain'}, 1, 'original'),
            ('waiting_human', 'SUBMISSION_UNCERTAIN', 'original'),
        )
        self.assertEqual(
            expired_remote_lease({'capture_ready': True}, 1, 'original'),
            ('queued', 'LEASE_EXPIRED', 'original'),
        )
        self.assertEqual(expired_remote_lease({}, 1, 'original'), ('queued', 'LEASE_EXPIRED', None))

    def test_app_factory_isolates_storage(self):
        from collector.bootstrap import create_app
        from collector.infrastructure.settings import ServerSettings

        with tempfile.TemporaryDirectory() as temp:
            apps = []
            for name in ('first', 'second'):
                data = Path(temp) / name
                data.mkdir()
                settings = ServerSettings(ROOT, data, 'test-worker-key', '', ('localhost',))
                apps.append(create_app(settings=settings))
            created = apps[0].state.local_service.create_task(
                CreateTask(question='isolated fixture', platforms=['doubao']), 'fixture'
            )
            self.assertFalse(created.replayed)
            self.assertEqual(len(apps[0].state.local_service.list_tasks()), 1)
            self.assertEqual(apps[1].state.local_service.list_tasks(), [])

    def test_remote_migration_preserves_tasks_and_enables_wal(self):
        from collector.bootstrap import create_app
        from collector.infrastructure.settings import ServerSettings

        with tempfile.TemporaryDirectory() as temp:
            settings = ServerSettings(ROOT, Path(temp), 'worker-fixture', 'a' * 32, ('localhost',))
            original = create_app(settings=settings)
            created = original.state.local_service.create_task(
                CreateTask(question='existing task', platforms=['doubao']), 'migration-fixture'
            )
            migrated = create_app(remote=True, settings=settings)
            self.assertEqual(
                migrated.state.local_service.task(created.task['id'])['question'], 'existing task'
            )
            with closing(sqlite3.connect(Path(temp) / 'collector.db')) as db:
                self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')


class FakeAdapter:
    url = 'https://www.doubao.com/chat/fixture'

    def __init__(self, done=True):
        self.done, self.sent, self.exported = done, False, False

    async def navigate(self, url):
        self.url = url

    async def dismiss_known_overlays(self):
        pass

    async def check_login(self, platform):
        pass

    def validate_rules(self):
        pass

    async def new_chat(self):
        pass

    async def has_messages(self):
        return False

    async def configure(self, requested):
        return {}

    async def configure_thinking(self, requested):
        return {'thinking_mode': requested, 'thinking_mode_status': 'verified'}

    async def confirm_thinking(self, requested):
        pass

    async def fill_question(self, question):
        pass

    async def send(self):
        self.sent = True

    async def confirm_question(self, question):
        pass

    async def wait_finished(self, question, timeout=300):
        return self.done

    async def collect(self, *args):
        self.exported = True
        return {'answer': 'fixture', 'limitations': []}


class CollectionUseCaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_cannot_change_the_original_thinking_mode(self):
        from unittest.mock import AsyncMock

        adapter = FakeAdapter()
        adapter.configure_thinking = AsyncMock(
            side_effect=AssertionError('must not switch an existing conversation')
        )

        async def report(stage, checkpoint=None):
            pass

        job = {
            'platform': 'doubao',
            'request': {'question': 'fixture', 'thinking_mode': True},
            'checkpoint': {
                'submission': 'submitted',
                'conversation_url': adapter.url,
                'actual_options': {'thinking_mode': False, 'thinking_mode_status': 'verified'},
            },
        }
        with self.assertRaises(CollectionError) as error:
            await run_collection(adapter, job, report, Path('.'), {}, {'doubao': adapter.url})
        self.assertEqual(error.exception.code, 'THINKING_MODE_UNVERIFIED')
        adapter.configure_thinking.assert_not_awaited()
        self.assertFalse(adapter.sent)

    async def test_send_requires_durable_acknowledgment(self):
        adapter = FakeAdapter()

        async def report(stage, checkpoint=None):
            if stage == 'submitting':
                raise OSError('checkpoint unavailable')

        with self.assertRaises(OSError):
            await run_collection(
                adapter,
                {'platform': 'doubao', 'request': {'question': 'fixture'}},
                report,
                Path('.'),
                {},
                {'doubao': adapter.url},
            )
        self.assertFalse(adapter.sent)
        self.assertFalse(adapter.exported)

    async def test_unfinished_answer_is_not_exported(self):
        adapter = FakeAdapter(done=False)

        async def report(stage, checkpoint=None):
            pass

        with self.assertRaises(CollectionError) as caught:
            await run_collection(
                adapter,
                {'platform': 'doubao', 'request': {'question': 'fixture'}},
                report,
                Path('.'),
                {},
                {'doubao': adapter.url},
            )
        self.assertEqual(caught.exception.code, 'GENERATION_TIMEOUT')
        self.assertTrue(adapter.sent)
        self.assertFalse(adapter.exported)


if __name__ == '__main__':
    unittest.main(verbosity=2)
