import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/review-snapshot.py'


class ReviewSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.seed = Path(temporary.name) / 'seed'
        cls.seed.mkdir()
        (cls.seed / 'tracked file.txt').write_bytes(b'original\n')
        (cls.seed / '.gitignore').write_text('ignored/\n')
        for arguments in (
            ('init', '-q'),
            ('config', 'user.name', 'Snapshot Test'),
            ('config', 'user.email', 'snapshot@example.test'),
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
        self.tracked = self.repo / 'tracked file.txt'

    def git(self, *arguments, input=None):
        return subprocess.run(
            ['git', '-C', str(self.repo), *arguments],
            check=True, capture_output=True, input=input,
        ).stdout

    def cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True, text=True, timeout=20,
            env=dict(os.environ, AGENTS_HOME=str(self.directory / 'metrics home')),
        )

    def capture(self, scope=None):
        arguments = ['--repo', self.repo]
        if scope is not None:
            scope_path = self.directory / 'scope.json'
            scope_path.write_text(json.dumps(scope))
            arguments += ['--scope-from', scope_path]
        result = self.cli(*arguments)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def compare(self, before, after, expected_code=1):
        first = self.directory / 'before snapshot.json'
        second = self.directory / 'after snapshot.json'
        first.write_text(json.dumps(before))
        second.write_text(json.dumps(after))
        result = self.cli('compare', first, second)
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_unchanged_snapshot_ignores_timestamps_and_metrics(self):
        before = self.capture()
        os.utime(self.tracked, (1234567890, 1234567890))
        after = self.capture()
        after['metrics']['elapsed_seconds'] = 999
        self.assertEqual(self.compare(before, after, 0), {
            'changed': False, 'changed_paths': [], 'head_changed': False,
        })
        self.assertEqual(before['snapshot'], after['snapshot'])
        self.assertEqual(before['metrics']['hashed_bytes'], 18)
        self.assertGreaterEqual(before['metrics']['elapsed_seconds'], 0)
        self.assertEqual(before['snapshot']['worktree']['tracked file.txt']['sha256'],
                         '25718360e05d3c2d0963d1381e9dd4dae5fca789244ee4b9f861adcc0cc96218')

    def test_many_file_capture_matches_independently_computed_digests(self):
        expected = {}
        for index in range(16):
            name = f'nested/file-{index:02d}.txt' if index % 2 else f'file-{index:02d}.txt'
            content = f'capture {index}\n'.encode() * (index + 1)
            expected[name] = content
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        first = self.capture()
        worktree = first['snapshot']['worktree']
        self.assertEqual(set(worktree), set(expected) | {'tracked file.txt', '.gitignore'})
        for name, content in expected.items():
            with self.subTest(name=name):
                self.assertEqual(worktree[name]['type'], 'file')
                self.assertEqual(worktree[name]['sha256'], hashlib.sha256(content).hexdigest())
        self.assertEqual(first['metrics']['hashed_bytes'], 18 + sum(map(len, expected.values())))
        second = self.capture()
        self.assertEqual(first['snapshot'], second['snapshot'])
        self.assertFalse(self.compare(first, second, 0)['changed'])

    def test_already_modified_tracked_content_changes_with_identical_porcelain(self):
        self.tracked.write_text('dirty before\n')
        status = self.git('status', '--porcelain', '-z')
        before = self.capture()
        self.tracked.write_text('dirty after!\n')
        self.assertEqual(self.git('status', '--porcelain', '-z'), status)
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_already_untracked_content_changes_with_identical_porcelain(self):
        untracked = self.repo / 'untracked file.txt'
        untracked.write_text('before\n')
        status = self.git('status', '--porcelain', '-z')
        before = self.capture()
        untracked.write_text('after\n')
        self.assertEqual(self.git('status', '--porcelain', '-z'), status)
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['untracked file.txt'])

    def test_untracked_additions_and_removals_are_detected(self):
        before = self.capture()
        added = self.repo / 'added file.txt'
        added.write_bytes(b'added')
        after = self.capture()
        self.assertEqual(self.compare(before, after)['changed_paths'], ['added file.txt'])
        added.unlink()
        self.assertEqual(self.compare(after, self.capture())['changed_paths'], ['added file.txt'])

    def test_tracked_deletion_is_explicit(self):
        before = self.capture()
        self.tracked.unlink()
        after = self.capture()
        self.assertEqual(after['snapshot']['worktree']['tracked file.txt'], {'type': 'missing'})
        self.assertEqual(self.compare(before, after)['changed_paths'], ['tracked file.txt'])

    def test_index_content_changes_without_a_worktree_change(self):
        before = self.capture()
        self.tracked.write_bytes(b'staged\n')
        self.git('add', 'tracked file.txt')
        self.tracked.write_bytes(b'original\n')
        after = self.capture()
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertEqual(self.compare(before, after)['changed_paths'], ['tracked file.txt'])

    def test_index_mode_change_is_detected(self):
        before = self.capture()
        self.git('update-index', '--chmod=+x', 'tracked file.txt')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_staging_empty_intent_to_add_invalidates_review_evidence(self):
        name = 'empty\nintent\t"quote" and spaces.txt'
        (self.repo / name).touch()
        self.git('add', '-N', name)
        before = self.capture()
        self.git('add', name)
        after = self.capture()
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertEqual(self.compare(before, after), {
            'changed': True, 'changed_paths': [name], 'head_changed': False,
        })

    def test_staging_intent_to_add_over_nonempty_head_invalidates_review_evidence(self):
        self.tracked.write_bytes(b'')
        self.git('rm', '--cached', 'tracked file.txt')
        self.git('add', '-N', 'tracked file.txt')
        before = self.capture()
        self.git('add', 'tracked file.txt')
        after = self.capture()
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertEqual(self.compare(before, after), {
            'changed': True, 'changed_paths': ['tracked file.txt'], 'head_changed': False,
        })

    def test_staging_nonempty_intent_to_add_invalidates_review_evidence(self):
        name = 'nonempty.txt'
        (self.repo / name).write_bytes(b'new content\n')
        self.git('add', '-N', name)
        before = self.capture()
        self.assertTrue(before['snapshot']['index'][1]['intent_to_add'])
        self.git('add', name)
        after = self.capture()
        self.assertFalse(after['snapshot']['index'][1]['intent_to_add'])
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertEqual(self.compare(before, after)['changed_paths'], [name])

    def test_index_timestamp_refresh_does_not_invalidate_review_evidence(self):
        before = self.capture()
        os.utime(self.tracked, (1234567890, 1234567890))
        self.git('update-index', '--refresh')
        after = self.capture()
        self.assertEqual(before['snapshot'], after['snapshot'])
        self.assertFalse(self.compare(before, after, 0)['changed'])

    def test_unmerged_index_stages_remain_distinct(self):
        original = self.git('rev-parse', 'HEAD:tracked file.txt').strip()
        other = self.git('rev-parse', 'HEAD:.gitignore').strip()
        self.git('update-index', '--index-info', input=(
            b'0 ' + b'0' * 40 + b'\ttracked file.txt\n'
            b'100644 ' + original + b' 1\ttracked file.txt\n'
            b'100644 ' + original + b' 2\ttracked file.txt\n'
            b'100644 ' + other + b' 3\ttracked file.txt\n'
        ))
        before = self.capture()
        entries = [entry for entry in before['snapshot']['index']
                   if entry['path'] == 'tracked file.txt']
        self.assertEqual([entry['stage'] for entry in entries], [1, 2, 3])
        self.assertEqual([entry['intent_to_add'] for entry in entries], [False, False, False])
        self.assertFalse(self.compare(before, self.capture(), 0)['changed'])
        self.git('update-index', '--index-info',
                 input=b'100644 ' + other + b' 2\ttracked file.txt\n')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_index_visibility_flags_change_with_identical_porcelain(self):
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                status = self.git('status', '--porcelain', '-z')
                before = self.capture()
                self.git('update-index', f'--{flag}', 'tracked file.txt')
                try:
                    self.assertEqual(self.git('status', '--porcelain', '-z'), status)
                    flagged = self.capture()
                    self.assertEqual(self.compare(before, flagged)['changed_paths'], ['tracked file.txt'])
                finally:
                    self.git('update-index', f'--no-{flag}', 'tracked file.txt')
                self.assertEqual(self.git('status', '--porcelain', '-z'), status)
                self.assertEqual(self.compare(flagged, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_index_visibility_flags_hide_and_reveal_dirty_tracked_files(self):
        self.tracked.write_text('dirty tracked file\n')
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                status = self.git('status', '--porcelain', '-z')
                self.assertEqual(status, b' M tracked file.txt\0')
                before = self.capture()
                self.git('update-index', f'--{flag}', 'tracked file.txt')
                try:
                    self.assertEqual(self.git('status', '--porcelain', '-z'), b'')
                    flagged = self.capture()
                    self.assertEqual(self.compare(before, flagged)['changed_paths'], ['tracked file.txt'])
                finally:
                    self.git('update-index', f'--no-{flag}', 'tracked file.txt')
                self.assertEqual(self.git('status', '--porcelain', '-z'), status)
                self.assertEqual(self.compare(flagged, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_hidden_tracked_content_changes_with_identical_porcelain(self):
        for flag in ('assume-unchanged', 'skip-worktree'):
            with self.subTest(flag=flag):
                self.git('update-index', f'--{flag}', 'tracked file.txt')
                try:
                    self.tracked.write_text('hidden before\n')
                    before = self.capture()
                    self.assertEqual(self.git('status', '--porcelain', '-z'), b'')
                    self.tracked.write_text('hidden after!\n')
                    self.assertEqual(self.git('status', '--porcelain', '-z'), b'')
                    self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])
                finally:
                    self.git('update-index', f'--no-{flag}', 'tracked file.txt')

    def test_combined_index_flags_preserve_nul_delimited_paths(self):
        name = 'line\nbreak\t"quote" and spaces.txt'
        (self.repo / name).write_text('tracked\n')
        self.git('add', name)
        before = self.capture()
        self.git('update-index', '--skip-worktree', name)
        skipped = self.capture()
        self.assertEqual(self.compare(before, skipped)['changed_paths'], [name])
        self.git('update-index', '--assume-unchanged', name)
        combined = self.capture()
        self.assertEqual(self.compare(skipped, combined)['changed_paths'], [name])
        self.git('update-index', '--no-skip-worktree', name)
        self.assertEqual(self.compare(combined, self.capture())['changed_paths'], [name])

    def test_worktree_mode_change_is_detected(self):
        before = self.capture()
        self.tracked.chmod(0o755)
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_head_only_change_is_detected(self):
        before = self.capture()
        self.git('commit', '--allow-empty', '-qm', 'New HEAD')
        self.assertEqual(self.compare(before, self.capture()), {
            'changed': True, 'changed_paths': [], 'head_changed': True,
        })

    def test_branch_change_at_same_commit_is_detected(self):
        before = self.capture()
        self.git('checkout', '-qb', 'other-branch')
        after = self.capture()
        self.assertEqual(before['snapshot']['head']['commit'], after['snapshot']['head']['commit'])
        self.assertTrue(self.compare(before, after)['head_changed'])

    def test_rename_reports_both_paths(self):
        before = self.capture()
        self.git('mv', 'tracked file.txt', 'renamed file.txt')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'],
                         ['renamed file.txt', 'tracked file.txt'])

    def test_unborn_repository_has_null_commit_and_captures_staged_files(self):
        self.repo = self.directory / 'unborn repository'
        self.repo.mkdir()
        self.git('init', '-q')
        (self.repo / 'new.txt').write_text('new')
        self.git('add', 'new.txt')
        snapshot = self.capture()['snapshot']
        self.assertIsNone(snapshot['head']['commit'])
        self.assertEqual(snapshot['tracked_paths'], ['new.txt'])

    def test_staging_empty_intent_to_add_without_head_invalidates_review_evidence(self):
        self.repo = self.directory / 'unborn repository'
        self.repo.mkdir()
        self.git('init', '-q')
        (self.repo / 'empty.txt').touch()
        self.git('add', '-N', 'empty.txt')
        before = self.capture()
        self.git('add', 'empty.txt')
        after = self.capture()
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertIsNone(after['snapshot']['head']['commit'])
        self.assertEqual(self.compare(before, after)['changed_paths'], ['empty.txt'])

    def test_nul_delimited_names_round_trip_without_quoting(self):
        name = 'line\nbreak\t"quote" and spaces.txt'
        (self.repo / name).write_bytes(b'first')
        before = self.capture()
        self.assertEqual(before['snapshot']['untracked_paths'], [name])
        (self.repo / name).write_bytes(b'second')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], [name])

    @unittest.skipIf(sys.platform == 'darwin', 'macOS rejects non-UTF-8 filenames; requires a byte-preserving filesystem')
    def test_non_utf8_path_round_trips(self):
        name = os.fsdecode(b'non-utf8-\xff.txt')
        (self.repo / name).write_bytes(b'first')
        before = self.capture()
        (self.repo / name).write_bytes(b'second')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], [name])

    @unittest.skipIf(sys.platform == 'darwin', 'macOS rejects non-UTF-8 filenames; requires a byte-preserving filesystem')
    def test_non_utf8_intent_to_add_path_round_trips(self):
        name = os.fsdecode(b'intent-\xff.txt')
        (self.repo / name).touch()
        self.git('add', '-N', name)
        before = self.capture()
        self.git('add', name)
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], [name])

    def test_ignored_artifacts_are_excluded_but_ignored_tracked_files_are_hashed(self):
        ignored = self.repo / 'ignored'
        ignored.mkdir()
        (ignored / 'tracked.txt').write_bytes(b'tracked')
        self.git('add', '-f', 'ignored/tracked.txt')
        before = self.capture()
        (ignored / 'build.bin').write_bytes(b'build output')
        self.assertFalse(self.compare(before, self.capture(), 0)['changed'])
        (ignored / 'tracked.txt').write_bytes(b'changed')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['ignored/tracked.txt'])

    def test_scoped_capture_omits_out_of_scope_mutations(self):
        (self.repo / 'scoped untracked.txt').write_bytes(b'scoped\n')
        scope = ['tracked file.txt', 'scoped untracked.txt']
        before = self.capture(scope)
        self.assertEqual(before['snapshot']['tracked_paths'], ['tracked file.txt'])
        self.assertEqual(before['snapshot']['untracked_paths'], ['scoped untracked.txt'])
        self.assertEqual([entry['path'] for entry in before['snapshot']['index']],
                         ['tracked file.txt'])
        self.assertEqual(before['snapshot']['worktree']['scoped untracked.txt']['sha256'],
                         hashlib.sha256(b'scoped\n').hexdigest())
        self.tracked.write_bytes(b'mutated\n')
        (self.repo / '.gitignore').write_text('ignored/\nmutated\n')
        (self.repo / 'out of scope.txt').write_bytes(b'outside the scope\n')
        after = self.capture(scope)
        self.assertEqual(set(after['snapshot']['worktree']), set(scope))
        self.assertNotIn('out of scope.txt', after['snapshot']['untracked_paths'])
        self.assertEqual(self.compare(before, after)['changed_paths'], ['tracked file.txt'])

    def test_scoped_index_evidence_omits_out_of_scope_staging(self):
        scope = ['tracked file.txt']
        before = self.capture(scope)
        self.tracked.write_bytes(b'staged\n')
        self.git('add', 'tracked file.txt')
        self.tracked.write_bytes(b'original\n')
        (self.repo / '.gitignore').write_text('mutated\n')
        self.git('add', '.gitignore')
        after = self.capture(scope)
        self.assertEqual([entry['path'] for entry in after['snapshot']['index']],
                         ['tracked file.txt'])
        self.assertEqual(before['snapshot']['worktree'], after['snapshot']['worktree'])
        self.assertEqual(self.compare(before, after)['changed_paths'], ['tracked file.txt'])

    def test_scoped_compare_keeps_exit_codes(self):
        scope = ['tracked file.txt']
        before = self.capture(scope)
        self.assertEqual(self.compare(before, self.capture(scope), 0), {
            'changed': False, 'changed_paths': [], 'head_changed': False,
        })
        self.git('commit', '--allow-empty', '-qm', 'New HEAD')
        self.assertEqual(self.compare(before, self.capture(scope)), {
            'changed': True, 'changed_paths': [], 'head_changed': True,
        })
        corrupt = json.loads(json.dumps(before))
        corrupt['snapshot']['index'][0]['mode'] = 'bad'
        self.assertIn('error', self.compare(before, corrupt, 2))

    def test_capture_without_scope_remains_whole_repository(self):
        (self.repo / 'extra file.txt').write_bytes(b'extra\n')
        whole = self.capture()['snapshot']
        self.assertEqual(set(whole),
                         {'version', 'repo', 'scope', 'head', 'index', 'tracked_paths',
                          'untracked_paths', 'worktree'})
        self.assertIsNone(whole['scope'])
        self.assertEqual(whole['tracked_paths'], ['.gitignore', 'tracked file.txt'])
        self.assertEqual(whole['untracked_paths'], ['extra file.txt'])
        self.assertEqual(set(whole['worktree']),
                         {'.gitignore', 'tracked file.txt', 'extra file.txt'})

    def test_capture_stores_sorted_unique_scope_and_empty_scope(self):
        scoped = self.capture(['tracked file.txt', '.gitignore', 'tracked file.txt'])
        self.assertEqual(scoped['snapshot']['scope'], ['.gitignore', 'tracked file.txt'])
        reordered = self.capture(['.gitignore', 'tracked file.txt'])
        self.assertEqual(self.compare(scoped, reordered, 0), {
            'changed': False, 'changed_paths': [], 'head_changed': False,
        })
        empty = self.capture([])
        self.assertEqual(empty['snapshot']['scope'], [])
        self.assertEqual(empty['snapshot']['worktree'], {})

    def test_scope_argument_errors_are_json_with_exit_two(self):
        malformed = self.directory / 'malformed scope.txt'
        malformed.write_text('nonsense')
        result = self.cli('--repo', self.repo, '--scope-from', malformed)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid scope', json.loads(result.stdout)['error'])
        escaping = self.directory / 'escaping scope.json'
        escaping.write_text(json.dumps(['../outside']))
        result = self.cli('--repo', self.repo, '--scope-from', escaping)
        self.assertEqual(result.returncode, 2)
        self.assertIn('invalid scope', json.loads(result.stdout)['error'])
        before = self.capture()
        first = self.directory / 'before snapshot.json'
        second = self.directory / 'after snapshot.json'
        first.write_text(json.dumps(before))
        second.write_text(json.dumps(before))
        result = self.cli('compare', '--scope-from', escaping, first, second)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_symlink_targets_are_recorded_without_hashing_outside_contents(self):
        outside = self.directory / 'outside.txt'
        outside.write_bytes(b'outside before')
        link = self.repo / 'link'
        link.symlink_to(outside)
        before = self.capture()
        self.assertEqual(before['snapshot']['worktree']['link']['type'], 'symlink')
        self.assertEqual(before['snapshot']['worktree']['link']['target'], str(outside))
        self.assertEqual(before['metrics']['hashed_bytes'], 18)
        outside.write_bytes(b'outside after')
        self.assertFalse(self.compare(before, self.capture(), 0)['changed'])
        link.unlink()
        link.symlink_to('missing target')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['link'])

    def test_symlink_ancestor_does_not_read_outside_repository(self):
        folder = self.repo / 'folder'
        folder.mkdir()
        (folder / 'file.txt').write_text('inside')
        self.git('add', 'folder/file.txt')
        before = self.capture()
        (folder / 'file.txt').unlink()
        folder.rmdir()
        outside = self.directory / 'outside'
        outside.mkdir()
        (outside / 'file.txt').write_text('outside')
        folder.symlink_to(outside, target_is_directory=True)
        after = self.capture()
        self.assertEqual(after['snapshot']['worktree']['folder/file.txt']['type'], 'symlink-parent')
        self.assertEqual(after['metrics']['hashed_bytes'], 18)
        self.assertEqual(self.compare(before, after)['changed_paths'], ['folder', 'folder/file.txt'])

    def test_file_type_change_is_detected(self):
        before = self.capture()
        self.tracked.unlink()
        self.tracked.symlink_to('missing target')
        self.assertEqual(self.compare(before, self.capture())['changed_paths'], ['tracked file.txt'])

    def test_special_files_block_capture_without_opening_them(self):
        self.tracked.unlink()
        os.mkfifo(self.tracked)
        result = self.cli('--repo', self.repo)
        self.assertEqual(result.returncode, 2)
        self.assertIn('unsupported file type', json.loads(result.stdout)['error'])

    def test_capture_is_read_only_for_head_index_and_worktree(self):
        before_index = (self.repo / '.git/index').read_bytes()
        before_head = self.git('rev-parse', 'HEAD')
        before_content = self.tracked.read_bytes()
        self.capture()
        self.assertEqual((self.repo / '.git/index').read_bytes(), before_index)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before_head)
        self.assertEqual(self.tracked.read_bytes(), before_content)

    def test_non_repository_capture_is_an_io_error(self):
        result = self.cli('--repo', self.directory)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_usage_errors_are_json_with_exit_two(self):
        for arguments in ((), ('--repo', self.repo, 'compare'), ('compare',), ('unknown',)):
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stdout))

    def test_compare_rejects_different_repositories(self):
        before = self.capture()
        after = self.capture()
        after['snapshot']['repo'] = str(self.directory / 'other repo')
        self.assertIn('different repositories', self.compare(before, after, 2)['error'])

    def test_compare_rejects_different_scopes(self):
        first = self.capture(['tracked file.txt'])
        second = self.capture(['.gitignore'])
        self.assertIn('different scopes', self.compare(first, second, 2)['error'])
        whole = self.capture()
        same_paths_scoped = self.capture(['.gitignore', 'tracked file.txt'])
        self.assertIn('different scopes', self.compare(whole, same_paths_scoped, 2)['error'])
        self.assertIn('different scopes', self.compare(same_paths_scoped, whole, 2)['error'])

    def test_compare_requires_valid_scope_in_schema(self):
        before = self.capture()
        missing_scope = json.loads(json.dumps(before))
        missing_scope['snapshot'].pop('scope', None)
        self.assertIn('invalid snapshot schema', self.compare(before, missing_scope, 2)['error'])
        for invalid in ('tracked file.txt', 123, ['../outside'],
                        ['tracked file.txt', '.gitignore'], ['.gitignore', '.gitignore']):
            with self.subTest(invalid=invalid):
                corrupt = json.loads(json.dumps(before))
                corrupt['snapshot']['scope'] = invalid
                self.assertIn('invalid scope', self.compare(before, corrupt, 2)['error'])

    def test_compare_missing_file_is_an_io_error(self):
        result = self.cli('compare', self.directory / 'missing', self.directory / 'also missing')
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_compare_rejects_invalid_snapshots(self):
        before = self.capture()
        for invalid in ({}, {'snapshot': {}}, {'snapshot': [], 'metrics': {}},
                        {'snapshot': before['snapshot'], 'metrics': {'hashed_bytes': -1}}):
            with self.subTest(invalid=invalid):
                self.assertIn('error', self.compare(before, invalid, 2))

    def test_compare_rejects_malformed_json(self):
        bad = self.directory / 'bad.json'
        bad.write_text('{')
        result = self.cli('compare', bad, bad)
        self.assertEqual(result.returncode, 2)
        self.assertIn('error', json.loads(result.stdout))

    def test_compare_rejects_corrupt_content_evidence(self):
        before = self.capture()
        cases = (
            ('head', {'commit': 'not-an-object', 'ref': None}),
            ('index', [{'path': 'tracked file.txt', 'mode': 'bad', 'object': 'x', 'stage': 0}]),
            ('tracked_paths', ['../outside']),
            ('untracked_paths', ['tracked file.txt']),
            ('worktree', {'tracked file.txt': {'type': 'file', 'mode': 420, 'sha256': 'bad'}}),
        )
        for key, value in cases:
            with self.subTest(key=key):
                after = json.loads(json.dumps(before))
                after['snapshot'][key] = value
                self.assertIn('error', self.compare(before, after, 2))

    def test_compare_rejects_invalid_index_flags(self):
        before = self.capture()
        for field, value in (('assume_unchanged', 1), ('skip_worktree', 'true'),
                             ('intent_to_add', 1)):
            with self.subTest(field=field):
                after = json.loads(json.dumps(before))
                after['snapshot']['index'][0][field] = value
                self.assertIn('invalid index', self.compare(before, after, 2)['error'])


if __name__ == '__main__':
    unittest.main()
