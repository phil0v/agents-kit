#!/usr/bin/env python3
import importlib.util
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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


CHANGES = os.path.join("openspec", "changes")
METADATA_FILE = ".openspec.yaml"
PATH_TOKEN = re.compile(r"`([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)`")
IMPORT_PATH = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2}/")
REMOVAL_VERB = re.compile(r"\b(?:remove|removes|delete|deletes)\s+(?:the\s+)?(?:file\s+)?$", re.I)
RENAME_VERB = re.compile(r"\b(?:rename|renames|move|moves)\s+(?:the\s+)?(?:file\s+)?$", re.I)
RENAME_TARGET = re.compile(r"\s+(?:to|->)\s")
REMOVAL_JOINER = re.compile(r"(?:,\s+(?:and\s+|or\s+)?|\s+(?:and|or)\s+)")
DEFERRED_SECTION = re.compile(r"(?ms)^## Deferred.*?(?=^## |\Z)")
NAME_A_CHANGE = "name an open change under openspec/changes/, or nothing to list them"
FIX_ARTIFACTS = "fix the change's artifacts and rerun"
BASE_SPECS = "the base specs no longer take this delta — fix them and rerun"
BROKEN_CLI = "the openspec CLI is missing or broken — fix its install, not the change"
EXIT_USAGE = 2
EXIT_SEVERAL = 3
EXIT_NONE = 4
DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}-")
ARCHIVE_REFUSAL = "Archive would refuse this delta"


def usage():
    print("usage: spec-preflight [--archive-verdict] [<change name> | <path to tasks.md>]", file=sys.stderr)
    print("       spec-preflight --batch-arguments < arguments.json", file=sys.stderr)
    sys.exit(EXIT_USAGE)


def batch_arguments(args):
    try:
        if args != ["--batch-arguments"]:
            raise ValueError("--batch-arguments cannot be combined with other CLI arguments")
        document = json.load(sys.stdin)
        if not isinstance(document, dict) or set(document) != {"changes", "no_commit", "archive"}:
            raise ValueError("expected an object with changes, no_commit and archive")
        changes = document["changes"]
        if not isinstance(changes, list) or not changes or any(
            not isinstance(change, str) or not change.strip() for change in changes
        ):
            raise ValueError("changes must be a nonempty array of nonempty strings")
        if not isinstance(document["no_commit"], bool) or not isinstance(document["archive"], bool):
            raise ValueError("no_commit and archive must be booleans")
    except (ValueError, OSError) as error:
        print(json.dumps({"error": "invalid_batch_arguments", "message": str(error)}))
        return EXIT_USAGE

    if len(changes) > 1 and document["no_commit"]:
        print(json.dumps({
            "error": "unsupported_batch_no_commit",
            "message": "multiple changes require per-change commits; use separate --no-commit "
                       "invocations or request a different commit policy",
        }))
        return 1
    document["archive_skipped"] = document["archive"] and document["no_commit"]
    print(json.dumps(document))
    return 0


class Failure(Exception):
    def __init__(self, message, detail=(), *, stop):
        super().__init__(message)
        self.message = message
        self.detail = list(detail)
        self.stop = stop


