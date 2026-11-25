import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[1]
SKILL_NAMES = {
    'code-reviewer',
}
PORTABLE_FRONTMATTER_KEYS = {'name', 'description', 'argument-hint', 'user-invocable', 'allowed-tools'}


class PackagePortabilityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name).resolve() / 'different checkout' / 'code-quality'
        shutil.copytree(PACKAGE, self.package, symlinks=True, ignore=shutil.ignore_patterns('__pycache__'))

    def test_manifest_uses_skill_autodiscovery(self):
        data = json.loads((self.package / 'plugin.json').read_text())
        self.assertEqual(data['$schema'], 'https://agent-plugins.org/schemas/1.0.0/plugin.schema.json')
        self.assertEqual(data['name'], 'code-quality')
        self.assertEqual(data['version'], '0.1.0')
        self.assertNotIn('skills', data)
        self.assertEqual(
            {path.parent.name for path in (self.package / 'skills').glob('*/SKILL.md')},
            SKILL_NAMES,
        )

    def test_relative_resource_links_resolve_after_relocation(self):
        links = []
        for document in self.package.rglob('*.md'):
            for target in re.findall(r'\[[^\]]*\]\(([^\s)]+)\)', document.read_text()):
                if re.match(r'[a-z]+://|#', target):
                    continue
                resource = (document.parent / target.split('#')[0]).resolve()
                links.append((document, target, resource))
        self.assertGreater(len(links), 10)
        for document, target, resource in links:
            with self.subTest(document=str(document.relative_to(self.package)), target=target):
                self.assertTrue(resource.is_relative_to(self.package))
                self.assertTrue(resource.is_file(), str(resource))

    def test_package_contains_only_internal_regular_resources(self):
        for path in self.package.rglob('*'):
            with self.subTest(path=str(path.relative_to(self.package))):
                self.assertFalse(path.is_symlink())
                self.assertTrue(path.resolve().is_relative_to(self.package))

    def test_exposed_frontmatter_has_only_portable_metadata(self):
        for entry in (self.package / 'skills').glob('*/SKILL.md'):
            with self.subTest(skill=entry.parent.name):
                frontmatter = entry.read_text().split('---', 2)[1]
                keys = set(re.findall(r'^([\w-]+):', frontmatter, flags=re.MULTILINE))
                self.assertIn('name', keys)
                self.assertIn('description', keys)
                self.assertTrue(keys.issubset(PORTABLE_FRONTMATTER_KEYS), keys)


if __name__ == '__main__':
    unittest.main()
