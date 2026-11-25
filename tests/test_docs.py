import re
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[1]
DOCUMENTS = (REPOSITORY / 'README.md', REPOSITORY / 'docs/install.md')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})')
LINK = re.compile(r'\[[^\]]*\]\(([^\s)]+)\)')
HEADING = re.compile(r'^#{1,6}\s+(.+?)\s*$', re.MULTILINE)


def unfenced_lines(text):
    marker = None
    for number, line in enumerate(text.splitlines(), 1):
        match = FENCE.match(line)
        if match:
            if marker is None:
                marker = match.group(1)[0]
            elif match.group(1)[0] == marker:
                marker = None
            continue
        if marker is None:
            yield number, line


def anchors(path):
    return {
        re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        for heading in HEADING.findall(path.read_text(encoding='utf-8'))
    }


class DocumentationLinksTest(unittest.TestCase):
    def test_relative_links_resolve(self):
        checked = 0
        for document in DOCUMENTS:
            lines = list(unfenced_lines(document.read_text(encoding='utf-8')))
            for number, line in lines:
                for target in LINK.findall(line):
                    if re.match(r'[a-z]+://', target):
                        continue
                    path, _, fragment = target.partition('#')
                    destination = (document.parent / path).resolve() if path else document
                    checked += 1
                    with self.subTest(
                            document=str(document.relative_to(REPOSITORY)),
                            line=number, target=target):
                        self.assertTrue(destination.exists(), target)
                        if fragment and destination.is_file() and destination.suffix == '.md':
                            self.assertIn(fragment, anchors(destination), target)
        self.assertGreater(checked, 10)


if __name__ == '__main__':
    unittest.main()
