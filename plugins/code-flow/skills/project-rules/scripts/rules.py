from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from bisect import bisect_left, bisect_right
from collections.abc import Iterator
from itertools import accumulate
from pathlib import Path, PureWindowsPath

INDEX = "AGENTS.md"
RULES_DEFAULT = "docs/project-rules.md"
BEGIN = "<!-- project-rules:begin -->"
END = "<!-- project-rules:end -->"
BLOCK_BUDGET = 12
BLOCK_WORDS = 150
CHECKS_HEADING = "## Checks"
INDEX_LINES = ("Done:", "Frameworks:")
RULES_ORDER = ("## Checks", "## Ownership", "## Approaches", "## Code style", "## Design", "## Framework")
MIN_GIT = (2, 31)
MIN_PYTHON = (3, 9)


def _load_fences():
    source = Path(__file__).resolve().with_name("markdown_fences.py")
    spec = importlib.util.spec_from_file_location("_project_rules_fences", source)
    module = importlib.util.module_from_spec(spec)
    exec(compile(source.read_bytes(), str(source), "exec", dont_inherit=True), module.__dict__)
    return module


_fences = _load_fences()
_unfenced_lines = _fences.unfenced_lines
_unclosed_fence = _fences.unclosed_fence
_MARKERS = re.compile(f"{re.escape(BEGIN)}|{re.escape(END)}")
_INLINE = re.compile(r"`+|<!--")
_SPAN_END = re.compile(r"`+|(?<=\n)[ \t]*\n")
_INDEX_LINK = re.compile(r"\[([^\[\]\r\n]+)\]\(")
_BARE_FIELD = re.compile(r"[^\s<>()\\]+")
_RELATIVE_DESTINATION = re.compile(r"[A-Za-z0-9/._~!$&'*+,;=@-]+")
_ENTITY = re.compile(r"&(?:[A-Za-z][A-Za-z0-9]*|#[0-9]+|#[xX][0-9A-Fa-f]+);")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_HEADING = re.compile(r" {0,3}(#{1,6})[ \t]+(\S.*)")
_EXTERNAL = re.compile(r"https?://", re.IGNORECASE)
_GIT_PASSTHROUGH = ("GIT_PAGER", "GIT_TERMINAL_PROMPT")
_GIT_CHILD_KEYS = ("PATH", "HOME", "SystemRoot")
_GIT_TIMEOUT = 20
_GIT_VERSION = re.compile(r"git version (\d+)\.(\d+)(?:\.(\d+))?")
_IGNORE_LINE = re.compile(r"(?P<source>.*):(?P<line>\d+):(?P<pattern>.*)\t(?P<path>.*)")


def _escaped(text: str, offset: int) -> bool:
    end = offset
    while offset and text[offset - 1] == "\\":
        offset -= 1
    return (end - offset) % 2 == 1


def _visible_ranges(lines: list[str], offsets: list[int]) -> Iterator[tuple[int, int]]:
    first = previous = 0
    for number, _ in _unfenced_lines(lines):
        if number != previous + 1:
            if previous:
                yield offsets[first - 1], offsets[previous]
            first = number
        if not first:
            first = number
        previous = number
    if previous:
        yield offsets[first - 1], offsets[previous]


def _marker_positions(
    text: str, ranges: list[tuple[int, int]], offsets: list[int],
) -> tuple[list[int], tuple[int, int] | None]:
    markers = []
    for start, end in ranges:
        for match in _MARKERS.finditer(text, start, end):
            line = bisect_right(offsets, match.start())
            body = text[offsets[line - 1]:offsets[line]].removesuffix("\n")
            if body.strip(" \t") != match[0]:
                raise ValueError(f"line {line}: project-rules marker must be standalone")
            markers.append((match[0], match.start(), line))
    if not markers:
        return [], None
    if len(markers) != 2 or markers[0][0] == markers[1][0]:
        line = markers[1][2] if len(markers) > 1 else markers[0][2]
        raise ValueError(f"line {line}: expected one begin and one end marker (unpaired or duplicate markers)")
    if markers[0][0] != BEGIN:
        raise ValueError(f"line {markers[0][2]}: out-of-order project-rules markers")
    return [markers[0][1], markers[1][1]], (markers[0][2], markers[1][2])


