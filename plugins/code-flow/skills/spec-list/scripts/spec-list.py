#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path


def _load_package_helper(module_name, filename):
    here = Path(__file__).resolve().parent
    package_root = here.parents[2]
    spec = importlib.util.spec_from_file_location(
        module_name, package_root / "scripts" / filename
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_repository = _load_package_helper("code_flow_repository_helper", "repository.py")
git_toplevel = _repository.git_toplevel

_openspec_metadata = _load_package_helper(
    "code_flow_openspec_metadata", "openspec_metadata.py"
)
is_draft = _openspec_metadata.is_draft
parse_created = _openspec_metadata.parse_created


TASK_LINE = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])\s*\[\s*([xX]?)\s*\]")
PRIORITY = re.compile(r"^x-priority:\s*[pP]([0-3])\s*(?:#.*)?$", re.MULTILINE)
WHY_SECTION = re.compile(r"(?ms)^## Why\s*$(.*?)(?=^## |\Z)")
SUMMARY_WIDTH = 80
DESCRIPTION_WIDTH = 100
DESCRIPTION_LIMIT = 180
UNSET_PRIORITY = 4

IN_PROGRESS, NOT_STARTED, READY_TO_ARCHIVE, DRAFT = range(4)
GROUP_TITLES = {
    IN_PROGRESS: "In progress",
    NOT_STARTED: "Not started",
    READY_TO_ARCHIVE: "Ready to archive",
    DRAFT: "Draft",
}

METADATA_KEYS = ("created", "x-priority", "x-draft")


def try_read(path, errors, name, filename):
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        errors.append(f"{name}: {filename} is unreadable")
        return ""


def count_tasks(tasks_text):
    markers = []
    for line in tasks_text.splitlines():
        match = TASK_LINE.match(line)
        if match:
            markers.append(match.group(1))
    done = sum(1 for marker in markers if marker)
    return done, len(markers)


def parse_priority(metadata):
    match = PRIORITY.search(metadata)
    return int(match.group(1)) if match else UNSET_PRIORITY


def describe(proposal):
    section = WHY_SECTION.search(proposal)
    if not section:
        return ""
    paragraph = re.split(r"\n\s*\n", section.group(1).strip())[0]
    return " ".join(paragraph.split())


def clip(text, limit):
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    if cut < 0:
        return text[: limit - 1] + "…"
    return text[:cut].rstrip() + "…"


def description_lines(text):
    if not text:
        return []
    return textwrap.wrap(
        text,
        width=DESCRIPTION_WIDTH,
        initial_indent="    ",
        subsequent_indent="    ",
        break_long_words=False,
        break_on_hyphens=False,
    )


def change_group(done, total, draft):
    if draft:
        return DRAFT
    if total and done == total:
        return READY_TO_ARCHIVE
    if done:
        return IN_PROGRESS
    return NOT_STARTED


def capability_paths(changes_dir, name):
    specs_dir = os.path.join(changes_dir, name, "specs")
    if not os.path.isdir(specs_dir):
        return frozenset()
    paths = set()
    for base, dirs, files in os.walk(specs_dir):
        if "spec.md" in files:
            paths.add(os.path.relpath(base, specs_dir).replace(os.sep, "/"))
    return frozenset(paths)


def _is_active_change_name(name, path, changes_real):
    if name == "archive" or name.startswith("."):
        return False
    if os.path.exists(path) and not os.path.isdir(path):
        return False
    real = os.path.realpath(path)
    try:
        return os.path.commonpath([real, changes_real]) == changes_real
    except ValueError:
        return False


def active_change_names(changes_dir):
    if not os.path.isdir(changes_dir):
        return []
    names = []
    changes_real = os.path.realpath(changes_dir)
    for name in os.listdir(changes_dir):
        path = os.path.join(changes_dir, name)
        if _is_active_change_name(name, path, changes_real):
            names.append(name)
    return names


def run_openspec_list(root):
    if shutil.which("openspec") is None:
        return None, None
    try:
        result = subprocess.run(
            ["openspec", "list", "--json"],
            cwd=root,
            timeout=30,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=dict(os.environ, OPENSPEC_TELEMETRY="0"),
        )
    except (OSError, FileNotFoundError):
        return None, None
    if result.returncode != 0:
        return None, f"openspec list --json failed with exit code {result.returncode}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"openspec list --json output is not valid JSON: {exc}"
    progress = {}
    for entry in data.get("changes", []):
        name = entry.get("name")
        completed = entry.get("completedTasks")
        total = entry.get("totalTasks")
        if name is not None and isinstance(completed, int) and isinstance(total, int):
            progress[name] = (completed, total)
    return progress, None


def check_metadata_errors(name, metadata, priority, created, errors):
    if re.search(r"^x-priority:\s*", metadata, re.MULTILINE) and priority == UNSET_PRIORITY:
        errors.append(f"{name}: .openspec.yaml has invalid x-priority")
    if re.search(r"^created:\s*", metadata, re.MULTILINE) and created is None:
        errors.append(f"{name}: .openspec.yaml has invalid created date")


