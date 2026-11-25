import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROOT, load_script_module


REPOSITORY = ROOT.parents[1]
SYNC = ROOT / 'tools' / 'sync-standards.py'
STANDARDS = ROOT / 'standards'
SOURCE_PACKAGE = REPOSITORY / 'plugins' / 'code-quality'


class SyncStandardsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sync = load_script_module(SYNC, 'sync_standards_script')

    def copy_repository(self, directory):
        repository = Path(directory) / 'repository with spaces'
        package = repository / 'plugins' / 'code-flow'
        (package / 'tools').mkdir(parents=True)
        shutil.copy(SYNC, package / 'tools' / SYNC.name)
        shutil.copytree(STANDARDS, package / 'standards')
        shutil.copytree(SOURCE_PACKAGE, repository / 'plugins' / SOURCE_PACKAGE.name)
        return repository

    def run_sync(self, repository, *arguments):
        return subprocess.run(
            [sys.executable, str(repository / 'plugins/code-flow/tools/sync-standards.py'), *arguments],
            capture_output=True, text=True,
        )

    def snapshot(self, repository):
        standards = repository / 'plugins/code-flow/standards'
        return {path.name: path.read_bytes() for path in standards.iterdir() if path.is_file()}

    def test_committed_standards_reproduce_from_code_quality_sources_byte_for_byte(self):
        rendered = self.sync.render_all(REPOSITORY)
        self.assertEqual(set(rendered), set(self.sync.SPECS))
        for name, text in sorted(rendered.items()):
            with self.subTest(standard=name):
                self.assertEqual((STANDARDS / name).read_bytes(), text.encode('utf-8'))
                self.assertNotIn(b'flow:', text.encode('utf-8'))
                self.assertNotIn(b'../../../references/', text.encode('utf-8'))
        self.assertEqual(self.sync.TARGET_DIR, STANDARDS)
        unmanaged = {path.name for path in STANDARDS.iterdir()} - set(rendered)
        self.assertEqual(unmanaged, {'review-protocol.md', 'code-reviewer.md'})

    def test_every_spec_names_an_existing_source_with_its_declared_markers(self):
        for name, spec in sorted(self.sync.SPECS.items()):
            with self.subTest(standard=name):
                source = REPOSITORY / spec['source']
                self.assertTrue(source.is_relative_to(SOURCE_PACKAGE), source)
                self.assertTrue(source.is_file(), source)
                text = source.read_text(encoding='utf-8')
                self.assertEqual(text.count(self.sync.HEADER_MARKER), 1 if 'header' in spec else 0, source)
                for identifier in spec.get('alt', {}):
                    self.assertEqual(text.count(f'<!-- flow:{identifier} -->'), 1, (source, identifier))
                    self.assertEqual(text.count(f'<!-- /flow:{identifier} -->'), 1, (source, identifier))

    def test_check_passes_on_the_committed_tree(self):
        result = subprocess.run([sys.executable, str(SYNC), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_check_detects_drifted_and_missing_standards_and_sync_restores_them(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.copy_repository(directory)
            self.assertEqual(self.run_sync(repository, '--check').returncode, 0)
            standards = repository / 'plugins/code-flow/standards'
            unmanaged = standards / 'review-protocol.md'
            unmanaged_before = unmanaged.read_bytes()
            drifted = standards / 'symfony.md'
            drifted.write_text('drifted\n')
            missing = standards / 'golang.md'
            missing.unlink()

            check = self.run_sync(repository, '--check')
            self.assertEqual(check.returncode, 1, check.stderr)
            self.assertEqual(check.stdout, '')
            self.assertIn('stale standards: standards/golang.md standards/symfony.md', check.stderr)
            self.assertEqual(drifted.read_text(), 'drifted\n')
            self.assertFalse(missing.exists())

            written = self.run_sync(repository)
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertIn('wrote 7 files', written.stdout)
            self.assertEqual(drifted.read_bytes(), (STANDARDS / 'symfony.md').read_bytes())
            self.assertEqual(missing.read_bytes(), (STANDARDS / 'golang.md').read_bytes())
            self.assertEqual(unmanaged.read_bytes(), unmanaged_before)
            self.assertEqual(self.run_sync(repository, '--check').returncode, 0)

    def test_source_edit_reaches_the_generated_standard_on_the_next_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.copy_repository(directory)
            source = repository / self.sync.SPECS['golang.md']['source']
            source.write_text(source.read_text(encoding='utf-8') + '\nAppended rule.\n')
            check = self.run_sync(repository, '--check')
            self.assertEqual(check.returncode, 1)
            self.assertIn('standards/golang.md', check.stderr)
            self.assertNotIn('standards/symfony.md', check.stderr)
            self.assertEqual(self.run_sync(repository).returncode, 0)
            self.assertTrue((repository / 'plugins/code-flow/standards/golang.md').read_text().endswith('\nAppended rule.\n'))

    def test_missing_header_marker_fails_before_any_standard_is_written(self):
        mutations = {
            'header marker removed': lambda text: text.replace('<!-- flow:header -->\n', '', 1),
            'header marker duplicated': lambda text: text.replace('<!-- flow:header -->', '<!-- flow:header -->\n<!-- flow:header -->', 1),
            'region opening removed': lambda text: text.replace('<!-- flow:comments -->', '', 1),
            'region closing removed': lambda text: text.replace('<!-- /flow:comments -->', '', 1),
            'unknown region left behind': lambda text: text.replace('## Comments', '<!-- flow:unknown -->x<!-- /flow:unknown -->\n## Comments', 1),
        }
        for label, mutate in mutations.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                repository = self.copy_repository(directory)
                relative = self.sync.SPECS['symfony.md']['source']
                source = repository / relative
                source.write_text(mutate(source.read_text(encoding='utf-8')), encoding='utf-8')
                before = self.snapshot(repository)
                with self.assertRaises(self.sync.SyncError) as ctx:
                    self.sync.render_all(repository)
                self.assertIn(relative, str(ctx.exception))
                for arguments in ((), ('--check',)):
                    result = self.run_sync(repository, *arguments)
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertEqual(result.stdout, '')
                    payload = json.loads(result.stderr)
                    self.assertEqual(set(payload), {'error'})
                    self.assertIn(relative, payload['error'])
                self.assertEqual(self.snapshot(repository), before)

    def test_marker_left_in_a_pure_copy_source_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self.copy_repository(directory)
            relative = self.sync.SPECS['code-style.md']['source']
            source = repository / relative
            source.write_text('<!-- flow:header -->\n' + source.read_text(encoding='utf-8'), encoding='utf-8')
            before = self.snapshot(repository)
            result = self.run_sync(repository)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('unresolved marker', json.loads(result.stderr)['error'])
            self.assertEqual(self.snapshot(repository), before)

    def test_missing_source_package_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(self.sync.SyncError):
                self.sync.render_all(Path(directory))

    def test_unknown_generator_arguments_preserve_every_standard(self):
        cases = (('--chek',), ('--check', '--unknown'), ('unexpected',), ('--check=true',), ('--source', 'x'))
        for arguments in cases:
            with self.subTest(arguments=arguments), tempfile.TemporaryDirectory() as directory:
                repository = self.copy_repository(directory)
                before = self.snapshot(repository)
                result = self.run_sync(repository, *arguments)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn('usage:', result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertEqual(self.snapshot(repository), before)

    def test_step_offset_applies_to_source_headings_only(self):
        offset = self.sync.offset_steps('**Step 1 — A**\n**Step 12 — B**\n`**Step 3 — C**`\n', 1)
        self.assertEqual(offset, '**Step 2 — A**\n**Step 13 — B**\n`**Step 3 — C**`\n')
        rendered = self.sync.render('symfony.md', self.sync.SPECS['symfony.md'], REPOSITORY)
        headings = [line for line in rendered.splitlines() if line.startswith('**Step ')]
        self.assertEqual(
            headings,
            ['**Step 1 — Resolve scope**', '**Step 2 — Architectural summary**',
             '**Step 3 — Verify in context**', '**Step 4 — Numbered findings**'],
        )


if __name__ == '__main__':
    unittest.main()