def _code_span_end(text: str, run: re.Match, end: int) -> int:
    for candidate in _SPAN_END.finditer(text, run.end(), end):
        if candidate[0].endswith("\n"):
            break
        if candidate[0] == run[0]:
            return candidate.end()
    return run.end()


def _mask_inline(
    text: str, ranges: list[tuple[int, int]], markers: list[int], offsets: list[int],
) -> Iterator[tuple[int, int, int]]:
    comment = None
    for start, end in ranges:
        visible = cursor = start
        while cursor < end:
            if comment is not None:
                closer = text.find("-->", cursor, end)
                stop = end if closer < 0 else closer + 3
                marker = bisect_left(markers, cursor)
                if marker < len(markers) and markers[marker] < stop:
                    if markers[0] < comment < markers[1]:
                        line = bisect_right(offsets, comment)
                        raise ValueError(f"line {line}: unclosed HTML comment before the end marker")
                    line = bisect_right(offsets, markers[marker])
                    raise ValueError(f"line {line}: HTML comment overlaps a project-rules marker")
                if closer >= 0:
                    comment = None
                visible = cursor = stop
                continue
            token = _INLINE.search(text, cursor, end)
            if not token:
                break
            cursor = token.end()
            if _escaped(text, token.start()):
                continue
            if token[0] == "<!--":
                if token.start() in markers:
                    continue
                comment = token.start()
            else:
                cursor = _code_span_end(text, token, end)
                if cursor == token.end():
                    continue
            if visible < token.start():
                yield visible, token.start(), end
            visible = cursor
        if comment is not None and markers and markers[0] < comment < markers[1]:
            line = bisect_right(offsets, comment)
            raise ValueError(f"line {line}: unclosed HTML comment in the project-rules block")
        if visible < end:
            yield visible, end, end


def _prepare_scan(text: str):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    bodies = text.split("\n")
    lines = [line + "\n" for line in bodies[:-1]] + bodies[-1:]
    offsets = list(accumulate(map(len, lines), initial=0))
    ranges = list(_visible_ranges(lines, offsets))
    return text, lines, offsets, ranges, _unclosed_fence(lines)


def _destination_end(text: str, start: int, end: int) -> int:
    depth = 0
    cursor = start
    while cursor < end:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == ")":
            if not depth:
                return cursor
            depth -= 1
        elif char == "(":
            depth += 1
        cursor += 1
    return end


def _index_links(
    text: str, ranges: list[tuple[int, int, int]], start: int, end: int,
) -> Iterator[tuple[int, str, bool]]:
    cursor = start
    for visible, limit, section_end in ranges:
        cursor = max(cursor, visible)
        limit = min(limit, end)
        while match := _INDEX_LINK.search(text, cursor, limit):
            cursor = match.end()
            if _escaped(text, match.start()):
                continue
            if text[match.start() - 1:match.start()] == "!" and not _escaped(text, match.start() - 1):
                newline = text.find("\n", cursor, limit)
                image_limit = limit if newline < 0 else newline
                image_end = _destination_end(text, cursor, image_limit)
                if image_end < image_limit:
                    cursor = image_end + 1
                continue
            stop = _destination_end(text, cursor, min(section_end, end))
            destination = text[cursor:stop]
            cursor = stop + 1
            yield match.start(), destination, stop < limit and bool(_BARE_FIELD.fullmatch(destination))


def _qualified(path: str) -> bool:
    windows = PureWindowsPath(path)
    return bool(windows.drive or windows.root or path.startswith("/"))


def _valid_destination(path: str) -> bool:
    return bool(_RELATIVE_DESTINATION.fullmatch(path) and not path.startswith("/") and not _ENTITY.search(path))