def _read_change_files(change_dir, name):
    read_errors = []
    metadata = try_read(
        os.path.join(change_dir, ".openspec.yaml"), read_errors, name, ".openspec.yaml"
    )
    proposal = try_read(
        os.path.join(change_dir, "proposal.md"), read_errors, name, "proposal.md"
    )
    tasks_text = try_read(
        os.path.join(change_dir, "tasks.md"), read_errors, name, "tasks.md"
    )
    return metadata, proposal, tasks_text, read_errors


def collect_changes(root):
    changes_dir = os.path.join(root, "openspec", "changes")
    changes_real = os.path.realpath(changes_dir)
    with ThreadPoolExecutor(max_workers=1) as executor:
        cli_future = executor.submit(run_openspec_list, root)
        disk_names = active_change_names(changes_dir)
        cached = {
            name: _read_change_files(os.path.join(changes_dir, name), name)
            for name in disk_names
        }
        cli_progress, cli_error = cli_future.result()
    candidates = sorted(set(disk_names) | set(cli_progress or ()))
    names = [
        name
        for name in candidates
        if _is_active_change_name(name, os.path.join(changes_dir, name), changes_real)
    ]
    rows = []
    errors = []
    if cli_error is not None:
        errors.append(cli_error)
    for name in names:
        change_dir = os.path.join(changes_dir, name)
        if name in cached:
            metadata, proposal, tasks_text, read_errors = cached[name]
            errors.extend(read_errors)
        elif os.path.isdir(change_dir):
            metadata, proposal, tasks_text, read_errors = _read_change_files(
                change_dir, name
            )
            errors.extend(read_errors)
        else:
            metadata = ""
            proposal = ""
            tasks_text = ""
        if cli_progress and name in cli_progress:
            done, total = cli_progress[name]
        else:
            done, total = count_tasks(tasks_text)
        ratio = f"{done}/{total}" if total else "-"
        draft = is_draft(metadata)
        priority = parse_priority(metadata)
        created = parse_created(metadata)
        check_metadata_errors(name, metadata, priority, created, errors)
        group = change_group(done, total, draft)
        description = describe(proposal)
        rows.append((group, priority, created or date.max, name, ratio, description))
    rows.sort(key=lambda row: (row[0], row[1], row[2], row[3]))
    return rows, errors


def render(rows, errors, full, short):
    print(f"{len(rows)} active change{'' if len(rows) == 1 else 's'}")
    for group, title in GROUP_TITLES.items():
        group_rows = [row for row in rows if row[0] == group]
        if not group_rows:
            continue
        print()
        print(title)
        if short:
            for row in group_rows:
                print(row[3])
        elif full:
            name_width = max(len(row[3]) for row in group_rows)
            progress_width = max(len(row[4]) for row in group_rows)
            for row in group_rows:
                print()
                priority_label = f"P{row[1]}" if row[1] < UNSET_PRIORITY else "--"
                header = (
                    f"▸ {row[3]:<{name_width}}  {priority_label}  "
                    f"{row[4]:<{progress_width}}"
                ).rstrip()
                print(header)
                for line in description_lines(clip(row[5], DESCRIPTION_LIMIT)):
                    print(line)
        else:
            name_width = max(len(row[3]) for row in rows)
            progress_width = max(len(row[4]) for row in rows)
            for row in group_rows:
                priority_label = f"P{row[1]}" if row[1] < UNSET_PRIORITY else "--"
                line = (
                    f"{row[3]:<{name_width}}  {priority_label}  "
                    f"{row[4]:<{progress_width}}  {clip(row[5], SUMMARY_WIDTH)}"
                ).rstrip()
                print(line)
    if errors:
        print()
        print("Errors")
        for error in errors:
            print(error)


class MetadataError(Exception):
    pass


class SpecListArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        print(f"spec-list: {message}", file=sys.stderr)
        raise SystemExit(2)


def parse_priority_option(value):
    if not re.fullmatch(r"P[0-3]", value):
        raise argparse.ArgumentTypeError(
            f"invalid priority {value!r} (expected P0, P1, P2 or P3)"
        )
    return value


def parse_created_option(value):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(
            f"invalid created date {value!r} (expected YYYY-MM-DD)"
        )
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid created date {value!r} (not a calendar date)"
        )
    return value


def parse_scalar_line(body, key):
    match = re.fullmatch(rf"{key}:\s*([^#\s|>'\"][^#\s]*)\s*(#.*)?", body)
    if not match:
        return None
    return match.group(1), match.group(2) or ""


