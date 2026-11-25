import builtins
import io
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from _support import ROOT, load_script_module


SCRIPT = ROOT / "skills/project-rules/scripts/rules.py"
FENCES = ROOT / "skills/project-rules/scripts/markdown_fences.py"
BEGIN = "<!-- project-rules:begin -->"
END = "<!-- project-rules:end -->"
RULES = "docs/project-rules.md"
SUCCESS = "project-rules: layout valid\n"
DONE = "Done: run `make test`."
FRAMEWORKS = "Frameworks: none."
RULES_TEXT = "# Project rules\n\n## Checks\n\n- Run `make test`.\n"


@contextmanager
def observe_paths(opens, probes, on_open, on_probe):
    def wrap(operation, observe):
        def instrument(path=".", *args, **kwargs):
            if isinstance(path, (str, bytes, os.PathLike)):
                observe(Path(os.path.abspath(os.fsdecode(path))))
            return operation(path, *args, **kwargs)

        return instrument

    with ExitStack() as stack:
        for owner, name in opens:
            stack.enter_context(patch.object(owner, name, wrap(getattr(owner, name), on_open)))
        for owner, name in probes:
            stack.enter_context(patch.object(owner, name, wrap(getattr(owner, name), on_probe)))
        yield


OPEN_CALLS = ((builtins, "open"), (io, "open"), (os, "open"))
METADATA_CALLS = tuple((os, name) for name in ("stat", "lstat", "access", "readlink", "listdir", "scandir"))