def read_metadata(change_dir):
    try:
        with open(os.path.join(change_dir, METADATA_FILE), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def inside_changes(change_dir, changes_real):
    real = os.path.realpath(change_dir)
    try:
        return os.path.commonpath([real, changes_real]) == changes_real
    except ValueError:
        return False


def archived_name(root, name):
    archive = os.path.join(root, CHANGES, "archive")
    if not os.path.isdir(archive):
        return None
    for entry in os.listdir(archive):
        if not os.path.isdir(os.path.join(archive, entry)):
            continue
        if DATE_PREFIX.sub("", entry) == name:
            return name
    return None


def resolve_name(root, name, changes_real):
    change_dir = os.path.join(root, CHANGES, name)
    if os.path.isfile(os.path.join(change_dir, "tasks.md")):
        if not inside_changes(change_dir, changes_real):
            raise Failure(
                f"change: {name} — symlinked outside openspec/changes/",
                stop=NAME_A_CHANGE,
            )
        if is_draft(read_metadata(change_dir)):
            raise Failure(
                f"change: {name} — draft, remove x-draft from "
                f"{os.path.join(CHANGES, name, METADATA_FILE)} to apply it",
                stop=NAME_A_CHANGE,
            )
        return name, os.path.join(CHANGES, name, "tasks.md")
    if archived_name(root, name) is not None:
        raise Failure(f"change: {name} — archived, under openspec/changes/archive/", stop=NAME_A_CHANGE)
    if os.path.isdir(change_dir):
        raise Failure(f"change: {name} — no tasks.md under openspec/changes/{name}/", stop=NAME_A_CHANGE)
    raise Failure(f"change: {name} — no directory under openspec/changes/", stop=NAME_A_CHANGE)


def resolve_path(root, argument, changes_real):
    relpath = os.path.relpath(os.path.realpath(argument), root)
    parts = relpath.split(os.sep)
    if len(parts) >= 3 and parts[0] == "openspec" and parts[1] == "changes":
        rest = parts[2:]
        if rest[0] == "archive":
            if len(rest) >= 2:
                name = DATE_PREFIX.sub("", rest[1])
                raise Failure(f"change: {name} — archived, under openspec/changes/archive/", stop=NAME_A_CHANGE)
        elif len(rest) == 2 and rest[1] == "tasks.md":
            return resolve_name(root, rest[0], changes_real)
    raise Failure(f"change: {argument} — not a tasks.md under openspec/changes/", stop=NAME_A_CHANGE)


def resolve(root, argument, changes_real):
    if "/" in argument or argument.endswith(".md"):
        return resolve_path(root, argument, changes_real)
    return resolve_name(root, argument, changes_real)


def discover(changes_dir):
    if not os.path.isdir(changes_dir):
        return []
    changes_real = os.path.realpath(changes_dir)
    pairs = []
    for name in os.listdir(changes_dir):
        if name == "archive":
            continue
        change_dir = os.path.join(changes_dir, name)
        tasks = os.path.join(change_dir, "tasks.md")
        if not os.path.isfile(tasks) or not inside_changes(change_dir, changes_real):
            continue
        if is_draft(read_metadata(change_dir)):
            continue
        pairs.append((name, os.path.join(CHANGES, name, "tasks.md")))
    return sorted(pairs)


def status_shaped(document):
    status = document.get("status")
    return isinstance(status, list) and all(
        isinstance(entry, dict) and "message" in entry for entry in status
    )


def items_shaped(document):
    items = document.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return False
    item = items[0]
    if not isinstance(item.get("valid"), bool):
        return False
    issues = item.get("issues", [])
    return isinstance(issues, list) and all(
        isinstance(issue, dict) and "level" in issue and "path" in issue
        and isinstance(issue.get("message"), str)
        for issue in issues
    )


def run_openspec(root, prefix, *args):
    try:
        return subprocess.run(
            ["openspec", *args],
            cwd=root,
            env=dict(os.environ, OPENSPEC_TELEMETRY="0"),
            timeout=30,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise Failure(f"{prefix}: openspec not found on PATH", stop=BROKEN_CLI)
    except subprocess.TimeoutExpired:
        raise Failure(f"{prefix}: openspec timed out", stop=BROKEN_CLI)
    except OSError as error:
        raise Failure(f"{prefix}: openspec could not run: {error.strerror}", stop=BROKEN_CLI)


def run_openspec_json(root, prefix, shaped, *args):
    result = run_openspec(root, prefix, *args)
    try:
        document = json.loads(result.stdout)
    except ValueError:
        document = None
    if isinstance(document, dict) and status_shaped(document):
        raise Failure(
            f"{prefix}: " + "; ".join(entry["message"] for entry in document["status"]),
            stop=FIX_ARTIFACTS,
        )
    if not isinstance(document, dict) or not shaped(document):
        lines = (result.stdout.splitlines() + result.stderr.splitlines())[:20]
        raise Failure(f"{prefix}: unreadable output", detail=lines, stop=BROKEN_CLI)
    return result, document


def run_validate(root, name):
    result, document = run_openspec_json(
        root,
        "validate",
        items_shaped,
        "validate", name, "--type", "change", "--strict", "--no-interactive", "--json",
    )
    items = document["items"]
    issues = items[0].get("issues", [])
    rendered = [
        f"validate: [{issue['level']}] {issue['path']}: {issue['message']}" for issue in issues
    ]
    errors = [issue for issue in issues if issue["level"] == "ERROR"]
    refused = any(issue["message"].startswith(ARCHIVE_REFUSAL) for issue in errors)
    if result.returncode != 0 or not items[0]["valid"] or errors:
        if not rendered:
            raise Failure(f"validate: exit {result.returncode}", stop=BROKEN_CLI)
        stop = BASE_SPECS if refused else FIX_ARTIFACTS
        raise Failure(rendered[0], detail=rendered[1:], stop=stop)
    return rendered


def print_validate(rendered):
    for line in rendered:
        print(line)
    print("validate: ok")


def check_validate(root, name):
    rendered = run_validate(root, name)
    print_validate(rendered)
    return rendered


def progress_shaped(document):
    progress = document.get("progress")
    return isinstance(progress, dict) and all(
        isinstance(progress.get(key), int) and not isinstance(progress.get(key), bool)
        for key in ("total", "complete")
    )


def run_progress(root, name):
    result, document = run_openspec_json(
        root, "tasks", progress_shaped, "instructions", "apply", "--change", name, "--json",
    )

    if result.returncode != 0:
        raise Failure(f"tasks: exit {result.returncode}", stop=BROKEN_CLI)

    progress = document["progress"]
    if progress["total"] == 0:
        raise Failure(f"tasks: {name} — no checkbox task in tasks.md", stop=FIX_ARTIFACTS)
    return progress["complete"], progress["total"]


def print_progress(complete, total):
    print(f"tasks: {complete}/{total} done")


def check_progress(root, name):
    complete, total = run_progress(root, name)
    print_progress(complete, total)
    return complete, total


def referenced_paths(text):
    paths = set()
    for line in DEFERRED_SECTION.sub("", text).splitlines():
        removed_end = None
        for match in PATH_TOKEN.finditer(line):
            path = match.group(1)
            before = line[: match.start()]
            if REMOVAL_VERB.search(before) or (
                removed_end is not None and REMOVAL_JOINER.fullmatch(line[removed_end : match.start()])
            ):
                removed_end = match.end()
                continue
            if IMPORT_PATH.match(path):
                continue
            if RENAME_VERB.search(before) and RENAME_TARGET.match(line, match.end()):
                continue
            if "." in path.rsplit("/", 1)[1] or path.count("/") >= 2:
                paths.add(path)
    return sorted(paths)


def check_paths(root, name):
    texts = []
    for artifact in ("tasks.md", "design.md"):
        path = os.path.join(root, CHANGES, name, artifact)
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                texts.append(f.read())
    missing = [path for path in referenced_paths("\n".join(texts)) if not os.path.exists(os.path.join(root, path))]
    for path in missing:
        print(f"missing: {path}")
    return missing


def render_archive_verdict(complete, total, validate_lines, missing):
    reasons = []
    if complete != total:
        reasons.append(f"{complete}/{total} tasks done")
    if validate_lines:
        reasons.append("validate warnings")
    if missing:
        reasons.append("missing paths")
    return f"archive: ask — {', '.join(reasons)}" if reasons else "archive: clean"


def stop_with(failure, archive_verdict_requested):
    if archive_verdict_requested:
        print("archive: stop")
    print(failure.message, file=sys.stderr)
    for line in failure.detail:
        print(line, file=sys.stderr)
    print(f"stop: {failure.stop}", file=sys.stderr)
    sys.exit(1)


def main():
    args = sys.argv[1:]
    if "--batch-arguments" in args:
        sys.exit(batch_arguments(args))
    archive_verdict_requested = "--archive-verdict" in args
    args = [arg for arg in args if arg != "--archive-verdict"]
    if len(args) > 1 or any(arg.startswith("-") for arg in args):
        usage()

    root = git_toplevel(os.getcwd())
    changes_real = os.path.realpath(os.path.join(root, CHANGES))

    try:
        if args:
            name, relpath = resolve(root, args[0], changes_real)
        else:
            pairs = discover(os.path.join(root, CHANGES))
            if not pairs:
                print("no open change under openspec/changes/ — name one", file=sys.stderr)
                sys.exit(EXIT_NONE)
            if len(pairs) > 1:
                for pair_name, pair_relpath in pairs:
                    print(f"{pair_name}\t{pair_relpath}")
                sys.exit(EXIT_SEVERAL)
            name, relpath = pairs[0]
        print(f"change: {name} — {relpath}")
        with ThreadPoolExecutor(max_workers=2) as executor:
            validate_future = executor.submit(run_validate, root, name)
            progress_future = executor.submit(run_progress, root, name)
            validate_lines = validate_future.result()
            print_validate(validate_lines)
            complete, total = progress_future.result()
            print_progress(complete, total)
        missing = check_paths(root, name)
    except Failure as failure:
        stop_with(failure, archive_verdict_requested)

    if archive_verdict_requested:
        print(render_archive_verdict(complete, total, validate_lines, missing))
    sys.exit(0)


if __name__ == "__main__":
    main()
