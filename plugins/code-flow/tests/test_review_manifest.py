import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/review-manifest.py'


def lines(count):
    return ''.join(f'line {number}\n' for number in range(count))


class ReviewManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.seed = Path(temporary.name) / 'seed'
        (cls.seed / 'src').mkdir(parents=True)
        (cls.seed / 'constraints').mkdir()
        (cls.seed / 'src/alpha.py').write_text('base\n')
        (cls.seed / 'src/beta.py').write_text('base\n')
        (cls.seed / 'constraints/limits.md').write_text('constraint\n')
        for arguments in (
            ('init', '-q'),
            ('config', 'user.name', 'Manifest Test'),
            ('config', 'user.email', 'manifest@example.test'),
            ('add', '.'),
            ('commit', '-qm', 'Initial fixture'),
        ):
            subprocess.run(
                ['git', '-C', str(cls.seed), *arguments],
                check=True, capture_output=True,
            )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / 'repository with spaces'
        shutil.copytree(self.seed, self.repo)
        self.write('src/alpha.py', lines(3))
        self.write('src/beta.py', lines(4))
        self.manifest_path = self.directory / 'round manifest.json'

    def git(self, *arguments):
        return subprocess.run(
            ['git', '-C', str(self.repo), *arguments],
            check=True, capture_output=True,
        ).stdout

    def write(self, relative, content):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def empty_tree(self):
        return subprocess.run(
            ['git', '-C', str(self.repo), 'hash-object', '-w', '-t', 'tree', '--stdin'],
            input=b'', capture_output=True, check=True,
        ).stdout.decode().strip()

    def make_unborn_repo(self, name='unborn'):
        repo = self.directory / name
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        self.repo = repo

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True, text=True, timeout=20,
            env=dict(os.environ, AGENTS_HOME=str(self.directory / 'metrics home')),
        )

    def group(self, identifier, paths, pack=None):
        entry = {
            'id': identifier,
            'paths': list(paths),
            'original_diff': ['git', 'diff', 'HEAD~1', 'HEAD', '--', *paths],
            'current_diff': ['git', 'diff', 'HEAD', '--', *paths],
        }
        if pack is not None:
            entry['context_pack'] = pack
        return entry

    def criterion(self, identifier, group='g1', evidence=()):
        return {
            'id': identifier,
            'text': f'criterion {identifier} text',
            'source': 'plan: acceptance',
            'group': group,
            'evidence': list(evidence),
        }

    def manifest(self, **overrides):
        identity = {
            'root': str(self.repo.resolve()),
            'git_dir': str((self.repo / '.git').resolve()),
        }
        manifest = {
            'version': 1,
            'repository': identity,
            'round_kind': 'initial-correctness',
            'original_scope': ['src/alpha.py', 'src/beta.py'],
            'round_scope': ['src/alpha.py', 'src/beta.py'],
            'added_paths': [],
            'excluded': [],
            'scoped_untracked': [],
            'constraints': ['constraints/limits.md'],
            'groups': [self.group('g1', ['src/alpha.py', 'src/beta.py'])],
            'acceptance': [self.criterion('A1'), self.criterion('A2')],
        }
        manifest.update(overrides)
        return manifest

    def validate(self, manifest, expected=0, repo=None):
        self.manifest_path.write_text(json.dumps(manifest))
        result = self.cli('validate', '--repo', repo or self.repo, '--manifest', self.manifest_path)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def init(self, *arguments, expected=0):
        result = self.cli('init', '--repo', self.repo, *arguments)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def criterion_input(self, identifier, **binding):
        entry = {
            'id': identifier,
            'text': f'criterion {identifier} text',
            'source': 'plan: acceptance',
        }
        entry.update(binding)
        return entry

    def test_valid_manifest_with_spaces_passes(self):
        self.write('docs/new file.md', lines(5))
        manifest = self.manifest(
            original_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            round_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            scoped_untracked=['docs/new file.md'],
            groups=[self.group('g1', ['src/alpha.py', 'src/beta.py']),
                    self.group('g2', ['docs/new file.md'])],
            acceptance=[self.criterion('A1'), self.criterion('A2', group='g2')],
        )
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_identity_mismatch_is_rejected(self):
        other = self.directory / 'other repo'
        other.mkdir()
        subprocess.run(['git', '-C', str(other), 'init', '-q'], check=True)
        result = self.validate(self.manifest(), expected=2, repo=other)
        self.assertIn('identity', result['error'])

    def test_identity_with_extra_head_field_is_rejected(self):
        manifest = self.manifest()
        manifest['repository']['head'] = 'abc'
        result = self.validate(manifest, expected=2)
        self.assertIn('identity', result['error'])

    def test_absolute_path_is_rejected(self):
        result = self.validate(self.manifest(round_scope=['src/alpha.py', 'src/beta.py', '/etc/passwd']), expected=2)
        self.assertIn('round_scope', result['error'])

    def test_escaping_path_is_rejected(self):
        result = self.validate(self.manifest(round_scope=['src/alpha.py', 'src/beta.py', '../outside.py']), expected=2)
        self.assertIn('round_scope', result['error'])

    def test_unassigned_scope_path_is_rejected(self):
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py'])])
        result = self.validate(manifest, expected=2)
        self.assertIn('src/beta.py', result['error'])

    def test_unexpected_assigned_path_is_rejected(self):
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py', 'src/beta.py', 'src/gamma.py'])])
        result = self.validate(manifest, expected=2)
        self.assertIn('src/gamma.py', result['error'])

    def test_duplicate_assignment_is_rejected(self):
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py', 'src/beta.py']),
                                       self.group('g2', ['src/alpha.py'])])
        result = self.validate(manifest, expected=2)
        self.assertIn('more than one group', result['error'])

    def test_unrelated_untracked_file_is_permitted(self):
        self.write('scratch notes.txt', 'unrelated\n')
        self.assertEqual(self.validate(self.manifest())['valid'], True)

    def test_scoped_untracked_requires_assignment(self):
        self.write('docs/new file.md', lines(5))
        manifest = self.manifest(
            original_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            round_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            scoped_untracked=['docs/new file.md'],
        )
        result = self.validate(manifest, expected=2)
        self.assertIn('docs/new file.md', result['error'])

    def test_scoped_untracked_outside_round_scope_is_rejected(self):
        self.write('docs/new file.md', lines(5))
        manifest = self.manifest(scoped_untracked=['docs/new file.md'])
        result = self.validate(manifest, expected=2)
        self.assertIn('untracked', result['error'])

    def test_initial_round_rejects_carried_only_criterion(self):
        manifest = self.manifest(acceptance=[self.criterion('A1'),
                                           self.criterion('A2', group=None, evidence=['round 1: met'])])
        result = self.validate(manifest, expected=2)
        self.assertIn('A2', result['error'])

    def test_targeted_round_allows_assigned_and_carried_criteria(self):
        manifest = self.manifest(
            round_kind='targeted-correctness',
            acceptance=[self.criterion('A1'),
                        self.criterion('A2', group=None, evidence=['round 1 reviewer g1: met by test-inspection'])],
        )
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_final_style_round_carries_forward_full_evidence(self):
        manifest = self.manifest(
            round_kind='final-style',
            acceptance=[self.criterion('A1', group=None, evidence=['round 1 reviewer g1: met']),
                        self.criterion('A2', group=None, evidence=['round 2 reviewer g2: met'])],
        )
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_final_style_round_rejects_assigned_criterion(self):
        manifest = self.manifest(round_kind='final-style')
        result = self.validate(manifest, expected=2)
        self.assertIn('final style', result['error'])

    def test_contradictory_evidence_is_rejected(self):
        manifest = self.manifest(round_kind='targeted-correctness',
                                 acceptance=[self.criterion('A1', evidence=['round 1: met'])])
        result = self.validate(manifest, expected=2)
        self.assertIn('contradictory', result['error'])

    def test_unowned_criterion_is_rejected(self):
        manifest = self.manifest(round_kind='targeted-correctness',
                                 acceptance=[self.criterion('A1', group=None)])
        result = self.validate(manifest, expected=2)
        self.assertIn('unowned', result['error'])

    def test_unknown_round_kind_is_rejected(self):
        result = self.validate(self.manifest(round_kind='post-exit'), expected=2)
        self.assertIn('round_kind', result['error'])

    def test_group_file_limit(self):
        paths = [f'src/file {number}.py' for number in range(13)]
        for path in paths:
            self.write(path, 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Add files')
        for path in paths:
            self.write(path, lines(2))
        manifest = self.manifest(
            original_scope=paths, round_scope=paths,
            groups=[self.group('g1', paths)],
            acceptance=[self.criterion('A1')],
        )
        result = self.validate(manifest, expected=2)
        self.assertIn('twelve', result['error'].replace('12', 'twelve'))

    def test_group_line_limit(self):
        self.write('src/alpha.py', lines(1200))
        self.write('src/beta.py', lines(400))
        result = self.validate(self.manifest(), expected=2)
        self.assertIn('1500', result['error'])

    def test_single_oversized_file_gets_its_own_group(self):
        self.write('src/alpha.py', lines(2000))
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py']),
                                       self.group('g2', ['src/beta.py'])])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_unsafe_or_wrong_scope_diff_commands_are_rejected_without_writes(self):
        sentinel = self.repo / 'sentinel.txt'
        sentinel.write_text('preserve me\n')
        paths = ['src/alpha.py', 'src/beta.py']
        commands = [
            ['git', 'diff', f'--output={sentinel}', 'HEAD', '--', *paths],
            ['git', 'diff', '--output', str(sentinel), 'HEAD', '--', *paths],
            *[['git', 'diff', option, 'HEAD', '--', *paths] for option in (
                '--quiet', '--stat', '--numstat', '--name-only', '--raw',
                '--no-patch', '--ext-diff', '--textconv', '--no-index',
                '--relative=src', '--color=always', '--ignore-all-space',
                '--diff-filter=A', '--exit-code', '--word-diff',
            )],
            ['git', 'diff', 'HEAD', '--', 'constraints/limits.md'],
            ['git', 'diff', 'HEAD', '--', 'src'],
            ['git', 'diff', 'HEAD', '--', 'src/*'],
            ['git', 'diff', 'HEAD', '--', ':(glob)src/*'],
            ['git', 'diff', 'HEAD', '--', paths[0]],
            ['git', 'diff', 'HEAD', '--', *paths, paths[0]],
            ['git', 'diff', 'HEAD:src/alpha.py', '--', *paths],
            ['git', 'diff', 'HEAD', 'HEAD', 'HEAD', '--', *paths],
            ['git', 'diff', 'HEAD', *paths],
        ]
        for key in ('original_diff', 'current_diff'):
            for command in commands:
                with self.subTest(key=key, command=command):
                    manifest = self.manifest()
                    manifest['groups'][0][key] = command
                    result = self.validate(manifest, expected=2)
                    self.assertIn(key, result['error'])
                    self.assertEqual(sentinel.read_text(), 'preserve me\n')

    def test_supported_diff_comparisons_enforce_line_limits(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Small baseline')
        self.git('branch', 'comparison')
        self.write('src/alpha.py', lines(1600))
        self.git('add', '.')
        self.git('commit', '-qm', 'Large comparison')
        paths = ['src/alpha.py', 'src/beta.py']
        for comparison in (
            ['HEAD~1', 'HEAD'], ['HEAD~1..HEAD'], ['comparison...HEAD'],
            ['--find-renames', 'comparison..HEAD'],
        ):
            with self.subTest(comparison=comparison):
                manifest = self.manifest()
                manifest['groups'][0]['current_diff'] = [
                    'git', 'diff', *comparison, '--', *paths,
                ]
                self.assertIn('1500', self.validate(manifest, expected=2)['error'])
        self.write('src/alpha.py', lines(3200))
        manifest = self.manifest()
        manifest['groups'][0]['current_diff'] = ['git', 'diff', '--', *paths]
        self.assertIn('1500', self.validate(manifest, expected=2)['error'])
        for comparison, expected in (
            ([], 0), (['HEAD'], 2), (['--cached'], 2), (['--staged', 'HEAD'], 2),
        ):
            with self.subTest(comparison=comparison):
                self.git('add', '.')
                manifest = self.manifest()
                manifest['groups'][0]['current_diff'] = [
                    'git', 'diff', *comparison, '--', *paths,
                ]
                self.validate(manifest, expected=expected)

    def test_current_revision_cannot_fall_back_to_a_worktree_path(self):
        manifest = self.manifest()
        manifest['groups'][0]['current_diff'] = [
            'git', 'diff', 'src/alpha.py', '--', *manifest['round_scope'],
        ]
        self.assertIn('rev-parse', self.validate(manifest, expected=2)['error'])

    def test_large_deletion_counts_and_single_file_exception(self):
        for path in ('src/alpha.py', 'src/deleted \t"é.txt'):
            with self.subTest(path=path):
                self.write(path, lines(1601))
                self.git('add', '.')
                self.git('commit', '-qm', 'Large deletion baseline')
                (self.repo / path).unlink()
                paths = [path, 'src/beta.py']
                manifest = self.manifest(
                    original_scope=paths, round_scope=paths,
                    groups=[self.group('g1', paths)],
                )
                self.assertIn('1601', self.validate(manifest, expected=2)['error'])
                manifest['groups'] = [self.group('g1', [path]),
                                      self.group('g2', ['src/beta.py'])]
                self.assertTrue(self.validate(manifest)['valid'])

    def test_staged_additions_on_an_unborn_branch(self):
        self.make_unborn_repo()
        self.write('src/alpha.py', lines(1601))
        self.write('src/beta.py', 'base\n')
        self.git('add', '.')
        manifest = self.manifest()
        for key in ('original_diff', 'current_diff'):
            manifest['groups'][0][key] = [
                'git', 'diff', '--cached', '--', *manifest['round_scope'],
            ]
        self.assertIn('1500', self.validate(manifest, expected=2)['error'])

    def test_root_commit_comparison_accepts_the_empty_tree_endpoint(self):
        root_repo = self.directory / 'root-commit'
        root_repo.mkdir()
        subprocess.run(['git', '-C', str(root_repo), 'init', '-q'], check=True)
        self.repo = root_repo
        self.git('config', 'user.name', 'Manifest Test')
        self.git('config', 'user.email', 'manifest@example.test')
        self.write('src/alpha.py', lines(1601))
        self.write('src/beta.py', 'base\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Root commit')
        empty_tree = self.empty_tree()
        manifest = self.manifest()
        for key in ('original_diff', 'current_diff'):
            manifest['groups'][0][key] = [
                'git', 'diff', empty_tree, 'HEAD', '--', *manifest['round_scope'],
            ]
        self.assertIn('1602', self.validate(manifest, expected=2)['error'])
        manifest['groups'][0]['current_diff'] = [
            'git', 'diff', f'{empty_tree}..HEAD', '--', *manifest['round_scope'],
        ]
        self.assertIn('1602', self.validate(manifest, expected=2)['error'])
        manifest['groups'] = [self.group('g1', ['src/alpha.py']),
                              self.group('g2', ['src/beta.py'])]
        for group in manifest['groups']:
            for key in ('original_diff', 'current_diff'):
                group[key] = ['git', 'diff', empty_tree, 'HEAD', '--', *group['paths']]
        self.assertTrue(self.validate(manifest)['valid'])
        packet = json.loads(self.cli(
            'render', '--manifest', self.manifest_path, '--group', 'g1',
        ).stdout)
        for key in ('original_diff', 'current_diff'):
            result = subprocess.run(
                packet[key], cwd=self.repo, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('+line 1600', result.stdout)

    def test_literal_group_path_cannot_expand_to_a_directory(self):
        manifest = self.manifest(
            original_scope=['src'], round_scope=['src'],
            groups=[self.group('g1', ['src'])],
        )
        self.assertIn('outside the group', self.validate(manifest, expected=2)['error'])

    def test_git_quoted_paths_count_at_the_exact_limit(self):
        for name in ('space name', 'tab\tname', 'quote"name', 'unicode-é', 'glob[1]'):
            with self.subTest(name=name):
                path = f'src/{name}.txt'
                self.write(path, 'base\n')
                self.git('add', '.')
                self.git('commit', '-qm', 'Add unusual filename')
                manifest = self.manifest(
                    original_scope=[path, 'src/beta.py'],
                    round_scope=[path, 'src/beta.py'],
                    groups=[self.group('g1', [path, 'src/beta.py'])],
                )
                self.write(path, lines(1499))
                self.assertTrue(self.validate(manifest)['valid'])
                self.write(path, lines(1500))
                self.assertIn('1501', self.validate(manifest, expected=2)['error'])
                (self.repo / path).unlink()
                self.assertTrue(self.validate(manifest)['valid'])

    def test_rename_counts_edits_once_under_the_new_path(self):
        old, new = 'src/old name.txt', 'src/new\t"name.txt'
        self.write(old, lines(2000))
        self.git('add', '.')
        self.git('commit', '-qm', 'Rename baseline')
        self.git('mv', old, new)
        self.write(new, lines(2000) + lines(1501))
        manifest = self.manifest(
            original_scope=[old, new], round_scope=[old, new],
            groups=[self.group('g1', [old, new])],
        )
        self.assertIn('1501', self.validate(manifest, expected=2)['error'])
        self.write(new, lines(2000) + lines(1500))
        self.assertTrue(self.validate(manifest)['valid'])

    def test_diff_configuration_cannot_hide_changes_or_execute_helpers(self):
        helper = self.directory / 'helper.py'
        marker = self.directory / 'helper-ran'
        helper.write_text(f'import pathlib\npathlib.Path({str(marker)!r}).touch()\n')
        command = f'"{sys.executable}" "{helper}"'
        self.git('config', 'diff.external', command)
        self.git('config', 'diff.hostile.textconv', command)
        self.git('config', 'diff.relative', 'true')
        self.git('config', 'color.ui', 'always')
        self.git('config', 'core.pager', command)
        self.git('config', 'diff.algorithm', 'histogram')
        self.write('.gitattributes', '*.py diff=hostile\n')
        self.write('src/alpha.py', lines(1600))
        self.assertIn('1500', self.validate(self.manifest(), expected=2)['error'])
        self.assertFalse(marker.exists())

    def test_shorthand_and_unicode_revision_comparisons(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'Comparison baseline')
        self.git('branch', 'référence')
        self.git('checkout', '-qb', 'working')
        self.git('checkout', '-q', 'référence')
        self.write('src/alpha.py', lines(1601))
        self.git('add', '.')
        self.git('commit', '-qm', 'Large comparison')
        for comparison in (
            ['@~1', '@'], ['@{-1}', '@'], ['working..référence'],
            ['working...référence'], ['référence~1', 'référence'],
        ):
            with self.subTest(comparison=comparison):
                manifest = self.manifest()
                for key in ('original_diff', 'current_diff'):
                    manifest['groups'][0][key] = [
                        'git', 'diff', *comparison, '--', *manifest['round_scope'],
                    ]
                self.assertIn('1500', self.validate(manifest, expected=2)['error'])

    def test_separator_filename_is_a_literal_assigned_path(self):
        self.write('--', 'baseline\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Separator filename baseline')
        self.write('--', lines(1601))
        manifest = self.manifest(
            original_scope=['--', 'src/beta.py'], round_scope=['--', 'src/beta.py'],
            groups=[self.group('g1', ['--', 'src/beta.py'])],
        )
        self.assertIn('1500', self.validate(manifest, expected=2)['error'])
        manifest['groups'] = [self.group('g1', ['--']), self.group('g2', ['src/beta.py'])]
        self.assertTrue(self.validate(manifest)['valid'])
        packet = json.loads(self.cli(
            'render', '--manifest', self.manifest_path, '--group', 'g1',
        ).stdout)
        result = subprocess.run(
            packet['current_diff'], cwd=self.repo, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('+line 1600', result.stdout)

    def test_rendered_comparisons_are_safe_literal_patch_commands(self):
        path = 'src/glob[1].txt'
        sibling = 'src/glob1.txt'
        self.write(path, 'baseline literal\n')
        self.write(sibling, 'baseline sibling\n')
        self.write('.gitattributes', '*.txt diff=hostile\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Literal paths baseline')
        self.write(path, 'historical literal\n')
        self.write(sibling, 'historical sibling\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Historical comparison')
        self.write(path, 'current literal\n')
        self.write(sibling, 'current sibling\n')
        helper = self.directory / 'render-helper.py'
        marker = self.directory / 'render-helper-ran'
        helper.write_text(f'import pathlib\npathlib.Path({str(marker)!r}).touch()\n')
        command = f'"{sys.executable}" "{helper}"'
        self.git('config', 'diff.external', command)
        self.git('config', 'diff.hostile.textconv', command)
        self.git('config', 'core.pager', command)
        self.git('config', 'color.ui', 'always')
        self.git('config', 'diff.relative', 'true')
        manifest = self.manifest(
            original_scope=[path], round_scope=[path],
            groups=[self.group('g1', [path])],
        )
        self.validate(manifest)
        packet = json.loads(self.cli(
            'render', '--manifest', self.manifest_path, '--group', 'g1',
        ).stdout)
        for key, expected, absent in (
            ('original_diff', '+historical literal', '+current literal'),
            ('current_diff', '+current literal', '+historical literal'),
        ):
            with self.subTest(key=key):
                result = subprocess.run(
                    packet[key], cwd=self.repo, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertNotIn(absent, result.stdout)
                self.assertNotIn('sibling', result.stdout)
                self.assertNotIn('\x1b[', result.stdout)
                self.assertFalse(marker.exists())

    def test_scoped_untracked_counts_full_lines(self):
        self.write('docs/big.md', lines(1600))
        manifest = self.manifest(
            original_scope=['src/alpha.py', 'src/beta.py', 'docs/big.md'],
            round_scope=['src/alpha.py', 'src/beta.py', 'docs/big.md'],
            scoped_untracked=['docs/big.md'],
            groups=[self.group('g1', ['src/alpha.py', 'src/beta.py', 'docs/big.md'])],
        )
        result = self.validate(manifest, expected=2)
        self.assertIn('1500', result['error'])
        manifest['groups'] = [self.group('g1', ['src/alpha.py', 'src/beta.py']),
                              self.group('g2', ['docs/big.md'])]
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_added_path_with_reason(self):
        self.write('src/gamma.py', lines(2))
        manifest = self.manifest(
            round_scope=['src/alpha.py', 'src/beta.py', 'src/gamma.py'],
            added_paths=[{'path': 'src/gamma.py', 'reason': 'fix required another path'}],
            groups=[self.group('g1', ['src/alpha.py', 'src/beta.py', 'src/gamma.py'])],
        )
        self.assertEqual(self.validate(manifest)['valid'], True)
        manifest['added_paths'] = [{'path': 'src/gamma.py', 'reason': ''}]
        result = self.validate(manifest, expected=2)
        self.assertIn('reason', result['error'])

    def test_every_hunk_counts_toward_the_line_limit(self):
        self.write('src/alpha.py', lines(1600))
        self.git('add', '.')
        self.git('commit', '-qm', 'large fixture')
        path = self.repo / 'src/alpha.py'
        modified = [f'line {number}' for number in range(1600)]
        for number in range(100, 500):
            modified[number] = f'changed {number}'
        for number in range(1000, 1400):
            modified[number] = f'changed {number}'
        path.write_text('\n'.join(modified) + '\n')
        result = self.validate(self.manifest(), expected=2)
        self.assertIn('1500', result['error'])

    def test_render_is_deterministic_and_complete(self):
        self.write('docs/new file.md', lines(5))
        manifest = self.manifest(
            round_kind='targeted-correctness',
            original_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            round_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            scoped_untracked=['docs/new file.md'],
            groups=[self.group('g1', ['src/alpha.py', 'src/beta.py']),
                    self.group('g2', ['docs/new file.md'])],
            acceptance=[self.criterion('A1'),
                        self.criterion('A2', group=None, evidence=['round 1 reviewer g1: met'])],
        )
        self.validate(manifest)
        first = self.cli('render', '--manifest', self.manifest_path, '--group', 'g1')
        second = self.cli('render', '--manifest', self.manifest_path, '--group', 'g1')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        packet = json.loads(first.stdout)
        self.assertEqual(set(packet), {
            'group_id', 'manifest_path', 'round_kind', 'assigned_paths',
            'original_diff', 'current_diff', 'scoped_untracked', 'constraints',
            'criteria', 'carried_evidence',
        })
        self.assertEqual(packet['group_id'], 'g1')
        self.assertEqual(packet['manifest_path'], str(self.manifest_path))
        self.assertEqual(packet['assigned_paths'], ['src/alpha.py', 'src/beta.py'])
        self.assertEqual(packet['current_diff'], [
            'git', '--no-pager', '--literal-pathspecs', 'diff',
            '--no-ext-diff', '--no-textconv', '--no-color', '--no-relative',
            '--find-renames=50%', '--diff-algorithm=myers', '--indent-heuristic',
            '--ignore-submodules=none', '-l0', 'HEAD', '--', 'src/alpha.py', 'src/beta.py',
        ])
        self.assertEqual(packet['scoped_untracked'], [])
        self.assertEqual(packet['constraints'], ['constraints/limits.md'])
        self.assertEqual([criterion['id'] for criterion in packet['criteria']], ['A1'])
        self.assertEqual([criterion['id'] for criterion in packet['carried_evidence']], ['A2'])
        untracked = self.cli('render', '--manifest', self.manifest_path, '--group', 'g2')
        self.assertEqual(json.loads(untracked.stdout)['scoped_untracked'], ['docs/new file.md'])

    def test_context_pack_validates_and_renders(self):
        pack = str((self.directory / 'g1 pack.txt').resolve())
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py', 'src/beta.py'], pack=pack)])
        self.assertEqual(self.validate(manifest)['valid'], True)
        result = self.cli('render', '--manifest', self.manifest_path, '--group', 'g1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['context_pack'], pack)

    def test_relative_context_pack_is_rejected(self):
        manifest = self.manifest(groups=[self.group('g1', ['src/alpha.py', 'src/beta.py'], pack='packs/g1.txt')])
        result = self.validate(manifest, expected=2)
        self.assertIn('context_pack', result['error'])

    def test_render_without_context_pack_omits_the_field(self):
        self.validate(self.manifest())
        result = self.cli('render', '--manifest', self.manifest_path, '--group', 'g1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('context_pack', json.loads(result.stdout))

    def test_render_unknown_group_is_rejected(self):
        self.validate(self.manifest())
        result = self.cli('render', '--manifest', self.manifest_path, '--group', 'nope')
        self.assertEqual(result.returncode, 2)
        self.assertIn('unknown group', json.loads(result.stdout)['error'])

    def test_init_output_validates_for_every_round_kind(self):
        for kind, criterion in (
            ('initial-correctness', self.criterion_input('A1', group='g1')),
            ('targeted-correctness', self.criterion_input('A1', paths=['src/alpha.py'])),
            ('post-exit-correctness', self.criterion_input('A1', paths=['src/beta.py'])),
            ('final-style', self.criterion_input('A1', evidence=['round 1 reviewer g1: met'])),
        ):
            with self.subTest(kind=kind):
                manifest = self.init(
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', kind,
                    '--group', 'g1=src/alpha.py,src/beta.py',
                    '--constraints', json.dumps(['constraints/limits.md']),
                    '--acceptance', json.dumps([criterion]),
                )
                self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_synthesizes_baseline_diffs_per_group(self):
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py',
            '--group', 'g2=src/beta.py',
        )
        self.assertEqual({group['id']: group['paths'] for group in manifest['groups']},
                         {'g1': ['src/alpha.py'], 'g2': ['src/beta.py']})
        for group in manifest['groups']:
            for key in ('original_diff', 'current_diff'):
                self.assertEqual(group[key], ['git', 'diff', 'HEAD', '--', *group['paths']])
        self.assertEqual(self.validate(manifest)['valid'], True)
        self.git('add', '.')
        self.git('commit', '-qm', 'Baseline point')
        self.write('src/alpha.py', lines(6))
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py',
            '--baseline', 'HEAD~1',
        )
        for group in manifest['groups']:
            for key in ('original_diff', 'current_diff'):
                self.assertEqual(group[key], ['git', 'diff', 'HEAD~1', '--', *group['paths']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_carries_added_paths_into_the_round_scope(self):
        self.write('src/gamma.py', lines(2))
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--added', json.dumps({'src/gamma.py': 'fix required another path'}),
            '--group', 'g1=src/alpha.py,src/beta.py,src/gamma.py',
        )
        self.assertEqual(manifest['added_paths'],
                         [{'path': 'src/gamma.py', 'reason': 'fix required another path'}])
        self.assertEqual(manifest['round_scope'],
                         ['src/alpha.py', 'src/beta.py', 'src/gamma.py'])
        self.assertIn('src/gamma.py', manifest['scoped_untracked'])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_refuses_an_added_path_already_in_scope(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            '--added', json.dumps({'src/alpha.py': 'fix required another path'}),
            expected=2,
        )
        self.assertIn('already in original scope', result['error'])

    def test_init_derives_scoped_untracked_from_git(self):
        self.write('.gitignore', 'ignored/\n')
        self.write('ignored/note.md', lines(10))
        self.write('docs/new file.md', lines(5))
        manifest = self.init(
            '--scope-from', json.dumps(
                ['src/alpha.py', 'src/beta.py', 'docs/new file.md', 'ignored/note.md']),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
        )
        self.assertEqual(manifest['scoped_untracked'], ['docs/new file.md'])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_accepts_an_untracked_directory_symlink_as_a_zero_line_scope_path(self):
        os.symlink('src', self.repo / 'skills-link')
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'skills-link']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py,skills-link',
        )
        self.assertEqual(manifest['scoped_untracked'], ['skills-link'])
        self.assertEqual(manifest['round_scope'], ['skills-link', 'src/alpha.py', 'src/beta.py'])
        self.assertEqual(manifest['groups'][0]['paths'],
                         ['src/alpha.py', 'src/beta.py', 'skills-link'])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_refuses_a_scope_path_present_nowhere_in_the_repository(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/typo.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/typo.py',
            expected=2,
        )
        self.assertIn('does not exist', result['error'])
        self.assertIn('src/typo.py', result['error'])

    def test_init_allows_a_scope_path_tracked_at_head_but_deleted_in_the_worktree(self):
        self.git('rm', '-qf', 'src/beta.py')
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            '--group', 'g2=src/beta.py',
        )
        self.assertEqual([group['paths'] for group in manifest['groups']],
                         [['src/alpha.py'], ['src/beta.py']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_on_an_unborn_branch_resolves_scope_paths_from_the_index(self):
        self.make_unborn_repo()
        self.write('src/alpha.py', lines(3))
        self.write('src/beta.py', lines(4))
        self.git('add', '.')
        empty_tree = self.empty_tree()
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            '--group', 'g2=src/beta.py',
            '--baseline', empty_tree,
        )
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_auto_groups_pair_tests_and_group_unpaired_by_directory(self):
        self.write('src/parser.py', lines(30))
        self.write('src/service.py', lines(40))
        self.write('src/service_test.py', lines(12))
        self.write('tests/test_parser.py', lines(10))
        self.write('docs/guide.md', lines(5))
        scope = ['docs/guide.md', 'src/alpha.py', 'src/beta.py', 'src/parser.py',
                 'src/service.py', 'src/service_test.py', 'tests/test_parser.py']
        manifest = self.init(
            '--scope-from', json.dumps(scope),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
            '--acceptance', json.dumps([
                self.criterion_input('A1', paths=['src/alpha.py', 'src/beta.py']),
                self.criterion_input('A2', paths=['tests/test_parser.py']),
            ]),
        )
        assigned = [path for group in manifest['groups'] for path in group['paths']]
        self.assertEqual(sorted(assigned), sorted(scope))
        self.assertEqual(len(assigned), len(set(assigned)))
        owners = {path: group['id'] for group in manifest['groups'] for path in group['paths']}
        self.assertEqual(owners['src/parser.py'], owners['tests/test_parser.py'])
        self.assertEqual(owners['src/service.py'], owners['src/service_test.py'])
        self.assertEqual(owners['src/alpha.py'], owners['src/beta.py'])
        self.assertNotEqual(owners['src/alpha.py'], owners['docs/guide.md'])
        self.assertTrue(all(len(group['paths']) <= 12 for group in manifest['groups']))
        self.assertEqual(manifest['scoped_untracked'], sorted(
            path for path in scope if path not in ('src/alpha.py', 'src/beta.py')))
        self.assertEqual(
            [criterion['group'] for criterion in manifest['acceptance']],
            [owners['src/alpha.py'], owners['tests/test_parser.py']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_auto_groups_respect_line_limits(self):
        self.write('src/big.py', lines(1000))
        self.write('src/huge.py', lines(2000))
        self.write('src/mid.py', lines(400))
        self.write('src/small.py', lines(200))
        manifest = self.init(
            '--scope-from', json.dumps(
                ['src/big.py', 'src/huge.py', 'src/mid.py', 'src/small.py']),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
            '--acceptance', json.dumps([self.criterion_input('A1', paths=['src/small.py'])]),
        )
        self.assertEqual(sorted(sorted(group['paths']) for group in manifest['groups']), [
            ['src/big.py'], ['src/huge.py'], ['src/mid.py', 'src/small.py']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_auto_groups_respect_the_file_limit(self):
        paths = [f'src/module {number}.py' for number in range(13)]
        for path in paths:
            self.write(path, 'content\n')
        manifest = self.init(
            '--scope-from', json.dumps(paths),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
            '--acceptance', json.dumps([self.criterion_input('A1', paths=['src/module 0.py'])]),
        )
        self.assertEqual(sorted(len(group['paths']) for group in manifest['groups']), [1, 12])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_refuses_a_path_assigned_to_two_groups(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            '--group', 'g2=src/alpha.py,src/beta.py',
            expected=2,
        )
        self.assertIn('more than one group', result['error'])

    def test_init_accepts_spaces_around_group_path_parts(self):
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py, src/beta.py',
        )
        self.assertEqual([group['paths'] for group in manifest['groups']],
                         [['src/alpha.py', 'src/beta.py']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_refuses_an_empty_group_path_part(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,',
            expected=2,
        )
        self.assertIn('--group', result['error'])
        self.assertIn('g1=src/alpha.py,', result['error'])

    def test_init_refuses_explicit_groups_over_the_line_limit(self):
        self.write('src/alpha.py', lines(1200))
        self.write('src/beta.py', lines(400))
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py',
            expected=2,
        )
        self.assertIn('1500', result['error'])

    def test_init_refuses_acceptance_round_kind_mismatches(self):
        carried = self.criterion_input('A1', evidence=['round 1 reviewer g1: met'])
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py',
            '--acceptance', json.dumps([carried]),
            expected=2,
        )
        self.assertIn('initial correctness', result['error'])
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'final-style',
            '--group', 'g1=src/alpha.py,src/beta.py',
            '--acceptance', json.dumps([self.criterion_input('A1', group='g1')]),
            expected=2,
        )
        self.assertIn('final style', result['error'])

    def test_init_refuses_a_criterion_naming_an_unknown_group(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py',
            '--acceptance', json.dumps([self.criterion_input('A1', group='g2')]),
            expected=2,
        )
        self.assertIn('unknown owning group', result['error'])

    def test_init_refuses_criterion_paths_spanning_groups(self):
        self.write('docs/new file.md', lines(5))
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'docs/new file.md']),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
            '--acceptance', json.dumps([
                self.criterion_input('A1', paths=['src/alpha.py', 'docs/new file.md'])]),
            expected=2,
        )
        self.assertIn('more than one group', result['error'])

    def test_init_exclude_keeps_the_lockfile_in_original_scope_only(self):
        self.write('composer.lock', lines(2))
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'composer.lock']),
            '--round-kind', 'initial-correctness',
            '--exclude', json.dumps(['composer.lock']),
            '--group', 'g1=src/alpha.py,src/beta.py',
        )
        self.assertEqual(manifest['original_scope'],
                         ['composer.lock', 'src/alpha.py', 'src/beta.py'])
        self.assertEqual(manifest['excluded'], ['composer.lock'])
        self.assertEqual(manifest['round_scope'], ['src/alpha.py', 'src/beta.py'])
        self.assertEqual([group['paths'] for group in manifest['groups']],
                         [['src/alpha.py', 'src/beta.py']])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_validate_rejects_an_excluded_path_in_the_round_scope(self):
        result = self.validate(self.manifest(excluded=['src/alpha.py']), expected=2)
        self.assertIn('round scope', result['error'])

    def test_validate_rejects_an_excluded_scoped_untracked_path(self):
        self.write('docs/new file.md', lines(5))
        manifest = self.manifest(
            original_scope=['src/alpha.py', 'src/beta.py', 'docs/new file.md'],
            excluded=['docs/new file.md'],
            scoped_untracked=['docs/new file.md'],
        )
        result = self.validate(manifest, expected=2)
        self.assertIn('scoped untracked', result['error'])

    def test_validate_rejects_an_excluded_path_outside_the_original_scope(self):
        result = self.validate(self.manifest(excluded=['src/typo.py']), expected=2)
        self.assertIn('original scope', result['error'])

    def test_init_exclude_drops_an_untracked_generated_path(self):
        self.write('pnpm-lock.yaml', lines(2))
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'pnpm-lock.yaml']),
            '--round-kind', 'initial-correctness',
            '--exclude', json.dumps(['pnpm-lock.yaml']),
            '--auto-groups',
        )
        self.assertEqual(manifest['excluded'], ['pnpm-lock.yaml'])
        self.assertEqual(manifest['scoped_untracked'], [])
        self.assertEqual(manifest['round_scope'], ['src/alpha.py', 'src/beta.py'])
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_refuses_an_excluded_path_outside_the_scope(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--exclude', json.dumps(['composer.lock']),
            '--group', 'g1=src/alpha.py,src/beta.py',
            expected=2,
        )
        self.assertIn('--scope-from', result['error'])

    def test_init_refuses_an_excluded_added_path(self):
        self.write('src/gamma.py', lines(2))
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--added', json.dumps({'src/gamma.py': 'fix required another path'}),
            '--exclude', json.dumps(['src/gamma.py']),
            '--group', 'g1=src/alpha.py,src/beta.py',
            expected=2,
        )
        self.assertIn('added', result['error'])

    def test_init_refuses_exclusions_that_empty_the_scope(self):
        self.write('composer.lock', lines(2))
        result = self.init(
            '--scope-from', json.dumps(['composer.lock']),
            '--round-kind', 'initial-correctness',
            '--exclude', json.dumps(['composer.lock']),
            '--auto-groups',
            expected=2,
        )
        self.assertIn('empty the scope', result['error'])

    def test_init_distinguishes_an_empty_scope_from_exclusions_emptying_it(self):
        result = self.init(
            '--scope-from', json.dumps([]),
            '--round-kind', 'initial-correctness',
            '--auto-groups',
            expected=2,
        )
        self.assertIn('empty scope', result['error'])
        self.assertNotIn('exclusions', result['error'])
        self.write('composer.lock', lines(2))
        result = self.init(
            '--scope-from', json.dumps(['composer.lock']),
            '--round-kind', 'initial-correctness',
            '--exclude', json.dumps(['composer.lock']),
            '--auto-groups',
            expected=2,
        )
        self.assertIn('exclusions empty the scope', result['error'])

    def test_init_json_flags_accept_file_inputs(self):
        self.write('src/gamma.py', lines(2))
        scope = self.directory / 'scope input.json'
        added = self.directory / 'added input.json'
        constraints = self.directory / 'constraints input.json'
        acceptance = self.directory / 'acceptance input.json'
        scope_json = json.dumps(['src/alpha.py', 'src/beta.py'])
        added_json = json.dumps({'src/gamma.py': 'fix required another path'})
        constraints_json = json.dumps(['constraints/limits.md'])
        acceptance_json = json.dumps([self.criterion_input('A1', group='g1')])
        scope.write_text(scope_json + '\n')
        added.write_text(added_json + '\n')
        constraints.write_text(constraints_json + '\n')
        acceptance.write_text(acceptance_json + '\n')
        inline = self.init(
            '--scope-from', scope_json,
            '--added', added_json,
            '--constraints', constraints_json,
            '--acceptance', acceptance_json,
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py,src/gamma.py',
        )
        from_files = self.init(
            '--scope-from', f'@{scope}',
            '--added', f'@{added}',
            '--constraints', f'@{constraints}',
            '--acceptance', f'@{acceptance}',
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py,src/beta.py,src/gamma.py',
        )
        self.assertEqual({key: value for key, value in from_files.items() if key != 'run'},
                         {key: value for key, value in inline.items() if key != 'run'})

    def test_init_file_inputs_share_the_inline_validation(self):
        scope = self.directory / 'scope input.json'
        scope.write_text(json.dumps(['src/../alpha.py']))
        from_file = self.init(
            '--scope-from', f'@{scope}',
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        inline = self.init(
            '--scope-from', json.dumps(['src/../alpha.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        self.assertEqual(from_file['error'], inline['error'])

    def test_init_rejects_an_unreadable_file_input(self):
        missing = self.directory / 'missing input.json'
        result = self.init(
            '--scope-from', f'@{missing}',
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        self.assertIn('--scope-from', result['error'])
        self.assertIn(str(missing), result['error'])

    def test_init_rejects_a_non_utf8_file_input(self):
        binary = self.directory / 'added binary.json'
        binary.write_bytes(b'\xff\xfe[')
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py']),
            '--added', f'@{binary}',
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        self.assertIn('--added', result['error'])
        self.assertIn(str(binary), result['error'])

    def test_init_rejects_invalid_json_in_a_file_input(self):
        broken = self.directory / 'broken input.json'
        broken.write_text('not json')
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py']),
            '--added', f'@{broken}',
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        self.assertIn('--added', result['error'])
        self.assertIn(str(broken), result['error'])

    def test_init_help_documents_the_acceptance_contract(self):
        result = self.cli('init', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('id, text, source, group, evidence', result.stdout)
        self.assertIn('an owning group or carries evidence', result.stdout)
        self.assertIn('assigns every criterion', result.stdout)
        self.assertIn('assigns none', result.stdout)

    def test_init_followup_narrows_round_scope_from_group_union(self):
        self.write('docs/extra.md', lines(3))
        self.write('src/gamma.py', lines(2))
        for kind in ('targeted-correctness', 'post-exit-correctness', 'final-style'):
            with self.subTest(kind=kind):
                manifest = self.init(
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'docs/extra.md']),
                    '--added', json.dumps({'src/gamma.py': 'fix required another path'}),
                    '--round-kind', kind,
                    '--group', 'g1=src/alpha.py,src/gamma.py',
                )
                self.assertEqual(
                    manifest['original_scope'],
                    ['docs/extra.md', 'src/alpha.py', 'src/beta.py'],
                )
                self.assertEqual(manifest['round_scope'], ['src/alpha.py', 'src/gamma.py'])
                self.assertEqual(manifest['scoped_untracked'], ['src/gamma.py'])
                self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_initial_round_refuses_implicit_group_narrowing(self):
        result = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'initial-correctness',
            '--group', 'g1=src/alpha.py',
            expected=2,
        )
        self.assertIn('src/beta.py', result['error'])

    def test_init_followup_preserves_group_validation_errors(self):
        self.write('composer.lock', lines(2))
        out_of_scope = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py,src/typo.py',
            expected=2,
        )
        self.assertIn('assigned paths outside the round scope', out_of_scope['error'])
        self.assertIn('src/typo.py', out_of_scope['error'])
        excluded_group = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'composer.lock']),
            '--exclude', json.dumps(['composer.lock']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py,composer.lock',
            expected=2,
        )
        self.assertIn('assigned paths outside the round scope', excluded_group['error'])
        self.assertIn('composer.lock', excluded_group['error'])
        duplicate = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py',
            '--group', 'g2=src/alpha.py',
            expected=2,
        )
        self.assertIn('more than one group', duplicate['error'])

    def test_init_round_scope_narrows_auto_groups_and_supports_file_input(self):
        self.write('docs/extra.md', lines(3))
        round_scope_file = self.directory / 'round scope.json'
        round_scope_json = json.dumps(['src/alpha.py'])
        round_scope_file.write_text(round_scope_json + '\n')
        inline = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'docs/extra.md']),
            '--round-kind', 'targeted-correctness',
            '--round-scope', round_scope_json,
            '--auto-groups',
        )
        from_file = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'docs/extra.md']),
            '--round-kind', 'targeted-correctness',
            '--round-scope', f'@{round_scope_file}',
            '--auto-groups',
        )
        self.assertEqual({key: value for key, value in from_file.items() if key != 'run'},
                         {key: value for key, value in inline.items() if key != 'run'})
        self.assertEqual(
            inline['original_scope'],
            ['docs/extra.md', 'src/alpha.py', 'src/beta.py'],
        )
        self.assertEqual(inline['round_scope'], ['src/alpha.py'])
        self.assertEqual(inline['scoped_untracked'], [])
        self.assertEqual([group['paths'] for group in inline['groups']], [['src/alpha.py']])
        self.assertEqual(self.validate(inline)['valid'], True)

    def test_init_round_scope_validation_errors(self):
        self.write('composer.lock', lines(2))
        cases = (
            (
                'initial round rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'initial-correctness',
                    '--round-scope', json.dumps(['src/alpha.py']),
                    '--auto-groups',
                ],
                'initial correctness round cannot narrow --round-scope',
            ),
            (
                'empty round scope rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'targeted-correctness',
                    '--round-scope', json.dumps([]),
                    '--auto-groups',
                ],
                'empty round scope',
            ),
            (
                'excluded path in round scope rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py', 'composer.lock']),
                    '--exclude', json.dumps(['composer.lock']),
                    '--round-kind', 'targeted-correctness',
                    '--round-scope', json.dumps(['src/alpha.py', 'composer.lock']),
                    '--auto-groups',
                ],
                'excluded path inside --round-scope',
            ),
            (
                'out of scope path rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'targeted-correctness',
                    '--round-scope', json.dumps(['src/alpha.py', 'src/typo.py']),
                    '--auto-groups',
                ],
                'outside eligible scope',
            ),
            (
                'invalid path rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'targeted-correctness',
                    '--round-scope', json.dumps(['../outside.py']),
                    '--auto-groups',
                ],
                'invalid --round-scope',
            ),
            (
                'explicit group missing round-scope path rejected',
                [
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'targeted-correctness',
                    '--round-scope', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--group', 'g1=src/alpha.py',
                ],
                'scoped paths without a group assignment',
            ),
        )
        for name, arguments, expected_error in cases:
            with self.subTest(name=name):
                result = self.init(*arguments, expected=2)
                self.assertIn(expected_error, result['error'])

    def test_init_accepts_stored_manifest_criterion_objects(self):
        stored = [
            self.criterion('A1', group='g1', evidence=[]),
            self.criterion('A2', group=None, evidence=['round 1 reviewer g1: met']),
            {
                'id': 'A3',
                'text': 'criterion A3 text',
                'source': 'plan: acceptance',
                'group': None,
                'paths': ['src/alpha.py'],
                'evidence': [],
            },
        ]
        manifest = self.init(
            '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
            '--round-kind', 'targeted-correctness',
            '--group', 'g1=src/alpha.py',
            '--acceptance', json.dumps(stored),
        )
        self.assertEqual(
            [(entry['id'], entry['group'], entry['evidence']) for entry in manifest['acceptance']],
            [
                ('A1', 'g1', []),
                ('A2', None, ['round 1 reviewer g1: met']),
                ('A3', 'g1', []),
            ],
        )
        self.assertEqual(self.validate(manifest)['valid'], True)

    def test_init_rejects_criteria_with_zero_or_multiple_active_bindings(self):
        cases = (
            ('no binding', self.criterion_input('A1')),
            ('group and evidence', self.criterion('A1', group='g1', evidence=['round 1: met'])),
            ('group and paths', self.criterion_input('A1', group='g1', paths=['src/alpha.py'])),
            (
                'paths and evidence',
                self.criterion_input('A1', paths=['src/alpha.py'], evidence=['round 1: met']),
            ),
        )
        for name, entry in cases:
            with self.subTest(name=name):
                result = self.init(
                    '--scope-from', json.dumps(['src/alpha.py', 'src/beta.py']),
                    '--round-kind', 'targeted-correctness',
                    '--group', 'g1=src/alpha.py',
                    '--acceptance', json.dumps([entry]),
                    expected=2,
                )
                self.assertIn('expected one of group, paths, or evidence', result['error'])


if __name__ == '__main__':
    unittest.main()
