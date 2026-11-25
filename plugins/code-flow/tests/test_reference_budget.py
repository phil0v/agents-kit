import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _support import ROOT

CLOSEOUT_WORD_LIMIT = 7200


class ReferenceBudgetTests(unittest.TestCase):
    def test_closeout_reference_stays_within_word_budget(self):
        path = ROOT / "references" / "closeout.md"
        word_count = len(path.read_text(encoding="utf-8").split())
        self.assertLessEqual(
            word_count,
            CLOSEOUT_WORD_LIMIT,
            f"{path.name} has {word_count} words, limit is {CLOSEOUT_WORD_LIMIT}",
        )


if __name__ == "__main__":
    unittest.main()
