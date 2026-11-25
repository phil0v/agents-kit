#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import _tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None


def find_context(content, expected, cursor, end=False):
    positions = [len(content) - len(expected)] if end else range(cursor, len(content) - len(expected) + 1)
    for normalize in (lambda line: line, str.rstrip, str.strip):
        for position in positions:
            if position >= cursor and [normalize(line) for line in content[position:position + len(expected)]] == [normalize(line) for line in expected]:
                return position
    raise ValueError("patch context not found")


def apply_hunks(current, block):
    content = current.splitlines()
    cursor = 0
    chunks = []
    chunk = []
    anchor = None
    for line in block + ["@@"]:
        if line.startswith("@@") or line == "*** End of File":
            if chunk:
                chunks.append((anchor, chunk, line == "*** End of File"))
                chunk = []
            anchor = line[3:] if line.startswith("@@ ") else None
            continue
        chunk.append(line)
    for anchor, chunk, end in chunks:
        if anchor:
            cursor = find_context(content, [anchor], cursor) + 1
        old = [line[1:] for line in chunk if line[0] != "+"]
        new = [line[1:] for line in chunk if line[0] != "-"]
        start = cursor
        if old:
            try:
                start = find_context(content, old, cursor, end)
            except ValueError:
                if old[-1] != "":
                    raise
                old = old[:-1]
                if new[-1:] == [""]:
                    new = new[:-1]
                start = find_context(content, old, cursor, end)
        elif anchor is None:
            start = len(content)
        content[start:start + len(old)] = new
        cursor = start + len(new)
    return "\n".join(content) + "\n"


def patch_files(command, cwd, preview):
    lines = command.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ValueError("expected an apply_patch patch")
    files = {}
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        operation, separator, name = header.partition(": ")
        if not separator or operation not in ("*** Add File", "*** Update File", "*** Delete File"):
            raise ValueError(f"invalid patch header: {header}")
        source = (cwd / name).resolve()
        destination = source
        if operation == "*** Delete File":
            files[source] = None
            continue
        if operation == "*** Update File" and lines[index].startswith("*** Move to: "):
            destination = (cwd / lines[index][13:]).resolve()
            files[source] = None
            index += 1
        block = []
        while index < len(lines) - 1 and (not lines[index].startswith("*** ")
                                        or lines[index] == "*** End of File"):
            block.append(lines[index])
            index += 1
        if operation == "*** Add File":
            if any(not line.startswith("+") for line in block):
                raise ValueError(f"invalid added file: {name}")
            files[destination] = "\n".join(line[1:] for line in block) + "\n"
            continue
        if not preview:
            files[destination] = ""
            continue
        current = files.get(source)
        if current is None:
            current = source.read_text()
        block = [line or " " for line in block]
        for line in block:
            if line.startswith("@@") or line == "*** End of File":
                continue
            if line[0] not in " +-":
                raise ValueError(f"invalid hunk in {name}")
        files[destination] = apply_hunks(current, block)
    return files


def claude_files(tool_input, before):
    path = Path(tool_input["file_path"])
    if not before:
        return {path: ""}
    if "content" in tool_input:
        return {path: tool_input["content"]}
    try:
        current = path.read_text()
    except OSError:
        return {}
    old = tool_input["old_string"]
    if old not in current:
        return {}
    return {path: current.replace(old, tool_input["new_string"], -1 if tool_input.get("replace_all") else 1)}


