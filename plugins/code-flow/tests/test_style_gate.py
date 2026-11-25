import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import load_script_module


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'style-gate.py'


class StyleGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / 'repository'
        self.repo.mkdir()
        self.bin = self.directory / 'bin'
        self.bin.mkdir()
        self.module = load_script_module(SCRIPT, 'style_gate')

    def stub(self, name, body):
        path = self.bin / name
        path.write_text(f'#!/bin/sh\n{body}\n')
        path.chmod(0o755)
        return path

    def environment(self):
        return dict(os.environ, PATH=str(self.bin),
                    AGENTS_HOME=str(self.directory / 'metrics home'))

    def use_stub_path(self):
        self.addCleanup(os.environ.__setitem__, 'PATH', os.environ.get('PATH', ''))
        os.environ['PATH'] = str(self.bin)

    def cli(self, *arguments):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True, text=True, timeout=30, env=self.environment(),
        )
        report = json.loads(result.stdout)
        for record in report.get('files', []):
            for outcome in record['tools']:
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)
        return result.returncode, report

    def write(self, name, content):
        path = self.repo / name
        path.write_text(content)
        return path

    def without_log_paths(self, records):
        return [{**record, 'tools': [{key: value for key, value in outcome.items()
                                      if key != 'log_path'} for outcome in record['tools']]}
                for record in records]

    def test_table_resolution_per_extension(self):
        for tool in ('goimports', 'golangci-lint', 'php-cs-fixer', 'prettier',
                     'eslint', 'ruff', 'shfmt', 'shellcheck'):
            self.stub(tool, 'exit 0')
        (self.repo / '.golangci.yml').write_text('')
        (self.repo / '.editorconfig').write_text('')
        (self.repo / 'pyproject.toml').write_text('[tool.ruff]\n')
        self.use_stub_path()
        cases = {
            'main.go': [('goimports', 'format', ['-w', '@PATH']),
                        ('golangci-lint', 'lint',
                         ['run', '--new-from-rev', 'HEAD', './.'])],
            'base.php': [('php-cs-fixer', 'format', ['fix', '--quiet', '@PATH'])],
            'app.ts': [('prettier', 'format', ['--write', '@PATH']),
                       ('eslint', 'lint', ['@PATH'])],
            'mod.py': [('ruff', 'format', ['format', '@PATH']),
                       ('ruff', 'lint',
                        ['check', '--output-format', 'concise', '@PATH'])],
            'run.sh': [('shfmt', 'format', ['-w', '@PATH']),
                       ('shellcheck', 'lint', ['-f', 'gcc', '@PATH'])],
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                path = self.write(name, 'content\n')
                entries = list(self.module.entries(path, self.repo))
                self.assertEqual([entry.mode for entry in entries],
                                 [mode for _, mode, _ in expected])
                for entry, (tool, _, tail) in zip(entries, expected):
                    tail = [str(path) if argument == '@PATH' else argument
                            for argument in tail]
                    self.assertEqual(Path(entry.command[0]).name, tool)
                    self.assertEqual(entry.command[1:], tail)
                self.assertEqual(list(self.module.commands(path, self.repo)),
                                 [entry.command for entry in entries])

    def test_go_falls_back_to_gofmt_without_golangci_config(self):
        self.stub('gofmt', 'exit 0')
        self.stub('golangci-lint', 'exit 0')
        self.use_stub_path()
        path = self.write('main.go', 'package main\n')
        entries = list(self.module.entries(path, self.repo))
        self.assertEqual([(entry.tool, entry.mode) for entry in entries],
                         [('gofmt', 'format')])
        (self.repo / '.golangci.yml').write_text('')
        entries = list(self.module.entries(path, self.repo))
        self.assertEqual([entry.tool for entry in entries], ['gofmt', 'golangci-lint'])

    def test_shfmt_requires_editorconfig_and_ruff_format_requires_config(self):
        self.stub('shfmt', 'exit 0')
        self.stub('shellcheck', 'exit 0')
        self.stub('ruff', 'exit 0')
        self.use_stub_path()
        script = self.write('run.sh', 'echo hi\n')
        self.assertEqual([entry.tool for entry in self.module.entries(script, self.repo)],
                         ['shellcheck'])
        source = self.write('mod.py', 'x = 1\n')
        self.assertEqual([entry.mode for entry in self.module.entries(source, self.repo)],
                         ['lint'])
        (self.repo / 'ruff.toml').write_text('')
        self.assertEqual([entry.mode for entry in self.module.entries(source, self.repo)],
                         ['format', 'lint'])

    def test_unsupported_extension_has_no_tools(self):
        path = self.write('notes.md', '# title\n')
        self.assertEqual(list(self.module.entries(path, self.repo)), [])
        code, report = self.cli('--repo', self.repo, 'notes.md')
        self.assertEqual(code, 0)
        record = report['files'][0]
        self.assertEqual(record['tools'], [])
        self.assertEqual(record['uncovered'], [])
        self.assertEqual(record['sha256'], hashlib.sha256(b'# title\n').hexdigest())

    def test_clean_run_reports_schema_and_exit_zero(self):
        self.stub('goimports', 'exit 0')
        source = self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, 'main.go')
        self.assertEqual(code, 0)
        self.assertEqual(report['repo'], str(self.repo.resolve()))
        self.assertFalse(report['deadline_exceeded'])
        self.assertEqual(report['rule_family_coverage'],
                         self.module.RULE_FAMILY_COVERAGE)
        record = report['files'][0]
        self.assertEqual(record['path'], 'main.go')
        self.assertFalse(record['mutated'])
        self.assertEqual(record['sha256'],
                         hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(record['uncovered'], [])
        self.assertEqual(record['covered_families'], ['Imports'])
        outcome = record['tools'][0]
        self.assertEqual(outcome['tool'], 'goimports')
        self.assertEqual(outcome['mode'], 'format')
        self.assertEqual(outcome['exit_status'], 0)
        self.assertFalse(outcome['timed_out'])
        self.assertIsNone(outcome['diagnostics'])
        self.assertTrue(Path(outcome['log_path']).is_file())
        self.assertFalse(Path(outcome['log_path']).is_relative_to(self.repo))

    def test_formatter_mutation_precedes_lint_and_updates_hash(self):
        marker = self.directory / 'seen-by-eslint'
        self.stub('prettier', 'shift; printf "formatted\\n" > "$1"')
        self.stub('eslint', f'/bin/cp "$1" "{marker}"')
        source = self.write('app.js', 'unformatted\n')
        code, report = self.cli('--repo', self.repo, 'app.js')
        self.assertEqual(code, 0)
        self.assertEqual(source.read_text(), 'formatted\n')
        self.assertEqual(marker.read_text(), 'formatted\n')
        record = report['files'][0]
        self.assertTrue(record['mutated'])
        self.assertEqual(record['sha256'], hashlib.sha256(b'formatted\n').hexdigest())
        self.assertEqual([outcome['mode'] for outcome in record['tools']],
                         ['format', 'lint'])

    def test_lint_diagnostics_exit_one(self):
        self.stub('ruff', 'if [ "$1" = check ]; then echo "mod.py:1: E501"; exit 1; fi')
        (self.repo / 'ruff.toml').write_text('')
        self.write('mod.py', 'x = 1\n')
        code, report = self.cli('--repo', self.repo, 'mod.py')
        self.assertEqual(code, 1)
        lint = report['files'][0]['tools'][1]
        self.assertEqual(lint['exit_status'], 1)
        self.assertIn('mod.py:1: E501', lint['diagnostics'])
        self.assertIn('mod.py:1: E501', Path(lint['log_path']).read_text())
        self.assertEqual(report['files'][0]['covered_families'], [])

    def test_missing_tools_are_uncovered_never_failures(self):
        source = self.write('mod.py', 'x = 1\n')
        (self.repo / 'ruff.toml').write_text('')
        code, report = self.cli('--repo', self.repo, 'mod.py')
        self.assertEqual(code, 0)
        record = report['files'][0]
        self.assertEqual(record['tools'], [])
        self.assertEqual(record['uncovered'], ['ruff'])
        self.assertEqual(record['covered_families'], [])
        self.assertEqual(source.read_text(), 'x = 1\n')
        self.assertEqual(record['sha256'], hashlib.sha256(b'x = 1\n').hexdigest())

    def test_hash_is_stable_across_a_rerun(self):
        self.stub('prettier', 'shift; printf "formatted\\n" > "$1"')
        self.stub('eslint', 'exit 0')
        self.write('app.js', 'unformatted\n')
        first_code, first = self.cli('--repo', self.repo, 'app.js')
        second_code, second = self.cli('--repo', self.repo, 'app.js')
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first['files'][0]['sha256'], second['files'][0]['sha256'])
        self.assertFalse(second['files'][0]['mutated'])

    def test_files_from_json(self):
        self.stub('shellcheck', 'exit 0')
        self.write('run.sh', 'echo hi\n')
        self.write('notes.md', 'text\n')
        listing = self.directory / 'files.json'
        listing.write_text(json.dumps(['run.sh', 'notes.md']))
        code, report = self.cli('--repo', self.repo, '--files-from', listing)
        self.assertEqual(code, 0)
        self.assertEqual([record['path'] for record in report['files']],
                         ['run.sh', 'notes.md'])

    def test_directory_symlink_is_reported_as_the_link_itself(self):
        (self.repo / 'package/skills/spec-new').mkdir(parents=True)
        (self.repo / 'skills').mkdir()
        link = self.repo / 'skills' / 'spec-new'
        os.symlink('../package/skills/spec-new', link)
        listing = self.directory / 'files.json'
        listing.write_text(json.dumps(['skills/spec-new']))
        code, report = self.cli('--repo', self.repo, '--files-from', listing)
        self.assertEqual(code, 0)
        record = report['files'][0]
        self.assertEqual(record['path'], 'skills/spec-new')
        self.assertEqual(record['sha256'],
                         hashlib.sha256(os.fsencode(os.readlink(link))).hexdigest())
        self.assertEqual(record['mutated'], False)
        self.assertEqual(record['tools'], [])
        self.assertEqual(record['uncovered'], [])
        self.assertEqual(record['covered_families'], [])

    def test_files_from_non_json_names_expected_format(self):
        listing = self.directory / 'files.txt'
        listing.write_text('run.sh\nnotes.md\n')
        code, report = self.cli('--repo', self.repo, '--files-from', listing)
        self.assertEqual(code, 2)
        self.assertEqual(report['error'],
                         '--files-from must carry a JSON list of file paths')

    def test_cli_rejects_all_escape_forms_before_running_any_tool(self):
        marker = self.directory / 'tool-ran'
        self.stub('prettier', f'printf ran > "{marker}"; printf changed > "$2"')
        source = self.write('app.js', 'internal\n')
        external = self.directory / 'external.js'
        external.write_bytes(b'external\n')
        (self.repo / 'linked.js').symlink_to(external)
        (self.repo / 'linked-directory').symlink_to(self.directory, target_is_directory=True)
        for escaped in (str(external), '../external.js', 'linked.js',
                        'linked-directory/external.js'):
            with self.subTest(escaped=escaped):
                code, report = self.cli('--repo', self.repo, 'app.js', escaped)
                self.assertEqual(code, 2)
                self.assertIn('outside --repo', report['error'])
                self.assertEqual(external.read_bytes(), b'external\n')
                self.assertEqual(source.read_bytes(), b'internal\n')
                self.assertFalse(marker.exists())

    def test_cli_accepts_internal_absolute_paths_and_canonical_repo_alias(self):
        self.stub('prettier', 'exit 0')
        self.stub('eslint', 'exit 0')
        source = self.write('app.js', 'internal\n')
        alias = self.directory / 'alias'
        alias.symlink_to(self.repo, target_is_directory=True)
        code, report = self.cli('--repo', alias, alias / source.name)
        self.assertEqual(code, 0)
        self.assertEqual(report['repo'], str(self.repo.resolve()))
        self.assertEqual(report['files'][0]['path'], 'app.js')

    def test_check_only_cli_uses_read_only_commands(self):
        cases = {
            'goimports': ('main.go', [(['-d'], 0,
                                      'goimports requires formatting\nformatting required')]),
            'gofmt': ('main.go', [(['-d'], 0,
                                  'gofmt requires formatting\nformatting required')]),
            'php-cs-fixer': ('base.php', [
                (['fix', '--dry-run', '--diff', '--using-cache=no'], 1,
                 'php-cs-fixer exited 1\nformatting required')]),
            'prettier': ('app.js', [
                (['--check'], 1, 'prettier exited 1\nformatting required')]),
            'ruff': ('mod.py', [
                (['format', '--check', '--no-cache'], 1,
                 'ruff exited 1\nformatting required'),
                (['check', '--no-fix', '--no-fix-only', '--no-cache',
                  '--output-format', 'concise'], 1,
                 'ruff exited 1\nformatting required')]),
            'shfmt': ('run.sh', [(['-d'], 1, 'shfmt exited 1\nformatting required')]),
        }
        (self.repo / 'ruff.toml').write_text('[lint]\n')
        (self.repo / '.editorconfig').write_text('')
        for tool, (name, expected) in cases.items():
            with self.subTest(tool=tool):
                for stub in self.bin.iterdir():
                    stub.unlink()
                source = self.write(name, 'unformatted\n')
                calls = self.directory / f'{tool}-calls.jsonl'
                stub = self.bin / tool
                stub.write_text(
                    f'#!{sys.executable}\n'
                    'import json, pathlib, sys\n'
                    f'expected = {expected!r}\n'
                    f'calls = pathlib.Path({str(calls)!r})\n'
                    'previous = calls.read_text() if calls.exists() else ""\n'
                    'position = len(previous.splitlines())\n'
                    'calls.write_text(previous + json.dumps(sys.argv[1:]) + "\\n")\n'
                    'print("formatting required")\n'
                    'sys.exit(expected[position][1])\n'
                )
                stub.chmod(0o755)
                code, report = self.cli('--repo', self.repo, '--check-only', name)
                self.assertEqual(code, 1)
                self.assertTrue(report['check_only'])
                self.assertEqual(
                    [json.loads(line) for line in calls.read_text().splitlines()],
                    [[*arguments, str(source.resolve())] for arguments, _, _ in expected])
                record = report['files'][0]
                self.assertFalse(record['mutated'])
                self.assertEqual(record['sha256'], hashlib.sha256(b'unformatted\n').hexdigest())
                self.assertEqual(record['covered_families'], [])
                self.assertEqual(
                    [(outcome['command'][1:-1], outcome['exit_status'],
                      outcome['diagnostics']) for outcome in record['tools']], expected)

    def test_check_only_preserves_worktree_bytes_and_index(self):
        self.stub('prettier',
                  'if [ "$1" = --write ]; then printf changed > "$2"; exit 0; fi\n'
                  'echo "formatting required"; exit 1')
        self.stub('eslint', 'exit 0')
        self.write('app.js', 'unformatted\n')
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True,
                       capture_output=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True,
                       capture_output=True)
        before_tree = {str(path.relative_to(self.repo)): path.read_bytes()
                       for path in self.repo.rglob('*') if path.is_file()}
        code, report = self.cli('--repo', self.repo, '--check-only', 'app.js')
        self.assertEqual(code, 1)
        self.assertEqual(
            {str(path.relative_to(self.repo)): path.read_bytes()
             for path in self.repo.rglob('*') if path.is_file()}, before_tree)
        self.assertFalse(report['files'][0]['mutated'])
        self.assertEqual(report['files'][0]['sha256'],
                         hashlib.sha256(b'unformatted\n').hexdigest())

    def test_check_only_go_lint_disables_configured_fixes(self):
        marker = self.directory / 'lint-arguments'
        self.stub('goimports', 'exit 0')
        self.stub('git', 'exit 0')
        self.stub('golangci-lint', f'printf "%s\\n" "$@" > "{marker}"')
        self.write('.golangci.yml', 'issues:\n  fix: true\n')
        self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, '--check-only', 'main.go')
        self.assertEqual(code, 0)
        self.assertEqual(marker.read_text().splitlines(),
                         ['run', '--fix=false', '--new-from-rev', 'HEAD', './.'])
        self.assertEqual(report['files'][0]['tools'][1]['exit_status'], 0)

    def test_go_lint_preflight_failures_are_not_silent_skips(self):
        marker = self.directory / 'linter-ran'
        self.stub('goimports', 'exit 0')
        self.stub('golangci-lint', f'printf ran > "{marker}"')
        self.write('.golangci.yml', '')
        self.write('main.go', 'package main\n')
        cases = [
            ('#!/bin/sh\nexit 128\n', 128),
            ('#!/nonexistent/style-gate-test-interpreter\n', None),
        ]
        for content, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                git = self.bin / 'git'
                git.write_text(content)
                git.chmod(0o755)
                code, report = self.cli('--repo', self.repo, '--check-only', 'main.go')
                self.assertEqual(code, 1)
                outcome = report['files'][0]['tools'][1]
                self.assertEqual(outcome['tool'], 'golangci-lint')
                self.assertEqual(outcome['exit_status'], expected_status)
                self.assertIsNotNone(outcome['diagnostics'])
                self.assertFalse(outcome.get('skipped', False))
                self.assertEqual(outcome['preflight_command'][1:],
                                 ['rev-parse', '--verify', '-q', 'HEAD'])
                self.assertFalse(marker.exists())

    def test_go_lint_preflight_timeout_blocks_main(self):
        marker = self.directory / 'linter-ran'
        self.stub('goimports', 'exit 0')
        self.stub('git', '/bin/sleep 5')
        self.stub('golangci-lint', f'printf ran > "{marker}"')
        self.write('.golangci.yml', '')
        self.write('main.go', 'package main\n')
        self.use_stub_path()
        output = io.StringIO()
        with patch.object(self.module, 'TOOL_TIMEOUT_SECONDS', 0.2):
            with contextlib.redirect_stdout(output):
                code = self.module.main(['--repo', str(self.repo), '--check-only', 'main.go'])
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        outcome = report['files'][0]['tools'][1]
        self.assertTrue(outcome['timed_out'])
        self.assertIn('git exceeded', outcome['diagnostics'])
        self.assertFalse(outcome.get('skipped', False))
        self.assertFalse(marker.exists())
        for result in report['files'][0]['tools']:
            self.addCleanup(Path(result['log_path']).unlink, missing_ok=True)

    def test_go_lint_skips_a_genuinely_unborn_head(self):
        git = shutil.which('git')
        subprocess.run([git, 'init', '-q', str(self.repo)], check=True, capture_output=True)
        marker = self.directory / 'linter-ran'
        self.stub('git', f'exec "{git}" "$@"')
        self.stub('goimports', 'exit 0')
        self.stub('golangci-lint', f'printf ran > "{marker}"')
        self.write('.golangci.yml', '')
        self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, '--check-only', 'main.go')
        self.assertEqual(code, 0)
        outcome = report['files'][0]['tools'][1]
        self.assertTrue(outcome['skipped'])
        self.assertEqual(outcome['reason'], 'no git HEAD')
        self.assertIsNone(outcome['diagnostics'])
        self.assertFalse(marker.exists())

    def test_go_lint_uses_only_deadline_budget_left_after_preflight(self):
        self.stub('git', 'exit 0')
        self.stub('golangci-lint', 'exit 0')
        self.use_stub_path()
        self.write('.golangci.yml', '')
        source = self.write('main.go', 'package main\n')
        cases = [
            (35.0, [('git', 30), ('golangci-lint', 5)], False),
            (40.0, [('git', 30)], True),
        ]
        for after_preflight, expected_calls, exceeded in cases:
            with self.subTest(after_preflight=after_preflight):
                calls = []

                def execute(entry, cwd, timeout, environment):
                    calls.append((entry.tool, timeout))
                    log = self.directory / f'{entry.tool}.log'
                    log.write_text('')
                    self.addCleanup(log.unlink, missing_ok=True)
                    return {'tool': entry.tool, 'mode': entry.mode,
                            'command': entry.command, 'exit_status': 0,
                            'timed_out': False, 'mutated': False,
                            'diagnostics': None, 'log_path': str(log)}

                with patch.object(self.module, 'execute', side_effect=execute):
                    with patch.object(self.module.time, 'monotonic',
                                      side_effect=[0.0, 0.0, after_preflight]):
                        report = self.module.run_gate(self.repo, [source],
                                                      deadline_seconds=40)
                self.assertEqual(calls, expected_calls)
                self.assertEqual(report['deadline_exceeded'], exceeded)
                self.assertEqual(report['files'][0]['sha256'],
                                 hashlib.sha256(b'package main\n').hexdigest())

    def test_clean_check_only_go_reports_import_coverage(self):
        self.stub('goimports', '[ "$1" = -d ]')
        self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, '--check-only', 'main.go')
        self.assertEqual(code, 0)
        self.assertEqual(report['files'][0]['covered_families'], ['Imports'])
        self.assertFalse(report['files'][0]['mutated'])

    def test_check_only_detects_unexpected_mutation_even_on_failure(self):
        self.stub('ruff', 'for path do :; done; printf changed > "$path"; exit 1')
        source = self.write('mod.py', 'original\n')
        code, report = self.cli('--repo', self.repo, '--check-only', 'mod.py')
        self.assertEqual(code, 1)
        record = report['files'][0]
        self.assertTrue(record['mutated'])
        self.assertIn('mutated a check-only input', record['tools'][0]['diagnostics'])
        self.assertEqual(record['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(record['covered_families'], [])

    def test_check_only_launch_failure_blocks_with_diagnostic(self):
        executable = self.bin / 'goimports'
        executable.write_text('#!/nonexistent/style-gate-test-interpreter\n')
        executable.chmod(0o755)
        source = self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, '--check-only', 'main.go')
        self.assertEqual(code, 1)
        record = report['files'][0]
        self.assertIsNone(record['tools'][0]['exit_status'])
        self.assertIsNotNone(record['tools'][0]['diagnostics'])
        self.assertEqual(record['covered_families'], [])
        self.assertEqual(source.read_bytes(), b'package main\n')

    def test_check_only_deadline_exhaustion_returns_failure(self):
        self.stub('ruff', 'exit 0')
        self.use_stub_path()
        self.write('mod.py', 'x = 1\n')
        output = io.StringIO()
        with patch.object(self.module.time, 'monotonic', side_effect=[0.0, 100.0]):
            with contextlib.redirect_stdout(output):
                code = self.module.main(['--repo', str(self.repo), '--check-only', 'mod.py'])
        self.assertEqual(code, 1)
        report = json.loads(output.getvalue())
        self.assertTrue(report['deadline_exceeded'])
        self.assertEqual(report['files'][0]['tools'], [])

    def test_deadline_exhaustion_finalizes_the_record(self):
        self.stub('prettier', 'shift; printf "formatted\\n" > "$1"')
        self.stub('eslint', 'exit 0')
        self.use_stub_path()
        path = self.write('app.js', 'unformatted\n')
        calls = iter([0.0, 0.0, 100.0])
        self.addCleanup(setattr, self.module, 'time', self.module.time)
        self.module.time = type('FakeTime', (), {'monotonic': staticmethod(lambda: next(calls))})
        record, exceeded = self.module.run_file(path, self.repo, self.environment(), deadline=100.0)
        self.assertTrue(exceeded)
        self.assertEqual(record['sha256'], hashlib.sha256(b'formatted\n').hexdigest())
        self.assertTrue(record['mutated'])
        self.assertEqual(record['covered_families'], [])

    def test_parallel_gate_records_equal_serial_run_file(self):
        self.stub('prettier', 'shift; printf "formatted\\n" > "$1"')
        self.stub('eslint', 'exit 0')
        self.stub('ruff', 'if [ "$1" = check ]; then echo "gamma.py:1: E501"; exit 1; fi')
        names = ['alpha.js', 'beta.js', 'gamma.py', 'delta.js', 'notes.md']
        for name in names:
            self.write(name, 'unformatted\n')
        code, report = self.cli('--repo', self.repo, *names)
        self.assertEqual(code, 1)
        self.assertEqual(set(report),
                         {'repo', 'check_only', 'deadline_seconds', 'deadline_exceeded',
                          'rule_family_coverage', 'gate_fingerprint', 'uncovered', 'files'})
        self.assertFalse(report['deadline_exceeded'])
        self.assertEqual([record['path'] for record in report['files']], names)
        for name in names:
            self.write(name, 'unformatted\n')
        self.use_stub_path()
        resolved_repo = self.repo.resolve()
        deadline = self.module.time.monotonic() + self.module.DEADLINE_SECONDS
        reference = []
        for name in names:
            record, exceeded = self.module.run_file((self.repo / name).resolve(),
                                                    resolved_repo, self.environment(), deadline)
            self.assertFalse(exceeded)
            reference.append(record)
            for outcome in record['tools']:
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)
        self.assertEqual(self.without_log_paths(report['files']),
                         self.without_log_paths(reference))

    def test_duplicate_names_run_one_serial_task_per_file(self):
        self.stub('prettier',
                  'shift; IFS= read -r content < "$1"; '
                  'if [ "$content" = unformatted ]; then printf "formatted\\n" > "$1"; fi')
        self.stub('eslint', 'exit 0')
        self.write('app.js', 'unformatted\n')
        self.write('other.js', 'unformatted\n')
        (self.repo / 'alias.js').symlink_to(self.repo / 'app.js')
        code, report = self.cli('--repo', self.repo, 'app.js', 'other.js', 'alias.js')
        self.assertEqual(code, 0)
        self.assertEqual([record['path'] for record in report['files']],
                         ['app.js', 'other.js', 'app.js'])
        self.assertEqual([record['mutated'] for record in report['files']],
                         [True, True, False])
        self.assertEqual((self.repo / 'app.js').read_text(), 'formatted\n')
        self.assertEqual((self.repo / 'other.js').read_text(), 'formatted\n')

    def test_hardlinked_names_run_one_serial_task(self):
        self.stub('prettier',
                  'shift; IFS= read -r content < "$1"; '
                  'if [ "$content" = unformatted ]; then printf "formatted\\n" > "$1"; fi')
        self.stub('eslint', 'exit 0')
        self.write('app.js', 'unformatted\n')
        self.write('other.js', 'unformatted\n')
        os.link(self.repo / 'app.js', self.repo / 'twin.js')
        code, report = self.cli('--repo', self.repo, 'app.js', 'other.js', 'twin.js')
        self.assertEqual(code, 0)
        self.assertEqual([record['path'] for record in report['files']],
                         ['app.js', 'other.js', 'twin.js'])
        self.assertEqual([record['mutated'] for record in report['files']],
                         [True, True, False])
        self.assertEqual((self.repo / 'app.js').read_text(), 'formatted\n')
        self.assertEqual((self.repo / 'other.js').read_text(), 'formatted\n')

    def test_deadline_refusal_omits_unsubmitted_files(self):
        self.stub('ruff', '/bin/sleep 5')
        self.use_stub_path()
        names = ['first.py', 'second.py', 'third.py', 'fourth.py']
        for name in names:
            self.write(name, 'x = 1\n')
        with patch.object(self.module, 'GATE_WORKERS', 2):
            report = self.module.run_gate(self.repo, [self.repo / name for name in names],
                                          deadline_seconds=0.5)
        self.assertEqual([record['path'] for record in report['files']],
                         ['first.py', 'second.py'])
        self.assertTrue(report['deadline_exceeded'])
        for record in report['files']:
            for outcome in record['tools']:
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)

    def test_deadline_refusal_keeps_input_order_prefix_for_straddled_duplicates(self):
        self.stub('ruff', '/bin/sleep 5')
        self.use_stub_path()
        names = ['a.py', 'b.py', 'twin.py']
        self.write('a.py', 'x = 1\n')
        self.write('b.py', 'x = 1\n')
        os.link(self.repo / 'a.py', self.repo / 'twin.py')
        with patch.object(self.module, 'GATE_WORKERS', 1):
            report = self.module.run_gate(self.repo, [self.repo / name for name in names],
                                          deadline_seconds=0.5)
        self.assertEqual([record['path'] for record in report['files']], ['a.py'])
        self.assertTrue(report['deadline_exceeded'])
        self.assertTrue(report['files'][0]['tools'][0]['timed_out'])
        for record in report['files']:
            for outcome in record['tools']:
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)

    def test_task_exception_propagates_out_of_run_gate(self):
        with self.assertRaises(OSError):
            self.module.run_gate(self.repo, [self.repo / 'absent.py'])

    def test_tool_timeout_is_a_diagnostic(self):
        self.stub('shellcheck', '/bin/sleep 5')
        self.use_stub_path()
        self.addCleanup(setattr, self.module, 'TOOL_TIMEOUT_SECONDS',
                        self.module.TOOL_TIMEOUT_SECONDS)
        self.module.TOOL_TIMEOUT_SECONDS = 0.2
        path = self.write('run.sh', 'echo hi\n')
        report = self.module.run_gate(self.repo, [path], check_only=True)
        outcome = report['files'][0]['tools'][0]
        self.assertTrue(outcome['timed_out'])
        self.assertIsNotNone(outcome['diagnostics'])

    def test_usage_and_io_errors_exit_two(self):
        existing = self.write('mod.py', 'x = 1\n')
        cases = [
            ('--repo', self.repo),
            ('--repo', self.repo, 'missing.py'),
            ('--repo', self.directory / 'absent', 'mod.py'),
            ('--repo', existing, 'mod.py'),
        ]
        for arguments in cases:
            with self.subTest(arguments=arguments):
                code, report = self.cli(*arguments)
                self.assertEqual(code, 2)
                self.assertIn('error', report)
        listing = self.directory / 'bad.json'
        listing.write_text('{"not": "a list"}')
        code, report = self.cli('--repo', self.repo, '--files-from', listing)
        self.assertEqual(code, 2)
        self.assertIn('error', report)


    def test_configured_tool_replaces_discovered_entry_and_strips_prefix(self):
        self.stub('prettier', 'exit 0')
        (self.repo / '.style-gate.json').write_text(json.dumps({
            'tools': {'eslint': {
                'mode': 'lint',
                'extensions': ['.ts'],
                'check': ['docker', 'exec', 'bidder_node', 'sh', '-c',
                          'cd /app && npx eslint {path}'],
                'strip_prefix': 'frontend/',
            }}}))
        (self.repo / 'frontend/src').mkdir(parents=True)
        target = self.write('frontend/src/app.ts', 'export {};\n')
        entries = list(self.module.entries(target, self.repo))
        self.assertEqual([entry.tool for entry in entries], ['prettier', 'eslint'])
        eslint = entries[-1]
        self.assertEqual(eslint.mode, 'lint')
        expected = ['docker', 'exec', 'bidder_node', 'sh', '-c',
                    'cd /app && npx eslint src/app.ts']
        self.assertEqual(eslint.command, expected)
        self.assertEqual(eslint.check_command, expected)

    def test_configured_tool_suppresses_same_named_discovered_entry(self):
        self.stub('prettier', 'exit 0')
        self.stub('eslint', 'exit 0')
        (self.repo / '.style-gate.json').write_text(json.dumps({
            'tools': {'eslint': {
                'mode': 'lint',
                'extensions': ['.ts'],
                'check': ['docker', 'exec', 'bidder_node', 'sh', '-c',
                          'cd /app && npx eslint {path}'],
                'strip_prefix': 'frontend/',
            }}}))
        (self.repo / 'frontend/src').mkdir(parents=True)
        target = self.write('frontend/src/app.ts', 'export {};\n')
        entries = list(self.module.entries(target, self.repo))
        self.assertEqual([entry.tool for entry in entries], ['prettier', 'eslint'])
        self.assertEqual(entries[-1].command[0], 'docker')

    def test_malformed_config_raises_and_fails_gate_with_exit_two(self):
        self.write('app.ts', 'export {};\n')
        (self.repo / '.style-gate.json').write_text('{broken')
        with self.assertRaises(ValueError):
            self.module.load_tool_config(self.repo)
        code, report = self.cli('--repo', self.repo, 'app.ts')
        self.assertEqual(code, 2)
        self.assertIn('error', report)

    def test_multiple_go_files_deduplicate_head_and_package_golangci_lint(self):
        events = self.directory / 'events.log'
        self.stub('goimports',
                  f'echo "goimports:$2" >> "{events}"\n'
                  'shift; printf "package main\\n// formatted\\n" > "$1"')
        self.stub('git', f'echo "git:$*" >> "{events}"')
        self.stub('golangci-lint',
                  f'pwd_dir=$(pwd -P); echo "golangci-lint:$pwd_dir:$*" >> "{events}"')
        self.write('.golangci.yml', '')
        (self.repo / 'pkg').mkdir()
        files = ['a.go', 'b.go', 'pkg/c.go']
        for name in files:
            self.write(name, 'package main\n')
        code, report = self.cli('--repo', self.repo, *files)
        self.assertEqual(code, 0)
        self.assertEqual([record['path'] for record in report['files']], files)
        recorded = events.read_text().splitlines()
        git_events = [line for line in recorded if line.startswith('git:')]
        lint_events = [line for line in recorded if line.startswith('golangci-lint:')]
        self.assertEqual(git_events, ['git:rev-parse --verify -q HEAD'])
        resolved_repo = self.repo.resolve()
        self.assertCountEqual(lint_events, [
            f'golangci-lint:{resolved_repo}:run --new-from-rev HEAD ./.',
            f'golangci-lint:{resolved_repo / "pkg"}:run --new-from-rev HEAD ./.',
        ])
        root_format_indices = [
            index for index, line in enumerate(recorded)
            if line in (f'goimports:{resolved_repo / "a.go"}',
                        f'goimports:{resolved_repo / "b.go"}')
        ]
        root_lint_index = recorded.index(
            f'golangci-lint:{resolved_repo}:run --new-from-rev HEAD ./.'
        )
        self.assertEqual(len(root_format_indices), 2)
        self.assertTrue(all(index < root_lint_index for index in root_format_indices))
        expected_sha = hashlib.sha256(b'package main\n// formatted\n').hexdigest()
        lint_logs = []
        for record in report['files']:
            self.assertTrue(record['mutated'])
            self.assertEqual(record['sha256'], expected_sha)
            self.assertEqual(record['covered_families'], ['Imports'])
            self.assertEqual([tool['tool'] for tool in record['tools']],
                             ['goimports', 'golangci-lint'])
            lint_logs.append(record['tools'][1]['log_path'])
            self.assertTrue(Path(record['tools'][1]['log_path']).is_file())
        self.assertEqual(len(set(lint_logs)), 3)

    def test_multiple_go_files_do_not_block_single_worker_pool(self):
        events = self.directory / 'events.log'
        self.stub('goimports', f'echo "goimports:$2" >> "{events}"')
        self.stub('git', f'echo "git:$*" >> "{events}"')
        self.stub('golangci-lint', f'echo "golangci-lint:$*" >> "{events}"')
        self.use_stub_path()
        self.write('.golangci.yml', '')
        paths = [self.write('a.go', 'package main\n'),
                 self.write('b.go', 'package main\n')]
        with patch.object(self.module, 'GATE_WORKERS', 1):
            report = self.module.run_gate(self.repo, paths)
        for record in report['files']:
            for outcome in record['tools']:
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)
        recorded = events.read_text().splitlines()
        self.assertEqual([line for line in recorded if line.startswith('git:')],
                         ['git:rev-parse --verify -q HEAD'])
        self.assertEqual([line for line in recorded if line.startswith('golangci-lint:')],
                         ['golangci-lint:run --new-from-rev HEAD ./.'])
        self.assertEqual([record['covered_families'] for record in report['files']],
                         [['Imports'], ['Imports']])

    def test_multiple_go_files_share_preflight_failure_and_unborn_head(self):
        self.stub('goimports', 'exit 0')
        self.stub('golangci-lint', 'exit 0')
        self.write('.golangci.yml', '')
        self.write('a.go', 'package main\n')
        self.write('b.go', 'package main\n')
        git_calls = self.directory / 'git-calls.log'
        self.stub('git', f'echo called >> "{git_calls}"; exit 1')
        code, report = self.cli('--repo', self.repo, 'a.go', 'b.go')
        self.assertEqual(code, 0)
        self.assertEqual(len(git_calls.read_text().splitlines()), 1)
        for record in report['files']:
            self.assertTrue(record['tools'][1]['skipped'])
            self.assertEqual(record['tools'][1]['reason'], 'no git HEAD')
        git_calls.unlink()
        self.stub('git', f'echo called >> "{git_calls}"; exit 128')
        code, report = self.cli('--repo', self.repo, 'a.go', 'b.go')
        self.assertEqual(code, 1)
        self.assertEqual(len(git_calls.read_text().splitlines()), 1)
        log_paths = []
        for record in report['files']:
            outcome = record['tools'][1]
            self.assertEqual(outcome['exit_status'], 128)
            self.assertEqual(outcome['preflight_command'][1:],
                             ['rev-parse', '--verify', '-q', 'HEAD'])
            log_paths.append(outcome['log_path'])
            self.assertTrue(Path(outcome['log_path']).is_file())
        self.assertEqual(len(set(log_paths)), 2)

    def test_multiple_go_files_deadline_expiry_finalizes_deferred_without_timeout(self):
        self.stub('goimports', 'exit 0')
        self.stub('git', 'exit 0')
        self.stub('golangci-lint', 'exit 0')
        self.use_stub_path()
        self.write('.golangci.yml', '')
        paths = [self.write('a.go', 'package main\n'),
                 self.write('b.go', 'package main\n')]
        lint_calls = []

        def fake_execute(entry, cwd, timeout, environment):
            if entry.tool == 'golangci-lint':
                lint_calls.append(entry.tool)
            if entry.tool == 'goimports':
                Path(entry.command[-1]).write_text('package main\n// formatted\n')
            log = self.directory / f'{entry.tool}-{len(lint_calls)}.log'
            log.write_text('')
            self.addCleanup(log.unlink, missing_ok=True)
            return {'tool': entry.tool, 'mode': entry.mode, 'command': entry.command,
                    'exit_status': 0, 'timed_out': False, 'mutated': False,
                    'diagnostics': None, 'log_path': str(log)}

        with patch.object(self.module, 'GATE_WORKERS', 1):
            with patch.object(self.module, 'execute', side_effect=fake_execute):
                with patch.object(self.module.time, 'monotonic',
                                  side_effect=[0.0, 1.0, 2.0, 3.0, 4.0, 100.0]):
                    report = self.module.run_gate(self.repo, paths, deadline_seconds=10)
        self.assertTrue(report['deadline_exceeded'])
        self.assertEqual(lint_calls, [])
        self.assertEqual(len(report['files']), 1)
        record = report['files'][0]
        self.assertTrue(record['mutated'])
        self.assertEqual(record['sha256'],
                         hashlib.sha256(b'package main\n// formatted\n').hexdigest())
        self.assertEqual(record['covered_families'], ['Imports'])
        self.assertEqual([tool['tool'] for tool in record['tools']], ['goimports'])
        self.assertFalse(any(tool['timed_out'] for tool in record['tools']))

    def test_multiple_go_files_share_golangci_lint_diagnostics(self):
        self.stub('goimports', 'exit 0')
        self.stub('git', 'exit 0')
        self.stub('golangci-lint', 'echo "a.go:1:1: lint error"; exit 1')
        self.write('.golangci.yml', '')
        self.write('a.go', 'package main\n')
        self.write('b.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, 'a.go', 'b.go')
        self.assertEqual(code, 1)
        log_paths = []
        for record in report['files']:
            lint = record['tools'][1]
            self.assertEqual(lint['exit_status'], 1)
            self.assertIn('a.go:1:1: lint error', lint['diagnostics'])
            self.assertIn('a.go:1:1: lint error', Path(lint['log_path']).read_text())
            log_paths.append(lint['log_path'])
        self.assertEqual(len(set(log_paths)), 2)

    def test_report_carries_gate_fingerprint_and_uncovered_and_is_stable(self):
        self.stub('goimports', 'exit 0')
        self.write('main.go', 'package main\n')
        first_code, first = self.cli('--repo', self.repo, 'main.go')
        second_code, second = self.cli('--repo', self.repo, 'main.go')
        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first['uncovered'], [])
        self.assertEqual(second['uncovered'], [])
        fingerprint = first['gate_fingerprint']
        self.assertIsNone(fingerprint['config_sha256'])
        self.assertEqual(fingerprint, second['gate_fingerprint'])
        self.assertEqual(set(fingerprint['tools']), set(self.module.DEFAULT_TOOLS))
        self.assertEqual(fingerprint['tools']['goimports'], {
            'path': str(self.bin / 'goimports'),
            'size': (self.bin / 'goimports').stat().st_size,
        })
        self.assertIsNone(fingerprint['tools']['ruff'])

    def test_fingerprint_flag_matches_report_and_runs_no_tools(self):
        marker = self.directory / 'tool-ran'
        self.stub('ruff', f'printf ran > "{marker}"; exit 0')
        self.write('mod.py', 'x = 1\n')
        fp_code, fingerprint = self.cli('--fingerprint', '--repo', self.repo)
        self.assertEqual(fp_code, 0)
        self.assertFalse(marker.exists())
        gate_code, report = self.cli('--repo', self.repo, 'mod.py')
        self.assertEqual(gate_code, 0)
        self.assertTrue(marker.exists())
        self.assertEqual(fingerprint, report['gate_fingerprint'])

    def test_fingerprint_changes_when_config_or_tool_binary_changes(self):
        self.stub('ruff', 'exit 0')
        self.write('mod.py', 'x = 1\n')
        _, baseline = self.cli('--fingerprint', '--repo', self.repo)

        ruff_stub = self.stub('ruff', 'exit 0  # longer binary content')
        _, after_binary = self.cli('--fingerprint', '--repo', self.repo)
        self.assertNotEqual(after_binary, baseline)
        self.assertEqual(after_binary['tools']['ruff']['size'], ruff_stub.stat().st_size)

        config = {
            'tools': {
                'ruff': {
                    'extensions': ['.py'],
                    'mode': 'lint',
                    'check': ['ruff', 'check', '--no-cache', '{path}'],
                },
                'custom-lint': {
                    'extensions': ['.py'],
                    'mode': 'lint',
                    'check': ['ruff', 'check', '{path}'],
                },
            },
        }
        config_path = self.repo / '.style-gate.json'
        config_path.write_text(json.dumps(config))
        _, after_config = self.cli('--fingerprint', '--repo', self.repo)
        self.assertNotEqual(after_config, after_binary)
        self.assertEqual(after_config['config_sha256'],
                         hashlib.sha256(config_path.read_bytes()).hexdigest())
        self.assertEqual(after_config['tools']['ruff'], {
            'path': str(ruff_stub),
            'size': ruff_stub.stat().st_size,
            'check': ['ruff', 'check', '--no-cache', '{path}'],
        })
        self.assertEqual(after_config['tools']['custom-lint'], {
            'path': str(ruff_stub),
            'size': ruff_stub.stat().st_size,
            'check': ['ruff', 'check', '{path}'],
        })

        config['tools']['ruff']['check'] = ['ruff', 'check', '--no-fix', '{path}']
        config_path.write_text(json.dumps(config))
        _, after_check_edit = self.cli('--fingerprint', '--repo', self.repo)
        self.assertNotEqual(after_check_edit, after_config)

    def test_top_level_uncovered_lists_missing_tools_with_null_fingerprint(self):
        self.write('mod.py', 'x = 1\n')
        self.write('other.py', 'y = 2\n')
        self.write('main.go', 'package main\n')
        code, report = self.cli('--repo', self.repo, 'mod.py', 'other.py', 'main.go')
        self.assertEqual(code, 0)
        self.assertEqual(report['uncovered'], ['goimports', 'ruff'])
        self.assertIsNone(report['gate_fingerprint']['tools']['ruff'])
        self.assertIsNone(report['gate_fingerprint']['tools']['goimports'])


if __name__ == '__main__':
    unittest.main()
