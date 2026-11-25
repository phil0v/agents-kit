import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import load_script_module


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/context-pack.py'
REPOSITORY_SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/repository.py'


class ContextPackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / 'repository with spaces'
        self.repo.mkdir()
        self.write('src/alpha.py',
                   'import os\nimport json\n\n\ndef alpha_run():\n    return helper()\n\n\ndef helper():\n    return 1\n\n\nclass Worker:\n    def act(self):\n        return 2\n')
        self.write('src/caller.py', 'from src.alpha import alpha_run\n\n\ndef consume():\n    return alpha_run()\n')
        self.write('tests/test_alpha.py', 'def test_alpha_run():\n    assert True\n')
        self.write('src/server.go',
                   'package main\n\nimport "fmt"\n\nfunc Serve() {\n\tfmt.Println()\n}\n\nconst port = 1\n')
        self.write('src/server_test.go', 'package main\n\nfunc TestServe(t interface{}) {\n\t_ = port\n}\n')
        self.write('src/Util.php',
                   '<?php\nuse Vendor\\Thing;\n\nclass Util {\n    public function gather() {\n    }\n}\n')
        self.write('tests/UtilTest.php', '<?php\nclass UtilTest {\n}\n')
        self.write('src/app.ts',
                   "import {x} from './dep';\n\nexport function render(): number {\n  return 1;\n}\n\nexport const config = {};\n")
        self.write('src/app.test.ts', "import {render} from './app';\n\nrender();\n")
        self.write('tools/run.sh', '#!/bin/sh\ndeploy() {\n  :\n}\n')
        self.write('tools/release.sh', '#!/bin/sh\nsource tools/run.sh\ndeploy\n')
        self.write('agents/worker.toml', '[agents.worker]\nmodel = "sonnet"\n')
        self.write('src/worker.py', 'def worker():\n    return 1\n')
        self.write('docs/notes.txt', 'plain prose\n')
        self.write('vendor/ignored.py', 'def alpha_run():\n    pass\n')

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def number_definitions(self, count, prefix='number'):
        return ''.join(f'def {prefix}_{index}():\n    pass\n\n' for index in range(count))

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), '--repo', str(self.repo), *map(str, arguments)],
            capture_output=True, text=True, timeout=30,
            env=dict(os.environ, AGENTS_METRICS='off'),
        )

    def pack(self, *arguments):
        result = self.cli(*arguments)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        return result.stdout

    def test_python_outline_imports_callers_and_tests(self):
        output = json.loads(self.pack('--format', 'json', 'src/alpha.py'))
        entry = output['files'][0]
        self.assertTrue(entry['supported'])
        names = [symbol['name'] for symbol in entry['outline']]
        self.assertEqual(['alpha_run', 'helper', 'Worker', 'act'], names)
        self.assertEqual(5, entry['outline'][0]['line'])
        self.assertEqual(['os', 'json'], entry['imports'])
        self.assertIn('src/caller.py:1', entry['callers']['alpha_run'])
        self.assertNotIn('Worker', entry['callers'])
        self.assertEqual(['tests/test_alpha.py'], entry['tests'])

    def test_symbol_less_file_produces_empty_callers(self):
        self.write('src/constants.py', 'VALUE = 1\nOTHER = 2\n')
        output = json.loads(self.pack('--format', 'json', 'src/constants.py'))
        entry = output['files'][0]
        self.assertTrue(entry['supported'])
        self.assertEqual(entry['outline'], [])
        self.assertEqual(entry['callers'], {})

    def test_go_outline_and_tests(self):
        output = json.loads(self.pack('--format', 'json', 'src/server.go'))
        entry = output['files'][0]
        self.assertEqual(['Serve', 'port'], [symbol['name'] for symbol in entry['outline']])
        self.assertEqual(['fmt'], entry['imports'])
        self.assertEqual(['src/server_test.go'], entry['tests'])
        self.assertIn('src/server_test.go:4', entry['callers']['port'])

    def test_php_outline(self):
        output = json.loads(self.pack('--format', 'json', 'src/Util.php'))
        entry = output['files'][0]
        self.assertEqual(['Util', 'gather'], [symbol['name'] for symbol in entry['outline']])
        self.assertEqual(['Vendor\\Thing'], entry['imports'])
        self.assertEqual(['tests/UtilTest.php'], entry['tests'])

    def test_typescript_outline_imports_and_callers(self):
        output = json.loads(self.pack('--format', 'json', 'src/app.ts'))
        entry = output['files'][0]
        self.assertEqual(['render', 'config'], [symbol['name'] for symbol in entry['outline']])
        self.assertEqual(['./dep'], entry['imports'])
        self.assertIn('src/app.test.ts:1', entry['callers']['render'])
        self.assertEqual(['src/app.test.ts'], entry['tests'])

    def test_shell_outline_and_callers(self):
        output = json.loads(self.pack('--format', 'json', 'tools/run.sh'))
        entry = output['files'][0]
        self.assertEqual(['deploy'], [symbol['name'] for symbol in entry['outline']])
        self.assertIn('tools/release.sh:3', entry['callers']['deploy'])

    def test_markdown_outline_excludes_fenced_code(self):
        self.write('docs/guide.md',
                   '# Guide\n\nIntro text.\n\n```sh\n# not a heading\necho hello\n```\n\n'
                   '## Usage ##\n\nMore text.\n\n~~~\n# hidden to end of file\n')
        output = json.loads(self.pack('--format', 'json', 'docs/guide.md'))
        entry = output['files'][0]
        self.assertTrue(entry['supported'])
        self.assertEqual([{'name': 'Guide', 'line': 1}, {'name': 'Usage', 'line': 10}], entry['outline'])
        self.assertEqual([], entry['imports'])

    def test_markdown_outline_callers_and_tests(self):
        self.write('docs/deploy.md', '# Deploy runbook\n\nIntro.\n\n## Retry policy\n\nText.\n')
        self.write('src/pipeline.py', 'RUNBOOK = "Deploy runbook"\n')
        self.write('tests/test_deploy.py', 'def test_deploy():\n    assert True\n')
        output = json.loads(self.pack('--format', 'json', 'docs/deploy.md'))
        entry = output['files'][0]
        self.assertEqual(['Deploy runbook', 'Retry policy'], [symbol['name'] for symbol in entry['outline']])
        self.assertIn('src/pipeline.py:1', entry['callers']['Deploy runbook'])
        self.assertNotIn('Retry policy', entry['callers'])
        self.assertEqual(['tests/test_deploy.py'], entry['tests'])

    def test_toml_outline_sections_and_top_level_keys(self):
        self.write('agents/runner.toml',
                   'name = "runner"\n\n[agents.worker]\ndescription = "does work"\n  indented = true\n\n'
                   '[[forms]]\nname = "form"\n')
        output = json.loads(self.pack('--format', 'json', 'agents/runner.toml'))
        entry = output['files'][0]
        self.assertTrue(entry['supported'])
        self.assertEqual(
            [{'name': 'name', 'line': 1}, {'name': 'agents.worker', 'line': 3},
             {'name': 'description', 'line': 4}, {'name': 'name', 'line': 8}],
            entry['outline'])
        self.assertEqual([], entry['imports'])

    def test_vendored_files_are_excluded_from_caller_search(self):
        output = json.loads(self.pack('--format', 'json', 'src/alpha.py'))
        self.assertNotIn('vendor/ignored.py', json.dumps(output))

    def test_unsupported_file_contributes_path_only(self):
        output = json.loads(self.pack('--format', 'json', 'docs/notes.txt'))
        entry = output['files'][0]
        self.assertEqual('docs/notes.txt', entry['path'])
        self.assertFalse(entry['supported'])
        self.assertNotIn('outline', entry)

    def test_directory_symlink_maps_the_link_and_its_target(self):
        self.write('plugins/code-flow/skills/spec-flow/SKILL.md', '# Spec flow\n')
        (self.repo / 'skills').mkdir()
        os.symlink('../plugins/code-flow/skills/spec-flow', self.repo / 'skills' / 'spec-flow')
        output = json.loads(self.pack('--format', 'json', 'skills/spec-flow'))
        entry = output['files'][0]
        self.assertEqual('skills/spec-flow', entry['path'])
        self.assertFalse(entry['supported'])
        self.assertEqual('../plugins/code-flow/skills/spec-flow', entry['symlink_target'])
        self.assertNotIn('outline', entry)
        text = self.pack('skills/spec-flow')
        self.assertIn('== skills/spec-flow', text)
        self.assertIn('symlink: ../plugins/code-flow/skills/spec-flow', text)

    def test_mixed_file_set_in_text_form(self):
        output = self.pack('src/alpha.py', 'docs/notes.txt')
        self.assertIn('== src/alpha.py', output)
        self.assertIn('alpha_run', output)
        self.assertIn('== docs/notes.txt', output)
        self.assertIn('unsupported file type', output)

    def test_multi_file_entries_equal_single_file_entries(self):
        files = ('src/alpha.py', 'src/caller.py', 'src/server.go', 'src/Util.php',
                 'src/app.ts', 'tools/run.sh', 'agents/worker.toml', 'src/worker.py',
                 'docs/notes.txt')
        combined = json.loads(self.pack('--format', 'json', *files))
        self.assertEqual(list(files), [entry['path'] for entry in combined['files']])
        for path, entry in zip(files, combined['files']):
            alone = json.loads(self.pack('--format', 'json', path))['files'][0]
            self.assertEqual(alone, entry)
        sections = [self.pack(path).split('\n', 1)[1] for path in files]
        self.assertEqual(''.join(sections), self.pack(*files).split('\n', 1)[1])

    def test_callers_stay_limited_to_each_files_own_symbols(self):
        output = json.loads(self.pack('--format', 'json', 'src/alpha.py', 'src/caller.py'))
        alpha, caller = output['files']
        self.assertIn('src/caller.py:1', alpha['callers']['alpha_run'])
        self.assertNotIn('consume', alpha['callers'])
        self.assertNotIn('alpha_run', caller['callers'])

    def test_repository_is_scanned_once_per_invocation(self):
        module = load_script_module(SCRIPT, 'context_pack')
        with patch.object(module.repository, 'iter_text_lines',
                          wraps=module.repository.iter_text_lines) as scanned:
            files = module.analyze_files(self.repo, ['src/alpha.py', 'src/server.go', 'docs/notes.txt'])
            self.assertEqual(3, len(files))
            self.assertEqual([call(self.repo)], scanned.call_args_list)
        with patch.object(module.repository, 'iter_text_lines',
                          wraps=module.repository.iter_text_lines) as scanned:
            module.analyze_files(self.repo, ['docs/notes.txt'])
            self.assertEqual(0, scanned.call_count)

    def test_outline_cap_marks_truncation(self):
        self.write('src/many.py', self.number_definitions(30))
        output = json.loads(self.pack('--format', 'json', 'src/many.py'))
        entry = output['files'][0]
        self.assertEqual(20, len(entry['outline']))
        self.assertIn('outline', entry['truncated'])

    def test_files_from_json(self):
        listing = Path(self.temporary.name) / 'files.json'
        listing.write_text(json.dumps(['src/app.ts', 'tools/run.sh']))
        output = json.loads(self.pack('--files-from', str(listing), '--format', 'json'))
        self.assertEqual(['src/app.ts', 'tools/run.sh'],
                         [entry['path'] for entry in output['files']])

    def test_usage_failure_exits_2(self):
        result = self.cli()
        self.assertEqual(2, result.returncode)
        self.assertIn('error', json.loads(result.stdout))

    def test_missing_file_exits_2(self):
        result = self.cli('src/absent.py')
        self.assertEqual(2, result.returncode)

    def test_total_output_is_bounded(self):
        for index in range(110):
            imports = ''.join(f'import dependency_{index}_{item}\n' for item in range(20))
            self.write(f'src/module_{index:03}.py', imports + '\n' + self.number_definitions(20, f'number_{index}'))
        modules = [f'src/module_{index:03}.py' for index in range(110)]
        output = self.pack(*modules)
        self.assertLessEqual(len(output.encode()), 65536 + 32)
        self.assertTrue(output.endswith('... truncated: output\n'))

    def test_json_output_bounds_omitted_paths_and_preserves_the_total(self):
        cases = (
            ('ascii', 'a' * 200),
            ('unicode', '\u00e9' * 100),
        )
        for label, stem in cases:
            with self.subTest(case=label):
                relative = f'docs/{stem}.txt'
                self.write(relative, 'content\n')
                listing = Path(self.temporary.name) / f'{label}.json'
                listing.write_text(json.dumps([relative] * 400))
                output = self.pack('--format', 'json', '--files-from', listing)
                self.assertLessEqual(len(output.encode()), 65536)
                payload = json.loads(output)
                self.assertGreater(payload['omitted_count'], 0)
                self.assertEqual(len(payload['files']) + payload['omitted_count'], 400)
                self.assertLess(len(payload['omitted_files']), payload['omitted_count'])
                self.assertTrue(all(path == relative for path in payload['omitted_files']))
                self.assertEqual(payload['repository'], str(self.repo.resolve()))

    def init_git_repo(self, directory):
        for arguments in (
            ('init',),
            ('config', 'user.name', 'Test User'),
            ('config', 'user.email', 'test@example.com'),
        ):
            subprocess.run(
                ['git', '-C', str(directory), *arguments],
                check=True, capture_output=True, stdin=subprocess.DEVNULL,
            )

    def test_git_worktree_skips_gitignore_and_preserves_excluded_dirs(self):
        self.write('.gitignore', 'ignored_dir/\n*.ignored.py\n')
        self.write('ignored_dir/secret.py', 'def secret():\n    return alpha_run()\n')
        self.write('src/skip.ignored.py', 'def skipped():\n    return alpha_run()\n')
        self.init_git_repo(self.repo)
        subprocess.run(
            ['git', '-C', str(self.repo), 'add', '.gitignore', 'src/alpha.py', 'vendor/ignored.py'],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        subprocess.run(
            ['git', '-C', str(self.repo), 'commit', '-m', 'initial'],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        self.write('src/untracked.py', 'def added():\n    return alpha_run()\n')

        repository = load_script_module(REPOSITORY_SCRIPT, 'repository_script')
        discovered = [
            os.path.relpath(path, self.repo)
            for path in repository.iter_files(str(self.repo))
        ]
        self.assertIn('src/alpha.py', discovered)
        self.assertIn('src/untracked.py', discovered)
        self.assertNotIn('ignored_dir/secret.py', discovered)
        self.assertNotIn('src/skip.ignored.py', discovered)
        self.assertNotIn('vendor/ignored.py', discovered)

        output = json.loads(self.pack('--format', 'json', 'src/alpha.py'))
        callers = output['files'][0]['callers']['alpha_run']
        self.assertIn('src/untracked.py:2', callers)
        self.assertNotIn('ignored_dir/secret.py:2', callers)
        self.assertNotIn('src/skip.ignored.py:2', callers)
        self.assertNotIn('vendor/ignored.py', json.dumps(output))

    def test_git_iter_files_preserves_top_down_order_and_linked_worktrees(self):
        work_root = Path(self.temporary.name) / 'ordered repo'
        work_root.mkdir()
        for relpath in (
            'z_root.txt',
            'a_root.txt',
            'build/dropped.txt',
            'b_dir/deep/leaf.txt',
            'a_dir/build',
            'a_dir/m.txt',
            'a_dir/a.txt',
        ):
            target = work_root / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('x\n')
        (work_root / '.gitignore').write_text('ignored.txt\n')
        (work_root / 'ignored.txt').write_text('x\n')

        self.init_git_repo(work_root)
        subprocess.run(
            ['git', '-C', str(work_root), 'add', '.gitignore', 'z_root.txt', 'a_root.txt',
             'build/dropped.txt', 'b_dir/deep/leaf.txt', 'a_dir/build', 'a_dir/m.txt', 'a_dir/a.txt'],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        subprocess.run(
            ['git', '-C', str(work_root), 'commit', '-m', 'seed'],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )

        repository = load_script_module(REPOSITORY_SCRIPT, 'repository_script')
        expected = [
            os.path.join(str(work_root), '.gitignore'),
            os.path.join(str(work_root), 'a_root.txt'),
            os.path.join(str(work_root), 'z_root.txt'),
            os.path.join(str(work_root), 'a_dir', 'a.txt'),
            os.path.join(str(work_root), 'a_dir', 'build'),
            os.path.join(str(work_root), 'a_dir', 'm.txt'),
            os.path.join(str(work_root), 'b_dir', 'deep', 'leaf.txt'),
        ]
        self.assertEqual(expected, list(repository.iter_files(str(work_root))))

        linked = Path(self.temporary.name) / 'linked worktree'
        subprocess.run(
            ['git', '-C', str(work_root), 'worktree', 'add', '--detach', str(linked)],
            check=True, capture_output=True, stdin=subprocess.DEVNULL,
        )
        (linked / 'ignored.txt').write_text('ignored in linked\n')
        linked_expected = [
            os.path.join(str(linked), os.path.relpath(path, work_root))
            for path in expected
        ]
        self.assertEqual(linked_expected, list(repository.iter_files(str(linked))))

    def test_iter_files_falls_back_to_os_walk_without_git_or_on_git_error(self):
        repository = load_script_module(REPOSITORY_SCRIPT, 'repository_script')
        with patch.object(repository.subprocess, 'run') as mock_run:
            non_git_files = list(repository.iter_files(str(self.repo)))
        mock_run.assert_not_called()
        self.assertIn(os.path.join(str(self.repo), 'src', 'alpha.py'), non_git_files)
        self.assertNotIn(os.path.join(str(self.repo), 'vendor', 'ignored.py'), non_git_files)

        self.write('.gitignore', 'ignored.txt\n')
        self.write('ignored.txt', 'x\n')
        self.init_git_repo(self.repo)
        expected_command = [
            'git', '-C', str(self.repo), 'ls-files', '-z',
            '--cached', '--others', '--exclude-standard',
        ]
        for error in (
            OSError('missing git'),
            subprocess.TimeoutExpired(['git'], 10),
        ):
            with self.subTest(error=type(error).__name__):
                with patch.object(repository.subprocess, 'run', side_effect=error) as mock_run:
                    fallback = list(repository.iter_files(str(self.repo)))
                mock_run.assert_called_once()
                self.assertEqual(expected_command, mock_run.call_args.args[0])
                self.assertEqual(subprocess.DEVNULL, mock_run.call_args.kwargs['stdin'])
                self.assertTrue(mock_run.call_args.kwargs['capture_output'])
                self.assertEqual(10, mock_run.call_args.kwargs['timeout'])
                self.assertEqual('0', mock_run.call_args.kwargs['env']['GIT_OPTIONAL_LOCKS'])
                self.assertIn(os.path.join(str(self.repo), 'ignored.txt'), fallback)
                self.assertNotIn(os.path.join(str(self.repo), 'vendor', 'ignored.py'), fallback)
        failed = subprocess.CompletedProcess(['git'], 128, b'', b'fatal')
        with patch.object(repository.subprocess, 'run', return_value=failed) as mock_run:
            fallback = list(repository.iter_files(str(self.repo)))
        mock_run.assert_called_once()
        self.assertEqual(expected_command, mock_run.call_args.args[0])
        self.assertIn(os.path.join(str(self.repo), 'ignored.txt'), fallback)
        self.assertNotIn(os.path.join(str(self.repo), 'vendor', 'ignored.py'), fallback)


if __name__ == '__main__':
    unittest.main()