def _requires_directory(path: str) -> bool:
    return path.endswith(("/", "/.", os.sep, os.sep + "."))


def _skipped_destination(destination: str) -> bool:
    return destination.startswith("#") or bool(_EXTERNAL.match(destination))


class _PathFinding(ValueError):
    pass


def _metadata(path: Path) -> os.stat_result | None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return None
    if (getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            and not stat.S_ISLNK(entry.st_mode)):
        raise _PathFinding("path contains a redirecting reparse point")
    return entry


def _listed_spelling(path: Path, listings: dict[Path, list[str]]) -> str | None:
    parent = path.parent
    names = listings.get(parent)
    if names is None:
        try:
            names = list(os.listdir(parent))
        except OSError:
            names = []
        listings[parent] = names
    if path.name in names:
        return None
    folded = path.name.casefold()
    return next((name for name in names if name.casefold() == folded), None)


def _resolve_contained(
    root: Path, base: Path, path: Path, strict_case: bool = False,
) -> tuple[Path, os.stat_result | None, bool]:
    pending = [(component, False) for component in reversed(path.parts)]
    current = base
    followed = 0
    has_missing_component = False
    entry = None
    listings: dict[Path, list[str]] = {}
    while pending:
        component, exempt = pending.pop()
        if component == "..":
            if current == root:
                raise _PathFinding("path escapes project root")
            current = current.parent
            entry = _metadata(current)
            continue
        if _qualified(component):
            raise _PathFinding("path component would reset or escape project root")
        current /= component
        entry = _metadata(current)
        if strict_case and not exempt:
            existing = _listed_spelling(current, listings)
            if existing is not None:
                raise _PathFinding(f"component {component!r} differs in case from existing {existing!r}")
        if entry is None:
            has_missing_component = True
            continue
        if stat.S_ISLNK(entry.st_mode):
            followed += 1
            if followed > 40:
                raise _PathFinding("too many symbolic links")
            raw_target = os.readlink(current)
            target = Path(raw_target)
            current = current.parent
            if target.is_absolute():
                try:
                    target = target.relative_to(root)
                except ValueError:
                    raise _PathFinding("symlink path escapes project root") from None
                current = root
            elif target.anchor:
                raise _PathFinding("symlink path escapes project root")
            if _requires_directory(raw_target):
                pending.append((".", True))
            pending.extend((part, True) for part in reversed(target.parts))
            entry = _metadata(current) if not pending else None
        elif pending and not stat.S_ISDIR(entry.st_mode):
            raise _PathFinding("path has a non-directory ancestor")
    return current, entry, has_missing_component


def _shares_index_slot(root: Path, component: str) -> bool:
    if component == INDEX:
        return True
    if component.casefold() != INDEX.casefold():
        return False
    names = set(os.listdir(root))
    if component in names and INDEX in names:
        return False
    if component in names or INDEX in names:
        other = INDEX if component in names else component
        return _metadata(root / other) is not None
    for name in sorted(names):
        if not name.isascii() or name == name.swapcase() or name.swapcase() in names:
            continue
        return _metadata(root / name.swapcase()) is not None
    return True


def _select_rules(root: Path, selected: str) -> tuple[Path, str]:
    if not selected or _qualified(selected) or selected.startswith("-") or "#" in selected:
        raise _PathFinding("expected a repository-relative file path without a leading '-' or '#'")
    if any(component.casefold() == ".git" for component in Path(selected).parts):
        raise _PathFinding(".git is reserved")
    target = Path(selected)
    parent, _, _ = _resolve_contained(root, root, target.parent, strict_case=True)
    if _qualified(target.name):
        raise _PathFinding("path component would reset or escape project root")
    resolved = parent / target.name
    if (existing := _listed_spelling(resolved, {})) is not None:
        raise _PathFinding(f"component {target.name!r} differs in case from existing {existing!r}")
    try:
        entry = resolved.lstat()
    except FileNotFoundError:
        entry = None
    except NotADirectoryError:
        raise _PathFinding("path has a non-directory ancestor") from None
    relative = resolved.relative_to(root)
    posix = relative.as_posix()
    if posix.startswith("-"):
        raise _PathFinding("normalized file path starts with '-'")
    if any(component.casefold() == ".git" for component in relative.parts):
        raise _PathFinding(".git is reserved")
    if relative.parts and _shares_index_slot(root, relative.parts[0]):
        raise _PathFinding(f"file/directory collision with {INDEX}")
    if not _valid_destination(posix):
        raise _PathFinding(f"destination grammar rejects {posix!r}")
    if entry is not None and not stat.S_ISLNK(entry.st_mode) and not stat.S_ISREG(entry.st_mode):
        raise _PathFinding("not a regular file")
    return resolved, posix


