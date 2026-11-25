import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import RESOURCE, ROOT, frontmatter, load_role


class PackageTests(unittest.TestCase):
    def test_portable_manifest_uses_fixed_skill_discovery(self):
        manifest = json.loads((ROOT / "plugin.json").read_text())
        self.assertEqual(set(manifest) - {"extensions"}, {"$schema", "name", "version", "description"})
        self.assertIsInstance(manifest.get("extensions", {}), dict)
        self.assertEqual(manifest["$schema"], "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json")
        self.assertEqual(manifest["name"], "code-flow")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertTrue(manifest["description"])

    def test_claude_plugin_registers_every_canonical_role(self):
        manifest = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        forms = {"./harnesses/claude/" + path.stem + ".md" for path in (ROOT / "roles").glob("*.toml")}
        self.assertEqual(set(manifest["agents"]), forms)
        self.assertEqual(len(manifest["agents"]), len(forms))

    def test_only_requested_workflows_are_exposed(self):
        skills = {path.parent.name: path for path in (ROOT / "skills").glob("*/SKILL.md")}
        self.assertEqual(set(skills), {"wrap-up", "spec-flow", "spec-new", "spec-batch", "spec-list", "plan-new", "plan-do", "plan-revise", "project-rules"})
        for name, path in skills.items():
            with self.subTest(skill=name):
                data = frontmatter(path)
                self.assertEqual(data.get("name"), name)
                self.assertTrue(data.get("description"))
                self.assertTrue(data.get("argument-hint"))
                self.assertLessEqual(len(data["description"].encode()), 1024)

    def test_agent_resources_keep_supported_models_and_efforts(self):
        expected = {
            "worker": ("gpt-6.2-sol", "high"),
            "reviewer": ("gpt-6.2-sol", "high"),
            "plan-reviewer": ("gpt-6.2-sol", "high"),
            "spec-reviewer": ("gpt-6.2-sol", "high"),
            "style-reviewer": ("gpt-5.6-luna", "medium"),
            "tool-runner": ("gpt-5.6-luna", "low"),
        }
        agents = {path.stem: load_role(path) for path in (ROOT / "roles").glob("*.toml")}
        self.assertEqual(set(agents), set(expected))
        for name, (model, effort) in expected.items():
            with self.subTest(role=name):
                role = agents[name]
                self.assertEqual(role["name"], name)
                self.assertEqual((role["model"], role["model_reasoning_effort"]), (model, effort))
                self.assertTrue(role["developer_instructions"])
                self.assertEqual(set(role), {"name", "description", "model", "model_reasoning_effort", "developer_instructions"})

    def test_resources_resolve_from_a_relocated_skill_path(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "installed plugin with spaces"
            shutil.copytree(ROOT, relocated, ignore=shutil.ignore_patterns("__pycache__"))
            skill = relocated / "skills/wrap-up/SKILL.md"
            self.assert_package_resources(skill, relocated)

    def test_resources_resolve_from_global_links_to_a_separate_package(self):
        with tempfile.TemporaryDirectory() as directory:
            installation = Path(directory) / "global skills with spaces"
            relocated = Path(directory) / "plugin cache with spaces" / "code-flow"
            shutil.copytree(ROOT, relocated, ignore=shutil.ignore_patterns("__pycache__"))
            installation.mkdir()
            for source in (relocated / "skills").iterdir():
                (installation / source.name).symlink_to(source, target_is_directory=True)
            skill = installation / "plan-do" / "SKILL.md"
            self.assertTrue(skill.parent.is_symlink())
            self.assert_package_resources(skill, relocated)

    def assert_package_resources(self, skill, expected_package):
        package = skill.resolve(strict=True).parents[2]
        self.assertEqual(package, expected_package.resolve())
        files = list(package.glob("skills/**/*.md")) + list(package.glob("references/*.md")) + list(package.glob("standards/*.md")) + list(package.glob("roles/*.toml"))
        references = [(path, relative) for path in files for relative in RESOURCE.findall(path.read_text())]
        self.assertTrue(references)
        for source, relative in references:
            with self.subTest(source=source.relative_to(package), resource=relative):
                resource = (package / relative).resolve(strict=True)
                self.assertTrue(resource.is_relative_to(package))
        for relative in (
            "scripts/role-profile.py", "tools/sync-agents.py",
            "skills/plan-new/scripts/plan.py",
            "skills/plan-new/scripts/run_state.py",
            "skills/project-rules/scripts/rules.py",
            "skills/spec-flow/scripts/spec-preflight.py",
            "skills/spec-list/scripts/spec-list.py",
        ):
            with self.subTest(script=relative):
                result = subprocess.run(
                    [sys.executable, "-c", "import runpy, sys; runpy.run_path(sys.argv[1], run_name='packaging_probe')", str(package / relative)],
                    cwd=skill.parent.parent, text=True, capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_relocated_resolver_returns_package_profiles_from_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "installed plugin with spaces"
            shutil.copytree(ROOT, relocated, ignore=shutil.ignore_patterns("__pycache__"))
            skills = Path(directory) / "global skill links"
            skills.mkdir()
            (skills / "plan-do").symlink_to(relocated / "skills/plan-do", target_is_directory=True)
            package = (skills / "plan-do/SKILL.md").resolve(strict=True).parents[2]
            canonical = load_role(package / "roles/reviewer.toml")
            for harness, model, effort in (
                ("codex", "gpt-6.2-sol", "high"),
                ("claude", "opus", "high"),
                ("pi", "openai/gpt-6.2-sol", "high"),
            ):
                with self.subTest(harness=harness):
                    result = subprocess.run(
                        [sys.executable, str(package / "scripts/role-profile.py"), "--harness", harness, "--role", "reviewer"],
                        cwd=directory, text=True, capture_output=True, timeout=20,
                        env=dict(os.environ, AGENTS_METRICS="off"))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    profile = json.loads(result.stdout)
                    self.assertEqual(profile["model"], model)
                    self.assertEqual(profile["effort"], effort)
                    self.assertEqual(profile["effort_policy"], "explicit")
                    self.assertEqual(profile["developer_instructions"], canonical["developer_instructions"])

    def test_relocated_resolver_blocks_missing_canonical_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            relocated = Path(directory) / "incomplete plugin with spaces"
            shutil.copytree(ROOT, relocated, ignore=shutil.ignore_patterns("roles", "__pycache__"))
            skill = relocated / "skills/plan-do/SKILL.md"
            package = skill.resolve(strict=True).parents[2]
            result = subprocess.run(
                [sys.executable, str(package / "scripts/role-profile.py"), "--harness", "codex", "--role", "reviewer"],
                cwd=directory, text=True, capture_output=True, timeout=20,
                env=dict(os.environ, AGENTS_METRICS="off"))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout), {"error": "no canonical roles found"})


if __name__ == "__main__":
    unittest.main()
