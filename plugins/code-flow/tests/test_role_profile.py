import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROOT, copy_package, load_role

SCRIPT = ROOT / 'scripts' / 'role-profile.py'


def resolve(*arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, arguments)],
        capture_output=True, text=True,
        env=dict(os.environ, AGENTS_METRICS='off'),
    )


def resolve_profile(harness, role, *extra):
    result = resolve('--harness', harness, '--role', role, *extra)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def resolve_effort(harness, role, *extra):
    return resolve_profile(harness, role, *extra)['effort']


class GateReportEffortTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repo'
        self.repo.mkdir()
        self.target = self.repo / 'src' / 'changed.go'
        self.target.parent.mkdir()
        self.target.write_text('package changed\n')
        self.report_path = Path(self.temporary.name) / 'report.json'

    def write_report(self, record_overrides=None, **report_overrides):
        record = {
            'path': 'src/changed.go',
            'sha256': hashlib.sha256(self.target.read_bytes()).hexdigest(),
            'mutated': False,
            'covered_families': ['Imports'],
            'tools': [
                {'tool': 'goimports', 'mode': 'format', 'exit_status': 0,
                 'timed_out': False, 'mutated': False, 'diagnostics': None},
            ],
        }
        if record_overrides is not None:
            record.update(record_overrides)
        report = {
            'repo': str(self.repo),
            'deadline_exceeded': False,
            'rule_family_coverage': {'Imports': ['goimports']},
            'files': [record],
        }
        report.update(report_overrides)
        self.report_path.write_text(json.dumps(report))
        return self.report_path

    def effort(self, role, *extra):
        return resolve_effort('codex', role, *extra)

    def gate(self, role='style-reviewer'):
        return self.effort(role, '--gate-report', self.report_path, '--repo', self.repo)

    def test_verified_report_resolves_low_effort(self):
        self.write_report()
        self.assertEqual(self.gate(), 'low')

    def test_flag_absent_resolves_default_effort(self):
        self.assertEqual(self.effort('style-reviewer'), 'medium')

    def test_verified_report_does_not_override_runtime_effort(self):
        self.write_report()
        result = resolve('--harness', 'agentic', '--role', 'style-reviewer',
                         '--gate-report', self.report_path, '--repo', self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads(result.stdout)
        self.assertEqual(profile['effort_policy'], 'unsupported')
        self.assertIsNone(profile['effort'])

    def test_verified_report_is_ignored_for_other_roles(self):
        self.write_report()
        self.assertEqual(self.gate('reviewer'), 'high')

    def test_missing_report_file_is_no_evidence(self):
        self.assertEqual(self.gate(), 'medium')

    def test_hash_mismatch_is_no_evidence(self):
        self.write_report()
        self.target.write_text('package changed // edited after the gate ran\n')
        self.assertEqual(self.gate(), 'medium')

    def test_failed_or_diagnostic_tool_is_no_evidence(self):
        cases = (
            (1, None),
            (0, 'goimports requires formatting\nfixture diff'),
        )
        for exit_status, diagnostics in cases:
            with self.subTest(exit_status=exit_status, diagnostics=diagnostics):
                self.write_report({'tools': [
                    {'tool': 'goimports', 'mode': 'format', 'exit_status': exit_status,
                     'timed_out': False, 'mutated': False, 'diagnostics': diagnostics},
                ]})
                self.assertEqual(self.gate(), 'medium')

    def test_mutated_file_is_no_evidence(self):
        self.write_report({'mutated': True})
        self.assertEqual(self.gate(), 'medium')

    def test_deadline_exceeded_is_no_evidence(self):
        self.write_report(deadline_exceeded=True)
        self.assertEqual(self.gate(), 'medium')

    def test_malformed_report_exits_with_json_error(self):
        self.report_path.write_text('{not json')
        result = resolve('--harness', 'codex', '--role', 'style-reviewer',
                         '--gate-report', self.report_path, '--repo', self.repo)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_gate_report_requires_repo(self):
        self.write_report()
        result = resolve('--harness', 'codex', '--role', 'style-reviewer',
                         '--gate-report', self.report_path)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))