def _case_collision(path: Path, directory_entries: dict[Path, list[str]]) -> str | None:
    parent = path.parent
    entries = directory_entries.get(parent)
    if entries is None:
        try:
            entries = sorted(os.listdir(parent))
        except FileNotFoundError:
            entries = []
        directory_entries[parent] = entries
    slot = path.name
    if slot in entries:
        return None
    folded = slot.casefold()
    return next((entry for entry in entries if entry.casefold() == folded), None)


def _slots(root: Path, rules_path: Path) -> tuple[dict[Path, os.stat_result], list[str]]:
    entries: dict[Path, os.stat_result] = {}
    findings = []
    for path in (root / INDEX, rules_path):
        relative = path.relative_to(root).as_posix()
        try:
            entry = _metadata(path)
            if entry is not None and not stat.S_ISREG(entry.st_mode):
                raise _PathFinding("managed path is a symlink" if stat.S_ISLNK(entry.st_mode)
                                   else "managed path is not a regular file")
        except (OSError, ValueError) as error:
            findings.append(f"{relative}: {error}")
            continue
        if entry is not None:
            entries[path] = entry
    directory_entries: dict[Path, list[str]] = {}
    for path in list(entries):
        collision = _case_collision(path, directory_entries)
        if collision is not None:
            findings.append(f"{path.relative_to(root).as_posix()}: case collision with {collision!r}")
            del entries[path]
    return entries, findings


def _read_managed(root: Path, path: Path, expected: os.stat_result) -> str:
    try:
        resolved, entry, missing = _resolve_contained(root, root, path.relative_to(root))
    except _PathFinding as error:
        raise OSError(f"managed path changed after preflight: {error}") from None
    if resolved != path or missing or entry is None or not os.path.samestat(entry, expected):
        raise OSError("managed path changed after preflight")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), encoding="utf-8") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(opened, expected):
            raise OSError("managed path changed after preflight")
        return stream.read()


def _document_text(root: Path, path: Path, entry: os.stat_result | None, draft_dir: Path | None) -> str:
    if draft_dir is not None:
        draft = draft_dir / path.name
        draft_entry = _metadata(draft)
        if draft_entry is None:
            raise _PathFinding("missing draft file")
        if not stat.S_ISREG(draft_entry.st_mode):
            raise _PathFinding("not a regular file")
        with open(draft, encoding="utf-8") as stream:
            return stream.read()
    if entry is None:
        raise _PathFinding("missing managed file")
    return _read_managed(root, path, entry)


def _resolve_local(
    root: Path, base: Path, path: str, absent: Path | None = None,
) -> tuple[Path, os.stat_result | None]:
    if _qualified(path):
        raise _PathFinding("absolute or drive-qualified link target")
    if _SCHEME.match(path):
        raise _PathFinding("disallowed link scheme")
    if _requires_directory(path):
        raise _PathFinding("link target is not a regular file")
    target = Path(path)
    if not target.parts:
        raise _PathFinding("link target is not a regular file")
    resolved, entry, missing = _resolve_contained(root, base, target)
    if missing or entry is None:
        if resolved == absent:
            return resolved, None
        raise _PathFinding("missing link target")
    if not stat.S_ISREG(entry.st_mode):
        raise _PathFinding("link target is not a regular file")
    return resolved, entry


