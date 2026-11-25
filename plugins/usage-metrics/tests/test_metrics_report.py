import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PACKAGE_ROOT / 'scripts' / 'metrics-report.py'
SPEC = importlib.util.spec_from_file_location('usage_metrics_report',
                                              REPORT_PATH)
metrics_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(metrics_report)


def usage_event(ts, run, plugin, event, seconds, status='ok', **optional):
    entry = {'ts': ts, 'run': run, 'plugin': plugin, 'event': event,
             'status': status, 'seconds': seconds}
    entry.update(optional)
    return json.dumps(entry)


def fixture_lines():
    return [
        usage_event('2026-12-16T10:00:05+00:00', 'run-a', 'code-flow',
                    'context-pack', 0.5, role='worker', model='gpt-6.1'),
        usage_event('2026-12-16T10:00:03+00:00', 'run-a', 'code-flow',
                    'review-manifest', 1.5, round_kind='final-style'),
        usage_event('2026-12-16T10:00:04+00:00', 'run-b', 'local-guards',
                    'backup-ref', 0.2),
        usage_event('2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
                    'review-manifest', 1.0,
                    round_kind='initial-correctness'),
        usage_event('2026-12-16T10:00:07+00:00', 'run-b', 'code-flow',
                    'run-check', 0.3),
        usage_event('2026-12-16T10:00:02+00:00', 'run-a', 'code-flow',
                    'context-pack', 2.0, role='reviewer', model='glm-4.7'),
        usage_event('2026-12-16T10:00:06+00:00', 'run-b', 'local-guards',
                    'backup-ref', 0.4, status='fail'),
    ]


class ReportTestCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop('AGENTS_HOME', None)
        self.agents_home = Path(tempfile.mkdtemp(prefix='usage-metrics-home-'))
        self.addCleanup(shutil.rmtree, self.agents_home, True)
        os.environ['AGENTS_HOME'] = str(self.agents_home)

    def write_log(self, directory, lines):
        log_path = directory / 'usage.jsonl'
        log_path.write_text(''.join(line + '\n' for line in lines),
                            encoding='utf-8')
        return log_path

    def write_default_log(self, lines):
        (self.agents_home / 'metrics').mkdir(parents=True, exist_ok=True)
        return self.write_log(self.agents_home / 'metrics', lines)

    def write_explicit_log(self, lines):
        directory = Path(tempfile.mkdtemp(prefix='usage-metrics-log-'))
        self.addCleanup(shutil.rmtree, directory, True)
        return self.write_log(directory, lines)

    def run_report(self, arguments=()):
        return subprocess.run(
            [sys.executable, str(REPORT_PATH)] + list(arguments),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True)


