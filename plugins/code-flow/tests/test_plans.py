import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROOT, RUN_STATE_SCRIPT, SCRIPT, load_plan_module


plan = load_plan_module()

APP_PY = 'def greet():\n    return "hello"\n\n\ndef flag():\n    return True\n'

VALID_PLAN = """# Greeting

## Context

Behavior lives in `src/app.py`.

## Summary

| File | Change |
|------|--------|
| `src/app.py` | Add a flag call. |

## Steps

1. [ ] Add flag call — wire flag into greet.
   depends on: none
   acceptance: A1
   - Call flag from greet, rests on `src/app.py:1` "def greet():".
   done when: greet calls flag
   test: python3 -m unittest tests.test_app -v
2. [ ] Polish docstring — tidy the module docstring.
   depends on: 1
   acceptance: A2
   creates: src/extra.py
   - Tidy prose.
   done when: docstring reads clean
   test: none — manual review

## Acceptance

- A1. greet calls flag
- A2. docstring is tidy

## Decisions

Sequential execution only.

## Out of scope

Packaging.

## Open questions

None.

## Verification

- python3 -m unittest discover -s tests -v
"""


class PlanTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "my repo"
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "app.py").write_text(APP_PY, encoding="utf-8")
        (self.repo / "plans").mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def write_plan(self, text=VALID_PLAN, name="my plan.md"):
        path = self.repo / "plans" / name
        path.write_text(text, encoding="utf-8")
        return path

    def run_cli(self, *args, cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
        )