class RoundKindEffortTest(unittest.TestCase):
    def test_post_exit_round_resolves_medium_effort(self):
        self.assertEqual(
            resolve_effort('codex', 'reviewer', '--round-kind', 'post-exit-correctness'),
            'medium',
        )

    def test_other_round_kinds_keep_default_effort(self):
        for round_kind in ('initial-correctness', 'targeted-correctness', 'final-style'):
            with self.subTest(round_kind=round_kind):
                self.assertEqual(
                    resolve_effort('codex', 'reviewer', '--round-kind', round_kind),
                    'high',
                )

    def test_round_kind_does_not_change_other_roles(self):
        self.assertEqual(
            resolve_effort('codex', 'worker', '--round-kind', 'post-exit-correctness'),
            'high',
        )

    def test_unsupported_policy_keeps_null_effort(self):
        profile = resolve_profile('agentic', 'reviewer',
                                  '--round-kind', 'post-exit-correctness')
        self.assertEqual(profile['effort_policy'], 'unsupported')
        self.assertIsNone(profile['effort'])

    def test_unknown_round_kind_exits_with_json_error(self):
        result = resolve('--harness', 'codex', '--role', 'reviewer',
                         '--round-kind', 'verification')
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))


class ProviderTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.committed = json.loads((ROOT / 'harnesses' / 'mapping.json').read_text())['pi']

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = copy_package(self.temporary.name)
        self.mapping_path = self.package / 'harnesses' / 'mapping.json'

    def update_mapping(self, mutate):
        mapping = json.loads(self.mapping_path.read_text())
        mutate(mapping)
        self.mapping_path.write_text(json.dumps(mapping))

    def write_pi_mapping(self, **keys):
        self.update_mapping(lambda mapping: mapping['pi'].update(keys))

    def string_table(self, prefix='acme'):
        return {name: f'{prefix}/{name}-model' for name in self.committed['roles']}

    def native_tools(self, name):
        role = self.committed['roles'][name]
        return [self.committed['tool_names'][tool] for tool in role['tools']]

    def resolve(self, harness, role, *extra):
        return subprocess.run(
            [sys.executable, str(self.package / 'scripts' / 'role-profile.py'),
             '--harness', harness, '--role', role, *map(str, extra)],
            capture_output=True, text=True,
            env=dict(os.environ, AGENTS_METRICS='off'),
        )

    def profile(self, harness, role, *extra):
        result = self.resolve(harness, role, *extra)
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assert_json_error(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('error', json.loads(result.stdout))

    def test_provider_less_mapping_resolves_committed_profiles(self):
        for name, role in self.committed['roles'].items():
            with self.subTest(role=name):
                profile = self.profile('pi', name)
                self.assertEqual(profile['model'], role['model'])
                self.assertEqual(profile['effort'], role['effort'])
                self.assertEqual(profile['effort_policy'], role['effort_policy'])
                self.assertEqual(profile['tools'], self.native_tools(name))

    def test_provider_table_supplies_per_role_models(self):
        self.write_pi_mapping(provider='acme', providers={'acme': self.string_table()})
        for name, role in self.committed['roles'].items():
            with self.subTest(role=name):
                profile = self.profile('pi', name)
                self.assertEqual(profile['model'], f'acme/{name}-model')
                self.assertEqual(profile['effort'], role['effort'])
                self.assertEqual(profile['effort_policy'], role['effort_policy'])
                self.assertEqual(profile['tools'], self.native_tools(name))
        worker = self.profile('pi', 'worker')
        self.assertEqual(
            worker['developer_instructions'],
            load_role(ROOT / 'roles' / 'worker.toml')['developer_instructions'],
        )

    def test_entry_effort_overrides_while_policy_stays_role_owned(self):
        table = self.string_table()
        table['worker'] = {'model': 'acme/worker-model', 'effort': 'low'}
        self.write_pi_mapping(provider='acme', providers={'acme': table})
        profile = self.profile('pi', 'worker')
        self.assertEqual(profile['model'], 'acme/worker-model')
        self.assertEqual(profile['effort'], 'low')
        self.assertEqual(profile['effort_policy'], self.committed['roles']['worker']['effort_policy'])
        self.assertEqual(profile['tools'], self.native_tools('worker'))
        self.assertEqual(
            self.profile('pi', 'reviewer')['effort'],
            self.committed['roles']['reviewer']['effort'],
        )

    def test_provider_flag_selects_the_named_table(self):
        self.write_pi_mapping(
            provider='acme',
            providers={'acme': self.string_table('acme'), 'beta': self.string_table('beta')},
        )
        self.assertEqual(self.profile('pi', 'worker')['model'], 'acme/worker-model')
        self.assertEqual(self.profile('pi', 'worker', '--provider', 'beta')['model'], 'beta/worker-model')

    def test_unknown_provider_flag_exits_with_json_error(self):
        self.write_pi_mapping(provider='acme', providers={'acme': self.string_table()})
        self.assert_json_error(self.resolve('pi', 'worker', '--provider', 'beta'))

    def test_provider_flag_without_providers_exits_with_json_error(self):
        for harness in ('pi', 'codex'):
            with self.subTest(harness=harness):
                self.assert_json_error(self.resolve(harness, 'worker', '--provider', 'acme'))

    def test_mapping_provider_missing_from_providers_exits_with_json_error(self):
        self.write_pi_mapping(provider='gone', providers={'acme': self.string_table()})
        for arguments in ((), ('--provider', 'acme')):
            with self.subTest(arguments=arguments):
                self.assert_json_error(self.resolve('pi', 'worker', *arguments))

    def test_providers_without_provider_exits_with_json_error(self):
        self.write_pi_mapping(providers={'acme': self.string_table()})
        for arguments in ((), ('--provider', 'acme')):
            with self.subTest(arguments=arguments):
                self.assert_json_error(self.resolve('pi', 'worker', *arguments))

    def test_table_role_mismatch_exits_with_json_error(self):
        missing = {name: f'acme/{name}-model' for name in self.committed['roles'] if name != 'tool-runner'}
        cases = (
            ('missing role', missing),
            ('unknown role', self.string_table() | {'archivist': 'acme/archivist-model'}),
        )
        for label, table in cases:
            with self.subTest(case=label):
                self.write_pi_mapping(provider='acme', providers={'acme': table})
                self.assert_json_error(self.resolve('pi', 'worker'))

    def test_malformed_entries_exit_with_json_error(self):
        cases = (
            ('unknown entry key', {'model': 'acme/worker-model', 'temperature': 0.2}),
            ('effort outside the pi set', {'model': 'acme/worker-model', 'effort': 'ultra'}),
            ('effort not a string', {'model': 'acme/worker-model', 'effort': 3}),
            ('missing entry model', {'effort': 'low'}),
            ('entry not a mapping', 3),
            ('empty string model', ''),
        )
        for label, entry in cases:
            with self.subTest(case=label):
                table = self.string_table()
                table['worker'] = entry
                self.write_pi_mapping(provider='acme', providers={'acme': table})
                self.assert_json_error(self.resolve('pi', 'worker'))

    def test_entry_effort_on_unsupported_policy_exits_with_json_error(self):
        table = self.string_table()
        table['tool-runner'] = {'model': 'acme/tool-runner-model', 'effort': 'low'}

        def configure(mapping):
            mapping['pi']['roles']['tool-runner'].update(effort=None, effort_policy='unsupported')
            mapping['pi'].update(provider='acme', providers={'acme': table})

        self.update_mapping(configure)
        self.assert_json_error(self.resolve('pi', 'tool-runner'))

    def test_unsupported_role_entry_without_effort_resolves(self):
        table = self.string_table()
        table['tool-runner'] = {'model': 'acme/tool-runner-model'}

        def configure(mapping):
            mapping['pi']['roles']['tool-runner'].update(effort=None, effort_policy='unsupported')
            mapping['pi'].update(provider='acme', providers={'acme': table})

        self.update_mapping(configure)
        profile = self.profile('pi', 'tool-runner')
        self.assertEqual(profile['model'], 'acme/tool-runner-model')
        self.assertIsNone(profile['effort'])
        self.assertEqual(profile['effort_policy'], 'unsupported')

    def test_antigravity_provider_table_overrides_model_and_rejects_effort(self):
        table = self.string_table()
        self.update_mapping(lambda mapping: mapping['antigravity'].update(
            provider='acme', providers={'acme': table, 'beta': self.string_table('beta')},
        ))
        antigravity_mapping = json.loads(self.mapping_path.read_text())['antigravity']
        for name, role in antigravity_mapping['roles'].items():
            with self.subTest(role=name):
                profile = self.profile('antigravity', name)
                self.assertEqual(profile['model'], f'acme/{name}-model')
                self.assertIsNone(profile['effort'])
                self.assertEqual(profile['effort_policy'], 'unsupported')
                self.assertEqual(
                    profile['tools'],
                    [antigravity_mapping['tool_names'][tool] for tool in role['tools']],
                )
        self.assertEqual(
            self.profile('antigravity', 'reviewer', '--provider', 'beta')['model'],
            'beta/reviewer-model',
        )
        bad_table = self.string_table()
        bad_table['reviewer'] = {'model': 'acme/reviewer-model', 'effort': 'high'}
        self.update_mapping(lambda mapping: mapping['antigravity'].update(
            provider='acme', providers={'acme': bad_table},
        ))
        self.assert_json_error(self.resolve('antigravity', 'reviewer'))

    def test_antigravity_rejects_explicit_effort_policy(self):
        self.update_mapping(lambda mapping: mapping['antigravity']['roles']['reviewer'].update(
            effort='high', effort_policy='explicit',
        ))
        self.assert_json_error(self.resolve('antigravity', 'reviewer'))

    def test_antigravity_ignores_gate_report_and_round_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            target = repo / 'changed.py'
            target.write_text('x = 1\n')
            report_path = Path(directory) / 'report.json'
            report_path.write_text(json.dumps({
                'repo': str(repo),
                'deadline_exceeded': False,
                'rule_family_coverage': {'Comments': ['ruff']},
                'files': [{
                    'path': 'changed.py',
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'mutated': False,
                    'covered_families': ['Comments'],
                    'tools': [{'tool': 'ruff', 'mode': 'check', 'exit_status': 0,
                               'timed_out': False, 'mutated': False, 'diagnostics': None}],
                }],
            }))
            gate_profile = self.profile(
                'antigravity', 'style-reviewer', '--gate-report', report_path, '--repo', repo,
            )
            self.assertIsNone(gate_profile['effort'])
            self.assertEqual(gate_profile['effort_policy'], 'unsupported')
        round_profile = self.profile('antigravity', 'reviewer', '--round-kind', 'post-exit-correctness')
        self.assertIsNone(round_profile['effort'])
        self.assertEqual(round_profile['effort_policy'], 'unsupported')

    def test_unselected_malformed_table_still_exits_with_json_error(self):
        for arguments in ((), ('--provider', 'acme')):
            with self.subTest(arguments=arguments):
                table = self.string_table()
                table['worker'] = {'model': 'beta/worker-model', 'effort': 'turbo'}
                self.write_pi_mapping(
                    provider='acme',
                    providers={'acme': self.string_table(), 'beta': table},
                )
                self.assert_json_error(self.resolve('pi', 'worker', *arguments))


class BatchRoleResolutionTest(unittest.TestCase):
    def test_batch_roles_resolve_in_input_order_preserving_single_role_profiles(self):
        roles = ['reviewer', 'worker', 'tool-runner']
        result = resolve('--harness', 'codex', '--role', ' reviewer , worker, tool-runner ')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(list(payload), ['profiles'])
        self.assertEqual(list(payload['profiles']), roles)
        for name in roles:
            with self.subTest(role=name):
                self.assertEqual(payload['profiles'][name], resolve_profile('codex', name))

    def test_batch_applies_gate_report_and_round_kind_per_role(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'repo'
            repo.mkdir()
            target = repo / 'changed.py'
            target.write_text('x = 1\n')
            report_path = Path(directory) / 'report.json'
            report_path.write_text(json.dumps({
                'repo': str(repo),
                'deadline_exceeded': False,
                'rule_family_coverage': {'Comments': ['ruff']},
                'files': [{
                    'path': 'changed.py',
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                    'mutated': False,
                    'covered_families': ['Comments'],
                    'tools': [{'tool': 'ruff', 'mode': 'check', 'exit_status': 0,
                               'timed_out': False, 'mutated': False, 'diagnostics': None}],
                }],
            }))
            result = resolve(
                '--harness', 'codex',
                '--role', 'worker,style-reviewer,reviewer',
                '--gate-report', report_path,
                '--repo', repo,
                '--round-kind', 'post-exit-correctness',
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profiles = json.loads(result.stdout)['profiles']
        self.assertEqual(profiles['worker']['effort'], 'high')
        self.assertEqual(profiles['style-reviewer']['effort'], 'low')
        self.assertEqual(profiles['reviewer']['effort'], 'medium')

    def test_batch_with_provider_override_resolves_all_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            package = copy_package(directory)
            mapping_path = package / 'harnesses' / 'mapping.json'
            mapping = json.loads(mapping_path.read_text())
            roles = mapping['pi']['roles']
            mapping['pi']['provider'] = 'acme'
            mapping['pi']['providers'] = {
                'acme': {name: f'acme/{name}-model' for name in roles},
                'beta': {name: f'beta/{name}-model' for name in roles},
            }
            mapping_path.write_text(json.dumps(mapping))
            result = subprocess.run(
                [sys.executable, str(package / 'scripts' / 'role-profile.py'),
                 '--harness', 'pi', '--role', 'worker,reviewer', '--provider', 'beta'],
                capture_output=True, text=True,
                env=dict(os.environ, AGENTS_METRICS='off'),
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profiles = json.loads(result.stdout)['profiles']
        self.assertEqual(profiles['worker']['model'], 'beta/worker-model')
        self.assertEqual(profiles['reviewer']['model'], 'beta/reviewer-model')

    def test_invalid_batch_role_lists_exit_with_json_error(self):
        list_error = 'invalid --role list: expected unique nonempty role names'
        cases = (
            ('empty single', '', 'unknown role: '),
            ('leading comma', ',worker', list_error),
            ('trailing comma', 'worker,', list_error),
            ('blank middle entry', 'worker, ,reviewer', list_error),
            ('duplicate role', 'worker,reviewer,worker', list_error),
            ('duplicate after strip', 'worker, worker', list_error),
            ('unknown role in batch', 'worker,archivist', 'unknown role: archivist'),
        )
        for label, raw_role, expected_error in cases:
            with self.subTest(case=label):
                result = resolve('--harness', 'codex', '--role', raw_role)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['error'], expected_error)

    def test_batch_gate_report_without_repo_exits_with_json_error(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path = Path(directory) / 'report.json'
            report_path.write_text('{}')
            result = resolve(
                '--harness', 'codex',
                '--role', 'worker,style-reviewer',
                '--gate-report', report_path,
            )
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(
            json.loads(result.stdout)['error'],
            '--gate-report requires --repo for hash verification',
        )

    def test_batch_records_comma_joined_role_and_model_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / 'metrics.jsonl'
            recorder = Path(directory) / 'recorder.py'
            recorder.write_text(
                'import json\n'
                'from pathlib import Path\n'
                f'LOG = Path({str(log_path)!r})\n'
                'def record(plugin, event, status, seconds, role=None, model=None):\n'
                '    with LOG.open("a") as handle:\n'
                '        handle.write(json.dumps({"status": status, "role": role, "model": model}) + "\\n")\n'
            )
            env = dict(os.environ, AGENTS_METRICS_MODULE=str(recorder))
            env.pop('AGENTS_METRICS', None)
            single = subprocess.run(
                [sys.executable, str(SCRIPT), '--harness', 'codex', '--role', 'worker'],
                capture_output=True, text=True, env=env,
            )
            batch = subprocess.run(
                [sys.executable, str(SCRIPT), '--harness', 'codex', '--role', ' worker , tool-runner '],
                capture_output=True, text=True, env=env,
            )
            self.assertEqual(single.returncode, 0, single.stdout + single.stderr)
            self.assertEqual(batch.returncode, 0, batch.stdout + batch.stderr)
            records = [json.loads(line) for line in log_path.read_text().splitlines()]
        worker_model = json.loads(single.stdout)['model']
        batch_profiles = json.loads(batch.stdout)['profiles']
        expected_batch_models = f"{batch_profiles['worker']['model']},{batch_profiles['tool-runner']['model']}"
        self.assertEqual(records, [
            {'status': 'ok', 'role': 'worker', 'model': worker_model},
            {'status': 'ok', 'role': 'worker,tool-runner', 'model': expected_batch_models},
        ])


if __name__ == '__main__':
    unittest.main()
