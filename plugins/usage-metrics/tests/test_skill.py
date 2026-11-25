import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL = PACKAGE_ROOT / 'skills' / 'metrics-report' / 'SKILL.md'


class MetricsReportSkillTest(unittest.TestCase):
    def test_frontmatter_declares_an_argument_hint(self):
        text = SKILL.read_text(encoding='utf-8')
        self.assertTrue(text.startswith('---\n'))
        end = text.find('\n---\n', 4)
        self.assertGreaterEqual(end, 4)
        hints = [line for line in text[4:end].splitlines()
                 if line.startswith('argument-hint:')]
        self.assertEqual(len(hints), 1)

    def test_layout_exposes_the_report_script_two_levels_above(self):
        script = SKILL.parents[2] / 'scripts' / 'metrics-report.py'
        self.assertTrue(script.is_file())


if __name__ == '__main__':
    unittest.main()
