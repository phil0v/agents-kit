import re
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]

STANDARD_PAIRS = (
    (
        'symfony',
        'plugins/code-flow/standards/symfony.md',
        'plugins/code-quality/skills/code-reviewer/references/symfony.md',
    ),
    (
        'frontend',
        'plugins/code-flow/standards/frontend.md',
        'plugins/code-quality/skills/code-reviewer/references/frontend.md',
    ),
    (
        'agent-config',
        'plugins/code-flow/standards/agent-config.md',
        'plugins/code-quality/skills/code-reviewer/references/agent-config.md',
    ),
    (
        'test-quality',
        'plugins/code-flow/standards/test-quality.md',
        'plugins/code-quality/skills/code-reviewer/references/test-quality.md',
    ),
    (
        'code-design',
        'plugins/code-flow/standards/code-design.md',
        'plugins/code-quality/references/code-design.md',
    ),
    (
        'code-style',
        'plugins/code-flow/standards/code-style.md',
        'plugins/code-quality/references/code-style.md',
    ),
)

SKIPPED_SECTIONS = frozenset({'## Routing', '## Conventions'})
FENCE_OPEN = re.compile(r'^ {0,3}(`{3,}|~{3,})(.*)$')
LINK_DESTINATION = re.compile(r'\[([^\]]+)\]\([^)]+\)')


def _closing_fence(line, marker, length):
    stripped = line.lstrip(' ')
    if len(line) - len(stripped) > 3:
        return False
    run = len(stripped) - len(stripped.lstrip(marker))
    if run < length:
        return False
    return not stripped[run:].strip(' \t')


def extract_section_items(text):
    sections = {}
    current = ''
    fence = None
    for line in text.splitlines():
        if fence is not None:
            if _closing_fence(line, *fence):
                fence = None
            continue
        match = FENCE_OPEN.match(line)
        if match:
            run, info = match.groups()
            if run[0] != '`' or '`' not in info:
                fence = (run[0], len(run))
                continue
        if line.startswith('## '):
            current = line.strip()
            if current not in SKIPPED_SECTIONS:
                sections.setdefault(current, [])
            continue
        if current in SKIPPED_SECTIONS:
            continue
        stripped = line.strip()
        if stripped.startswith(('- ', '* ', '|')):
            normalized = LINK_DESTINATION.sub(r'[\1]', stripped)
            sections.setdefault(current, []).append(normalized)
    return sections


def _missing_items(source_sections, target_sections, source_path, target_path):
    for section, items in source_sections.items():
        target_items = set(target_sections.get(section, ()))
        for item in items:
            if item not in target_items:
                label = section or '(top)'
                return (
                    f'Missing line in {target_path} (present in {source_path}) '
                    f'under {label}: {item}'
                )
    return None


def assert_pair_parity(left_path, right_path):
    left_sections = extract_section_items(left_path.read_text(encoding='utf-8'))
    right_sections = extract_section_items(right_path.read_text(encoding='utf-8'))
    failure = _missing_items(left_sections, right_sections, left_path, right_path)
    if failure is None:
        failure = _missing_items(right_sections, left_sections, right_path, left_path)
    if failure is not None:
        raise AssertionError(failure)


class StandardsParityTest(unittest.TestCase):
    def test_paired_standards_match(self):
        for name, flow_rel, quality_rel in STANDARD_PAIRS:
            with self.subTest(standard=name):
                assert_pair_parity(REPOSITORY / flow_rel, REPOSITORY / quality_rel)

    def test_parity_check_fails_when_bullet_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            left = root / 'flow.md'
            right = root / 'quality.md'
            removed = '- Second rule'
            full_text = '# Standard\n\n## Rules\n- First rule\n- Second rule\n'
            short_text = '# Standard\n\n## Rules\n- First rule\n'
            for missing_side in ('left', 'right'):
                with self.subTest(missing_side=missing_side):
                    left.write_text(
                        short_text if missing_side == 'left' else full_text,
                        encoding='utf-8',
                    )
                    right.write_text(
                        short_text if missing_side == 'right' else full_text,
                        encoding='utf-8',
                    )
                    with self.assertRaises(AssertionError) as ctx:
                        assert_pair_parity(left, right)
                    message = str(ctx.exception)
                    self.assertIn(removed, message)
                    self.assertIn(str(left), message)
                    self.assertIn(str(right), message)


if __name__ == '__main__':
    unittest.main()