class ProjectRulesTest(unittest.TestCase):
    def setUp(self):
        self.sandbox = Path(self.enter_temp_dir()).resolve()
        self.root = self.sandbox / "project"
        self.root.mkdir()
        self.external = self.sandbox / "external"
        self.external.mkdir()
        self.index = self.root / "AGENTS.md"
        self.rules = self.root / RULES

    def enter_temp_dir(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return temporary.name

    def write_index(self, lines):
        self.index.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def block(self, *links):
        self.write_index((BEGIN, DONE, FRAMEWORKS, *links, END))

    def populate(self, rules=RULES):
        self.rules = self.root / rules
        self.rules.parent.mkdir(parents=True, exist_ok=True)
        self.rules.write_text(RULES_TEXT, encoding="utf-8")
        self.link = f"[Rules]({rules})"
        self.block(self.link)
        return self.link

    def cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, args)], cwd=self.sandbox, env=env,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            timeout=20, check=False,
        )

    def run_validate(self, *options):
        return self.cli("validate", self.root, *options)

    def assert_valid(self, result):
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, SUCCESS, ""), result.stderr)

    def assert_findings(self, result, *expected):
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        lines = result.stderr.splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("project-rules: "), line)
        for fragment in expected:
            self.assertTrue(any(fragment in line for line in lines), (fragment, lines))

    def assert_lines(self, result, *expected):
        self.assert_findings(result, *expected)
        self.assertEqual(result.stderr.splitlines(), [f"project-rules: {line}" for line in expected])

    def verify_case_folding(self):
        probe = self.root / "Case-Probe"
        alternate = self.root / "case-probe"
        probe.touch(exist_ok=False)
        try:
            if alternate.exists():
                self.assertTrue(probe.samefile(alternate))
                return True
            alternate.touch(exist_ok=False)
            try:
                self.assertFalse(probe.samefile(alternate))
                return False
            finally:
                alternate.unlink()
        finally:
            probe.unlink()

    def test_default_rules_file_and_explicit_override(self):
        self.populate()
        self.assert_valid(self.run_validate())
        (self.root / "alias").symlink_to(self.root / "docs", target_is_directory=True)
        self.assert_valid(self.run_validate("--rules-file", "alias/./project-rules.md"))
        self.assert_findings(self.run_validate("--rules-file", "docs/other.md"), "docs/other.md: missing managed file",
                             "missing index link to docs/other.md", "extra index link")

    def test_rules_file_may_be_at_the_root(self):
        self.populate("project-rules.md")
        self.assert_valid(self.run_validate("--rules-file=project-rules.md"))

    def test_fragments_and_contained_aliases(self):
        self.populate()
        (self.root / "alias.md").symlink_to(self.rules)
        self.block("[rules](alias.md#not-checked)")
        (self.root / "docs" / "only-here.txt").touch()
        (self.root / "docs" / "rule-alias.txt").symlink_to("only-here.txt")
        self.rules.write_text(
            "# Project rules\n\n## Checks\n\n[relative](rule-alias.txt#absent) [a](http://example.test) "
            "[b](https://example.test) [fragment](#fragment) [self](project-rules.md)\n", encoding="utf-8",
        )
        self.assert_valid(self.run_validate())

    def test_in_file_link_rejects_a_symlink_cycle(self):
        self.populate()
        (self.root / "docs" / "cycle.md").symlink_to("cycle.md")
        self.rules.write_text("# Project rules\n\n## Checks\n\n[cycle](cycle.md)\n", encoding="utf-8")
        self.assert_lines(self.run_validate(), f"{RULES}: line 5: invalid link 'cycle.md': too many symbolic links")

    def test_file_links_reject_directory_only_suffixes_before_normalization(self):
        self.populate()
        for suffix in ("/", "/.", "/#section", "/.#section"):
            with self.subTest(suffix=suffix):
                self.block(f"[rules]({RULES}{suffix})")
                self.rules.write_text(f"# Project rules\n\n## Checks\n\n[target](project-rules.md{suffix})\n", encoding="utf-8")
                self.assert_findings(
                    self.run_validate(),
                    f"AGENTS.md: line 4: invalid index link {RULES + suffix!r}: link target is not a regular file",
                    f"{RULES}: line 5: invalid link {('project-rules.md' + suffix)!r}: link target is not a regular file",
                )

    def test_file_links_reject_directory_only_symlink_targets(self):
        self.populate()
        (self.root / "file.md").symlink_to(self.rules)
        (self.root / "chained.md").symlink_to("direct.md")
        direct = self.root / "direct.md"
        for target in (f"{RULES}/", f"{self.rules}/.", "file.md/", "file.md/."):
            with self.subTest(target=target):
                direct.symlink_to(target)
                self.block("[direct](direct.md)", "[chained](chained.md)", self.link)
                self.rules.write_text(
                    "# Project rules\n\n## Checks\n\n[direct](../direct.md)\n[chained](../chained.md)\n",
                    encoding="utf-8",
                )
                result = self.run_validate()
                direct.unlink()
                self.assert_lines(
                    result,
                    "AGENTS.md: line 4: invalid index link 'direct.md': path has a non-directory ancestor",
                    "AGENTS.md: line 5: invalid index link 'chained.md': path has a non-directory ancestor",
                    f"{RULES}: line 5: invalid link '../direct.md': path has a non-directory ancestor",
                    f"{RULES}: line 6: invalid link '../chained.md': path has a non-directory ancestor",
                )

    def test_links_accept_directory_only_symlink_targets_with_a_file_after_them(self):
        self.populate("docs/rules/project-rules.md")
        (self.root / "direct").symlink_to(f"{self.root / 'docs/rules'}/", target_is_directory=True)
        (self.root / "chained").symlink_to("direct/.", target_is_directory=True)
        self.block("[rules](chained/project-rules.md)")
        self.rules.write_text("# Project rules\n\n## Checks\n\n[self](../../chained/project-rules.md)\n", encoding="utf-8")
        self.assert_valid(self.run_validate("--rules-file", "chained/project-rules.md"))

    def test_missing_index_and_bad_markers(self):
        self.populate()
        self.index.unlink()
        self.assert_findings(self.run_validate(), "AGENTS.md: missing managed file")
        for lines, finding in (
            (("# Index",), "missing project-rules markers"),
            ((BEGIN,), "unpaired"),
            ((END,), "unpaired"),
            ((END, BEGIN), "out-of-order"),
            ((BEGIN, BEGIN, END), "duplicate markers"),
            ((BEGIN, END, END), "duplicate markers"),
            ((f"text {BEGIN}", END), "marker must be standalone"),
            ((BEGIN, f"{END} text"), "marker must be standalone"),
        ):
            with self.subTest(lines=lines):
                self.write_index(lines)
                self.assert_findings(self.run_validate(), "AGENTS.md: ", finding)

    def test_index_fences_and_findings_use_physical_lines(self):
        self.populate()
        for separator in ("\u2028", "\f"):
            with self.subTest(separator=separator):
                self.index.write_bytes((
                    f"{BEGIN}\r\n~~~\rtext{separator}~~~\n[masked](hidden.md)\r\n~~~\r\n{self.link}\r\n"
                    f"[visible](missing.md)\r{END}\n"
                ).encode("utf-8"))
                self.assert_lines(self.run_validate(), "AGENTS.md: line 7: invalid index link 'missing.md': missing link target",
                                  'AGENTS.md: line 8: index block has no "Done:" line', 'AGENTS.md: line 8: index block has no "Frameworks:" line')

    def test_rule_fences_and_findings_use_physical_lines(self):
        self.populate()
        for separator in ("\u2028", "\f"):
            with self.subTest(separator=separator):
                self.rules.write_bytes((
                    f"# Project rules\r\n## Checks\r\n~~~\rtext{separator}~~~\n## Masked\r\n~~~\r[visible](missing.md)\n"
                ).encode("utf-8"))
                self.assert_lines(self.run_validate(), f"{RULES}: line 7: invalid link 'missing.md': missing link target")

    def test_missing_canonical_link_and_file(self):
        self.populate()
        self.block()
        self.assert_findings(self.run_validate(), f"missing index link to {RULES}")
        self.populate()
        self.rules.unlink()
        self.assert_findings(self.run_validate(), f"{RULES}: missing managed file")

    def test_missing_directory_or_disallowed_link_targets_in_both_document_types(self):
        self.populate()
        (self.external / "outside.txt").touch()
        (self.root / "outside.txt").symlink_to(self.external / "outside.txt")
        (self.root / "folder").mkdir()
        (self.root / "docs" / "folder").mkdir()
        for index_target, rule_target, finding in (
            ("absent.md", "absent.md", "missing link target"),
            ("absent.md#a", "absent.md#a", "missing link target"),
            ("absent/../AGENTS.md", "absent/../project-rules.md", "missing link target"),
            ("folder", "folder", "link target is not a regular file"),
            ("ftp://example.test/a", "ftp://example.test/a", "disallowed link scheme"),
            ("/absolute.md", "/absolute.md", "absolute or drive-qualified link target"),
            ("C:/absolute.md", "C:/absolute.md", "absolute or drive-qualified link target"),
            ("//host/share/a.md", "//host/share/a.md", "absolute or drive-qualified link target"),
            ("../external/outside.txt", "../../external/outside.txt", "path escapes project root"),
            ("outside.txt", "../outside.txt", "symlink path escapes project root"),
        ):
            with self.subTest(target=index_target):
                self.block(self.link, f"[target]({index_target})")
                self.rules.write_text(f"# Project rules\n\n## Checks\n\n[target]({rule_target})\n", encoding="utf-8")
                self.assert_lines(
                    self.run_validate(),
                    f"AGENTS.md: line 5: invalid index link {index_target!r}: {finding}",
                    f"{RULES}: line 5: invalid link {rule_target!r}: {finding}",
                )

    def test_extra_and_duplicate_index_links(self):
        self.populate()
        (self.root / "extra.txt").touch()
        (self.root / "alias.md").symlink_to(self.rules)
        for link, finding in (
            ("[extra](extra.txt)", "line 5: extra index link 'extra.txt'"),
            ("[extra](http://example.test)", "line 5: invalid index link 'http://example.test': extra index link"),
            ("[extra](https://example.test)", "line 5: invalid index link 'https://example.test': extra index link"),
            ("[extra](#only)", "line 5: invalid index link '#only': extra index link"),
            (self.link, "line 5: duplicate index link 'docs/project-rules.md'"),
            ("[alias](alias.md)", "line 5: duplicate index link 'alias.md'"),
        ):
            with self.subTest(link=link):
                self.block(self.link, link)
                self.assert_lines(self.run_validate(), f"AGENTS.md: {finding}")

    def test_index_link_by_identity(self):
        self.populate()
        (self.root / "alias.md").symlink_to(self.rules)
        self.block("[rules](alias.md)")
        self.assert_valid(self.run_validate())
        (self.root / "other.md").write_text("x\n", encoding="utf-8")
        self.block("[rules](other.md)")
        self.assert_lines(self.run_validate(), "AGENTS.md: line 4: extra index link 'other.md'", f"AGENTS.md: line 5: missing index link to {RULES}")
        self.block()
        self.assert_lines(self.run_validate(), f"AGENTS.md: line 4: missing index link to {RULES}")
        self.block("Done: x", "  Frameworks: y  ")
        self.assert_lines(self.run_validate(), f"AGENTS.md: line 6: missing index link to {RULES}")
        self.write_index((BEGIN, "not Done:", "Frameworks", "Frameworks: yes", END))
        self.assert_lines(self.run_validate(), 'AGENTS.md: line 5: index block has no "Done:" line', f"AGENTS.md: line 5: missing index link to {RULES}")

    def test_bad_rule_structure_and_encoding(self):
        self.populate()
        for content, finding in (
            (b" \t\n", "blank rules file"),
            (b"# Rules\n\xff", "invalid UTF-8"),
            (b"Rules\n=====\n####### Not ATX\n    # Indented\n#\t \n", "line 1: expected exactly one H1 (found none)"),
            (b"# Rules\n## Checks\n   #\tFirst\n# Second\n", "line 4: expected exactly one H1 (found another H1)"),
            (b"# Rules\n## Checks\n ## Duplicate\n## Duplicate \t\n", "line 4: duplicate heading '## Duplicate'"),
            (b"# Rules\n", 'line 1: missing "## Checks" heading'),
            (b"# Rules\n### Checks\n", 'line 1: missing "## Checks" heading'),
        (b"# Rules\n##  Checks\n", 'line 1: missing "## Checks" heading'),
        ):
            with self.subTest(content=content):
                self.rules.write_bytes(content)
                self.assert_findings(self.run_validate(), f"{RULES}: ", finding)

    def test_checks_heading_accepts_closing_hashes(self):
        self.populate()
        self.rules.write_text("# Rules\n\n## Checks ##\n\n- x\n", encoding="utf-8")
        self.assert_findings(self.run_validate(), f"{RULES}: line 1: missing", "## Checks")

    def test_known_h2_headings_must_follow_the_canonical_order(self):
        self.populate()
        self.rules.write_text(
            "# Project rules\n\n## Checks\n\n## Ownership\n\n## Approaches\n\n## Code style\n\n## Design\n\n## Framework\n"
            "\n## Extra\n\n## Appendix\n", encoding="utf-8",
        )
        self.assert_valid(self.run_validate())
        self.rules.write_text("# Project rules\n\n## Framework\n\n## Checks\n\n## Ownership\n\n## Custom\n", encoding="utf-8")
        self.assert_lines(self.run_validate(), f'{RULES}: line 5: "## Checks" must come before "## Framework"',
                          f'{RULES}: line 7: "## Ownership" must come before "## Framework"')
        self.rules.write_text("# Project rules\n\n## Checks\n\n## Design\n\n## Code style\n\n## Framework\n", encoding="utf-8")
        self.assert_lines(self.run_validate(), f'{RULES}: line 7: "## Code style" must come before "## Design"')
        self.rules.write_text("# Project rules\n\n## Framework\n\n## Design\n\n## Code style\n\n## Approaches\n\n## Ownership\n\n## Checks\n", encoding="utf-8")
        self.assert_lines(
            self.run_validate(),
            f'{RULES}: line 5: "## Design" must come before "## Framework"',
            f'{RULES}: line 7: "## Code style" must come before "## Framework"',
            f'{RULES}: line 7: "## Code style" must come before "## Design"',
            f'{RULES}: line 9: "## Approaches" must come before "## Framework"',
            f'{RULES}: line 9: "## Approaches" must come before "## Design"',
            f'{RULES}: line 9: "## Approaches" must come before "## Code style"',
            f'{RULES}: line 11: "## Ownership" must come before "## Framework"',
            f'{RULES}: line 11: "## Ownership" must come before "## Design"',
            f'{RULES}: line 11: "## Ownership" must come before "## Code style"',
            f'{RULES}: line 11: "## Ownership" must come before "## Approaches"',
            f'{RULES}: line 13: "## Checks" must come before "## Framework"',
            f'{RULES}: line 13: "## Checks" must come before "## Design"',
            f'{RULES}: line 13: "## Checks" must come before "## Code style"',
            f'{RULES}: line 13: "## Checks" must come before "## Approaches"',
            f'{RULES}: line 13: "## Checks" must come before "## Ownership"',
        )

    def test_invalid_rules_file_selection(self):
        self.populate()
        (self.root / "escape").symlink_to(self.external, target_is_directory=True)
        (self.root / "not-a-file").touch()
        (self.root / "not-a-dir").mkdir()
        (self.root / "-normalized").mkdir()
        (self.root / "alias").symlink_to("-normalized", target_is_directory=True)
        for rules_file, finding in (
            (str(self.rules), "repository-relative file path"),
            ("C:drive-relative", "repository-relative file path"),
            (r"\\server\share", "repository-relative file path"),
            ("../external/x.md", "escapes project root"),
            ("escape/x.md", "escapes project root"),
            ("not-a-dir", "not a regular file"),
            ("not-a-file/child.md", "non-directory ancestor"),
            ("docs/project-rules.md#v2", "repository-relative file path"),
            ("-rules.md", "repository-relative file path"),
            ("alias/project-rules.md", "normalized file path starts with '-'"),
            ("", "repository-relative file path"),
        ):
            with self.subTest(rules_file=rules_file):
                self.assert_findings(self.run_validate(f"--rules-file={rules_file}"), "rules file ", finding)

    def test_usage_errors(self):
        self.index.touch()
        for args in (
            (), ("validate", self.index), ("validate", self.root, "--rules-file", "-rules"),
            ("validate", self.root, "--rules-dir", "docs"), ("prepare",), ("prepare", self.index),
            ("prepare", self.root, "--draft-dir", "x"), ("bogus", self.root), ("validate", self.root, self.root),
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
                self.assertIn("error:", result.stderr)

    def test_python_version_guard(self):
        module = load_script_module(SCRIPT, "project_rules_guard")
        errors = io.StringIO()
        with patch.object(module.sys, "version_info", (3, 8, 0, "final", 0)), patch("sys.stderr", errors):
            self.assertEqual(module.main(["validate", str(self.root)]), 2)
        self.assertEqual(errors.getvalue(), "error: Python 3.9 or newer is required\n")
        self.assertFalse(list(self.root.iterdir()))

    def test_index_slot_collisions(self):
        for rules_file in ("AGENTS.md", "AGENTS.md/nested.md"):
            with self.subTest(rules_file=rules_file):
                self.assert_findings(self.run_validate(f"--rules-file={rules_file}"), "file/directory collision")
        self.assertFalse(self.index.exists())

    def test_case_folding_storage_rejects_the_absent_index_slot_collision(self):
        if not self.verify_case_folding():
            self.skipTest("fixture filesystem has distinct case-sensitive slots")
        self.assertFalse(self.index.exists())
        self.assert_findings(self.run_validate("--rules-file=agents.md"), "file/directory collision")

    def test_case_sensitive_storage_accepts_distinct_index_and_rules_slots(self):
        if self.verify_case_folding():
            self.skipTest("fixture filesystem folds case")
        self.populate("agents.md")
        self.assertFalse(self.index.samefile(self.rules))
        self.assert_valid(self.run_validate("--rules-file=agents.md"))

    def test_misspelled_rules_file_component_is_rejected_on_every_filesystem(self):
        self.populate("docs/Project-Rules.md")
        self.assert_findings(self.run_validate(), "rules file 'docs/project-rules.md': component 'project-rules.md' differs in case from existing 'Project-Rules.md'")
        self.assert_valid(self.run_validate("--rules-file=docs/Project-Rules.md"))

    def test_managed_slot_differing_from_existing_entry_by_case_only_is_a_finding(self):
        self.populate()
        for path, alternate in ((self.index, "agents.md"), (self.rules, "Project-Rules.md")):
            if path == self.index and not self.verify_case_folding():
                continue
            with self.subTest(path=path.name):
                original = path.read_bytes()
                other = path.with_name(alternate)
                path.unlink()
                other.write_bytes(original)
                try:
                    expected = (f"{path.relative_to(self.root).as_posix()}: case collision with {alternate!r}" if path == self.index
                                else f"rules file {RULES!r}: component {path.name!r} differs in case from existing {alternate!r}")
                    self.assert_findings(self.run_validate(), expected)
                finally:
                    other.unlink()
                    path.write_bytes(original)

    def test_destination_grammar(self):
        self.populate("rules&v2.md")
        self.assert_valid(self.run_validate("--rules-file=rules&v2.md"))
        self.populate("rules&amp;v2.md")
        self.assert_findings(self.run_validate("--rules-file=rules&amp;v2.md"), "destination grammar")

    def test_git_directory_is_reserved(self):
        (self.root / ".git").mkdir()
        self.assert_findings(self.run_validate("--rules-file=.git/x.md"), "rules file '.git/x.md': .git is reserved")
        self.assert_findings(self.run_validate("--rules-file=docs/../.GIT/x.md"), ".git is reserved")

    def test_index_entity_like_file_spellings_are_not_decoded_or_split(self):
        self.populate()
        for link in ("a&amp;b.md", "a&madeup;b.md", "numeric&#38;", "hex&#x26;"):
            with self.subTest(link=link):
                (self.root / link).symlink_to(self.rules)
                self.block(f"[rules]({link})")
                self.assert_findings(self.run_validate(), f"invalid index link {link!r}: destination grammar", f"missing index link to {RULES}")
                (self.root / link).unlink()

    def test_index_code_and_comments_are_opaque(self):
        self.populate()
        for masked in (
            "`[bogus](missing.md)`",
            "<!-- [bogus](missing.md) -->",
            "`` with `\n[bogus](missing.md) <!--\n``",
            "<!-- `\n\n[bogus](missing.md) -->",
        ):
            with self.subTest(masked=masked):
                self.block(self.link, masked)
                self.assert_valid(self.run_validate())
        for wrapper in ("`{}`", "<!-- {} -->", "<!-- `\n\n{}\n-->"):
            with self.subTest(wrapper=wrapper):
                self.block(wrapper.format(self.link))
                self.assert_findings(self.run_validate(), f"missing index link to {RULES}")

    def test_literal_runs_escaped_openers_images_and_separated_link_parts(self):
        self.populate()
        self.block("`", "", self.link, "`", r"\<!--", r"\[escaped](missing.md)", "![image](missing.md)",
                   "[broken]<!-- comment -->(missing.md)", "[bro`code`ken](missing.md)")
        self.assert_valid(self.run_validate())

    def test_rule_code_and_comments_are_opaque(self):
        self.populate()
        for masked in (
            "`[bogus](missing.md)`",
            "<!-- [bogus](missing.md) -->",
            "`` with `\n[bogus](missing.md) <!--\n``",
            "<!-- `\n\n[bogus](missing.md) -->",
            "[broken]<!-- comment -->(missing.md)",
            "[bro<!-- comment -->ken](missing.md)",
            "[bro`code`ken](missing.md)",
            "[spaced] (missing.md)",
            r"\[escaped](missing.md)",
            "![image](missing.md)",
        ):
            with self.subTest(masked=masked):
                self.rules.write_text(f"# Project rules\n\n## Checks\n\n{masked}\n", encoding="utf-8")
                self.assert_valid(self.run_validate())
        self.rules.write_text("# Project rules\n\n## Checks\n\n`[masked](missing.md)` [visible](missing.md)\n", encoding="utf-8")
        self.assert_lines(self.run_validate(), f"{RULES}: line 5: invalid link 'missing.md': missing link target")

    def test_rule_links_skip_fragments_and_external_targets(self):
        self.populate()
        self.rules.write_text(
            "# Project rules\n\n## Checks\n\n[a](#frag) [b](https://example.test/a) [c](HTTP://example.test) [d](#)\n",
            encoding="utf-8",
        )
        self.assert_valid(self.run_validate())

    def test_rule_links_require_a_single_bare_destination(self):
        self.populate()
        (self.root / "docs" / "a b.md").touch()
        (self.root / "docs" / "a.md").touch()
        (self.root / "docs" / "a(b).md").touch()
        for field, finding in (
            ("<a b.md>", "expected a single bare destination"),
            ('a.md "title"', "expected a single bare destination"),
            ("a(b).md)", "expected a single bare destination"),
            ("a.md ", "expected a single bare destination"),
            ("a.md\t", "expected a single bare destination"),
            ("a.md#a", None),
        ):
            with self.subTest(field=field):
                text = f"# Project rules\n\n## Checks\n\n[x]({field})\n"
                self.rules.write_text(text, encoding="utf-8")
                if finding is None:
                    self.assert_valid(self.run_validate())
                else:
                    self.assert_lines(self.run_validate(), f"{RULES}: line 5: {finding}")

    def test_index_images_do_not_count_or_hide_following_links(self):
        self.populate()
        for image in ("![complete](missing.md)", "![nested](part[not-a-link](missing.md))", "![unfinished](missing.md"):
            with self.subTest(image=image):
                self.block(image, self.link)
                self.assert_valid(self.run_validate())

    def test_comments_cannot_overlap_markers_or_cross_fences_inside_block(self):
        self.populate()
        self.write_index(("<!-- comment", BEGIN, self.link, END, "-->"))
        self.assert_findings(self.run_validate(), "HTML comment overlaps a project-rules marker")
        self.write_index((BEGIN, self.link, "<!-- unclosed", END))
        self.assert_findings(self.run_validate(), "unclosed HTML comment before the end marker")
        self.block(self.link, "<!-- comment", "~~~", "code", "~~~", "-->")
        self.assert_findings(self.run_validate(), "unclosed HTML comment in the project-rules block")

    def test_invalid_index_fields_are_not_silently_skipped(self):
        self.populate()
        (self.root / "extra.md").touch()
        for field in ("<extra.md>)", 'extra.md "title")', "extra (nested).md)", r"extra\.md)", "", ")", "extra.md", "extra%20.md)", "extra.md?query)"):
            with self.subTest(field=field):
                self.block(self.link, f"[extra]({field}")
                self.assert_findings(self.run_validate(), "AGENTS.md: line 5: invalid index link")

    def test_nested_destination_is_one_invalid_candidate_even_with_masked_text(self):
        self.populate()
        self.block(self.link, "[outer](x `code` [inner](missing.md))")
        result = self.run_validate()
        self.assert_findings(result, "invalid index link 'x `code` [inner](missing.md)'")
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_fenced_markers_links_and_headings_are_invisible(self):
        self.populate()
        self.write_index(("```md", BEGIN, "[bogus](missing.md)", END, "```", BEGIN, DONE, FRAMEWORKS, self.link, END))
        self.rules.write_text("# Project rules\n\n## Checks\n\n~~~markdown\n# Masked\n# Extra\n[bad](absent)\n~~~\n", encoding="utf-8")
        self.assert_valid(self.run_validate())
        self.write_index(("```md", BEGIN, END, "```"))
        self.assert_findings(self.run_validate(), "missing project-rules markers")

    def test_non_fence_runs_do_not_hide_the_next_link(self):
        self.populate()
        for run in ("```", "~~~"):
            for prefix in ("inline ", "    ", "\t", " \t"):
                with self.subTest(run=run, prefix=prefix):
                    self.block(prefix + run, self.link)
                    self.assert_valid(self.run_validate())
        self.block("```bad`info", self.link)
        self.assert_valid(self.run_validate())

    def test_false_closers_do_not_unmask_and_longer_close_does(self):
        self.populate()
        for char, other in (("`", "~"), ("~", "`")):
            for candidate in ("inline " + char * 4, "    " + char * 4, "\t" + char * 4, " \t" + char * 4, char * 3, other * 4, char * 4 + " trailing"):
                with self.subTest(candidate=candidate):
                    fence = (char * 4 + "example", candidate, "[masked](missing.md)", "   " + char * 5 + "\t")
                    self.block(self.link, *fence)
                    self.assert_valid(self.run_validate())
                    self.block(self.link, *fence, "[visible](missing.md)")
                    self.assert_findings(self.run_validate(), "invalid index link 'missing.md': missing link target")

    def test_unclosed_fence_in_the_index_reports_the_opener_line(self):
        self.populate()
        for run in ("```", "~~~"):
            with self.subTest(run=run):
                self.write_index((BEGIN, DONE, FRAMEWORKS, self.link, END, run, "[masked](missing.md)", BEGIN, END))
                self.assert_lines(self.run_validate(), "AGENTS.md: line 6: unclosed fence")
        self.write_index(("```", "[bogus](missing.md)", BEGIN, END))
        self.assert_lines(self.run_validate(), "AGENTS.md: line 1: unclosed fence")
        self.write_index(("````", "~~~", "[bogus](missing.md)", END, BEGIN))
        self.assert_lines(self.run_validate(), "AGENTS.md: line 1: unclosed fence")
        self.write_index((BEGIN, DONE, FRAMEWORKS, "[bad](missing.md)", END, "```"))
        self.assert_lines(self.run_validate(), "AGENTS.md: line 4: invalid index link 'missing.md': missing link target",
                          f"AGENTS.md: line 5: missing index link to {RULES}", "AGENTS.md: line 6: unclosed fence")

    def test_unclosed_fence_in_the_rules_file_is_masked_through_eof(self):
        self.populate()
        for run in ("```", "~~~"):
            with self.subTest(run=run):
                self.rules.write_text(f"# Project rules\n\n## Checks\n\n{run}example\n# Extra\n[bad](absent)\n", encoding="utf-8")
                self.assert_lines(self.run_validate(), f"{RULES}: line 5: unclosed fence")

    def test_block_budget(self):
        self.populate()
        self.block(self.link, *("filler" for _ in range(8)), "", "   ")
        self.assert_valid(self.run_validate())
        self.block(self.link, *("filler" for _ in range(10)))
        self.assert_lines(self.run_validate(), "AGENTS.md: line 15: index block has 13 nonblank lines; the budget is 12")
        self.block(*("x" for _ in range(18)), self.link)
        self.assert_lines(self.run_validate(), "AGENTS.md: line 23: index block has 21 nonblank lines; the budget is 12")

    def test_block_word_budget(self):
        self.populate()
        filler = " ".join(["word"] * 25)
        self.block(self.link, *([filler] * 5), " ".join(["word"] * 18))
        self.assert_valid(self.run_validate())
        self.block(self.link, *([" ".join(["word"] * 24)] * 6))
        self.assert_lines(self.run_validate(), "AGENTS.md: line 11: index block has 151 words; the budget is 150")
        self.block(*([" ".join(["word"] * 30)] * 6), self.link)
        self.assert_lines(self.run_validate(), "AGENTS.md: line 11: index block has 187 words; the budget is 150")

    def draft_index(self, drafts, *links):
        (drafts / "AGENTS.md").write_text("\n".join(("# Index", BEGIN, DONE, FRAMEWORKS, *links, END)) + "\n", encoding="utf-8")

    def test_draft_dir_replaces_disk_content(self):
        self.populate()
        drafts = self.sandbox / "drafts"
        drafts.mkdir()
        self.rules.write_text("broken\n", encoding="utf-8")
        self.draft_index(drafts, self.link)
        (drafts / self.rules.name).write_text(RULES_TEXT, encoding="utf-8")
        self.assert_valid(self.run_validate("--draft-dir", drafts))
        self.assert_findings(self.run_validate(), f"{RULES}: line 1: expected exactly one H1")
        (drafts / self.rules.name).write_text("broken\n", encoding="utf-8")
        self.rules.write_text(RULES_TEXT, encoding="utf-8")
        self.assert_lines(self.run_validate("--draft-dir", drafts), f"{RULES}: line 1: expected exactly one H1 (found none)",
                          f'{RULES}: line 1: missing "## Checks" heading')
        (drafts / self.rules.name).write_bytes(b"# \xff\n")
        self.assert_lines(self.run_validate("--draft-dir", drafts), f"{RULES}: invalid UTF-8")
        (drafts / self.rules.name).unlink()
        (drafts / self.rules.name).mkdir()
        self.assert_lines(self.run_validate("--draft-dir", drafts), f"{RULES}: not a regular file")
        (drafts / self.rules.name).rmdir()
        self.assert_lines(self.run_validate("--draft-dir", drafts), f"{RULES}: missing draft file")
        (drafts / "AGENTS.md").unlink()
        self.assert_lines(self.run_validate("--draft-dir", drafts), "AGENTS.md: missing draft file", f"{RULES}: missing draft file")

    def test_draft_dir_must_be_an_existing_directory(self):
        self.populate()
        result = self.run_validate("--draft-dir", self.sandbox / "absent")
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("error:", result.stderr)
        result = self.run_validate("--draft-dir", self.index)
        self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_draft_dir_covers_missing_managed_files(self):
        self.populate()
        drafts = self.sandbox / "drafts"
        drafts.mkdir()
        self.rules.unlink()
        self.assert_findings(self.run_validate(), f"{RULES}: missing managed file")
        (drafts / self.rules.name).write_text(RULES_TEXT, encoding="utf-8")
        self.draft_index(drafts, self.link)
        self.assert_valid(self.run_validate("--draft-dir", drafts))
        self.rules.write_text(RULES_TEXT, encoding="utf-8")
        self.index.unlink()
        self.assert_valid(self.run_validate("--draft-dir", drafts))
        self.index.write_text("", encoding="utf-8")
        self.draft_index(drafts)
        self.assert_findings(self.run_validate("--draft-dir", drafts), f"missing index link to {RULES}")

    def test_draft_for_index_to_absent_rules_file(self):
        drafts = self.sandbox / "drafts"
        drafts.mkdir()
        self.draft_index(drafts, f"[Rules]({RULES})")
        (drafts / "project-rules.md").write_text(RULES_TEXT, encoding="utf-8")
        self.assert_valid(self.run_validate("--draft-dir", drafts))
        (drafts / "project-rules.md").unlink()
        self.assert_findings(self.run_validate("--draft-dir", drafts), f"{RULES}: missing draft file")

    def test_managed_symlink_leaves_are_rejected_not_read(self):
        self.populate()
        sentinel = self.external / "sentinel"
        sentinel.write_bytes(b"\xff")
        for path in (self.index, self.rules):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                path.symlink_to(sentinel)
                result = self.run_validate()
                self.assert_findings(result, f"{path.relative_to(self.root).as_posix()}: managed path is a symlink")
                self.assertNotIn("UTF-8", result.stderr)
                self.assertEqual(sentinel.read_bytes(), b"\xff")
                path.unlink()
                path.write_bytes(original)

    def test_contained_leaf_symlink_and_nonregular_slot_are_rejected(self):
        self.populate("docs/a.md")
        second = self.root / "docs/b.md"
        second.write_text(RULES_TEXT, encoding="utf-8")
        self.block("[Rules](docs/b.md)")
        self.assert_valid(self.run_validate("--rules-file", "docs/b.md"))
        second.unlink()
        second.symlink_to(self.root / "docs/a.md")
        (self.root / "docs" / "c.md").mkdir()
        self.assert_findings(self.run_validate("--rules-file", "docs/b.md"), "docs/b.md: managed path is a symlink")
        self.assert_findings(self.run_validate("--rules-file", "docs/c.md"), "rules file 'docs/c.md': not a regular file")

    def test_unreadable_documents_report_read_findings(self):
        validate = load_script_module(SCRIPT, "project_rules").validate
        self.populate()
        for blocked in (self.index, self.rules):
            with self.subTest(blocked=blocked):
                def refuse(path):
                    if path == blocked:
                        raise PermissionError("fixture denies content read")

                with observe_paths(OPEN_CALLS, (), refuse, None):
                    findings = validate(self.root)
                self.assertEqual(findings, [f"{blocked.relative_to(self.root).as_posix()}: cannot read file: fixture denies content read"])

    def test_findings_follow_document_order_then_source_line(self):
        self.populate()
        self.block(self.link, "[bad](first.md)", "[bad](second.md)")
        self.rules.write_text("# Rules\n## Checks\n[bad](one)\n# Another\n[bad](two)\n``` \n", encoding="utf-8")
        result = self.run_validate()
        self.assert_lines(
            result,
            "AGENTS.md: line 5: invalid index link 'first.md': missing link target",
            "AGENTS.md: line 6: invalid index link 'second.md': missing link target",
            f"{RULES}: line 3: invalid link 'one': missing link target",
            f"{RULES}: line 4: expected exactly one H1 (found another H1)",
            f"{RULES}: line 5: invalid link 'two': missing link target",
            f"{RULES}: line 6: unclosed fence",
        )

    def test_slot_checks_run_for_all_slots_before_any_content_open(self):
        module = load_script_module(SCRIPT, "project_rules")
        self.populate()
        sentinel = self.external / "sentinel"
        sentinel.write_bytes(b"\xff")
        actual = []
        with observe_paths(OPEN_CALLS, (), actual.append, None):
            self.assertEqual(module.validate(self.root), [])
        self.assertEqual(actual, [self.index, self.rules])

        def guard(path):
            actual.append(path)
            self.fail(f"content open attempted during preflight: {path}")

        actual.clear()
        alias = self.root / "escape"
        alias.symlink_to(self.external, target_is_directory=True)
        with observe_paths(OPEN_CALLS, (), guard, None):
            self.assertIn("rules file 'escape/x.md': symlink path escapes project root", module.validate(self.root, "escape/x.md"))
        self.assertEqual(actual, [])
        for path in (self.index, self.rules):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.unlink()
                path.symlink_to(sentinel)
                with observe_paths(OPEN_CALLS, (), guard, None):
                    findings = module.validate(self.root)
                self.assertEqual(actual, [])
                self.assertIn(f"{path.relative_to(self.root).as_posix()}: managed path is a symlink", findings)
                path.unlink()
                path.write_bytes(original)

    def test_escaped_links_never_probe_the_resolved_external_target(self):
        validate = load_script_module(SCRIPT, "project_rules").validate
        self.populate()
        sentinel = self.external / "sentinel.md"
        sentinel.write_bytes(b"\xff")
        (self.root / "alias.md").symlink_to(sentinel)
        (self.root / "docs" / "alias.md").symlink_to("../../../external/sentinel.md")
        (self.root / "chained.md").symlink_to("alias.md/.")

        def guard(path):
            if path == self.external or self.external in path.parents:
                probes.append(path)
                self.fail(f"external path probed: {path}")

        for document, target, finding in (
            (self.index, "../external/sentinel.md", "invalid index link"),
            (self.index, "alias.md", "invalid index link"),
            (self.index, "chained.md", "invalid index link"),
            (self.rules, "../../external/sentinel.md", "invalid link"),
            (self.rules, "alias.md", "invalid link"),
            (self.rules, "../chained.md", "invalid link"),
        ):
            with self.subTest(document=document.name, target=target):
                original = document.read_text(encoding="utf-8")
                link = f"[escape]({target})"
                if document == self.index:
                    self.block(self.link, link)
                else:
                    document.write_text(original + link + "\n", encoding="utf-8")
                probes = []
                with observe_paths(OPEN_CALLS, METADATA_CALLS, guard, guard):
                    findings = validate(self.root)
                self.assertEqual(probes, [])
                self.assertEqual(len(findings), 1, findings)
                self.assertIn(f"{finding} {target!r}:", findings[0])
                self.assertIn("escapes project root", findings[0])
                document.write_text(original, encoding="utf-8")
        self.assertEqual(sentinel.read_bytes(), b"\xff")

    def test_unclosed_fence_scanner(self):
        module = runpy.run_path(str(FENCES), run_name="project_rules_fences")
        unclosed = module["unclosed_fence"]
        self.assertIsNone(unclosed(["```", "code", "```\n"]))
        self.assertEqual(unclosed(["# a\n", "~~~\n", "code\n"]), 2)
        self.assertIsNone(unclosed(["```\n", "code\n", "````\n"]))
        for closer in ("``", "~~~", "``` trailing", "    ```", "\t```", "inline ```"):
            with self.subTest(closer=closer):
                self.assertEqual(unclosed(["```\n", "code\n", closer + "\n"]), 1)
        self.assertEqual(unclosed(["```\n", "code\n", "```\n", "~~~\n", "code\n"]), 4)
        self.assertEqual(unclosed(["~~~\n", "```\n"]), 1)
        self.assertEqual(unclosed(["````\n", "```\n"]), 1)
        self.assertEqual(list(module["unfenced_lines"](["~~~\n", "x\n", "~~~\n", "y\n"])), [(4, "y\n")])

    def test_relocated_runpy_load_is_inert_and_does_not_change_import_path(self):
        relocated = self.sandbox / "relocated"
        relocated.mkdir()
        for source in (SCRIPT, FENCES):
            shutil.copyfile(source, relocated / source.name)
        original_path = sys.path[:]
        output, errors = io.StringIO(), io.StringIO()
        with patch("sys.stdout", output), patch("sys.stderr", errors):
            namespace = runpy.run_path(str(relocated / SCRIPT.name))
        self.assertEqual(sys.path, original_path)
        self.assertEqual((output.getvalue(), errors.getvalue()), ("", ""))
        self.assertEqual(sorted(os.listdir(relocated)), ["markdown_fences.py", "rules.py"])
        self.assertEqual(namespace["_unclosed_fence"](["```\n"]), 1)
        self.assertEqual(namespace["validate"](self.root, ""), ["rules file '': expected a repository-relative file path without a leading '-' or '#'"])

    def test_relocated_import_and_usage_error_leave_package_unchanged(self):
        relocated = self.sandbox / "relocated"
        relocated.mkdir()
        for source in (SCRIPT, FENCES):
            shutil.copyfile(source, relocated / source.name)

        def snapshot():
            return {path.relative_to(relocated): path.read_bytes() if path.is_file() else None for path in relocated.rglob("*")}

        original = snapshot()
        script = str(relocated / SCRIPT.name)
        for args, status in (
            (("-c", "import runpy, sys; runpy.run_path(sys.argv[1])", script), 0),
            ((script, "unknown"), 2),
            ((script, "validate"), 2),
        ):
            with self.subTest(args=args):
                result = subprocess.run(
                    [sys.executable, "-E", *args], cwd=self.sandbox, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True, timeout=20, check=False,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(snapshot(), original)

    def test_embedded_drive_component_is_rejected_before_next_probe(self):
        validate = load_script_module(SCRIPT, "project_rules").validate
        (self.root / "docs").mkdir()
        probes = []
        lstat = Path.lstat

        def record(path):
            probes.append(path)
            return lstat(path)

        with patch.object(Path, "lstat", record):
            findings = validate(self.root, "docs/D:rules")
        self.assertEqual(probes, [self.root / "docs"])
        self.assertIn("path component would reset or escape project root", findings[0])

    def test_redirecting_reparse_directory_is_rejected_before_descent(self):
        self.populate()
        validate = load_script_module(SCRIPT, "project_rules").validate
        docs = self.root / "docs"
        lstat = Path.lstat
        probes = []

        def junction(path):
            probes.append(path)
            if path == docs:
                return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
            return lstat(path)

        with patch.object(Path, "lstat", junction):
            findings = validate(self.root)
        self.assertEqual(probes, [docs])
        self.assertIn("redirecting reparse point", findings[0])

    def test_managed_reads_refuse_symlink_replacements_after_preflight(self):
        module = load_script_module(SCRIPT, "project_rules")
        self.populate()
        real_slots = module._slots
        external_file = self.external / "project-rules.md"
        external_file.write_bytes(b"\xff")
        for managed, target in ((self.rules, external_file), (self.root / "docs", self.external)):
            with self.subTest(managed=managed):
                backup = managed.with_name(f"{managed.name}.backup")

                def replace(root, rules_path):
                    result = real_slots(root, rules_path)
                    managed.rename(backup)
                    managed.symlink_to(target, target_is_directory=backup.is_dir())
                    return result

                opened = []
                with patch.object(module, "_slots", replace):
                    with observe_paths(OPEN_CALLS, (), opened.append, None):
                        findings = module.validate(self.root)
                managed.unlink()
                backup.rename(managed)
                self.assertEqual(opened, [self.index])
                self.assertTrue(any("managed path changed after preflight" in finding for finding in findings), findings)

    def test_managed_read_checks_opened_identity_before_decoding(self):
        validate = load_script_module(SCRIPT, "project_rules").validate
        self.populate()
        managed = self.rules
        replacement = self.root / "docs" / "replacement.md"
        replacement.write_bytes(b"\xff")
        real_open = os.open

        def replace_at_open(path, flags, *args, **kwargs):
            if path == managed:
                replacement.replace(managed)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(os, "open", replace_at_open):
            findings = validate(self.root)
        self.assertEqual(findings, [f"{RULES}: cannot read file: managed path changed after preflight"])


def git_environment(home):
    return {"PATH": os.environ["PATH"], "HOME": str(home)}


@unittest.skipUnless(shutil.which("git"), "git required")
@unittest.skipUnless(os.name == "posix", "git worktree fixtures need a POSIX shell")
class PrepareTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.sandbox = Path(temporary.name).resolve()
        self.home = self.sandbox / "home"
        self.home.mkdir()
        self.env = git_environment(self.home)
        version = subprocess.run(["git", "--version"], env=self.env, capture_output=True, text=True, timeout=20, check=False)
        match = re.match(r"git version (\d+)\.(\d+)", version.stdout)
        if version.returncode or not match or (int(match[1]), int(match[2])) < (2, 31):
            self.skipTest("git 2.31 or newer required")
        self.root = self.sandbox / "project"
        self.root.mkdir()
        self.index = self.root / "AGENTS.md"
        self.rules = self.root / RULES
        self.rules.parent.mkdir()
        self.index.write_text(f"# Index\n{BEGIN}\n[Rules]({RULES})\n{END}\n", encoding="utf-8")
        self.rules.write_text(RULES_TEXT, encoding="utf-8")

    def git(self, *argv, cwd):
        return subprocess.run(["git", *argv], cwd=str(cwd), env=self.env, capture_output=True, text=True,
                              timeout=60, check=False)

    def init(self, directory):
        result = self.git("init", "-q", str(directory), cwd=self.sandbox)
        self.assertEqual(result.returncode, 0, result.stderr)
        return directory

    def run_prepare(self, start, *options, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "prepare", str(start), *map(str, options)], cwd=self.sandbox,
            env=env or self.env, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            timeout=60, check=False,
        )

    def assert_report(self, result, **expected):
        self.assertEqual((result.returncode, result.stderr), (0, ""), result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(list(report), sorted(report))
        self.assertEqual(set(report), {"root", "rules_file", "git_dir", "common_dir", "claude_md"})
        self.assertEqual(result.stdout.count("\n"), 1)
        for key, value in expected.items():
            self.assertEqual(report[key], str(value), key)
        return report

    def snapshot(self):
        return {path.relative_to(self.root): (path.is_dir(), path.stat().st_mtime_ns) for path in self.root.rglob("*")}

    def test_nested_start_resolves_the_root_and_writes_nothing(self):
        self.init(self.root)
        self.git("add", ".", cwd=self.root)
        before = self.snapshot()
        scripts_before = {path.relative_to(SCRIPT.parent): path.stat().st_mtime_ns for path in SCRIPT.parent.rglob("*")}
        result = self.run_prepare(self.root / "docs")
        self.assert_report(result, root=self.root, rules_file=RULES, git_dir=self.root / ".git", common_dir=self.root / ".git", claude_md="absent")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path.relative_to(SCRIPT.parent): path.stat().st_mtime_ns for path in SCRIPT.parent.rglob("*")}, scripts_before)

    def test_inherited_git_variables_stop_before_any_git_call(self):
        module = load_script_module(SCRIPT, "project_rules_prepare")

        def refuse(*args, **kwargs):
            self.fail("git called despite inherited variables")

        for variables in ({"GIT_DIR": "x"}, {"GIT_CONFIG": ""}, {"GIT_DIR": "x", "GIT_WORK_TREE": "y", "GIT_CONFIG_COUNT": "1"}):
            with self.subTest(variables=variables), patch.dict(os.environ, variables, clear=False):
                with patch.object(module, "_git", refuse):
                    self.assertEqual(module.prepare(self.root), (None, [f"inherited {','.join(sorted(variables))}; unset them and retry"]))

        def reach_git(*args, **kwargs):
            raise RuntimeError("git reached")

        with patch.dict(os.environ, {"GIT_PAGER": "cat", "GIT_TERMINAL_PROMPT": "0"}), patch.object(module, "_git", reach_git):
            with self.assertRaisesRegex(RuntimeError, "git reached"):
                module.prepare(self.root)

    def test_child_environment_is_allowlisted(self):
        module = load_script_module(SCRIPT, "project_rules_env")
        with patch.dict(os.environ, {"GIT_DIR": "x", "GIT_PAGER": "less", "LC_ALL": "ru_RU.UTF-8", "PATH": "/bin"}):
            self.assertEqual(module._child_env(), {"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile",
                                                   "GIT_CONFIG_VALUE_0": os.devnull, "PATH": "/bin", "HOME": os.environ["HOME"]})
        with patch.dict(os.environ, {"PATH": "/bin"}, clear=True):
            self.assertEqual(module._child_env(), {"LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                                                   "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.excludesFile",
                                                   "GIT_CONFIG_VALUE_0": os.devnull, "PATH": "/bin"})

    def test_non_repository_and_bare_repository_are_findings(self):
        result = self.run_prepare(self.root)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("project-rules: not a Git worktree: ", result.stderr)
        bare = self.sandbox / "bare.git"
        result = self.git("init", "-q", "--bare", str(bare), cwd=self.sandbox)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_prepare(bare)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("not a Git worktree", result.stderr)
        missing = self.run_prepare(self.sandbox / "absent")
        self.assertEqual((missing.returncode, missing.stdout), (2, ""))
        self.assertIn("error:", missing.stderr)

    def test_version_gate_rejects_old_git(self):
        shim = self.sandbox / "shim"
        shim.mkdir()
        (shim / "git").write_text(f"#!{sys.executable}\nimport sys\nprint('git version 2.30.5')\n")
        (shim / "git").chmod(0o755)
        self.init(self.root)
        env = dict(self.env, PATH=f"{shim}{os.pathsep}{self.env['PATH']}")
        result = self.run_prepare(self.root, env=env)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr, "project-rules: git 2.31 or newer is required; found 2.30.5\n")

    def test_missing_git_executable_is_a_finding(self):
        env = dict(self.env, PATH=str(self.sandbox / "empty"))
        result = self.run_prepare(self.root, env=env)
        self.assertEqual(result.stderr, "project-rules: git executable not found on PATH\n")

    def test_linked_worktree_reports_the_worktree_and_the_main_metadata(self):
        main = self.init(self.sandbox / "main")
        self.git("add", ".", cwd=main)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "init", cwd=main)
        linked = self.sandbox / "linked"
        result = self.git("worktree", "add", "-q", str(linked), cwd=main)
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copyfile(self.index, linked / "AGENTS.md")
        (linked / "docs").mkdir()
        shutil.copyfile(self.rules, linked / RULES)
        report = self.assert_report(result=self.run_prepare(linked), root=linked, rules_file=RULES, claude_md="absent")
        self.assertEqual(Path(report["git_dir"]), main / ".git" / "worktrees" / "linked")
        self.assertEqual(Path(report["common_dir"]), main / ".git")

    def test_ignored_rules_file_is_reported_and_the_index_is_not(self):
        self.init(self.root)
        (self.root / ".gitignore").write_text("docs/\n", encoding="utf-8")
        result = self.run_prepare(self.root)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertEqual(result.stderr, f"project-rules: {RULES} is ignored by .gitignore:1 (docs/)\n")
        (self.root / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
        result = self.run_prepare(self.root)
        self.assertEqual(result.stderr, "project-rules: AGENTS.md is ignored by .gitignore:1 (AGENTS.md)\n")
        (self.root / ".gitignore").write_text("*.md\n!docs/project-rules.md\n", encoding="utf-8")
        result = self.run_prepare(self.root)
        self.assertEqual(result.stderr, "project-rules: AGENTS.md is ignored by .gitignore:1 (*.md)\n")
        (self.root / ".gitignore").write_text("docs/*\n!docs/project-rules.md\n", encoding="utf-8")
        self.assert_report(self.run_prepare(self.root), claude_md="absent")

    def test_global_excludes_file_is_not_consulted(self):
        self.init(self.root)
        (self.home / ".config" / "git").mkdir(parents=True)
        (self.home / ".config" / "git" / "ignore").write_text("AGENTS.md\n", encoding="utf-8")
        self.assert_report(self.run_prepare(self.root), claude_md="absent")
        (self.root / ".gitignore").write_text("AGENTS.md\n", encoding="utf-8")
        result = self.run_prepare(self.root)
        self.assertEqual(result.stderr, "project-rules: AGENTS.md is ignored by .gitignore:1 (AGENTS.md)\n")

    def test_rules_file_inside_git_metadata_is_rejected(self):
        self.init(self.root)
        result = self.run_prepare(self.root, "--rules-file", ".git/config")
        self.assertEqual(result.stderr, "project-rules: rules file '.git/config': .git is reserved\n")
        result = self.run_prepare(self.root, "--rules-file", "docs/../.GIT/config")
        self.assertEqual(result.stderr, "project-rules: rules file 'docs/../.GIT/config': .git is reserved\n")
        metadata = self.root / "meta"
        result = self.git("init", "-q", "--separate-git-dir", str(metadata), str(self.root), cwd=self.sandbox)
        self.assertEqual(result.returncode, 0, result.stderr)
        (metadata / "rules.md").write_text(RULES_TEXT, encoding="utf-8")
        (self.root / "link").symlink_to(metadata, target_is_directory=True)
        for rules_file in ("meta/rules.md", "link/rules.md"):
            with self.subTest(rules_file=rules_file):
                result = self.run_prepare(self.root / "docs", "--rules-file", rules_file)
                self.assertEqual((result.returncode, result.stdout), (1, ""))
                self.assertEqual(result.stderr, "project-rules: rules file lies inside Git metadata\n")

    def test_misspelled_rules_file_component_on_case_folding_storage(self):
        self.init(self.root)
        probe = self.root / "Case-Probe"
        alternate = self.root / "case-probe"
        probe.touch()
        try:
            if not alternate.exists():
                self.skipTest("fixture filesystem has distinct case-sensitive slots")
        finally:
            probe.unlink()
        result = self.run_prepare(self.root, "--rules-file", "Docs/project-rules.md")
        self.assertEqual(result.stderr, "project-rules: rules file 'Docs/project-rules.md': component 'Docs' differs in case from existing 'docs'\n")
        result = self.run_prepare(self.root, "--rules-file", "docs/Project-Rules.md")
        self.assertIn("component 'Project-Rules.md' differs in case from existing 'project-rules.md'", result.stderr)

    def test_claude_md_states(self):
        self.init(self.root)
        report = self.assert_report(self.run_prepare(self.root), claude_md="absent")
        claude = self.root / "CLAUDE.md"
        claude.write_text("# Claude\n", encoding="utf-8")
        self.assert_report(self.run_prepare(self.root), claude_md="present")
        claude.write_text("# Claude\n\n  @AGENTS.md  \n", encoding="utf-8")
        self.assert_report(self.run_prepare(self.root), claude_md="imports")
        claude.write_bytes(b"@AGENTS.md\n\xff")
        self.assert_report(self.run_prepare(self.root), claude_md="present")
        claude.unlink()
        claude.symlink_to(self.index)
        self.assert_report(self.run_prepare(self.root), claude_md="unusual")
        claude.unlink()
        claude.mkdir()
        self.assert_report(self.run_prepare(self.root), claude_md="unusual")
        self.assertEqual(json.loads(json.dumps(report)), report)

    def test_prepare_reports_slot_findings(self):
        self.init(self.root)
        self.rules.unlink()
        self.rules.symlink_to(self.index)
        result = self.run_prepare(self.root)
        self.assertEqual(result.stderr, f"project-rules: {RULES}: managed path is a symlink\n")
        result = self.run_prepare(self.root, "--rules-file", "AGENTS.md")
        self.assertEqual(result.stderr, "project-rules: rules file 'AGENTS.md': file/directory collision with AGENTS.md\n")