def rewrite_metadata(text, updates, change):
    lines = text.split("\n")
    key_lines = {}
    for index, body in enumerate(lines):
        for key in updates:
            if re.match(rf"^{key}:", body):
                key_lines.setdefault(key, []).append(index)
    for key, indices in key_lines.items():
        if len(indices) > 1:
            raise MetadataError(f"{change}: .openspec.yaml has duplicate {key} lines")
    for key, value in updates.items():
        indices = key_lines.get(key, [])
        if not indices:
            continue
        body = lines[indices[0]]
        parsed = parse_scalar_line(body, key)
        if parsed is None:
            raise MetadataError(
                f"{change}: .openspec.yaml has an unsupported {key} line"
            )
        _, comment = parsed
        body = f"{key}: {value}"
        if comment:
            body += f" {comment}"
        lines[indices[0]] = body
    new_text = "\n".join(lines)
    missing = [key for key in METADATA_KEYS if key in updates and key not in key_lines]
    if missing:
        if new_text and not new_text.endswith("\n"):
            new_text += "\n"
        for key in missing:
            new_text += f"{key}: {updates[key]}\n"
    return new_text


def write_atomically(path, content):
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".openspec.yaml.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as tmp_file:
            tmp_file.write(content)
        os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def resolve_change_dir(root, change):
    if not change or "/" in change or "\\" in change:
        raise MetadataError(f"unsafe change name {change!r}")
    changes_dir = os.path.join(root, "openspec", "changes")
    changes_real = os.path.realpath(changes_dir)
    change_dir = os.path.join(changes_dir, change)
    real = os.path.realpath(change_dir)
    try:
        inside = os.path.commonpath([real, changes_real]) == changes_real
    except ValueError:
        inside = False
    if not inside:
        raise MetadataError(f"change {change!r} resolves outside openspec/changes")
    if change == "archive" or change.startswith(".") or not os.path.isdir(change_dir):
        raise MetadataError(f"change {change!r} is not an active change")
    return change_dir


def run_set_metadata(argv):
    parser = SpecListArgumentParser(prog="spec-list.py set-metadata", add_help=False)
    parser.add_argument("change")
    parser.add_argument("--priority", type=parse_priority_option)
    parser.add_argument("--created", type=parse_created_option)
    parser.add_argument("--draft", choices=("true", "false"))
    namespace = parser.parse_args(argv)
    updates = {}
    if namespace.priority is not None:
        updates["x-priority"] = namespace.priority
    if namespace.created is not None:
        updates["created"] = namespace.created
    if namespace.draft is not None:
        updates["x-draft"] = namespace.draft
    if not updates:
        print(
            "spec-list: at least one of --priority, --created, --draft is required",
            file=sys.stderr,
        )
        return 2
    try:
        root = git_toplevel(os.getcwd())
        change_dir = resolve_change_dir(root, namespace.change)
        metadata_path = os.path.join(change_dir, ".openspec.yaml")
        if not os.path.isfile(metadata_path):
            raise MetadataError(
                f"change {namespace.change!r} has no .openspec.yaml"
            )
        try:
            with open(metadata_path, "r", encoding="utf-8") as metadata_file:
                text = metadata_file.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise MetadataError(
                f"change {namespace.change!r} has an unreadable .openspec.yaml: {exc}"
            )
        new_text = rewrite_metadata(text, updates, namespace.change)
        try:
            write_atomically(metadata_path, new_text)
        except OSError as exc:
            raise MetadataError(
                f"failed to update {namespace.change}/.openspec.yaml: {exc}"
            )
    except MetadataError as exc:
        print(f"spec-list: {exc}", file=sys.stderr)
        return 1
    print(f"Updated metadata for {namespace.change}.")
    return 0


def run_listing(argv):
    parser = SpecListArgumentParser(prog="spec-list.py", add_help=False)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--full", action="store_true")
    modes.add_argument("--short", action="store_true")
    modes.add_argument("--batch", action="store_true")
    namespace = parser.parse_args(argv)
    try:
        root = git_toplevel(os.getcwd())
        openspec_dir = os.path.join(root, "openspec")
        if not os.path.isdir(openspec_dir):
            print("no openspec/ directory in this repository")
            return 0
        changes_dir = os.path.join(openspec_dir, "changes")
        if not os.path.isdir(changes_dir):
            print("no active changes under openspec/changes/")
            return 0
        rows, errors = collect_changes(root)
        if not rows:
            if errors:
                for error in errors:
                    print(f"spec-list: {error}", file=sys.stderr)
                return 1
            print("no active changes under openspec/changes/")
            return 0
        if namespace.batch:
            runnable = [row for row in rows if row[0] in (IN_PROGRESS, NOT_STARTED)]
            if not runnable:
                print("no unfinished changes to batch")
                return 0
            batches = []
            current, shared = [], set()
            for row in runnable:
                caps = capability_paths(changes_dir, row[3])
                if caps & shared:
                    batches.append(current)
                    current, shared = [], set()
                current.append(row[3])
                shared |= caps
            batches.append(current)
            for batch in batches:
                print(" ".join(shlex.quote(name) for name in batch))
            return 0
        render(rows, errors, namespace.full, namespace.short)
        return 0
    except Exception as exc:
        print(f"spec-list: {exc}", file=sys.stderr)
        return 1


def main():
    args = sys.argv[1:]
    if args and args[0] == "set-metadata":
        return run_set_metadata(args[1:])
    return run_listing(args)


if __name__ == "__main__":
    sys.exit(main())
