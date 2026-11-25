import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGES = ('code-flow', 'code-quality', 'local-guards', 'usage-metrics')


class SkillsPortabilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.installation = Path(temporary.name).resolve() / 'installation with spaces'
        self.plugins = self.installation / 'plugins'
        for package in PACKAGES:
            shutil.copytree(
                REPOSITORY / 'plugins' / package, self.plugins / package,
                symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(REPOSITORY / 'skills', self.installation / 'skills',
                        symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))
        self.scratch = self.plugins / 'code-flow' / 'skills' / 'project-rules'

    def skill_directories(self):
        directories = [self.plugins / package / 'skills' for package in PACKAGES]
        directories.append(self.installation / 'skills')
        return directories

    def test_relocated_packages_keep_the_full_workflow(self):
        package = self.plugins / 'code-flow'
        for relative in (
            'roles/reviewer.toml', 'references/dispatch.md',
            'scripts/role-profile.py', 'scripts/repository.py',
            'harnesses/mapping.json', 'skills/wrap-up/SKILL.md',
            'skills/plan-new/scripts/plan.py',
            'skills/plan-new/scripts/run_state.py',
            'skills/spec-flow/scripts/spec-preflight.py',
        ):
            with self.subTest(resource=relative):
                self.assertTrue((package / relative).is_file())

    def test_relative_links_resolve_inside_the_relocated_tree(self):
        unfenced_lines = self._load_fence_scanner()
        documents = sorted(
            document
            for directory in self.skill_directories()
            for document in directory.rglob('*.md'))
        self.assertGreater(len(documents), 2)
        for document in documents:
            for target, reason in self._link_errors(document, unfenced_lines):
                with self.subTest(
                        document=str(document.relative_to(self.installation)),
                        target=target):
                    self.fail(reason)

    def _load_fence_scanner(self):
        scanner = self.scratch / 'scripts' / 'markdown_fences.py'
        spec = importlib.util.spec_from_file_location('markdown_fences', scanner)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.unfenced_lines

    def _unfenced_blocks(self, document, unfenced_lines):
        block = []
        previous = 0
        with document.open(encoding='utf-8') as lines:
            for number, line in unfenced_lines(lines):
                if block and number != previous + 1:
                    yield ''.join(block)
                    block.clear()
                block.append(line)
                previous = number
        if block:
            yield ''.join(block)

    def _link_errors(self, document, unfenced_lines):
        directory = document.resolve().parent
        for block in self._unfenced_blocks(document, unfenced_lines):
            for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', block):
                if re.match(r'[a-z]+://|#', target):
                    continue
                resource = (directory / target.split('#')[0]).resolve()
                if not resource.is_relative_to(self.installation):
                    yield target, f'{resource} escapes the relocated installation'
                elif not resource.is_file():
                    yield target, f'{resource} is not a file'

    def test_relocated_links_ignore_fences_until_a_genuine_close(self):
        unfenced_lines = self._load_fence_scanner()
        document = self.scratch / 'fence-example.md'
        for marker, other in (('`', '~'), ('~', '`')):
            with self.subTest(marker=marker):
                document.write_text(
                    '[not a link\n'
                    f'   {marker * 4}markdown\n'
                    '[example](inside.md)\n'
                    f'{marker * 3}\n[example](after-short.md)\n'
                    f'{other * 4}\n[example](after-mismatch.md)\n'
                    f'inline {marker * 4}\n[example](after-inline.md)\n'
                    f'    {marker * 4}\n[example](after-four-spaces.md)\n'
                    f'\t{marker * 4}\n[example](after-tab.md)\n'
                    f'   {marker * 5} \t\n'
                    '](across-fence.md)\n'
                    '[real](missing.md)\n'
                    '[real](scripts)\n',
                    encoding='utf-8')
                self.assertEqual(
                    [target for target, _ in self._link_errors(document, unfenced_lines)],
                    ['missing.md', 'scripts'])

    def test_relocated_links_remain_visible_after_non_fence_runs(self):
        unfenced_lines = self._load_fence_scanner()
        document = self.scratch / 'non-fence-example.md'
        for run in ('inline ```', '    ```', '\t```', 'inline ~~~', '    ~~~', '\t~~~'):
            with self.subTest(run=run):
                document.write_text(f'{run}\n[real](missing.md)\n', encoding='utf-8')
                self.assertEqual(
                    [target for target, _ in self._link_errors(document, unfenced_lines)],
                    ['missing.md'])

    def test_relocated_fences_only_split_physical_lines(self):
        unfenced_lines = self._load_fence_scanner()
        document = self.scratch / 'physical-lines.md'
        for separator in ('\u2028', '\f'):
            with self.subTest(separator=separator):
                document.write_bytes(f'inline{separator}```\r\n[real](missing.md)\r'.encode('utf-8'))
                self.assertEqual(
                    [target for target, _ in self._link_errors(document, unfenced_lines)],
                    ['missing.md'])

    def test_relocated_links_resolve_fragments_and_contained_aliases(self):
        unfenced_lines = self._load_fence_scanner()
        document = self.scratch / 'location-example.md'
        fragment_link = '../../references/relocated-target.md#section'
        target = self.plugins / 'code-flow/references/relocated-target.md'
        (document.parent / 'contained.md').symlink_to('../../references/relocated-target.md')
        external = self.installation.parent / 'external.md'
        external.touch()
        (document.parent / 'external.md').symlink_to(external)
        document.write_text(
            '```markdown\n```\n'
            f'[relocated\nfile]({fragment_link})\n'
            '[alias](contained.md)\n'
            '[escape](external.md)\n'
            '[remote](https://example.invalid/example.md)\n'
            '[fragment](#section)\n',
            encoding='utf-8')
        self.assertEqual(
            [destination for destination, _ in self._link_errors(document, unfenced_lines)],
            [fragment_link, 'contained.md', 'external.md'])
        target.touch()
        self.assertEqual(
            [destination for destination, _ in self._link_errors(document, unfenced_lines)],
            ['external.md'])


if __name__ == '__main__':
    unittest.main()