def antigravity_files(tool_call, cwd, before):
    if not isinstance(tool_call, dict):
        raise ValueError("toolCall must be an object")
    name = tool_call.get("name")
    args = tool_call.get("args")
    if not isinstance(args, dict):
        raise ValueError("toolCall.args must be an object")
    raw_path = args.get("Path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("toolCall.args.Path must be a non-empty string")
    if name not in ("write_file", "edit_file"):
        raise ValueError(f"unsupported Antigravity tool: {name}")
    path = cwd / raw_path
    if not before:
        return {path: ""}
    if name == "write_file":
        content = args.get("Content")
        if not isinstance(content, str):
            raise ValueError("write_file args.Content must be a string")
        append = args.get("Append")
        if append is not None and not isinstance(append, bool):
            raise ValueError("write_file args.Append must be a boolean")
        if append is True and path.is_file():
            return {path: path.read_text() + content}
        return {path: content}
    target = args.get("Target")
    replacement = args.get("Replacement")
    if not isinstance(target, str) or not isinstance(replacement, str):
        raise ValueError("edit_file args.Target and args.Replacement must be strings")
    try:
        current = path.read_text()
    except (OSError, UnicodeDecodeError):
        return {}
    if target not in current:
        return {}
    return {path: current.replace(target, replacement)}


def reject_constant(value):
    raise ValueError(f"{value} is not valid JSON")


def validate(path, content):
    is_config = (path.name in ("config.toml", "hooks.json", "package.json", "pyproject.toml")
                 or (path.parent.name == "agents" and path.suffix == ".toml")
                 or (path.name.startswith("settings") and path.suffix == ".json"))
    if not is_config:
        return
    if path.suffix == ".json":
        json.loads(content, parse_constant=reject_constant)
    elif path.suffix == ".toml":
        if tomllib is not None:
            tomllib.loads(content)


def find_tool(name, root):
    for directory in (root / "node_modules/.bin", root / "vendor/bin"):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def _ruff_configured(path):
    for directory in path.parents:
        if any((directory / name).is_file() for name in ("ruff.toml", ".ruff.toml")):
            return True
        pyproject = directory / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            if "[tool.ruff" in pyproject.read_text():
                return True
        except OSError:
            continue
    return False


def project_root(path):
    for directory in path.parents:
        if (directory / ".git").exists():
            return directory
    return path.parent


def _embedded_commands(path, root):
    extension = path.suffix
    if extension == ".go":
        formatter = find_tool("goimports", root) or find_tool("gofmt", root)
        if formatter:
            yield [formatter, "-w", str(path)]
        if any((directory / name).is_file() for directory in path.parents
               for name in (".golangci.yml", ".golangci.yaml")):
            linter = find_tool("golangci-lint", root)
            if linter:
                yield [linter, "run", "--new-from-rev", "HEAD", "./."]
    elif extension == ".php":
        formatter = find_tool("php-cs-fixer", root)
        if formatter:
            yield [formatter, "fix", "--quiet", str(path)]
    elif extension in (".js", ".jsx", ".ts", ".tsx", ".vue"):
        formatter = find_tool("prettier", root)
        if formatter:
            yield [formatter, "--write", str(path)]
        linter = find_tool("eslint", root)
        if linter:
            yield [linter, str(path)]
    elif extension == ".py":
        linter = find_tool("ruff", root)
        if linter:
            if _ruff_configured(path):
                yield [linter, "format", str(path)]
            yield [linter, "check", "--output-format", "concise", str(path)]
    elif extension == ".sh":
        formatter = find_tool("shfmt", root)
        if formatter and (root / ".editorconfig").is_file():
            yield [formatter, "-w", str(path)]
        linter = find_tool("shellcheck", root)
        if linter:
            yield [linter, "-f", "gcc", str(path)]


def _shared_commands():
    source = Path(__file__).resolve().parent / "style-gate.py"
    try:
        specification = importlib.util.spec_from_file_location("code_flow_style_gate", source)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module.commands
    except Exception:
        return None


commands = _shared_commands() or _embedded_commands


def _cache_recorder():
    candidates = Path(__file__).resolve().parents[3].glob("usage-metrics/*/scripts/metrics.py")
    return max(candidates, key=_version_key, default=None)


def _version_key(path):
    parts = path.parents[1].name.split(".")
    if not all(part.isdigit() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def _recorder_module():
    explicit = os.environ.get("AGENTS_METRICS_MODULE")
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_file():
            return None
    else:
        source = Path(__file__).resolve().parents[2] / "usage-metrics" / "scripts" / "metrics.py"
        if not source.is_file():
            source = _cache_recorder()
            if source is None:
                home = Path(os.environ.get("AGENTS_HOME") or str(Path.home() / ".agents"))
                source = home / "plugins" / "usage-metrics" / "scripts" / "metrics.py"
            if not source.is_file():
                return None
    try:
        specification = importlib.util.spec_from_file_location("usage_metrics_recorder", source)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
    except Exception:
        return None
    return module


def _record_run(status, started):
    if os.environ.get("AGENTS_METRICS") == "off":
        return
    try:
        module = _recorder_module()
        if module is not None:
            module.record("code-flow", "edit-checks", status, time.monotonic() - started)
    except Exception:
        pass


def check_files(files):
    deadline = time.monotonic() + 90
    diagnostics = []
    environment = dict(os.environ, CI="1", NO_COLOR="1", TERM="dumb")
    for path, content in files.items():
        if content is None or not path.is_file():
            continue
        try:
            validate(path, path.read_text())
        except (OSError, ValueError) as error:
            diagnostics.append(f"{path}: {error}")
            continue
        root = project_root(path)
        for command in commands(path, root):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                diagnostics.append("edit checks exceeded 90 seconds")
                return diagnostics
            try:
                directory = root
                if path.suffix == ".go" and Path(command[0]).name == "golangci-lint":
                    git = find_tool("git", root)
                    if git is None:
                        continue
                    head = subprocess.run([git, "rev-parse", "--verify", "-q", "HEAD"],
                                          cwd=root, env=environment, capture_output=True,
                                          text=True, timeout=min(30, remaining))
                    if head.returncode:
                        continue
                    directory = path.parent
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        diagnostics.append("edit checks exceeded 90 seconds")
                        return diagnostics
                result = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                                        text=True, timeout=min(30, remaining))
            except (OSError, subprocess.TimeoutExpired) as error:
                diagnostics.append(f"{command[0]}: {error}")
                continue
            if result.returncode:
                diagnostics.append(f"{path}: {Path(command[0]).name} exited {result.returncode}\n"
                                   + (result.stdout + result.stderr).strip())
    return diagnostics


class GuardVerdictExit(SystemExit):
    pass


def _emit_pre_decision(antigravity, decision, reason=None):
    if antigravity:
        output = {"decision": decision}
        if reason is not None:
            output["reason"] = reason
        print(json.dumps(output))
        return
    if decision == "deny":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                          "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def _emit_post_result(antigravity, diagnostics_text=""):
    if diagnostics_text:
        print(diagnostics_text[:12000], file=sys.stderr)
    if antigravity:
        print("{}")
        return
    if diagnostics_text:
        raise GuardVerdictExit(2)


def main():
    antigravity = ("--antigravity" in sys.argv[1:]
                   or os.environ.get("LOCAL_GUARDS_HOOK_FORMAT") == "antigravity")
    before = True
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be an object")
        if not antigravity and ("toolCall" in payload or "hookEventName" in payload):
            antigravity = True
        event_name = payload.get("hookEventName") or payload.get("hook_event_name")
        before = event_name == "PreToolUse"
        cwd = Path(payload.get("cwd") or os.getcwd())
        if antigravity:
            files = antigravity_files(payload["toolCall"], cwd, before)
        else:
            tool_input = payload["tool_input"]
            if "command" in tool_input:
                files = patch_files(tool_input["command"], cwd, before)
            elif "file_path" in tool_input:
                files = claude_files(tool_input, before)
            else:
                raise ValueError("tool input carries neither a patch command nor a file path")
        if before:
            for path, content in files.items():
                if content is not None:
                    validate(path, content)
            _emit_pre_decision(antigravity, "allow")
            return
        diagnostics = check_files(files)
        _emit_post_result(antigravity, "\n".join(diagnostics) if diagnostics else "")
    except (OSError, ValueError, KeyError) as error:
        reason = f"Edit checks: {error}"
        if before:
            _emit_pre_decision(antigravity, "deny", reason)
            return
        _emit_post_result(antigravity, reason)


if __name__ == "__main__":
    started = time.monotonic()
    status = "fail"
    try:
        main()
        status = "ok"
    except GuardVerdictExit:
        status = "ok"
        raise
    except SystemExit as exit_request:
        status = "ok" if not exit_request.code else "fail"
        raise
    finally:
        _record_run(status, started)
