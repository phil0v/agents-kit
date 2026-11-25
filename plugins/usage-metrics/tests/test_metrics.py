import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RECORDER_PATH = PACKAGE_ROOT / 'scripts' / 'metrics.py'
SPEC = importlib.util.spec_from_file_location('usage_metrics_recorder',
                                              RECORDER_PATH)
recorder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recorder)

METRICS_ENV_KEYS = ('AGENTS_HOME', 'AGENTS_METRICS', 'AGENTS_METRICS_MODULE',
                    'AGENTS_RUN_ID')
EVENT_SCHEMA = {'ts', 'run', 'plugin', 'event', 'status', 'seconds'}


class MetricsTestCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        for key in METRICS_ENV_KEYS:
            os.environ.pop(key, None)
        self.agents_home = Path(tempfile.mkdtemp(prefix='usage-metrics-home-'))
        self.addCleanup(shutil.rmtree, self.agents_home, True)
        os.environ['AGENTS_HOME'] = str(self.agents_home)

    def usage_log(self):
        return self.agents_home / 'metrics' / 'usage.jsonl'

    def recorded_events(self):
        lines = self.usage_log().read_text(encoding='utf-8').splitlines()
        return [json.loads(line) for line in lines]

    def run_cli(self, arguments):
        return subprocess.run(
            [sys.executable, str(RECORDER_PATH)] + arguments,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True)

    def block_target(self):
        blocker = self.agents_home / 'blocker'
        blocker.write_text('occupied', encoding='utf-8')
        os.environ['AGENTS_HOME'] = str(blocker / 'nested')


class NewRunIdTest(MetricsTestCase):
    def test_ids_are_distinct_hex_strings(self):
        self.assertRegex(recorder.new_run_id(), r'^[0-9a-f]{32}$')
        self.assertNotEqual(recorder.new_run_id(), recorder.new_run_id())


class RecordTest(MetricsTestCase):
    def test_appends_one_schema_event_creating_the_directory(self):
        self.assertFalse(self.usage_log().exists())
        self.assertTrue(
            recorder.record('code-flow', 'run-check', 'ok', 1.25))
        event = self.recorded_events()[0]
        self.assertEqual(set(event), EVENT_SCHEMA)
        self.assertEqual(event['plugin'], 'code-flow')
        self.assertEqual(event['event'], 'run-check')
        self.assertEqual(event['status'], 'ok')
        self.assertEqual(event['seconds'], 1.25)
        self.assertEqual(
            datetime.fromisoformat(event['ts']).utcoffset(), timedelta(0))

    def test_uninherited_events_open_fresh_run_ids(self):
        recorder.record('local-guards', 'backup-ref', 'ok', 1)
        recorder.record('local-guards', 'backup-ref', 'ok', 1)
        first, second = self.recorded_events()
        self.assertNotEqual(first['run'], second['run'])

    def test_run_id_is_inherited_from_agents_run_id(self):
        os.environ['AGENTS_RUN_ID'] = 'run-42'
        recorder.record('local-guards', 'backup-ref', 'ok', 1)
        recorder.record('local-guards', 'deny-git-push', 'fail', 2)
        self.assertEqual([event['run'] for event in self.recorded_events()],
                         ['run-42', 'run-42'])

    def test_optional_fields_travel_and_payloads_are_dropped(self):
        recorder.record('code-flow', 'role-profile', 'ok', 3,
                        role='reviewer', model='glm-4.7')
        recorder.record('code-flow', 'review-manifest', 'ok', 4,
                        round_kind='initial-correctness')
        recorder.record('code-flow', 'run-check', 'ok', 5,
                        payload='x', path='/repo/secret', filename='a.txt')
        dispatch, manifest, plain = self.recorded_events()
        self.assertEqual(set(dispatch), EVENT_SCHEMA | {'role', 'model'})
        self.assertEqual(dispatch['role'], 'reviewer')
        self.assertEqual(dispatch['model'], 'glm-4.7')
        self.assertEqual(set(manifest), EVENT_SCHEMA | {'round_kind'})
        self.assertEqual(manifest['round_kind'], 'initial-correctness')
        self.assertEqual(set(plain), EVENT_SCHEMA)

    def test_home_default_applies_when_agents_home_is_unset_or_empty(self):
        os.environ['AGENTS_HOME'] = ''
        os.environ['HOME'] = str(self.agents_home)
        self.assertTrue(
            recorder.record('code-flow', 'run-check', 'ok', 1))
        os.environ.pop('AGENTS_HOME')
        self.assertTrue(
            recorder.record('code-flow', 'run-check', 'ok', 1))
        default_log = (self.agents_home / '.agents' / 'metrics' /
                       'usage.jsonl')
        lines = default_log.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(lines), 2)