class ReportViewsTest(ReportTestCase):
    def setUp(self):
        super().setUp()
        self.log_path = self.write_explicit_log(fixture_lines())
        self.result = self.run_report([str(self.log_path)])

    def test_exits_zero_over_a_fixture_log(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertIn('lines read: 7', self.result.stdout)
        self.assertIn('valid events: 7', self.result.stdout)
        self.assertIn('skipped lines: 0', self.result.stdout)

    def test_counts_invocations_by_plugin_and_surface(self):
        output = self.result.stdout
        self.assertIn('code-flow: 5', output)
        self.assertIn('local-guards: 2', output)
        self.assertIn('code-flow / review-manifest: 2', output)
        self.assertIn('code-flow / context-pack: 2', output)
        self.assertIn('local-guards / backup-ref: 2', output)
        self.assertIn('code-flow / run-check: 1', output)

    def test_reports_duration_percentiles_per_surface(self):
        output = self.result.stdout
        self.assertIn(
            'code-flow / review-manifest: 2, p50 1.000, p95 1.500', output)
        self.assertIn(
            'code-flow / context-pack: 2, p50 0.500, p95 2.000', output)
        self.assertIn(
            'local-guards / backup-ref: 2, p50 0.200, p95 0.400', output)
        self.assertIn(
            'code-flow / run-check: 1, p50 0.300, p95 0.300', output)

    def test_rounds_per_run_list_round_kinds_in_timestamp_order(self):
        self.assertIn('run-a: 2 (initial-correctness, final-style)',
                      self.result.stdout)
        self.assertNotIn('run-b:', self.result.stdout)

    def test_dispatch_list_per_run_orders_by_timestamp(self):
        self.assertIn('run-a: reviewer=glm-4.7, worker=gpt-6.1',
                      self.result.stdout)


class MalformedLinesTest(ReportTestCase):
    def test_malformed_and_old_schema_lines_are_skipped_and_counted(self):
        lines = [
            'not a JSON line',
            json.dumps({'plugin': 'code-flow'}),
            json.dumps(42),
            usage_event('2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
                        'run-check', 0.4),
            usage_event('yesterday', 'run-a', 'code-flow', 'run-check', 0.4),
            usage_event('2026-12-16T10:00:02+00:00', 'run-a', 'code-flow',
                        'run-check', 0.4, status='success'),
            usage_event('2026-12-16T10:00:03+00:00', 'run-a', 'code-flow',
                        'run-check', 'fast'),
            usage_event('2026-12-16T10:00:04+00:00', 'run-a', 'code-flow',
                        'run-check', -1),
            usage_event('2026-12-16T10:00:05+00:00', 'run-a', 'code-flow',
                        'run-check', 0.4, role=''),
            usage_event('2026-12-16T10:00:06', 'run-a', 'code-flow',
                        'run-check', 0.4),
            '',
        ]
        log_path = self.write_explicit_log(lines)
        result = self.run_report([str(log_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lines read: 11', result.stdout)
        self.assertIn('valid events: 1', result.stdout)
        self.assertIn('skipped lines: 10', result.stdout)
        self.assertIn('code-flow / run-check: 1', result.stdout)


class NonFiniteSecondsTest(ReportTestCase):
    def test_non_finite_lines_are_skipped_not_in_any_view(self):
        lines = [
            usage_event('2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
                        'run-check', 0.4),
            usage_event('2026-12-16T10:00:02+00:00', 'run-b', 'code-flow',
                        'review-manifest', float('nan'),
                        round_kind='initial-correctness'),
            usage_event('2026-12-16T10:00:03+00:00', 'run-c', 'code-flow',
                        'context-pack', float('inf'),
                        role='reviewer', model='glm-4.7'),
        ]
        log_path = self.write_explicit_log(lines)
        result = self.run_report([str(log_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lines read: 3', result.stdout)
        self.assertIn('valid events: 1', result.stdout)
        self.assertIn('skipped lines: 2', result.stdout)
        self.assertIn('code-flow: 1', result.stdout)
        self.assertIn('code-flow / run-check: 1, p50 0.400, p95 0.400',
                      result.stdout)
        self.assertNotIn('review-manifest', result.stdout)
        self.assertNotIn('context-pack', result.stdout)
        self.assertNotIn('run-b: ', result.stdout)
        self.assertNotIn('run-c: ', result.stdout)
        self.assertNotIn('reviewer=glm-4.7', result.stdout)


class HugeIntegerSecondsTest(ReportTestCase):
    def test_huge_integer_seconds_are_skipped_not_in_any_view(self):
        lines = [
            usage_event('2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
                        'run-check', 0.4),
            usage_event('2026-12-16T10:00:02+00:00', 'run-huge', 'code-flow',
                        'context-pack', 10 ** 400),
        ]
        log_path = self.write_explicit_log(lines)
        result = self.run_report([str(log_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lines read: 2', result.stdout)
        self.assertIn('valid events: 1', result.stdout)
        self.assertIn('skipped lines: 1', result.stdout)
        self.assertIn('code-flow / run-check: 1', result.stdout)
        self.assertNotIn('context-pack', result.stdout)
        self.assertNotIn('run-huge: ', result.stdout)


class EmptyLogTest(ReportTestCase):
    def test_empty_log_exits_zero_with_zero_counts(self):
        log_path = self.write_explicit_log([])
        result = self.run_report([str(log_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('lines read: 0', result.stdout)
        self.assertIn('valid events: 0', result.stdout)


class LogLocationTest(ReportTestCase):
    def test_default_path_resolves_from_agents_home(self):
        self.write_default_log(fixture_lines())
        result = self.run_report()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('code-flow / review-manifest: 2', result.stdout)

    def test_explicit_path_argument_wins_over_the_default(self):
        explicit = self.write_explicit_log(fixture_lines()[:1])
        result = self.run_report([str(explicit)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('valid events: 1', result.stdout)

    def test_missing_log_exits_nonzero_and_names_the_path(self):
        missing = self.agents_home / 'metrics' / 'usage.jsonl'
        result = self.run_report([str(missing)])
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(missing), result.stderr)


class ReadOnlyTest(ReportTestCase):
    def test_report_leaves_the_usage_log_bytes_unchanged(self):
        log_path = self.write_explicit_log(fixture_lines() + ['torn line'])
        bytes_before = log_path.read_bytes()
        result = self.run_report([str(log_path)])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(log_path.read_bytes(), bytes_before)


class NearestRankTest(unittest.TestCase):
    def test_nearest_rank_takes_the_ceil_rank_of_sorted_values(self):
        values = list(range(1, 21))
        self.assertEqual(metrics_report.nearest_rank(values, 50), 10)
        self.assertEqual(metrics_report.nearest_rank(values, 95), 19)

    def test_nearest_rank_of_ten_values(self):
        values = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        self.assertEqual(metrics_report.nearest_rank(values, 50), 0.5)
        self.assertEqual(metrics_report.nearest_rank(values, 95), 1.0)

    def test_single_value_serves_every_percentile(self):
        self.assertEqual(metrics_report.nearest_rank([3.5], 50), 3.5)
        self.assertEqual(metrics_report.nearest_rank([3.5], 95), 3.5)


class EventErrorTest(unittest.TestCase):
    def test_schema_event_with_optional_fields_passes_validation(self):
        entry = json.loads(usage_event(
            '2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
            'run-check', 0.4, role='reviewer', model='glm-4.7',
            round_kind='initial-correctness'))
        self.assertIsNone(metrics_report.event_error(entry))

    def test_invalid_entries_carry_a_reason(self):
        base = json.loads(usage_event(
            '2026-12-16T10:00:01+00:00', 'run-a', 'code-flow',
            'run-check', 0.4))
        invalid_entries = [
            42,
            {'plugin': 'code-flow'},
            {**base, 'status': 'success'},
            {**base, 'seconds': 'fast'},
            {**base, 'seconds': -1},
            {**base, 'seconds': float('nan')},
            {**base, 'seconds': float('inf')},
            {**base, 'seconds': 10 ** 400},
            {**base, 'ts': 'yesterday'},
            {**base, 'ts': '2026-12-16T10:00:01'},
            {**base, 'role': ''},
        ]
        for entry in invalid_entries:
            self.assertTrue(metrics_report.event_error(entry))


if __name__ == '__main__':
    unittest.main()