def _local_target(root: Path, base: Path, path: str) -> os.stat_result:
    return _resolve_local(root, base, path)[1]


def _index_destination(
    root: Path, destination: str, bare: bool, rules_path: Path,
) -> tuple[Path, os.stat_result | None]:
    path = destination.partition("#")[0]
    if not bare:
        raise _PathFinding("expected a single bare destination")
    if not path or _EXTERNAL.match(path):
        raise _PathFinding("extra index link")
    if _qualified(path):
        raise _PathFinding("absolute or drive-qualified link target")
    if _SCHEME.match(path):
        raise _PathFinding("disallowed link scheme")
    if any(entity.start() < len(path) for entity in _ENTITY.finditer(destination)) or not _valid_destination(path):
        raise _PathFinding("destination grammar rejects the file path")
    return _resolve_local(root, root, path, rules_path)


def _is_rules_file(
    resolved: Path, entry: os.stat_result | None, rules_path: Path, rules_entry: os.stat_result | None,
) -> bool:
    if entry is not None and rules_entry is not None:
        return os.path.samestat(entry, rules_entry)
    return resolved == rules_path


def _check_index(
    root: Path, text: str, rules_path: Path, rules_rel: str, rules_entry: os.stat_result | None,
) -> list[str]:
    try:
        text, lines, offsets, ranges, unclosed = _prepare_scan(text)
        markers, pair = _marker_positions(text, ranges, offsets)
        visible = list(_mask_inline(text, ranges, markers, offsets))
    except ValueError as error:
        return [str(error)]
    if pair is None:
        if unclosed is not None:
            return [f"line {unclosed}: unclosed fence"]
        return ["missing project-rules markers"]
    begin, end = pair
    findings = []
    if unclosed is not None:
        findings.append((unclosed, "unclosed fence"))
    block = [lines[number - 1] for number in range(begin + 1, end) if lines[number - 1].strip()]
    if len(block) > BLOCK_BUDGET:
        findings.append((end, f"index block has {len(block)} nonblank lines; the budget is {BLOCK_BUDGET}"))
    words = sum(len(line.split()) for line in block)
    if words > BLOCK_WORDS:
        findings.append((end, f"index block has {words} words; the budget is {BLOCK_WORDS}"))
    for prefix in INDEX_LINES:
        if not any(line.strip().startswith(prefix) for line in block):
            findings.append((end, f'index block has no "{prefix}" line'))
    linked = False
    for offset, destination, bare in _index_links(text, visible, offsets[begin], offsets[end - 1]):
        line = bisect_right(offsets, offset)
        try:
            resolved, entry = _index_destination(root, destination, bare, rules_path)
        except (OSError, ValueError) as error:
            findings.append((line, f"invalid index link {destination!r}: {error}"))
            continue
        if not _is_rules_file(resolved, entry, rules_path, rules_entry):
            findings.append((line, f"extra index link {destination!r}"))
        elif linked:
            findings.append((line, f"duplicate index link {destination!r}"))
        else:
            linked = True
    if not linked:
        findings.append((end, f"missing index link to {rules_rel}"))
    findings.sort(key=lambda item: item[0])
    return [f"line {number}: {finding}" for number, finding in findings]


