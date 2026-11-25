import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
RECORDER = Path(__file__).resolve().parents[2] / 'usage-metrics' / 'scripts' / 'metrics.py'
EVENT_FIELDS = {'ts', 'run', 'plugin', 'event', 'status', 'seconds'}


class MetricsEmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'agents-home'
        self.home.mkdir()
        self.repo = self.root / 'repository'
        (self.repo / 'src').mkdir(parents=True)
        (self.repo / 'src' / 'alpha.py').write_text('base\n')
        (self.repo / 'docs.md').write_text('docs\n')
        for arguments in (
            ('init', '-q', '-b', 'main'),
            ('config', 'user.name', 'Metrics Test'),
            ('config', 'user.email', 'metrics@example.test'),
            ('add', '.'),
            ('commit', '-qm', 'Initial fixture'),
        ):
            subprocess.run(['git', '-C', str(self.repo), *arguments], check=True,
                           capture_output=True)
        (self.repo / 'src' / 'alpha.py').write_text('changed\n')
        self.environment = dict(os.environ, AGENTS_HOME=str(self.home), AGENTS_METRICS_MODULE='')
        self.environment.pop('AGENTS_RUN_ID', None)
        self.environment.pop('AGENTS_METRICS', None)

    def run_script(self, name, *arguments, scripts=None, **overrides):
        return subprocess.run(
            [sys.executable, str((scripts or SCRIPTS) / name), *map(str, arguments)],
            env=dict(self.environment, **overrides), capture_output=True, text=True,
            timeout=30,
        )

    def events(self):
        log = self.home / 'metrics' / 'usage.jsonl'
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def install_cache_layout(self):
        scripts = self.root / 'cache' / 'phil0v' / 'code-flow' / '0.3.0' / 'scripts'
        shutil.copytree(SCRIPTS.parent, scripts.parent)
        return scripts

    def install_cache_recorder(self, version, source=RECORDER):
        target = self.root / 'cache' / 'phil0v' / 'usage-metrics' / version / 'scripts' / 'metrics.py'
        target.parent.mkdir(parents=True)
        shutil.copy(source, target)

    def spy_recorder(self, version):
        log = self.root / f'{version}.log'
        stub = self.root / f'{version}.py'
        stub.write_text('import json, sys\n'
                        "if __name__ == '__main__':\n"
                        f"    with open({str(log)!r}, 'a') as log_file:\n"
                        "        log_file.write(json.dumps(sys.argv[1:]) + '\\n')\n"

                        'def record(*arguments, **options):\n'
                        f"    with open({str(log)!r}, 'a') as log_file:\n"
                        "        log_file.write(json.dumps(['record', *arguments]) + '\\n')\n")
        return stub

    def spy_calls(self, version):
        log = self.root / f'{version}.log'
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def ok_invocations(self):
        return {
            'run-check.py': ('--cwd', self.repo, '--', sys.executable, '-c', 'print("ok")'),
            'style-gate.py': ('--repo', self.repo, 'docs.md'),
            'review-manifest.py': self.init_arguments(),
            'review-snapshot.py': ('--repo', self.repo),
            'context-pack.py': ('--repo', self.repo, 'docs.md'),
            'role-profile.py': ('--harness', 'codex', '--role', 'reviewer'),
        }

    def init_arguments(self, round_kind='initial-correctness'):
        return ('init', '--repo', self.repo, '--scope-from', '["src/alpha.py"]',
                '--round-kind', round_kind, '--group', 'g1=src/alpha.py')

    def clean_run_check_log(self, result):
        self.addCleanup(Path(json.loads(result.stdout)['log_path']).unlink, missing_ok=True)

    def assert_event(self, event, name, status, run=None):
        self.assertEqual(set(event) - {'round_kind', 'role', 'model'}, EVENT_FIELDS)
        self.assertEqual((event['plugin'], event['event'], event['status']),
                         ('code-flow', name, status))
        self.assertRegex(event['ts'], r'^\d{4}-\d{2}-\d{2}T')
        if run is None:
            self.assertRegex(event['run'], r'^[0-9a-f]{32}$')
        else:
            self.assertEqual(event['run'], run)
        self.assertIsInstance(event['seconds'], float)
        self.assertGreaterEqual(event['seconds'], 0.0)

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_each_script_records_exactly_one_ok_event(self):
        invocations = self.ok_invocations()
        for index, (name, arguments) in enumerate(invocations.items(), start=1):
            with self.subTest(script=name):
                result = self.run_script(name, *arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if name == 'run-check.py':
                    self.clean_run_check_log(result)
                events = self.events()
                self.assertEqual(len(events), index)
                self.assert_event(events[-1], name[:-3], 'ok')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_cache_layout_resolves_versioned_sibling_recorder(self):
        self.install_cache_recorder('0.2.0')
        scripts = self.install_cache_layout()
        for index, (name, arguments) in enumerate(self.ok_invocations().items(), start=1):
            with self.subTest(script=name):
                result = self.run_script(name, *arguments, scripts=scripts)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if name == 'run-check.py':
                    self.clean_run_check_log(result)
                events = self.events()
                self.assertEqual(len(events), index)
                self.assert_event(events[-1], name[:-3], 'ok')

    def test_cache_layout_prefers_the_highest_numeric_version(self):
        versions = ('0.2.0', '0.10.0', '0.9.0', 'preview')
        for version in versions:
            self.install_cache_recorder(version, self.spy_recorder(version))
        scripts = self.install_cache_layout()
        result = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer',
                                 scripts=scripts)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([version for version in versions if self.spy_calls(version)], ['0.10.0'])
        calls = self.spy_calls('0.10.0')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:4], ['record', 'code-flow', 'role-profile', 'ok'])
        self.assertIsInstance(calls[0][4], float)
        self.assertEqual(self.events(), [])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_clone_layout_prefers_the_unversioned_sibling(self):
        clone = self.root / 'clone'
        scripts = clone / 'plugins' / 'code-flow' / 'scripts'
        shutil.copytree(SCRIPTS.parent, scripts.parent)
        sibling = clone / 'plugins' / 'usage-metrics' / 'scripts' / 'metrics.py'
        sibling.parent.mkdir(parents=True)
        shutil.copy(RECORDER, sibling)
        cached = clone / 'usage-metrics' / '9.9.9' / 'scripts' / 'metrics.py'
        cached.parent.mkdir(parents=True)
        shutil.copy(self.spy_recorder('cached'), cached)
        result = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer',
                                 scripts=scripts)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_event(self.events()[0], 'role-profile', 'ok')
        self.assertEqual(self.spy_calls('cached'), [])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_failed_invocations_record_fail_events(self):
        failures = {
            'run-check.py': ('--cwd', self.repo, '--', sys.executable, '-c', 'import sys; sys.exit(3)'),
            'style-gate.py': ('--repo', self.root / 'missing', 'docs.md'),
            'review-manifest.py': ('validate', '--repo', self.repo,
                                   '--manifest', self.root / 'missing.json'),
            'review-snapshot.py': ('--repo', self.root / 'missing'),
            'context-pack.py': ('--repo', self.repo, 'src/absent.py'),
            'role-profile.py': ('--harness', 'codex', '--role', 'archivist'),
        }
        for index, (name, arguments) in enumerate(failures.items(), start=1):
            with self.subTest(script=name):
                result = self.run_script(name, *arguments)
                self.assertNotEqual(result.returncode, 0)
                if name == 'run-check.py':
                    self.clean_run_check_log(result)
                events = self.events()
                self.assertEqual(len(events), index)
                self.assert_event(events[-1], name[:-3], 'fail')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_init_event_carries_round_kind_and_shares_the_echoed_run(self):
        first = self.run_script('review-manifest.py', *self.init_arguments())
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        manifest = json.loads(first.stdout)
        self.assertRegex(manifest['run'], r'^[0-9a-f]{32}$')
        event = self.events()[0]
        self.assertEqual(event['round_kind'], 'initial-correctness')
        self.assertEqual(event['run'], manifest['run'])
        second = self.run_script('review-manifest.py',
                                 *self.init_arguments('targeted-correctness'),
                                 AGENTS_RUN_ID=manifest['run'])
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(json.loads(second.stdout)['run'], manifest['run'])
        events = self.events()
        self.assertEqual(events[1]['round_kind'], 'targeted-correctness')
        self.assertEqual(events[1]['run'], manifest['run'])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_inherited_run_id_is_echoed_and_recorded_verbatim(self):
        result = self.run_script('review-manifest.py', *self.init_arguments(),
                                 AGENTS_RUN_ID='review-coordinate-42')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['run'], 'review-coordinate-42')
        self.assert_event(self.events()[0], 'review-manifest', 'ok', run='review-coordinate-42')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_non_generating_invocations_emit_plain_events_without_run_echo(self):
        manifest = json.loads(self.run_script('review-manifest.py',
                                              *self.init_arguments()).stdout)
        manifest_path = self.root / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest))
        validated = self.run_script('review-manifest.py', 'validate', '--repo', self.repo,
                                    '--manifest', manifest_path)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertNotIn('run', json.loads(validated.stdout))
        rendered = self.run_script('review-manifest.py', 'render', '--manifest', manifest_path,
                                   '--group', 'g1')
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        self.assertNotIn('run', json.loads(rendered.stdout))
        captured = self.run_script('review-snapshot.py', '--repo', self.repo)
        self.assertEqual(captured.returncode, 0, captured.stdout + captured.stderr)
        self.assertNotIn('run', json.loads(captured.stdout))
        events = self.events()
        self.assertEqual([event['event'] for event in events],
                         ['review-manifest', 'review-manifest', 'review-manifest',
                          'review-snapshot'])
        for event in events[1:]:
            self.assertNotIn('round_kind', event)

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_failed_init_prints_no_run_field_and_keeps_round_kind_on_the_event(self):
        result = self.run_script('review-manifest.py', 'init', '--repo', self.repo,
                                 '--scope-from', '["src/missing.py"]', '--round-kind',
                                 'initial-correctness', '--group', 'g1=src/missing.py')
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('run', json.loads(result.stdout))
        event = self.events()[0]
        self.assert_event(event, 'review-manifest', 'fail')
        self.assertEqual(event['round_kind'], 'initial-correctness')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_metrics_off_records_no_events(self):
        checked = self.run_script('run-check.py', '--cwd', self.repo, '--', sys.executable,
                                  '-c', 'print("ok")', AGENTS_METRICS='off')
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.clean_run_check_log(checked)
        initialized = self.run_script('review-manifest.py', *self.init_arguments(),
                                      AGENTS_METRICS='off')
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        self.assertRegex(json.loads(initialized.stdout)['run'], r'^[0-9a-f]{32}$')
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / 'metrics').exists())

    def test_unresolved_recorder_is_a_silent_no_op(self):
        missing = '/nonexistent/metrics-emission/metrics.py'
        gate_unresolved = self.run_script('style-gate.py', '--repo', self.repo, 'docs.md',
                                          AGENTS_METRICS_MODULE=missing)
        check_unresolved = self.run_script('run-check.py', '--cwd', self.repo, '--',
                                           sys.executable, '-c', 'print("ok")',
                                           AGENTS_METRICS_MODULE=missing)
        init_unresolved = self.run_script('review-manifest.py', *self.init_arguments(),
                                          AGENTS_METRICS_MODULE=missing)
        snapshot_unresolved = self.run_script('review-snapshot.py', '--repo', self.repo,
                                              AGENTS_METRICS_MODULE=missing)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / 'metrics').exists())
        gate_resolved = self.run_script('style-gate.py', '--repo', self.repo, 'docs.md')
        check_resolved = self.run_script('run-check.py', '--cwd', self.repo, '--', sys.executable,
                                         '-c', 'print("ok")')
        init_resolved = self.run_script('review-manifest.py', *self.init_arguments())
        snapshot_resolved = self.run_script('review-snapshot.py', '--repo', self.repo)
        self.assertEqual(
            (gate_unresolved.returncode, gate_unresolved.stdout, gate_unresolved.stderr),
            (gate_resolved.returncode, gate_resolved.stdout, gate_resolved.stderr))
        unresolved_check = json.loads(check_unresolved.stdout)
        resolved_check = json.loads(check_resolved.stdout)
        self.clean_run_check_log(check_unresolved)
        self.clean_run_check_log(check_resolved)
        self.assertEqual({key: value for key, value in unresolved_check.items() if key != 'log_path'},
                         {key: value for key, value in resolved_check.items() if key != 'log_path'})
        self.assertEqual(check_unresolved.stderr, check_resolved.stderr)
        without_run = {key: value for key, value in json.loads(init_resolved.stdout).items()
                       if key != 'run'}
        self.assertEqual(init_unresolved.returncode, init_resolved.returncode)
        self.assertEqual(init_unresolved.stderr, init_resolved.stderr)
        self.assertEqual(init_unresolved.stdout,
                         json.dumps(without_run, ensure_ascii=True, sort_keys=True) + '\n')
        unresolved_snapshot = json.loads(snapshot_unresolved.stdout)
        resolved_snapshot = json.loads(snapshot_resolved.stdout)
        for snapshot in (unresolved_snapshot, resolved_snapshot):
            snapshot['metrics'].pop('elapsed_seconds')
        self.assertEqual(unresolved_snapshot, resolved_snapshot)
        events = self.events()
        self.assertEqual([event['event'] for event in events],
                         ['style-gate', 'run-check', 'review-manifest', 'review-snapshot'])

    def test_init_run_env_with_unresolved_recorder_prints_no_run_field(self):
        missing = '/nonexistent/metrics-emission/metrics.py'
        inherited = self.run_script('review-manifest.py', *self.init_arguments(),
                                    AGENTS_METRICS_MODULE=missing,
                                    AGENTS_RUN_ID='review-unresolved-1')
        self.assertEqual(inherited.returncode, 0, inherited.stdout + inherited.stderr)
        self.assertNotIn('run', json.loads(inherited.stdout))
        plain = self.run_script('review-manifest.py', *self.init_arguments(),
                                AGENTS_METRICS_MODULE=missing)
        self.assertEqual((inherited.returncode, inherited.stdout, inherited.stderr),
                         (plain.returncode, plain.stdout, plain.stderr))
        self.assertEqual(self.events(), [])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_compare_answers_record_ok_and_compare_errors_record_fail(self):
        first = self.run_script('review-snapshot.py', '--repo', self.repo)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = self.root / 'before.json'
        before.write_text(first.stdout)
        (self.repo / 'src' / 'alpha.py').write_text('different\n')
        second = self.run_script('review-snapshot.py', '--repo', self.repo)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        after = self.root / 'after.json'
        after.write_text(second.stdout)
        differing = self.run_script('review-snapshot.py', 'compare', before, after)
        self.assertEqual(differing.returncode, 1, differing.stdout + differing.stderr)
        self.assertTrue(json.loads(differing.stdout)['changed'])
        failed = self.run_script('review-snapshot.py', 'compare', before,
                                 self.root / 'missing-snapshot.json')
        self.assertEqual(failed.returncode, 2, failed.stdout + failed.stderr)
        self.assertIn('error', json.loads(failed.stdout))
        events = self.events()
        self.assertEqual([event['status'] for event in events], ['ok', 'ok', 'ok', 'fail'])
        self.assert_event(events[2], 'review-snapshot', 'ok')
        self.assert_event(events[3], 'review-snapshot', 'fail')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_role_profile_event_carries_resolved_role_and_model(self):
        result = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads(result.stdout)
        event = self.events()[0]
        self.assert_event(event, 'role-profile', 'ok')
        self.assertEqual(event['role'], 'reviewer')
        self.assertEqual(event['model'], profile['model'])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_role_profile_records_the_runtime_default_model_marker(self):
        result = self.run_script('role-profile.py', '--harness', 'agentic', '--role', 'worker')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['model'], 'runtime-default')
        self.assertEqual(self.events()[0]['model'], 'runtime-default')

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_dispatch_events_share_one_run_and_order_by_timestamp(self):
        shared = 'review-dispatch-run-7'
        resolved = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer',
                                   AGENTS_RUN_ID=shared)
        model = json.loads(resolved.stdout)['model']
        packed = self.run_script('context-pack.py', '--repo', self.repo, '--role', 'reviewer',
                                 '--model', model, 'src/alpha.py', AGENTS_RUN_ID=shared)
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        events = self.events()
        self.assertEqual([event['event'] for event in events], ['role-profile', 'context-pack'])
        self.assertEqual({event['run'] for event in events}, {shared})
        self.assertEqual(events, sorted(events, key=lambda event: event['ts']))
        self.assertEqual([(event['role'], event['model']) for event in events],
                         [('reviewer', model)] * 2)

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_context_pack_without_flags_records_no_role_or_model(self):
        result = self.run_script('context-pack.py', '--repo', self.repo, 'src/alpha.py')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        event = self.events()[0]
        self.assert_event(event, 'context-pack', 'ok')
        self.assertNotIn('role', event)
        self.assertNotIn('model', event)

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_context_pack_flags_leave_output_unchanged(self):
        plain = self.run_script('context-pack.py', '--repo', self.repo, '--format', 'json',
                                'src/alpha.py')
        flagged = self.run_script('context-pack.py', '--repo', self.repo, '--role', 'reviewer',
                                  '--model', 'runtime-default', '--format', 'json', 'src/alpha.py')
        self.assertEqual((plain.returncode, plain.stdout, plain.stderr),
                         (flagged.returncode, flagged.stdout, flagged.stderr))

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_failed_dispatch_records_fail_events(self):
        unresolved = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'archivist')
        missing = self.run_script('context-pack.py', '--repo', self.repo, '--role', 'reviewer',
                                  '--model', 'runtime-default', 'src/absent.py')
        self.assertEqual(unresolved.returncode, 2, unresolved.stdout + unresolved.stderr)
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)
        events = self.events()
        self.assertEqual([(event['event'], event['status']) for event in events],
                         [('role-profile', 'fail'), ('context-pack', 'fail')])
        self.assertEqual(events[0]['role'], 'archivist')
        self.assertNotIn('model', events[0])
        self.assertEqual((events[1]['role'], events[1]['model']),
                         ('reviewer', 'runtime-default'))

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_dispatch_metrics_off_records_no_events(self):
        resolved = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer',
                                   AGENTS_METRICS='off')
        packed = self.run_script('context-pack.py', '--repo', self.repo, '--role', 'reviewer',
                                 '--model', 'runtime-default', 'src/alpha.py',
                                 AGENTS_METRICS='off')
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / 'metrics').exists())

    def test_dispatch_unresolved_recorder_is_a_silent_no_op(self):
        missing_module = '/nonexistent/metrics-emission/metrics.py'
        profile_unresolved = self.run_script('role-profile.py', '--harness', 'codex',
                                             '--role', 'reviewer',
                                             AGENTS_METRICS_MODULE=missing_module)
        pack_unresolved = self.run_script('context-pack.py', '--repo', self.repo, '--role',
                                          'reviewer', '--model', 'runtime-default',
                                          'src/alpha.py', AGENTS_METRICS_MODULE=missing_module)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.home / 'metrics').exists())
        profile_resolved = self.run_script('role-profile.py', '--harness', 'codex',
                                           '--role', 'reviewer')
        pack_resolved = self.run_script('context-pack.py', '--repo', self.repo, '--role',
                                        'reviewer', '--model', 'runtime-default',
                                        'src/alpha.py')
        self.assertEqual(
            (profile_unresolved.returncode, profile_unresolved.stdout, profile_unresolved.stderr),
            (profile_resolved.returncode, profile_resolved.stdout, profile_resolved.stderr))
        self.assertEqual(
            (pack_unresolved.returncode, pack_unresolved.stdout, pack_unresolved.stderr),
            (pack_resolved.returncode, pack_resolved.stdout, pack_resolved.stderr))
        self.assertEqual([event['event'] for event in self.events()],
                         ['role-profile', 'context-pack'])

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_dispatch_events_carry_no_paths_or_payloads(self):
        marker = 'dispatch-marker'
        (self.repo / f'{marker}.py').write_text('def marked():\n    pass\n')
        resolved = self.run_script('role-profile.py', '--harness', 'codex', '--role', 'reviewer')
        packed = self.run_script('context-pack.py', '--repo', self.repo, '--role', 'reviewer',
                                 '--model', 'runtime-default', f'{marker}.py')
        self.assertEqual(resolved.returncode, 0, resolved.stdout + resolved.stderr)
        self.assertEqual(packed.returncode, 0, packed.stdout + packed.stderr)
        raw = (self.home / 'metrics' / 'usage.jsonl').read_text()
        self.assertNotIn(marker, raw)
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn('src/alpha.py', raw)

    @unittest.skipUnless(RECORDER.is_file(), 'usage-metrics package is not installed alongside')
    def test_events_carry_no_paths_or_payloads(self):
        marker = 'metrics-emission-marker'
        (self.repo / f'{marker}.md').write_text('marked\n')
        checked = self.run_script('run-check.py', '--cwd', self.repo, '--', sys.executable,
                                  '-c', f'print("{marker}")')
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.clean_run_check_log(checked)
        gated = self.run_script('style-gate.py', '--repo', self.repo, f'{marker}.md')
        self.assertEqual(gated.returncode, 0, gated.stdout + gated.stderr)
        initialized = self.run_script('review-manifest.py', 'init', '--repo', self.repo,
                                      '--scope-from', json.dumps([f'{marker}.md']),
                                      '--round-kind', 'final-style', '--group',
                                      f'g1={marker}.md')
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        raw = (self.home / 'metrics' / 'usage.jsonl').read_text()
        self.assertNotIn(marker, raw)
        self.assertNotIn(str(self.root), raw)
        self.assertNotIn('src/alpha.py', raw)
        self.assertEqual(len(self.events()), 3)


if __name__ == '__main__':
    unittest.main()
