import json
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
PLUGINS = REPOSITORY / 'plugins'
PACKAGES = sorted(
    path for path in PLUGINS.iterdir()
    if path.is_dir() and (path / '.claude-plugin/plugin.json').exists()
)


class PackageManifestsTest(unittest.TestCase):
    def package_manifests(self, package):
        manifests = {}
        for relative in ('plugin.json', '.claude-plugin/plugin.json',
                         'package.json', '.codex-plugin/plugin.json',
                         '.antigravity-plugin/plugin.json'):
            path = package / relative
            if path.exists():
                manifests[relative] = json.loads(path.read_text())
        return manifests

    def test_every_package_declares_one_identity_across_manifests(self):
        self.assertEqual({path.name for path in PACKAGES},
                         {'code-quality', 'code-flow', 'local-guards',
                          'usage-metrics'})
        self.assertIn('.antigravity-plugin/plugin.json',
                      self.package_manifests(PLUGINS / 'local-guards'))
        marketplace = {
            entry['name']: entry
            for entry in json.loads(
                (REPOSITORY / '.claude-plugin/marketplace.json').read_text())['plugins']}
        for package in PACKAGES:
            manifests = self.package_manifests(package)
            manifests['marketplace.json'] = marketplace[package.name]
            with self.subTest(package=package.name):
                names = {manifest['name'].removeprefix('@phil0v/')
                         for manifest in manifests.values()}
                self.assertEqual(names, {package.name})
                versions = {manifest['version'] for manifest in manifests.values()}
                self.assertEqual(len(versions), 1, versions)
                descriptions = {manifest.get('description') for manifest in manifests.values()}
                self.assertEqual(len(descriptions), 1, descriptions)
                self.assertTrue(all(descriptions))

    def test_manifest_component_paths_exist(self):
        for package in PACKAGES:
            for relative, manifest in self.package_manifests(package).items():
                with self.subTest(package=package.name, manifest=relative):
                    for key in ('agents', 'skills', 'hooks'):
                        value = manifest.get(key)
                        if isinstance(value, str):
                            value = [value]
                        elif not isinstance(value, list):
                            continue
                        for entry in value:
                            self.assertTrue(str(entry).startswith('./'), entry)
                            self.assertTrue((package / entry).exists(), entry)
                    pi = manifest.get('pi', {})
                    for key in ('skills', 'extensions', 'prompts', 'themes'):
                        for entry in (pi.get(key) or []):
                            self.assertTrue((package / entry).exists(), entry)
                    for entry in manifest.get('pi-subagents', {}).get('agents', []):
                        self.assertTrue((package / entry).is_dir(), entry)

    def test_pi_runtime_dependencies_are_declared(self):
        code_flow = json.loads((PLUGINS / 'code-flow' / 'package.json').read_text())
        self.assertIn('pi-subagents', code_flow['dependencies'])
        local_guards = json.loads((PLUGINS / 'local-guards' / 'package.json').read_text())
        self.assertEqual(local_guards['peerDependencies'],
                         {'@earendil-works/pi-coding-agent': '*'})

    def test_claude_marketplace_lists_every_package(self):
        marketplace = json.loads(
            (REPOSITORY / '.claude-plugin/marketplace.json').read_text())
        self.assertEqual(marketplace['name'], 'phil0v')
        entries = marketplace['plugins']
        self.assertEqual({entry['name'] for entry in entries},
                         {path.name for path in PACKAGES})
        for entry in entries:
            with self.subTest(plugin=entry['name']):
                source = entry['source']
                self.assertTrue(source.startswith('./plugins/'), source)
                self.assertTrue((REPOSITORY / source).is_dir(), source)

    def test_pi_root_package_covers_every_skill_directory(self):
        manifest = json.loads((REPOSITORY / 'package.json').read_text())
        listed = {
            path.relative_to(REPOSITORY).as_posix()
            for pattern in manifest['pi']['skills']
            for path in REPOSITORY.glob(pattern.removeprefix('./'))}
        skills = {
            path.parent.relative_to(REPOSITORY).as_posix()
            for base in ('skills', *(f'plugins/{package.name}/skills' for package in PACKAGES))
            for path in (REPOSITORY / base).glob('*/SKILL.md')}
        self.assertEqual(listed, skills)
        for entry in manifest['pi']['extensions']:
            self.assertTrue((REPOSITORY / entry).is_file(), entry)
        for entry in manifest['pi-subagents']['agents']:
            self.assertTrue((REPOSITORY / entry).is_dir(), entry)
        self.assertIn('pi-subagents', manifest['dependencies'])


if __name__ == '__main__':
    unittest.main()