class PlanTests(PlanTestBase):
    def parse(self, path=None, text=VALID_PLAN):
        path = path or self.write_plan(text)
        return plan.parse_plan(str(path))

    def codes(self, diagnostics):
        return [d["code"] for d in diagnostics]

    def code_set(self, diagnostics):
        return {d["code"] for d in diagnostics}

    def validate(self, text=VALID_PLAN):
        parsed = self.parse(text=text)
        return plan.validate_plan(parsed, str(self.repo))

    def test_valid_plan_has_no_diagnostics(self):
        self.assertEqual(self.validate(), [])

    def test_parse_returns_sections_steps_and_contracts(self):
        parsed = self.parse()
        self.assertEqual(list(parsed["sections"].keys())[:3], ["Context", "Summary", "Steps"])
        self.assertEqual([s["id"] for s in parsed["steps"]], [1, 2])
        step1, step2 = parsed["steps"]
        self.assertTrue(step1["checkbox"])
        self.assertFalse(step1["done"])
        self.assertEqual(step1["depends_on"], [])
        self.assertEqual(step1["acceptance"], ["A1"])
        self.assertEqual(step1["done_when"], "greet calls flag")
        self.assertEqual(step2["depends_on"], [1])
        self.assertEqual(step2["creates"], ["src/extra.py"])
        self.assertEqual(step2["test"], "none — manual review")
        self.assertNotIn("checklist", parsed)
        self.assertEqual(parsed["acceptance"], ["A1", "A2"])

    def test_bare_heading_fails_missing_step_checkbox(self):
        text = VALID_PLAN.replace("1. [ ] Add flag call", "1. Add flag call")
        diagnostics = self.validate(text)
        self.assertIn("missing_step_checkbox", self.codes(diagnostics))
        self.assertEqual([d["step"] for d in diagnostics if d["code"] == "missing_step_checkbox"], [1])

    def test_ticked_heading_reports_done(self):
        text = VALID_PLAN.replace("1. [ ] Add flag call", "1. [x] Add flag call")
        parsed = self.parse(text=text)
        self.assertTrue(parsed["steps"][0]["done"])
        self.assertFalse(parsed["steps"][1]["done"])

    def test_checklist_section_fails_obsolete_section(self):
        text = VALID_PLAN.replace(
            "## Steps",
            "## Checklist\n\n- [ ] 1. Add flag call\n- [ ] 2. Polish docstring\n\n## Steps",
        )
        diagnostics = self.validate(text)
        self.assertIn("obsolete_section", self.codes(diagnostics))
        self.assertTrue(any("tick step headings" in d["message"] for d in diagnostics if d["code"] == "obsolete_section"))

    def test_out_of_order_sections_fail_section_order(self):
        decisions = "## Decisions\n\nSequential execution only.\n\n"
        out_of_scope = "## Out of scope\n\nPackaging.\n\n"
        text = VALID_PLAN.replace(decisions + out_of_scope, out_of_scope + decisions)
        self.assertIn("section_order", self.codes(self.validate(text)))

    def test_absent_optional_sections_produce_no_diagnostics(self):
        text = VALID_PLAN.replace("## Decisions\n\nSequential execution only.\n\n", "")
        text = text.replace("## Out of scope\n\nPackaging.\n\n", "")
        text = text.replace("## Open questions\n\nNone.\n\n", "")
        self.assertEqual(self.validate(text), [])
        parsed = self.parse(text=text)
        self.assertEqual(parsed["open_questions"], [])

    def test_missing_section_is_reported(self):
        text = VALID_PLAN.replace("## Verification\n\n- python3 -m unittest discover -s tests -v\n", "")
        parsed = self.parse(text=text)
        diagnostics = plan.validate_plan(parsed, str(self.repo))
        self.assertIn("missing_section", self.code_set(diagnostics))
        self.assertTrue(any(d["message"] == "## Verification section is missing" for d in diagnostics))

    def test_fenced_examples_are_not_parsed(self):
        text = VALID_PLAN.replace(
            "Behavior lives in `src/app.py`.",
            "Example plan, not real steps:\n\n"
            "```markdown\n## Steps\n\n9. Fake step\n   depends on: nope\n\n"
            "/etc/passwd and `../escape.py`\n```",
        )
        parsed = self.parse(text=text)
        self.assertEqual([s["id"] for s in parsed["steps"]], [1, 2])
        self.assertEqual(plan.validate_plan(parsed, str(self.repo)), [])

    def test_malformed_dependency_value(self):
        text = VALID_PLAN.replace("depends on: none", "depends on: one")
        self.assertIn("malformed_dependency", self.codes(self.validate(text)))

    def test_unknown_dependency_is_reported(self):
        unknown = VALID_PLAN.replace("depends on: none", "depends on: 9")
        self.assertIn("unknown_dependency", self.codes(self.validate(unknown)))

    def test_self_dependency_is_reported(self):
        self_dep = VALID_PLAN.replace("depends on: 1\n   acceptance: A2", "depends on: 2\n   acceptance: A2")
        self.assertIn("self_dependency", self.codes(self.validate(self_dep)))

    def test_cyclic_dependency_is_reported(self):
        cyclic = VALID_PLAN.replace("depends on: none", "depends on: 2")
        diagnostics = self.validate(cyclic)
        self.assertIn("cyclic_dependency", self.codes(diagnostics))
        self.assertIn(1, [d["step"] for d in diagnostics if d["code"] == "cyclic_dependency"])

    def test_missing_and_duplicate_step_ids(self):
        duplicate = VALID_PLAN.replace("2. [ ] Polish docstring", "1. [ ] Polish docstring")
        self.assertIn("duplicate_step_id", self.codes(self.validate(duplicate)))

        gap = VALID_PLAN.replace("2. [ ] Polish docstring", "3. [ ] Polish docstring")
        gap = gap.replace("depends on: 1\n   acceptance: A2", "depends on: 2\n   acceptance: A2")
        diagnostics = self.validate(gap)
        self.assertIn("missing_step_id", self.codes(diagnostics))
        self.assertIn("unknown_dependency", self.codes(diagnostics))

    def test_acceptance_references(self):
        unknown = VALID_PLAN.replace("acceptance: A1", "acceptance: A9")
        self.assertIn("unknown_acceptance_reference", self.codes(self.validate(unknown)))

        missing = VALID_PLAN.replace("   acceptance: A1\n", "")
        self.assertIn("missing_acceptance_reference", self.codes(self.validate(missing)))

    def test_missing_per_step_contract(self):
        no_done = VALID_PLAN.replace("   done when: greet calls flag\n", "")
        diagnostics = self.validate(no_done)
        self.assertIn("missing_step_contract", self.codes(diagnostics))

        no_test = VALID_PLAN.replace("   test: python3 -m unittest tests.test_app -v\n", "")
        self.assertIn("missing_step_contract", self.codes(self.validate(no_test)))

        bare_none = VALID_PLAN.replace(
            "   test: python3 -m unittest tests.test_app -v",
            "   test: none",
        )
        self.assertIn("missing_step_contract", self.codes(self.validate(bare_none)))

    def test_unresolved_open_question_blocks_attached_step(self):
        text = VALID_PLAN.replace("None.\n\n## Verification", "- Ship it? [step 1]\n\n## Verification")
        diagnostics = self.validate(text)
        blocked = [d for d in diagnostics if d["code"] == "open_question_unresolved"]
        self.assertEqual([d["step"] for d in blocked], [1])

        resolved = text.replace("- Ship it? [step 1]", "- [resolved] Ship it? [step 1]")
        self.assertNotIn("open_question_unresolved", self.codes(self.validate(resolved)))

    def test_absolute_escaping_and_symlinked_paths(self):
        absolute = VALID_PLAN.replace("`src/app.py`", "`/etc/hosts`", 1)
        self.assertIn("absolute_path", self.codes(self.validate(absolute)))

        escaping = VALID_PLAN.replace("`src/app.py`", "`../outside.md`", 1)
        self.assertIn("escaping_path", self.codes(self.validate(escaping)))

        outside = Path(self.tmp.name) / "outside.py"
        outside.write_text("x = 1\n", encoding="utf-8")
        (self.repo / "src" / "link.py").symlink_to(outside)
        linked = VALID_PLAN.replace("creates: src/extra.py", "creates: src/extra.py\n   - Also reads `src/link.py`.")
        self.assertIn("symlink_escape", self.codes(self.validate(linked)))

    def test_new_file_must_be_declared_as_creation(self):
        undeclared = VALID_PLAN.replace(
            "   - Tidy prose.",
            "   - Tidy prose in `src/brand_new.py`.",
        )
        self.assertIn("missing_file", self.codes(self.validate(undeclared)))

        declared = VALID_PLAN.replace("creates: src/extra.py", "creates: src/extra.py, src/brand_new.py")
        declared = declared.replace(
            "   - Tidy prose.",
            "   - Tidy prose in `src/brand_new.py`.",
        )
        self.assertEqual(self.validate(declared), [])

    def test_deleted_file_is_reported(self):
        path = self.write_plan()
        (self.repo / "src" / "app.py").unlink()
        parsed = plan.parse_plan(str(path))
        self.assertIn("missing_file", self.code_set(plan.validate_plan(parsed, str(self.repo))))

    def test_anchors_follow_moved_quotes(self):
        path = self.write_plan()
        (self.repo / "src" / "app.py").write_text("# header\n" + APP_PY, encoding="utf-8")
        parsed = plan.parse_plan(str(path))
        self.assertEqual(plan.check_anchors(parsed, str(self.repo), [1, 2]), [])

    def test_utf8_bom_changes_neither_title_nor_digest(self):
        plain_path = self.write_plan(VALID_PLAN, name="plain.md")
        bom_path = self.repo / "plans" / "bom.md"
        bom_path.write_bytes(b"\xef\xbb\xbf" + VALID_PLAN.encode("utf-8"))
        plain = plan.parse_plan(str(plain_path))
        bom = plan.parse_plan(str(bom_path))
        self.assertEqual(bom["title"], "Greeting")
        self.assertEqual(bom["title"], plain["title"])
        self.assertEqual(plan.plan_digest(bom), plan.plan_digest(plain))
        self.assertEqual(plan.validate_plan(bom, str(self.repo)), [])

    def test_single_quoted_anchor(self):
        source = 'def greet(name="world"):\n    return "hello"\n'
        (self.repo / "src" / "app.py").write_text(source, encoding="utf-8")
        text = VALID_PLAN.replace(
            "   - Call flag from greet, rests on `src/app.py:1` \"def greet():\".",
            "   - Call flag from greet, rests on `src/app.py:1` 'def greet(name=\"world\"):'.",
        )
        parsed = self.parse(text=text)
        self.assertEqual(plan.check_anchors(parsed, str(self.repo), [1]), [])
        changed = source.replace('"world"', '"universe"')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_missing"])

    def test_single_quoted_anchor_cut_by_apostrophe_reports_unparsable(self):
        source = 'def greet(name="world"):\n    return "it\'s hello"\n'
        (self.repo / "src" / "app.py").write_text(source, encoding="utf-8")
        text = VALID_PLAN.replace(
            "   - Call flag from greet, rests on `src/app.py:1` \"def greet():\".",
            "   - Call flag from greet, rests on `src/app.py:2` 'return \"it's hello\"'.",
        )
        parsed = self.parse(text=text)
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_unparsable"])
        self.assertEqual(diagnostics[0]["step"], 1)
        self.assertIn("other quote form", diagnostics[0]["message"])
        self.assertIn("neighbouring line", diagnostics[0]["message"])

    def test_cut_anchor_followed_by_digit_reports_unparsable(self):
        source = 'x = "a\'1"\n'
        (self.repo / "src" / "app.py").write_text(source, encoding="utf-8")
        text = VALID_PLAN.replace(
            "   - Call flag from greet, rests on `src/app.py:1` \"def greet():\".",
            "   - Call flag from greet, rests on `src/app.py:1` 'x = \"a'1\"'.",
        )
        parsed = self.parse(text=text)
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_unparsable"])

    def test_double_quoted_anchor_still_works(self):
        parsed = self.parse()
        self.assertEqual(plan.check_anchors(parsed, str(self.repo), [1]), [])

    def test_anchor_missing_when_quote_changes(self):
        path = self.write_plan()
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        parsed = plan.parse_plan(str(path))
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_missing"])
        self.assertEqual(diagnostics[0]["step"], 1)

    def test_anchor_ambiguous_when_quote_matches_several_lines(self):
        (self.repo / "src" / "dup.py").write_text("dup\nother\ndup\n", encoding="utf-8")
        text = VALID_PLAN.replace(
            "   - Call flag from greet, rests on `src/app.py:1` \"def greet():\".",
            "   - Call flag from greet, rests on `src/dup.py:2` \"dup\".",
        )
        path = self.write_plan(text)
        parsed = plan.parse_plan(str(path))
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_ambiguous"])

    def test_anchor_source_missing_for_deleted_file(self):
        path = self.write_plan()
        (self.repo / "src" / "app.py").unlink()
        parsed = plan.parse_plan(str(path))
        diagnostics = plan.check_anchors(parsed, str(self.repo), [1])
        self.assertEqual(self.codes(diagnostics), ["anchor_source_missing"])

    def test_unselected_step_anchors_are_not_checked(self):
        path = self.write_plan()
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        parsed = plan.parse_plan(str(path))
        self.assertEqual(plan.check_anchors(parsed, str(self.repo), [2]), [])

    def test_digest_ignores_progress_and_line_numbers(self):
        base = plan.plan_digest(self.parse())
        ticked = plan.plan_digest(self.parse(text=VALID_PLAN.replace("1. [ ] Add flag call", "1. [x] Add flag call")))
        moved = plan.plan_digest(self.parse(text=VALID_PLAN.replace("`src/app.py:1`", "`src/app.py:9`")))
        self.assertEqual(base, ticked)
        self.assertEqual(base, moved)

    def test_digest_changes_on_semantic_edit(self):
        base = plan.plan_digest(self.parse())
        edited = plan.plan_digest(
            self.parse(text=VALID_PLAN.replace("done when: greet calls flag", "done when: greet returns a flag"))
        )
        self.assertNotEqual(base, edited)

    def test_dependency_order_topological(self):
        reversed_plan = VALID_PLAN.replace(
            "1. [ ] Add flag call — wire flag into greet.\n   depends on: none",
            "1. [ ] Add flag call — wire flag into greet.\n   depends on: 2",
        ).replace(
            "2. [ ] Polish docstring — tidy the module docstring.\n   depends on: 1",
            "2. [ ] Polish docstring — tidy the module docstring.\n   depends on: none",
        )
        parsed = self.parse(text=reversed_plan)
        self.assertEqual(plan.dependency_order(parsed), [2, 1])

        cyclic = VALID_PLAN.replace("depends on: none", "depends on: 2")
        parsed_cyclic = self.parse(text=cyclic)
        self.assertIsNone(plan.dependency_order(parsed_cyclic))

    def test_in_place_verification_step_validates_clean(self):
        text = VALID_PLAN.replace(
            "2. [ ] Polish docstring — tidy the module docstring.\n   depends on: 1\n   acceptance: A2\n   creates: src/extra.py\n   - Tidy prose.\n   done when: docstring reads clean\n   test: none — manual review",
            "2. [ ] Verify greeting unchanged — confirm existing behavior still holds.\n   depends on: 1\n   acceptance: A2\n   - No code change needed; verify `src/app.py:1` \"def greet():\".\n   done when: existing greet behavior is confirmed\n   test: none — verification by inspection",
        )
        parsed = self.parse(text=text)
        self.assertEqual(parsed["steps"][1]["id"], 2)
        self.assertEqual(plan.validate_plan(parsed, str(self.repo)), [])

    def test_appended_undo_step_keeps_consecutive_ids(self):
        text = """# Greeting

## Context

Behavior lives in `src/app.py`.

## Summary

| File | Change |
|------|--------|
| `src/app.py` | Add and revert flag call. |

## Steps

1. [ ] Add flag call — wire flag into greet.
   depends on: none
   acceptance: A1
   - Call flag from greet, rests on `src/app.py:1` "def greet():".
   done when: greet calls flag
   test: python3 -m unittest tests.test_app -v
2. [ ] Polish docstring — tidy the module docstring.
   depends on: 1
   acceptance: A2
   creates: src/extra.py
   - Tidy prose.
   done when: docstring reads clean
   test: none — manual review
3. [ ] Revert flag call — remove the flag call if it proves unnecessary.
   depends on: 1
   acceptance: A3
   - Remove flag call from greet, rests on `src/app.py:1` "def greet():".
   done when: greet no longer calls flag
   test: python3 -m unittest tests.test_app -v

## Acceptance

- A1. greet calls flag
- A2. docstring is tidy
- A3. undo step is present

## Decisions

Sequential execution only.

## Out of scope

Packaging.

## Open questions

None.

## Verification

- python3 -m unittest discover -s tests -v
"""
        parsed = self.parse(text=text)
        self.assertEqual([s["id"] for s in parsed["steps"]], [1, 2, 3])
        self.assertEqual(plan.validate_plan(parsed, str(self.repo)), [])


