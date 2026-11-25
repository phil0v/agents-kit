import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

REPOSITORY = Path(__file__).resolve().parents[1]

TOMLLIB_REQUIRED = 'tomllib requires python 3.11+'
YAML_REQUIRED = 'PyYAML is not installed: python3 -m pip install -r requirements.txt'


def repository_files(pattern):
    result = subprocess.run(
        ['git', 'ls-files', '-z'],
        cwd=REPOSITORY, capture_output=True, text=True,
    )
    result.check_returncode()
    return sorted(
        REPOSITORY / path
        for path in result.stdout.split('\0')
        if path and Path(path).match(pattern) and (REPOSITORY / path).exists()
    )


def frontmatter(path):
    text = path.read_text()
    if not text.startswith('---\n'):
        return None
    end = text.find('\n---\n', 4)
    if end < 0:
        raise AssertionError(f'unterminated frontmatter: {path}')
    return yaml.safe_load(text[4:end])


@unittest.skipUnless(yaml is not None, YAML_REQUIRED)
class YamlFrontmatterTest(unittest.TestCase):
    def test_frontmatter_parses_everywhere(self):
        for path in repository_files('*.md'):
            if not path.read_text().startswith('---\n'):
                continue
            with self.subTest(path=str(path.relative_to(REPOSITORY))):
                data = frontmatter(path)
                self.assertIsInstance(data, dict)

    def test_skill_files_declare_matching_name_and_description(self):
        for path in repository_files('SKILL.md'):
            data = frontmatter(path)
            with self.subTest(path=str(path.relative_to(REPOSITORY))):
                self.assertIsInstance(data, dict)
                self.assertEqual(data.get('name'), path.parent.name)
                self.assertIsInstance(data.get('description'), str)
                self.assertTrue(data.get('description'))
                if 'version' not in data:
                    self.assertIsInstance(data.get('argument-hint'), str)
                    self.assertTrue(data.get('argument-hint').strip())

    def test_agent_forms_declare_name_description_and_model(self):
        forms = sorted((REPOSITORY / 'plugins/code-flow/harnesses').glob('*/*.md'))
        self.assertTrue(forms)
        for path in forms:
            data = frontmatter(path)
            with self.subTest(form=str(path.relative_to(REPOSITORY))):
                self.assertIsInstance(data, dict)
                self.assertEqual(data.get('name'), path.stem)
                self.assertIsInstance(data.get('description'), str)
                self.assertIsInstance(data.get('model'), str)


class JsonSyntaxTest(unittest.TestCase):
    def test_every_json_file_parses(self):
        for path in repository_files('*.json'):
            with self.subTest(path=str(path.relative_to(REPOSITORY))):
                json.loads(path.read_text())


class TomlAgentsTest(unittest.TestCase):
    @unittest.skipUnless(tomllib is not None, TOMLLIB_REQUIRED)
    def test_agent_toml_files_parse_with_expected_keys(self):
        agents = sorted(repository_files('plugins/code-flow/roles/*.toml'))
        self.assertTrue(agents)
        for path in agents:
            with self.subTest(path=path.name):
                values = tomllib.loads(path.read_text())
                self.assertEqual(set(values), {
                    'name', 'description', 'model',
                    'model_reasoning_effort', 'developer_instructions',
                })


class ShellSyntaxTest(unittest.TestCase):
    def test_guard_scripts_parse(self):
        for path in sorted((REPOSITORY / 'plugins/local-guards/scripts').glob('*.sh')):
            with self.subTest(path=path.name):
                result = subprocess.run(['bash', '-n', str(path)],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)


class StyleGateTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ruff') is not None, 'ruff is required')
    @unittest.skipUnless((REPOSITORY / '.style-gate.json').is_file(), '.style-gate.json is required')
    def test_repository_passes_its_style_gate(self):
        files = repository_files('*.py')
        self.assertTrue(files)
        gate = REPOSITORY / 'plugins/code-flow/scripts/style-gate.py'
        result = subprocess.run(
            [sys.executable, str(gate), '--repo', str(REPOSITORY), '--check-only', *map(str, files)],
            capture_output=True, text=True,
            env=dict(os.environ, AGENTS_METRICS='off'),
        )
        try:
            report = json.loads(result.stdout)
        except ValueError:
            report = {}
        for record in report.get('files', []):
            for outcome in record.get('tools', []):
                if outcome.get('log_path'):
                    self.addCleanup(Path(outcome['log_path']).unlink, missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stdout or result.stderr)


if __name__ == '__main__':
    unittest.main()