def _check_rule(root: Path, path: Path, text: str) -> list[str]:
    text, lines, offsets, ranges, unclosed = _prepare_scan(text)
    if not text.strip():
        return ["blank rules file"]
    findings = []
    headings = set()
    h1 = 0
    checks = False
    seen: dict[str, int] = {}
    for number, line in _unfenced_lines(lines):
        heading = _HEADING.fullmatch(line.rstrip("\n"))
        if not heading:
            continue
        trimmed = line.strip()
        if heading[1] == "##" and trimmed in RULES_ORDER:
            for name in seen:
                if RULES_ORDER.index(name) > RULES_ORDER.index(trimmed):
                    findings.append((number, f'"{trimmed}" must come before "{name}"'))
            seen.setdefault(trimmed, number)
        if trimmed in headings:
            findings.append((number, f"duplicate heading {trimmed!r}"))
        headings.add(trimmed)
        if heading[1] == "#":
            h1 += 1
            if h1 > 1:
                findings.append((number, "expected exactly one H1 (found another H1)"))
        if trimmed == CHECKS_HEADING:
            checks = True
    for offset, destination, bare in _index_links(text, list(_mask_inline(text, ranges, [], offsets)), 0, len(text)):
        if _skipped_destination(destination):
            continue
        number = bisect_right(offsets, offset)
        if not bare:
            findings.append((number, "expected a single bare destination"))
            continue
        try:
            _local_target(root, path.parent, destination.partition("#")[0])
        except (OSError, ValueError) as error:
            findings.append((number, f"invalid link {destination!r}: {error}"))
    if h1 == 0:
        findings.append((1, "expected exactly one H1 (found none)"))
    if unclosed is not None:
        findings.append((unclosed, "unclosed fence"))
    if not checks:
        findings.append((1, f'missing "{CHECKS_HEADING}" heading'))
    findings.sort(key=lambda item: item[0])
    return [f"line {number}: {finding}" for number, finding in findings]


def validate(root: Path, rules_file: str = RULES_DEFAULT, draft_dir: Path | None = None) -> list[str]:
    try:
        rules_path, rules_rel = _select_rules(root, rules_file)
    except (OSError, ValueError) as error:
        return [f"rules file {rules_file!r}: {error}"]
    entries, findings = _slots(root, rules_path)
    if findings:
        return findings
    index = root / INDEX
    index_entry = entries.get(index)
    rules_entry = entries.get(rules_path)
    for path, entry in ((index, index_entry), (rules_path, rules_entry)):
        relative = path.relative_to(root).as_posix()
        try:
            text = _document_text(root, path, entry, draft_dir)
        except UnicodeError:
            findings.append(f"{relative}: invalid UTF-8")
        except _PathFinding as error:
            findings.append(f"{relative}: {error}")
        except OSError as error:
            findings.append(f"{relative}: cannot read file: {error}")
        else:
            problems = (_check_index(root, text, rules_path, rules_rel, rules_entry) if path == index
                        else _check_rule(root, rules_path, text))
            findings.extend(f"{relative}: {problem}" for problem in problems)
    return findings


def _git(argv: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *argv], cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT, check=False,
    )


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _inherited_git() -> list[str]:
    return sorted(name for name in os.environ if name.startswith("GIT_") and name not in _GIT_PASSTHROUGH)


def _child_env() -> dict[str, str]:
    env = {
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.excludesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
    }
    for name in _GIT_CHILD_KEYS:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _git_version(cwd: Path, env: dict[str, str]) -> str:
    result = _git(["--version"], cwd, env)
    match = _GIT_VERSION.search(result.stdout) if result.returncode == 0 else None
    if match is None:
        return f"cannot determine git version: {_first_line(result.stderr) or _first_line(result.stdout)}"
    if (int(match[1]), int(match[2])) < MIN_GIT:
        found = match[0].removeprefix("git version ")
        return f"git {MIN_GIT[0]}.{MIN_GIT[1]} or newer is required; found {found}"
    return ""


def _git_worktree(start: Path, env: dict[str, str]) -> tuple[tuple[Path, Path, Path] | None, str]:
    result = _git(["rev-parse", "--path-format=absolute", "--show-toplevel", "--git-dir", "--git-common-dir"],
                  start, env)
    if result.returncode:
        return None, f"not a Git worktree: {_first_line(result.stderr)}"
    lines = result.stdout.splitlines()
    if len(lines) != 3:
        return None, "not a Git worktree: unexpected rev-parse output"
    root, git_dir, common_dir = (Path(os.path.realpath(line)) for line in lines)
    if root != start and root not in start.parents:
        return None, f"Git top level {root} does not contain {start}"
    return (root, git_dir, common_dir), ""