class PlanCliTests(PlanTestBase):
    def test_validate_exits_zero_for_valid_plan(self):
        path = self.write_plan()
        result = self.run_cli("validate", str(path))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["diagnostics"], [])
        self.assertEqual(len(payload["digest"]), 64)

    def test_validate_exits_one_for_invalid_plan(self):
        path = self.write_plan(VALID_PLAN.replace("depends on: none", "depends on: 9"))
        result = self.run_cli("validate", str(path))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["diagnostics"][0]["code"], "unknown_dependency")

    def test_validate_steps_scopes_unresolved_open_question(self):
        text = VALID_PLAN.replace("None.\n\n## Verification", "- Ship it? [step 1]\n\n## Verification")
        path = self.write_plan(text)
        result = self.run_cli("validate", str(path))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual([d["step"] for d in payload["diagnostics"] if d["code"] == "open_question_unresolved"], [1])

        scoped = self.run_cli("validate", str(path), "--steps", "2")
        self.assertEqual(scoped.returncode, 0)
        self.assertEqual(json.loads(scoped.stdout)["diagnostics"], [])

    def test_validate_steps_rejects_malformed_step_list(self):
        path = self.write_plan()
        result = self.run_cli("validate", str(path), "--steps", "1,x")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_validate_steps_rejects_unknown_step_id(self):
        path = self.write_plan()
        result = self.run_cli("validate", str(path), "--steps", "1,9")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")
        self.assertIn("9", payload["error"]["message"])

    def test_missing_plan_file_exits_two(self):
        result = self.run_cli("validate", str(self.repo / "plans" / "nope.md"))
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "io_error")

    def test_unknown_command_exits_two(self):
        result = self.run_cli("frobnicate")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_inspect_reports_dependency_order(self):
        reversed_plan = VALID_PLAN.replace(
            "1. [ ] Add flag call — wire flag into greet.\n   depends on: none",
            "1. [ ] Add flag call — wire flag into greet.\n   depends on: 2",
        ).replace(
            "2. [ ] Polish docstring — tidy the module docstring.\n   depends on: 1",
            "2. [ ] Polish docstring — tidy the module docstring.\n   depends on: none",
        )
        path = self.write_plan(reversed_plan)
        result = self.run_cli("inspect", str(path))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        orders = {step["id"]: step["order"] for step in payload["steps"]}
        self.assertEqual(orders, {2: 0, 1: 1})

    def test_inspect_reports_per_step_done_and_no_checklist(self):
        path = self.write_plan(VALID_PLAN.replace("1. [ ] Add flag call", "1. [x] Add flag call"))
        result = self.run_cli("inspect", str(path))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertNotIn("checklist", payload)
        done = {step["id"]: step["done"] for step in payload["steps"]}
        self.assertEqual(done, {1: True, 2: False})

    def test_inspect_to_init_refuses_checked_steps_without_durable_state(self):
        state_root = Path(self.tmp.name) / "state"
        for text in (
            VALID_PLAN.replace("1. [ ] Add flag call", "1. [x] Add flag call"),
            VALID_PLAN.replace("[ ]", "[x]"),
        ):
            with self.subTest(text=text):
                path = self.write_plan(text)
                inspected = self.run_cli("inspect", str(path))
                self.assertEqual(inspected.returncode, 0, inspected.stderr)
                steps = json.loads(inspected.stdout)["steps"]
                request = {
                    "identity": {
                        "worktree_root": str(self.repo), "git_dir": str(self.repo / ".git"),
                        "branch": "master", "head": "", "plan_realpath": str(path.resolve()),
                    },
                    "step_ids": [step["id"] for step in steps],
                    "step_dependencies": {str(step["id"]): step["depends_on"] for step in steps},
                }
                for attempted in (
                    request,
                    request | {"step_ids": [step["id"] for step in steps if not step["done"]], "step_dependencies": {}},
                ):
                    result = subprocess.run(
                        [sys.executable, str(RUN_STATE_SCRIPT), "--state-root", str(state_root), "init"],
                        input=json.dumps(attempted),
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(json.loads(result.stderr)["error"]["code"], "completion_state_missing")
                    self.assertEqual(list(state_root.glob("*.json")), [])

    def initialize_plan_state(self):
        path = self.write_plan()
        inspected = self.run_cli("inspect", str(path))
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        steps = json.loads(inspected.stdout)["steps"]
        state_root = Path(self.tmp.name) / "state"
        request = {
            "identity": {
                "worktree_root": str(self.repo), "git_dir": str(self.repo / ".git"),
                "branch": "master", "head": "", "plan_realpath": str(path.resolve()),
            },
            "step_ids": [step["id"] for step in steps],
            "step_dependencies": {str(step["id"]): step["depends_on"] for step in steps},
        }
        initialized = subprocess.run(
            [sys.executable, str(RUN_STATE_SCRIPT), "--state-root", str(state_root), "init"],
            input=json.dumps(request), capture_output=True, text=True,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        state_path = next(state_root.glob("*.json"))
        return path, state_root, request, state_path

    def test_inspect_to_init_keeps_all_steps_and_dependencies_pending(self):
        _, _, _, state_path = self.initialize_plan_state()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual({step_id: step["status"] for step_id, step in state["steps"].items()},
                         {"1": "pending", "2": "pending"})
        self.assertEqual(state["steps"]["2"]["depends_on"], ["1"])
        self.assertTrue(all(not step["evidence"] for step in state["steps"].values()))

    def test_existing_state_remains_authoritative_after_plan_checkbox_changes(self):
        path, state_root, request, state_path = self.initialize_plan_state()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        before = state_path.read_bytes()
        path.write_text(VALID_PLAN.replace("1. [ ] Add flag call", "1. [x] Add flag call"), encoding="utf-8")
        shown = subprocess.run(
            [sys.executable, str(RUN_STATE_SCRIPT), "--state-root", str(state_root), "show"],
            input=json.dumps({"identity": request["identity"]}), capture_output=True, text=True,
        )
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["steps"], state["steps"])
        self.assertEqual(state_path.read_bytes(), before)

    def test_inspect_returns_plan_title(self):
        path = self.write_plan()
        result = self.run_cli("inspect", str(path))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["title"], "Greeting")

    def test_openspec_repo_refused_with_stable_code(self):
        (self.repo / "openspec").mkdir()
        path = self.write_plan()
        for args in (("validate", str(path)), ("inspect", str(path)), ("anchors", str(path))):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 1, args)
            payload = json.loads(result.stdout)
            codes = [d["code"] for d in payload["diagnostics"]]
            self.assertIn("openspec_project", codes, args)
            diagnostic = payload["diagnostics"][codes.index("openspec_project")]
            self.assertEqual(diagnostic["path"], str((self.repo / "openspec").resolve()))
            self.assertIn(str(ROOT / "skills" / "spec-flow" / "SKILL.md"), diagnostic["message"])
            self.assertIn("plugins/code-flow/skills/spec-flow/SKILL.md", diagnostic["message"])

    def test_openspec_repo_select_refused_with_stable_code(self):
        (self.repo / "openspec").mkdir()
        self.write_plan(name="only.md")
        result = self.run_cli("select", str(self.repo))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual([d["code"] for d in payload["diagnostics"]], ["openspec_project"])

    def test_non_openspec_repo_has_no_openspec_diagnostic(self):
        path = self.write_plan()
        for args in (("validate", str(path)), ("inspect", str(path)), ("anchors", str(path)), ("select", str(self.repo))):
            result = self.run_cli(*args)
            payload = json.loads(result.stdout)
            codes = [d["code"] for d in payload["diagnostics"]]
            self.assertNotIn("openspec_project", codes, args)

    def test_select_single_candidate(self):
        self.write_plan(name="only.md")
        result = self.run_cli("select", str(self.repo))
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["selected"], "plans/only.md")
        self.assertEqual(payload["candidates"], ["plans/only.md"])

    def test_select_excludes_root_and_ticket_archives(self):
        self.write_plan(name="only.md")
        root_archive = self.repo / "plans" / "archive"
        ticket_archive = self.repo / "plans" / "TICKET-1" / "archive"
        root_archive.mkdir()
        ticket_archive.mkdir(parents=True)
        (root_archive / "2026-11-06-finished.md").write_text(VALID_PLAN, encoding="utf-8")
        (ticket_archive / "2026-11-06-finished.md").write_text(VALID_PLAN, encoding="utf-8")

        result = self.run_cli("select", str(self.repo))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["candidates"], ["plans/only.md"])

    def test_select_without_active_plans_excludes_archives(self):
        archive = self.repo / "plans" / "archive"
        archive.mkdir()
        (archive / "2026-11-06-finished.md").write_text(VALID_PLAN, encoding="utf-8")

        result = self.run_cli("select", str(self.repo))

        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["candidates"], [])
        self.assertIsNone(json.loads(result.stdout)["selected"])

    def test_relocated_select_excludes_archives_and_keeps_ticket_plans(self):
        package = SCRIPT.parents[3]
        relocated = Path(self.tmp.name) / "installed package with spaces"
        shutil.copytree(package, relocated, ignore=shutil.ignore_patterns("__pycache__"))
        root_archive = self.repo / "plans" / "archive"
        ticket = self.repo / "plans" / "TICKET-1"
        root_archive.mkdir()
        (ticket / "archive").mkdir(parents=True)
        (root_archive / "2026-11-06-finished.md").write_text(VALID_PLAN, encoding="utf-8")
        (ticket / "archive" / "2026-11-06-finished.md").write_text(VALID_PLAN, encoding="utf-8")
        (ticket / "active.md").write_text(VALID_PLAN, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(relocated / "skills/plan-new/scripts/plan.py"),
             "select", str(self.repo)],
            cwd=relocated, capture_output=True, text=True, timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["selected"], "plans/TICKET-1/active.md")
        self.assertEqual(json.loads(result.stdout)["candidates"], ["plans/TICKET-1/active.md"])

    def test_archived_plans_remain_valid_and_inspectable(self):
        completed = VALID_PLAN.replace("[ ]", "[x]")
        original = self.write_plan(completed, name="finished.md")
        original_digest = json.loads(self.run_cli("inspect", str(original)).stdout)["digest"]
        archive = self.repo / "plans" / "archive"
        archive.mkdir()
        archived = archive / "2026-11-06-finished.md"
        original.rename(archived)

        validation = self.run_cli("validate", str(archived))
        inspection = self.run_cli("inspect", str(archived))

        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertEqual(json.loads(validation.stdout)["diagnostics"], [])
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        payload = json.loads(inspection.stdout)
        self.assertEqual(payload["digest"], original_digest)
        self.assertEqual([step["done"] for step in payload["steps"]], [True, True])

    def test_select_lists_multiple_candidates(self):
        self.write_plan(name="b plan.md")
        self.write_plan(name="a plan.md")
        ticket = self.repo / "plans" / "TICKET-1"
        ticket.mkdir()
        (ticket / "c plan.md").write_text(VALID_PLAN, encoding="utf-8")
        result = self.run_cli("select", str(self.repo))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["selected"])
        self.assertEqual(
            payload["candidates"],
            ["plans/TICKET-1/c plan.md", "plans/a plan.md", "plans/b plan.md"],
        )

    def test_select_without_candidates(self):
        result = self.run_cli("select", str(self.repo))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["candidates"], [])

    def test_anchors_command_checks_only_requested_steps(self):
        path = self.write_plan()
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")

        all_steps = self.run_cli("anchors", str(path))
        self.assertEqual(all_steps.returncode, 1)
        payload = json.loads(all_steps.stdout)
        self.assertEqual(payload["diagnostics"][0]["code"], "anchor_missing")

        remaining = self.run_cli("anchors", str(path), "--steps", "2")
        self.assertEqual(remaining.returncode, 0)
        self.assertEqual(json.loads(remaining.stdout)["diagnostics"], [])

    def test_anchors_command_rejects_malformed_step_list(self):
        path = self.write_plan()
        result = self.run_cli("anchors", str(path), "--steps", "1,x")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_anchors_command_rejects_unknown_step_id(self):
        path = self.write_plan()
        result = self.run_cli("anchors", str(path), "--steps", "9")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")
        self.assertIn("9", payload["error"]["message"])

    def test_anchors_command_rejects_extra_argument(self):
        path = self.write_plan()
        result = self.run_cli("anchors", str(path), "extra")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")

    def test_anchor_unreadable_source_returns_io_error(self):
        if os.getuid() == 0:
            self.skipTest("chmod blocking does not apply when running as root")
        (self.repo / "src" / "secret.py").write_text("x = 1\n", encoding="utf-8")
        os.chmod(self.repo / "src" / "secret.py", 0o000)
        self.addCleanup(os.chmod, self.repo / "src" / "secret.py", 0o644)
        text = VALID_PLAN.replace(
            "   - Call flag from greet, rests on `src/app.py:1` \"def greet():\".",
            "   - Call flag from greet, rests on `src/secret.py:1` \"x = 1\".",
        )
        path = self.write_plan(text)
        result = self.run_cli("anchors", str(path), "--steps", "1")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["diagnostics"][0]["code"], "io_error")

    def test_remaining_step_anchor_mismatch_needs_amendment(self):
        path = self.write_plan()
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        result = self.run_cli("anchors", str(path), "--steps", "1")
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["diagnostics"][0]["code"], "anchor_missing")
        self.assertEqual(payload["diagnostics"][0]["step"], 1)

    def test_preflight_runs_stages_in_order(self):
        path = self.write_plan()
        result = self.run_cli("preflight", str(path))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([stage["stage"] for stage in payload["stages"]], ["openspec", "validate", "anchors"])
        self.assertTrue(all(stage["diagnostics"] == [] for stage in payload["stages"]))
        self.assertEqual(len(payload["digest"]), 64)

    def test_preflight_short_circuits_on_openspec_refusal(self):
        (self.repo / "openspec").mkdir()
        path = self.write_plan(VALID_PLAN.replace("depends on: none", "depends on: 9"))
        result = self.run_cli("preflight", str(path))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual([stage["stage"] for stage in payload["stages"]], ["openspec"])
        self.assertEqual(payload["stages"][0]["diagnostics"][0]["code"], "openspec_project")

    def test_preflight_short_circuits_on_validation_failure(self):
        path = self.write_plan(VALID_PLAN.replace("depends on: none", "depends on: 9"))
        result = self.run_cli("preflight", str(path))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual([stage["stage"] for stage in payload["stages"]], ["openspec", "validate"])
        self.assertEqual(payload["stages"][1]["diagnostics"][0]["code"], "unknown_dependency")

    def test_preflight_reaches_anchors_after_clean_validation(self):
        path = self.write_plan()
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        result = self.run_cli("preflight", str(path))
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertEqual([stage["stage"] for stage in payload["stages"]], ["openspec", "validate", "anchors"])
        self.assertEqual(payload["stages"][2]["diagnostics"][0]["code"], "anchor_missing")

    def test_preflight_steps_scopes_open_questions_and_anchors(self):
        text = VALID_PLAN.replace("None.\n\n## Verification", "- Ship it? [step 1]\n\n## Verification")
        path = self.write_plan(text)
        changed = APP_PY.replace('def greet():', 'def hello():')
        (self.repo / "src" / "app.py").write_text(changed, encoding="utf-8")
        scoped = self.run_cli("preflight", str(path), "--steps", "2")
        self.assertEqual(scoped.returncode, 0, scoped.stderr)
        payload = json.loads(scoped.stdout)
        self.assertTrue(all(stage["diagnostics"] == [] for stage in payload["stages"]))

        full = self.run_cli("preflight", str(path))
        self.assertEqual(full.returncode, 1)
        stages = json.loads(full.stdout)["stages"]
        self.assertEqual([stage["stage"] for stage in stages], ["openspec", "validate"])
        codes = {d["code"] for stage in stages for d in stage["diagnostics"]}
        self.assertEqual(codes, {"open_question_unresolved"})

    def test_preflight_rejects_malformed_step_list(self):
        path = self.write_plan()
        result = self.run_cli("preflight", str(path), "--steps", "1,x")
        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["error"]["code"], "usage")



if __name__ == "__main__":
    unittest.main()
