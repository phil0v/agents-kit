from __future__ import annotations

import re
from collections.abc import Iterable, Iterator


_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})([^\r\n]*)")


class _Fences:
    def __init__(self) -> None:
        self.opener = ""
        self.opener_line = 0

    def visible(self, number: int, line: str) -> bool:
        candidate = _FENCE.fullmatch(line.rstrip("\r\n"))
        if self.opener:
            if candidate:
                run, tail = candidate.groups()
                if run[0] == self.opener[0] and len(run) >= len(self.opener) and not tail.strip(" \t"):
                    self.opener = ""
            return False
        if candidate:
            run, info = candidate.groups()
            if run[0] == "~" or "`" not in info:
                self.opener = run
                self.opener_line = number
                return False
        return True

    @property
    def unclosed(self) -> int | None:
        return self.opener_line if self.opener else None


def unfenced_lines(lines: Iterable[str]) -> Iterator[tuple[int, str]]:
    fences = _Fences()
    for number, line in enumerate(lines, 1):
        if fences.visible(number, line):
            yield number, line


def unclosed_fence(lines: Iterable[str]) -> int | None:
    fences = _Fences()
    for number, line in enumerate(lines, 1):
        fences.visible(number, line)
    return fences.unclosed