def _within(ancestor: Path, path: Path) -> bool:
    return path == ancestor or ancestor in path.parents


def _ignored(root: Path, rules_rel: str, env: dict[str, str]) -> list[str]:
    result = _git(["check-ignore", "--no-index", "-v", "--", INDEX, rules_rel], root, env)
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        return [f"check-ignore failed: {_first_line(result.stderr)}"]
    findings = []
    for line in result.stdout.splitlines():
        match = _IGNORE_LINE.fullmatch(line)
        if match is None:
            findings.append(f"check-ignore output is unparsable: {line!r}")
        elif not match["pattern"].startswith("!"):
            findings.append(f"{match['path']} is ignored by {match['source']}:{match['line']} ({match['pattern']})")
    return findings


def _claude_md_state(root: Path) -> str:
    try:
        entry = (root / "CLAUDE.md").lstat()
    except OSError:
        return "absent"
    if not stat.S_ISREG(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return "unusual"
    try:
        text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "present"
    return "imports" if any(line.strip() == f"@{INDEX}" for line in text.splitlines()) else "present"


def prepare(start: Path, rules_file: str = RULES_DEFAULT) -> tuple[dict[str, str] | None, list[str]]:
    inherited = _inherited_git()
    if inherited:
        return None, [f"inherited {','.join(inherited)}; unset them and retry"]
    if shutil.which("git") is None:
        return None, ["git executable not found on PATH"]
    env = _child_env()
    finding = _git_version(start, env)
    if finding:
        return None, [finding]
    worktree, finding = _git_worktree(start, env)
    if worktree is None:
        return None, [finding]
    root, git_dir, common_dir = worktree
    try:
        rules_path, rules_rel = _select_rules(root, rules_file)
    except (OSError, ValueError) as error:
        return None, [f"rules file {rules_file!r}: {error}"]
    findings = []
    if _within(git_dir, rules_path) or _within(common_dir, rules_path):
        findings.append("rules file lies inside Git metadata")
    _, slot_findings = _slots(root, rules_path)
    findings.extend(slot_findings)
    if findings:
        return None, findings
    findings.extend(_ignored(root, rules_rel, env))
    if findings:
        return None, findings
    return {
        "root": str(root),
        "rules_file": rules_rel,
        "git_dir": str(git_dir),
        "common_dir": str(common_dir),
        "claude_md": _claude_md_state(root),
    }, []


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_command = commands.add_parser("prepare", allow_abbrev=False)
    prepare_command.add_argument("start")
    prepare_command.add_argument("--rules-file", default=RULES_DEFAULT)
    validate_command = commands.add_parser("validate", allow_abbrev=False)
    validate_command.add_argument("root")
    validate_command.add_argument("--rules-file", default=RULES_DEFAULT)
    validate_command.add_argument("--draft-dir")
    return parser


def _existing_directory(parser: argparse.ArgumentParser, value: str, label: str) -> Path:
    try:
        path = Path(value).resolve(strict=True)
        if not path.is_dir():
            raise ValueError("not a directory")
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(f"{label} must be an existing directory: {error}")
    return path


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print(f"error: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer is required", file=sys.stderr)
        return 2
    parser = _parser()
    arguments = parser.parse_args(argv)
    report = None
    if arguments.command == "prepare":
        start = _existing_directory(parser, arguments.start, "start")
        report, findings = prepare(start, arguments.rules_file)
    else:
        root = _existing_directory(parser, arguments.root, "root")
        draft_dir = _existing_directory(parser, arguments.draft_dir, "draft dir") if arguments.draft_dir is not None else None
        findings = validate(root, arguments.rules_file, draft_dir)
    if findings:
        for finding in findings:
            print(f"project-rules: {finding}", file=sys.stderr)
        return 1
    if report is not None:
        print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    else:
        print("project-rules: layout valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