class RecordNoopTest(MetricsTestCase):
    def test_metrics_off_disables_recording_entirely(self):
        os.environ['AGENTS_METRICS'] = 'off'
        self.assertFalse(
            recorder.record('code-flow', 'run-check', 'ok', 1))
        self.assertFalse((self.agents_home / 'metrics').exists())

    def test_unwritable_target_is_a_silent_noop(self):
        self.block_target()
        self.assertFalse(
            recorder.record('code-flow', 'run-check', 'ok', 1))
        self.assertFalse(self.usage_log().exists())

    def test_non_finite_seconds_is_a_silent_noop(self):
        for seconds in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(seconds=seconds):
                self.assertFalse(
                    recorder.record('code-flow', 'run-check', 'ok', seconds))
        self.assertFalse(self.usage_log().exists())


class RecordCliTest(MetricsTestCase):
    def test_cli_appends_the_event_with_optional_fields(self):
        result = self.run_cli(
            ['record', '--plugin', 'local-guards', '--event', 'backup-ref',
             '--status', 'ok', '--seconds', '2.5',
             '--role', 'worker', '--model', 'glm-4.7'])
        self.assertEqual(result.returncode, 0, result.stderr)
        event = self.recorded_events()[0]
        self.assertEqual(event['plugin'], 'local-guards')
        self.assertEqual(event['event'], 'backup-ref')
        self.assertEqual(event['status'], 'ok')
        self.assertEqual(event['seconds'], 2.5)
        self.assertEqual(event['role'], 'worker')
        self.assertEqual(event['model'], 'glm-4.7')

    def test_cli_records_round_kind(self):
        result = self.run_cli(
            ['record', '--plugin', 'code-flow', '--event', 'review-manifest',
             '--status', 'ok', '--seconds', '1',
             '--round-kind', 'final-style'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded_events()[0]['round_kind'],
                         'final-style')

    def test_cli_inherits_run_id_from_environment(self):
        os.environ['AGENTS_RUN_ID'] = 'run-7'
        result = self.run_cli(
            ['record', '--plugin', 'code-flow', '--event', 'run-check',
             '--status', 'ok', '--seconds', '0.1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.recorded_events()[0]['run'], 'run-7')

    def test_cli_exits_zero_when_recording_is_disabled(self):
        os.environ['AGENTS_METRICS'] = 'off'
        result = self.run_cli(
            ['record', '--plugin', 'local-guards', '--event', 'edit-checks',
             '--status', 'ok', '--seconds', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.usage_log().exists())

    def test_cli_exits_zero_on_unwritable_target(self):
        self.block_target()
        result = self.run_cli(
            ['record', '--plugin', 'local-guards', '--event', 'edit-checks',
             '--status', 'fail', '--seconds', '1'])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_non_finite_seconds_exits_zero_without_writing(self):
        for text in ('nan', 'inf'):
            with self.subTest(seconds=text):
                result = self.run_cli(
                    ['record', '--plugin', 'local-guards', '--event',
                     'edit-checks', '--status', 'ok', '--seconds', text])
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(self.usage_log().exists())


class ResolveRecorderPathTest(MetricsTestCase):
    def install_recorder_copy(self):
        installed = (self.agents_home / 'plugins' / 'usage-metrics' /
                     'scripts' / 'metrics.py')
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text('', encoding='utf-8')
        return installed

    def test_explicit_module_path_wins(self):
        override = self.agents_home / 'override-metrics.py'
        override.write_text('', encoding='utf-8')
        os.environ['AGENTS_METRICS_MODULE'] = str(override)
        self.assertEqual(
            recorder.resolve_recorder_path(self.agents_home / 'code-flow'),
            str(override))

    def test_missing_explicit_module_path_skips_recording(self):
        self.install_recorder_copy()
        os.environ['AGENTS_METRICS_MODULE'] = str(
            self.agents_home / 'missing.py')
        self.assertIsNone(
            recorder.resolve_recorder_path(self.agents_home / 'code-flow'))

    def test_adjacent_sibling_wins_over_the_installed_copy(self):
        siblings = Path(tempfile.mkdtemp(prefix='usage-metrics-siblings-'))
        self.addCleanup(shutil.rmtree, siblings, True)
        adjacent = siblings / 'usage-metrics' / 'scripts' / 'metrics.py'
        adjacent.parent.mkdir(parents=True)
        adjacent.write_text('', encoding='utf-8')
        self.install_recorder_copy()
        package_root = siblings / 'code-flow'
        package_root.mkdir()
        self.assertEqual(recorder.resolve_recorder_path(package_root),
                         str(adjacent))

    def test_installed_copy_resolves_without_a_sibling(self):
        installed = self.install_recorder_copy()
        lonely = Path(tempfile.mkdtemp(prefix='usage-metrics-lonely-'))
        self.addCleanup(shutil.rmtree, lonely, True)
        package_root = lonely / 'code-flow'
        package_root.mkdir()
        self.assertEqual(recorder.resolve_recorder_path(package_root),
                         str(installed))

    def test_unresolved_returns_none(self):
        lonely = Path(tempfile.mkdtemp(prefix='usage-metrics-lonely-'))
        self.addCleanup(shutil.rmtree, lonely, True)
        package_root = lonely / 'code-flow'
        package_root.mkdir()
        self.assertIsNone(recorder.resolve_recorder_path(package_root))


if __name__ == '__main__':
    unittest.main()
