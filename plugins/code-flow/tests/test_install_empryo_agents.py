import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROLES, copy_package


class InstallEmpryoAgentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = sorted(path.stem for path in ROLES.glob('*.toml'))
        cls.expected_names = {f'code-flow-{role}.md' for role in cls.roles}

    def run_installer(self, script_path, home, *args):
        env = dict(os.environ, HOME=str(home), AGENTS_METRICS='off')
        return subprocess.run(
            [sys.executable, str(script_path), *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
        )

    def test_first_install_repeated_install_and_unrelated_entry_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            package = copy_package(directory, 'package with spaces')
            home = Path(directory) / 'isolated home'
            home.mkdir()
            script = package / 'scripts/install-empryo-agents.py'

            first = self.run_installer(script, home)
            self.assertEqual(first.returncode, 0, first.stderr)

            agents_dir = home / '.empryo/agents'
            installed = {entry.name: entry for entry in agents_dir.iterdir()}
            self.assertEqual(set(installed), self.expected_names)
            for role in self.roles:
                name = f'code-flow-{role}.md'
                link = installed[name]
                self.assertTrue(link.is_symlink(), name)
                expected_target = str((package / 'harnesses/empryo' / name).resolve())
                self.assertEqual(os.readlink(link), expected_target)

            unrelated = agents_dir / 'custom.md'
            unrelated.write_text('# Unrelated custom agent\n')
            old_clone_link = agents_dir / 'code-flow-retired.md'
            old_clone_link.symlink_to('/moved/clone/harnesses/empryo/code-flow-retired.md')

            second = self.run_installer(script, home)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(unrelated.read_text(), '# Unrelated custom agent\n')
            self.assertTrue(old_clone_link.is_symlink())
            self.assertEqual(os.readlink(old_clone_link), '/moved/clone/harnesses/empryo/code-flow-retired.md')
            for role in self.roles:
                name = f'code-flow-{role}.md'
                self.assertEqual(os.readlink(agents_dir / name), str((package / 'harnesses/empryo' / name).resolve()))

    def test_link_follows_changed_source_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            package = copy_package(directory, 'package with spaces')
            home = Path(directory) / 'isolated home'
            home.mkdir()

            result = self.run_installer(package / 'scripts/install-empryo-agents.py', home)
            self.assertEqual(result.returncode, 0, result.stderr)

            source = package / 'harnesses/empryo/code-flow-worker.md'
            updated = source.read_text() + '\nUpdated content.\n'
            source.write_text(updated)

            link = home / '.empryo/agents/code-flow-worker.md'
            self.assertEqual(link.read_text(), updated)

    def test_rejects_invalid_sources_without_partial_install(self):
        for case in ('absent', 'directory', 'unreadable'):
            if case == 'unreadable' and os.geteuid() == 0:
                continue
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                package = copy_package(directory, 'package with spaces')
                home = Path(directory) / 'isolated home'
                home.mkdir()
                source = package / 'harnesses/empryo/code-flow-reviewer.md'
                try:
                    if case == 'absent':
                        source.unlink()
                    elif case == 'directory':
                        source.unlink()
                        source.mkdir()
                    elif case == 'unreadable':
                        source.chmod(0o000)

                    result = self.run_installer(package / 'scripts/install-empryo-agents.py', home)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('code-flow-reviewer.md', result.stderr)
                    agents_dir = home / '.empryo/agents'
                    self.assertFalse(agents_dir.exists() and any(agents_dir.iterdir()))
                finally:
                    if case == 'unreadable' and source.exists():
                        source.chmod(0o644)

    def assert_regular_file(self, path, expected_text):
        self.assertFalse(path.is_symlink())
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(), expected_text)

    def assert_symlink(self, path, expected_target):
        self.assertTrue(path.is_symlink())
        self.assertEqual(os.readlink(path), expected_target)

    def test_rejects_collisions_without_mutation_or_partial_install(self):
        def prepare_regular_file(conflict, _directory, _source):
            conflict.write_text('user file\n')
            return 'user file\n'

        def prepare_directory(conflict, _directory, _source):
            conflict.mkdir()
            (conflict / 'keep.txt').write_text('keep\n')
            return 'keep\n'

        def assert_directory(conflict, expected_keep_text):
            self.assertFalse(conflict.is_symlink())
            self.assertTrue(conflict.is_dir())
            self.assertEqual((conflict / 'keep.txt').read_text(), expected_keep_text)

        def prepare_relative_link(conflict, _directory, source):
            rel_target = os.path.relpath(source, conflict.parent)
            conflict.symlink_to(rel_target)
            return rel_target

        def prepare_dangling_link(conflict, directory, _source):
            dangling = str(Path(directory) / 'missing-target.md')
            conflict.symlink_to(dangling)
            return dangling

        def prepare_other_absolute_link(conflict, directory, _source):
            other = Path(directory) / 'other clone/code-flow-reviewer.md'
            other.parent.mkdir()
            other.write_text('other\n')
            other_target = str(other.resolve())
            conflict.symlink_to(other_target)
            return other_target

        cases = (
            ('regular_file', prepare_regular_file, self.assert_regular_file),
            ('directory', prepare_directory, assert_directory),
            ('relative_link', prepare_relative_link, self.assert_symlink),
            ('dangling_link', prepare_dangling_link, self.assert_symlink),
            ('other_absolute_link', prepare_other_absolute_link, self.assert_symlink),
        )
        for case, prepare, assert_preserved in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                package = copy_package(directory, 'package with spaces')
                home = Path(directory) / 'isolated home'
                agents_dir = home / '.empryo/agents'
                agents_dir.mkdir(parents=True)
                conflict = agents_dir / 'code-flow-reviewer.md'
                source = (package / 'harnesses/empryo/code-flow-reviewer.md').resolve()

                expected = prepare(conflict, directory, source)
                result = self.run_installer(package / 'scripts/install-empryo-agents.py', home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('code-flow-reviewer.md', result.stderr)

                entries = {entry.name for entry in agents_dir.iterdir()}
                self.assertEqual(entries, {'code-flow-reviewer.md'})
                assert_preserved(conflict, expected)

    def test_rejects_non_directory_parents_without_mutation(self):
        def prepare_regular_file(target, _directory):
            target.write_text('keep\n')
            return 'keep\n'

        def prepare_dangling_link(target, directory):
            dangling = str(Path(directory) / 'missing-parent')
            target.symlink_to(dangling)
            return dangling

        cases = (
            ('.empryo', 'regular_file', prepare_regular_file, self.assert_regular_file),
            ('.empryo', 'dangling_link', prepare_dangling_link, self.assert_symlink),
            ('.empryo/agents', 'regular_file', prepare_regular_file, self.assert_regular_file),
            ('.empryo/agents', 'dangling_link', prepare_dangling_link, self.assert_symlink),
        )
        for parent_rel, kind, prepare, assert_preserved in cases:
            with self.subTest(parent=parent_rel, kind=kind), tempfile.TemporaryDirectory() as directory:
                package = copy_package(directory, 'package with spaces')
                home = Path(directory) / 'isolated home'
                target = home / parent_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                expected = prepare(target, directory)

                result = self.run_installer(package / 'scripts/install-empryo-agents.py', home)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('destination parent is not a directory', result.stderr)
                self.assertNotIn('NotADirectoryError', result.stderr)
                assert_preserved(target, expected)

    def test_rejects_unexpected_arguments_without_creating_links(self):
        with tempfile.TemporaryDirectory() as directory:
            package = copy_package(directory, 'package with spaces')
            home = Path(directory) / 'isolated home'
            home.mkdir()
            result = self.run_installer(package / 'scripts/install-empryo-agents.py', home, '--force')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((home / '.empryo').exists())

    def test_resolves_package_root_from_script_realpath(self):
        with tempfile.TemporaryDirectory() as directory:
            package = copy_package(directory, 'package with spaces')
            home = Path(directory) / 'isolated home'
            home.mkdir()
            alias_dir = Path(directory) / 'bin links'
            alias_dir.mkdir()
            script_link = alias_dir / 'install-empryo'
            script_link.symlink_to(package / 'scripts/install-empryo-agents.py')

            result = self.run_installer(script_link, home)
            self.assertEqual(result.returncode, 0, result.stderr)
            for role in self.roles:
                name = f'code-flow-{role}.md'
                link = home / '.empryo/agents' / name
                self.assertTrue(link.is_symlink())
                self.assertEqual(os.readlink(link), str((package / 'harnesses/empryo' / name).resolve()))


if __name__ == '__main__':
    unittest.main()
