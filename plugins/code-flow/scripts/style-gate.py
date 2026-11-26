#!/usr/bin/env python3
import argparse
import collections
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

MODE_FORMAT = 'format'
MODE_LINT = 'lint'
DEADLINE_SECONDS = 90
TOOL_TIMEOUT_SECONDS = 30
GATE_WORKERS = 8
REPO_CONFIG_NAME = '.style-gate.json'
_HOST_USER_PATH = re.compile(r'/(?:Users|home)/[^\s/]+/')
_PLACEHOLDER = re.compile(r'\{(path|relpath)\}')

RULE_FAMILY_COVERAGE = {
    'Imports': ['goimports'],
}

Entry = collections.namedtuple(
    'Entry', ('mode', 'tool', 'command', 'check_command'), defaults=(None,)
)


def build_formatter_entry(tool, executable, path, write_args, check_args):
    return Entry(
        MODE_FORMAT,
        tool,
        [executable, *write_args, str(path)] if executable else None,
        [executable, *check_args, str(path)] if executable else None,
    )


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def find_tool(name, root):
    for directory in (root / 'node_modules/.bin', root / 'vendor/bin'):
        candidate = directory / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name)


def _ruff_configured(path):
    for directory in path.parents:
        if any((directory / name).is_file() for name in ('ruff.toml', '.ruff.toml')):
            return True
        pyproject = directory / 'pyproject.toml'
        if not pyproject.is_file():
            continue
        try:
            if '[tool.ruff' in pyproject.read_text():
                return True
        except OSError:
            continue
    return False


def _go_entries(path, root):
    goimports = find_tool('goimports', root)
    if goimports:
        yield build_formatter_entry('goimports', goimports, path, ['-w'], ['-d'])
    else:
        gofmt = find_tool('gofmt', root)
        yield build_formatter_entry(
            'gofmt' if gofmt else 'goimports', gofmt, path, ['-w'], ['-d']
        )
    if any(
        (directory / name).is_file()
        for directory in path.parents
        for name in ('.golangci.yml', '.golangci.yaml')
    ):
        linter = find_tool('golangci-lint', root)
        yield Entry(
            MODE_LINT,
            'golangci-lint',
            [linter, 'run', '--new-from-rev', 'HEAD', './.'] if linter else None,
            [linter, 'run', '--fix=false', '--new-from-rev', 'HEAD', './.']
            if linter
            else None,
        )


def _php_entries(path, root):
    fixer = find_tool('php-cs-fixer', root)
    yield build_formatter_entry(
        'php-cs-fixer',
        fixer,
        path,
        ['fix', '--quiet'],
        ['fix', '--dry-run', '--diff', '--using-cache=no'],
    )


def _js_entries(path, root):
    prettier = find_tool('prettier', root)
    yield build_formatter_entry('prettier', prettier, path, ['--write'], ['--check'])
    eslint = find_tool('eslint', root)
    yield Entry(MODE_LINT, 'eslint', [eslint, str(path)] if eslint else None)


def _py_entries(path, root):
    ruff = find_tool('ruff', root)
    if ruff is None:
        yield Entry(MODE_LINT, 'ruff', None)
        return
    if _ruff_configured(path):
        yield build_formatter_entry(
            'ruff', ruff, path, ['format'], ['format', '--check', '--no-cache']
        )
    yield Entry(
        MODE_LINT,
        'ruff',
        [ruff, 'check', '--output-format', 'concise', str(path)],
        [
            ruff,
            'check',
            '--no-fix',
            '--no-fix-only',
            '--no-cache',
            '--output-format',
            'concise',
            str(path),
        ],
    )


def _sh_entries(path, root):
    if (root / '.editorconfig').is_file():
        shfmt = find_tool('shfmt', root)
        yield build_formatter_entry('shfmt', shfmt, path, ['-w'], ['-d'])
    shellcheck = find_tool('shellcheck', root)
    yield Entry(
        MODE_LINT,
        'shellcheck',
        [shellcheck, '-f', 'gcc', str(path)] if shellcheck else None,
    )


TABLE = {
    '.go': _go_entries,
    '.php': _php_entries,
    '.js': _js_entries,
    '.jsx': _js_entries,
    '.ts': _js_entries,
    '.tsx': _js_entries,
    '.vue': _js_entries,
    '.py': _py_entries,
    '.sh': _sh_entries,
}

DEFAULT_TOOLS = (
    'eslint',
    'gofmt',
    'goimports',
    'golangci-lint',
    'php-cs-fixer',
    'prettier',
    'ruff',
    'shellcheck',
    'shfmt',
)


def _validate_template(name, key, template):
    if not isinstance(template, list):
        raise ValueError(f'{REPO_CONFIG_NAME}: {name}.{key} must be a list')
    for element in template:
        if not isinstance(element, str):
            raise ValueError(
                f'{REPO_CONFIG_NAME}: {name}.{key} entries must be strings'
            )
        if _HOST_USER_PATH.search(element):
            raise ValueError(
                f'{REPO_CONFIG_NAME}: {name}.{key} must not contain host user paths'
            )


def load_tool_config(repo):
    path = repo / REPO_CONFIG_NAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as error:
        raise ValueError(f'{REPO_CONFIG_NAME} is not valid JSON: {error}') from error
    if not isinstance(data, dict) or not isinstance(data.get('tools'), dict):
        raise ValueError(
            f'{REPO_CONFIG_NAME} must carry a "tools" object mapping tool names to settings'
        )
    for name, settings in data['tools'].items():
        if not isinstance(settings, dict):
            raise ValueError(
                f'{REPO_CONFIG_NAME}: {name} settings must be a JSON object'
            )
        for key in ('write', 'check'):
            template = settings.get(key)
            if template is not None:
                _validate_template(name, key, template)
    return data['tools']


def _resolve_configured_executable(settings, repo):
    template = settings.get('check') or settings.get('write')
    if not template:
        return None
    command = str(template[0])
    if os.sep in command or (os.altsep is not None and os.altsep in command):
        candidate = Path(command) if Path(command).is_absolute() else (repo / command)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None
    return find_tool(command, repo)


def _executable_fingerprint(executable):
    if executable is None:
        return None
    try:
        size = os.stat(executable).st_size
    except OSError:
        return None
    return {'path': executable, 'size': size}


def build_gate_fingerprint(repo):
    config_path = repo / REPO_CONFIG_NAME
    config_sha256 = (
        hashlib.sha256(config_path.read_bytes()).hexdigest()
        if config_path.is_file()
        else None
    )
    configured = load_tool_config(repo)
    tools = {}
    for name in sorted(set(DEFAULT_TOOLS) | set(configured)):
        if name in configured:
            settings = configured[name]
            entry = _executable_fingerprint(
                _resolve_configured_executable(settings, repo)
            )
            if entry is not None:
                check = settings.get('check')
                entry['check'] = list(check) if check is not None else None
            tools[name] = entry
        else:
            tools[name] = _executable_fingerprint(find_tool(name, repo))
    return {'config_sha256': config_sha256, 'tools': tools}


def configured_entry(name, settings, path, repo):
    if path.suffix not in settings.get('extensions', []):
        return None
    mode = settings.get('mode', MODE_LINT)
    if mode not in (MODE_FORMAT, MODE_LINT):
        raise ValueError(f'{REPO_CONFIG_NAME}: {name} carries an invalid mode')
    try:
        rel_path = path.relative_to(repo)
    except ValueError:
        return None
    argument = str(path)
    rel_argument = rel_path.as_posix()
    strip_prefix = settings.get('strip_prefix')
    if strip_prefix:
        try:
            stripped = rel_path.relative_to(strip_prefix)
        except ValueError:
            return None
        argument = stripped.as_posix()
        rel_argument = argument

    substitutions = {'path': argument, 'relpath': rel_argument}

    def build(key):
        template = settings.get(key)
        if template is None:
            return None
        return [
            _PLACEHOLDER.sub(lambda match: substitutions[match.group(1)], part)
            for part in template
        ]

    command = build('write') or build('check')
    if command is None:
        return None
    return Entry(mode, name, command, build('check'))


def entries(path, root):
    configured = {}
    for name, settings in load_tool_config(root).items():
        entry = configured_entry(name, settings, path, root)
        if entry is not None:
            configured[name] = entry
    resolver = TABLE.get(path.suffix)
    if resolver is None:
        yield from configured.values()
        return
    for entry in resolver(path, root):
        if entry.tool not in configured:
            yield entry
    yield from configured.values()


def commands(path, root):
    for entry in entries(path, root):
        if entry.command is not None:
            yield entry.command


class _DeferredTarget:
    def __init__(self, record, path, slot):
        self.record = record
        self.path = path
        self.slot = slot
        self.attached = False
        self.expired = False
        self.ready_to_finalize = False


def _write_temp_log(content):
    with tempfile.NamedTemporaryFile(
        prefix='code-flow-style-gate-', suffix='.log', delete=False
    ) as log:
        log.write(content)
        return log.name


def _run_head_preflight(repo, environment, remaining):
    git = find_tool('git', repo) or 'git'
    head = execute(
        Entry(MODE_LINT, 'git', [git, 'rev-parse', '--verify', '-q', 'HEAD']),
        repo,
        min(TOOL_TIMEOUT_SECONDS, remaining),
        environment,
    )
    if head['exit_status'] in (0, 1) and not head['timed_out']:
        Path(head['log_path']).unlink()
        return head['exit_status'] == 0, None
    head['preflight_command'] = head['command']
    return False, head


class _SharedState:
    def __init__(self, counts):
        self.lock = threading.Lock()
        self._head_lock = threading.Lock()
        self._head_checked = False
        self._has_head = False
        self._head_error = None
        self._counts = dict(counts)
        self._deferred = collections.defaultdict(list)

    def resolve_golangci_cwd(self, entry, path, repo, environment, remaining):
        with self._head_lock:
            if not self._head_checked:
                self._cache_head_preflight(repo, environment, remaining)
        if self._head_error is None:
            return (path.parent if self._has_head else None), None
        template, log_bytes = self._head_error
        outcome = dict(template)
        outcome['tool'] = entry.tool
        outcome['command'] = entry.command
        if log_bytes is not None:
            outcome['log_path'] = _write_temp_log(log_bytes)
        return None, outcome

    def _cache_head_preflight(self, repo, environment, remaining):
        has_head, head_error = _run_head_preflight(repo, environment, remaining)
        self._has_head = has_head
        if head_error is not None:
            log_path = head_error.get('log_path')
            log_bytes = None
            if log_path and Path(log_path).is_file():
                log_bytes = Path(log_path).read_bytes()
                Path(log_path).unlink()
            head_error['log_path'] = None
            self._head_error = (head_error, log_bytes)
        self._head_checked = True

    def decrement_without_run(self, key):
        with self.lock:
            remaining_count = self._counts.get(key, 0) - 1
            self._counts[key] = remaining_count
            if remaining_count <= 0:
                return self._deferred.pop(key, [])
        return []

    def claim_or_defer(self, key, record, path):
        with self.lock:
            remaining_count = self._counts.get(key, 0) - 1
            self._counts[key] = remaining_count
            if remaining_count > 0:
                slot = len(record['tools'])
                record['tools'].append(None)
                target = _DeferredTarget(record, path, slot)
                self._deferred[key].append(target)
                return target, []
            return None, self._deferred.pop(key, [])

    def pop_all_deferred(self):
        with self.lock:
            targets = [item for group in self._deferred.values() for item in group]
            self._deferred.clear()
            return targets


def _build_shared_state(repo, paths, check_only):
    if len(paths) <= 1:
        return None
    counts = collections.Counter()
    for path in paths:
        if path.suffix != '.go':
            continue
        for entry in entries(path, repo):
            if check_only and entry.check_command is not None:
                entry = entry._replace(command=entry.check_command)
            if entry.tool == 'golangci-lint' and entry.command is not None:
                counts[(path.parent, entry.mode, tuple(entry.command))] += 1
    return _SharedState(counts)


def resolve_tool_working_directory(
    entry, path, repo, environment, remaining, shared_state=None
):
    if entry.tool != 'golangci-lint':
        return repo, None
    if shared_state is not None:
        return shared_state.resolve_golangci_cwd(
            entry, path, repo, environment, remaining
        )
    has_head, head = _run_head_preflight(repo, environment, remaining)
    if head is None:
        return (path.parent if has_head else None), None
    head['tool'] = entry.tool
    head['command'] = entry.command
    return None, head


def execute(entry, cwd, timeout, environment):
    outcome = {
        'tool': entry.tool,
        'mode': entry.mode,
        'command': entry.command,
        'exit_status': None,
        'timed_out': False,
        'mutated': False,
        'diagnostics': None,
        'log_path': None,
    }
    with tempfile.NamedTemporaryFile(
        prefix='code-flow-style-gate-', suffix='.log', delete=False
    ) as log:
        outcome['log_path'] = log.name
        try:
            process = subprocess.Popen(
                entry.command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == 'posix',
            )
        except OSError as error:
            outcome['diagnostics'] = f'{entry.command[0]}: {error}'
            return outcome
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            outcome['timed_out'] = True
            if os.name != 'posix':
                process.kill()
        finally:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if outcome['timed_out']:
            process.wait()
        outcome['exit_status'] = process.returncode
    if outcome['timed_out']:
        outcome['diagnostics'] = f'{entry.tool} exceeded {timeout:.0f} seconds'
    elif outcome['exit_status']:
        output = Path(log.name).read_text(errors='replace').strip()
        outcome['diagnostics'] = (
            f'{entry.tool} exited {outcome["exit_status"]}\n{output}'
        )
    return outcome


def digest(path):
    if os.path.islink(path):
        return hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def covered_families(tools):
    clean = {
        outcome['tool']
        for outcome in tools
        if outcome.get('exit_status') == 0
        and not outcome.get('timed_out')
        and not outcome.get('diagnostics')
        and not outcome.get('skipped')
    }
    return [
        family
        for family, required in RULE_FAMILY_COVERAGE.items()
        if required and all(tool in clean for tool in required)
    ]


def finalize_record(record, path):
    record['sha256'] = digest(path)
    record['mutated'] = any(outcome['mutated'] for outcome in record['tools'])
    record['covered_families'] = covered_families(record['tools'])


def _build_lint_outcome(
    entry, base_outcome, before_digest, path, check_only, log_bytes=None
):
    outcome = dict(base_outcome)
    if log_bytes is not None:
        outcome['log_path'] = _write_temp_log(log_bytes)
    outcome['mutated'] = digest(path) != before_digest
    if check_only and outcome['mutated']:
        outcome['diagnostics'] = (
            f'{outcome["diagnostics"] or ""}\n{entry.tool} mutated a check-only input'
        ).strip()
    return outcome


def _finalize_expired_targets(targets, shared_state):
    for item in targets:
        with shared_state.lock:
            item.expired = True
            ready = item.ready_to_finalize
        if ready:
            item.record['tools'] = [
                tool for tool in item.record['tools'] if tool is not None
            ]
            finalize_record(item.record, item.path)


def _finish_record(record, path, deferred_targets, shared_state):
    if not deferred_targets:
        finalize_record(record, path)
        return
    should_finalize = True
    with shared_state.lock:
        for target in deferred_targets:
            if not target.attached and not target.expired:
                target.ready_to_finalize = True
                should_finalize = False
    if should_finalize:
        record['tools'] = [tool for tool in record['tools'] if tool is not None]
        finalize_record(record, path)


def _execute_shared_golangci(
    entry, path, cwd, remaining, environment, check_only, targets, shared_state
):
    before_targets = [(item, digest(item.path)) for item in targets]
    before = digest(path)
    outcome = execute(entry, cwd, min(TOOL_TIMEOUT_SECONDS, remaining), environment)
    current_outcome = _build_lint_outcome(entry, outcome, before, path, check_only)
    if not before_targets:
        return current_outcome
    log_path = outcome.get('log_path')
    log_bytes = Path(log_path).read_bytes() if log_path else None
    for item, item_before in before_targets:
        item_outcome = _build_lint_outcome(
            entry, outcome, item_before, item.path, check_only, log_bytes=log_bytes
        )
        with shared_state.lock:
            item.record['tools'][item.slot] = item_outcome
            item.attached = True
            ready = item.ready_to_finalize
        if ready:
            finalize_record(item.record, item.path)
    return current_outcome


def _resolve_entry_run(entry, path, repo, environment, deadline, shared_state):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, 0.0, None, True
    cwd, preflight_error = resolve_tool_working_directory(
        entry, path, repo, environment, remaining, shared_state=shared_state
    )
    if preflight_error is not None:
        return None, remaining, preflight_error, False
    if cwd is None:
        return (
            None,
            remaining,
            {
                'tool': entry.tool,
                'mode': entry.mode,
                'command': entry.command,
                'skipped': True,
                'reason': 'no git HEAD',
                'exit_status': None,
                'timed_out': False,
                'mutated': False,
                'diagnostics': None,
                'log_path': None,
            },
            False,
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, 0.0, None, True
    return cwd, remaining, None, False


def _run_entry(
    entry,
    path,
    repo,
    environment,
    deadline,
    check_only,
    record,
    deferred_targets,
    shared_state,
):
    if check_only and entry.check_command is not None:
        entry = entry._replace(command=entry.check_command)
    if entry.command is None:
        if entry.tool not in record['uncovered']:
            record['uncovered'].append(entry.tool)
        return False
    key = None
    if (
        shared_state is not None
        and path.suffix == '.go'
        and entry.tool == 'golangci-lint'
    ):
        key = (path.parent, entry.mode, tuple(entry.command))
    cwd, remaining, skip_outcome, exceeded = _resolve_entry_run(
        entry, path, repo, environment, deadline, shared_state
    )
    if cwd is None:
        if key is not None:
            _finalize_expired_targets(
                shared_state.decrement_without_run(key), shared_state
            )
        if skip_outcome is not None:
            record['tools'].append(skip_outcome)
        return exceeded
    if key is not None:
        target, targets = shared_state.claim_or_defer(key, record, path)
        if target is not None:
            deferred_targets.append(target)
            return False
        outcome = _execute_shared_golangci(
            entry, path, cwd, remaining, environment, check_only, targets, shared_state
        )
        record['tools'].append(outcome)
        return False
    before = digest(path)
    outcome = execute(entry, cwd, min(TOOL_TIMEOUT_SECONDS, remaining), environment)
    outcome = _build_lint_outcome(entry, outcome, before, path, check_only)
    if (
        check_only
        and entry.tool in ('goimports', 'gofmt')
        and outcome['exit_status'] == 0
        and not outcome['timed_out']
        and not outcome['mutated']
    ):
        output = Path(outcome['log_path']).read_text(errors='replace').strip()
        if output:
            outcome['diagnostics'] = f'{entry.tool} requires formatting\n{output}'
    record['tools'].append(outcome)
    return False


def run_file(path, repo, environment, deadline, check_only=False, shared_state=None):
    record = {
        'path': display_path(path, repo),
        'sha256': digest(path),
        'mutated': False,
        'tools': [],
        'uncovered': [],
        'covered_families': [],
    }
    ordered = sorted(entries(path, repo), key=lambda entry: entry.mode != MODE_FORMAT)
    deferred_targets = []
    exceeded = False
    for entry in ordered:
        if _run_entry(
            entry,
            path,
            repo,
            environment,
            deadline,
            check_only,
            record,
            deferred_targets,
            shared_state,
        ):
            exceeded = True
            break
    _finish_record(record, path, deferred_targets, shared_state)
    return record, exceeded


def display_path(path, repo):
    try:
        return str(path.relative_to(repo))
    except ValueError:
        return str(path)


def group_inputs(paths):
    groups = {}
    for index, path in enumerate(paths):
        try:
            stat = os.stat(path)
        except OSError:
            identity = path.resolve()
        else:
            identity = (stat.st_dev, stat.st_ino)
        groups.setdefault(identity, []).append((index, path))
    return list(groups.values())


def run_occurrences(
    occurrences, repo, environment, deadline, check_only, shared_state=None
):
    records = []
    exceeded = False
    for index, path in occurrences:
        record, exceeded = run_file(
            path, repo, environment, deadline, check_only, shared_state=shared_state
        )
        records.append((index, record))
        if exceeded:
            break
    return records, exceeded


def run_gate(repo, paths, deadline_seconds=DEADLINE_SECONDS, check_only=False):
    deadline = time.monotonic() + deadline_seconds
    environment = dict(os.environ, CI='1', NO_COLOR='1', TERM='dumb', PAGER='cat')
    report = {
        'repo': str(repo),
        'check_only': check_only,
        'deadline_seconds': deadline_seconds,
        'deadline_exceeded': False,
        'rule_family_coverage': RULE_FAMILY_COVERAGE,
        'gate_fingerprint': build_gate_fingerprint(repo),
        'uncovered': [],
        'files': [],
    }
    shared_state = _build_shared_state(repo, paths, check_only)
    collected = []
    pending = collections.deque(group_inputs(paths))
    with ThreadPoolExecutor(max_workers=GATE_WORKERS) as pool:
        futures = set()

        def submit(group):
            futures.add(
                pool.submit(
                    run_occurrences,
                    group,
                    repo,
                    environment,
                    deadline,
                    check_only,
                    shared_state,
                )
            )

        if pending:
            submit(pending.popleft())
        while pending or futures:
            if pending and len(futures) < GATE_WORKERS:
                if time.monotonic() >= deadline:
                    report['deadline_exceeded'] = True
                    pending.clear()
                    continue
                submit(pending.popleft())
                continue
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                records, exceeded = future.result()
                collected.extend(records)
                if exceeded:
                    report['deadline_exceeded'] = True
    if shared_state is not None:
        remaining_targets = shared_state.pop_all_deferred()
        if remaining_targets:
            report['deadline_exceeded'] = True
            _finalize_expired_targets(remaining_targets, shared_state)
    records = dict(collected)
    report['files'] = []
    uncovered = set()
    for index in range(len(paths)):
        if index not in records:
            break
        record = records[index]
        report['files'].append(record)
        uncovered.update(record.get('uncovered', ()))
    report['uncovered'] = sorted(uncovered)
    return report


def resolve_files(repo, names):
    repo = repo.resolve(strict=True)
    paths = []
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = repo / path
        resolved = path.resolve()
        if not resolved.is_relative_to(repo):
            raise ValueError(f'file is outside --repo: {name}')
        if path.is_symlink() and resolved.is_dir():
            paths.append(Path(os.path.abspath(path)))
            continue
        if not resolved.is_file():
            raise ValueError(f'not a file: {name}')
        paths.append(resolved)
    return paths


def parse_args(argv):
    parser = Parser(
        description='Run the formatter/linter table over a scoped file list. A repository may pin tools in a .style-gate.json config at its root.'
    )
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--files-from', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--fingerprint', action='store_true')
    parser.add_argument('files', nargs='*')
    args = parser.parse_args(argv)
    if args.fingerprint:
        return args.repo, [], False, True
    names = list(args.files)
    if args.files_from is not None:
        message = '--files-from must carry a JSON list of file paths'
        try:
            data = json.loads(args.files_from.read_text())
        except json.JSONDecodeError as error:
            raise ValueError(message) from error
        if not isinstance(data, list) or not all(
            isinstance(item, str) for item in data
        ):
            raise ValueError(message)
        names.extend(data)
    if not names:
        raise ValueError('at least one file argument or --files-from entry is required')
    return args.repo, names, args.check_only, False


def _version_key(path):
    parts = path.parents[1].name.split('.')
    if not all(part.isdigit() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def _cache_recorder():
    candidates = (
        Path(__file__).resolve().parents[3].glob('usage-metrics/*/scripts/metrics.py')
    )
    return max(candidates, key=_version_key, default=None)


def _recorder_module():
    explicit = os.environ.get('AGENTS_METRICS_MODULE')
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_file():
            return None
    else:
        source = (
            Path(__file__).resolve().parents[2]
            / 'usage-metrics'
            / 'scripts'
            / 'metrics.py'
        )
        if not source.is_file():
            source = _cache_recorder()
            if source is None:
                home = Path(
                    os.environ.get('AGENTS_HOME') or str(Path.home() / '.agents')
                )
                source = home / 'plugins' / 'usage-metrics' / 'scripts' / 'metrics.py'
            if not source.is_file():
                return None
    try:
        specification = importlib.util.spec_from_file_location(
            'usage_metrics_recorder', source
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
    except Exception:
        return None
    return module


def _record_run(status, started):
    if os.environ.get('AGENTS_METRICS') == 'off':
        return
    try:
        module = _recorder_module()
        if module is not None:
            module.record('code-flow', 'style-gate', status, time.monotonic() - started)
    except Exception:
        pass


def main(argv=None):
    try:
        repo, names, check_only, fingerprint = parse_args(argv)
        repo = repo.resolve(strict=True)
        if not repo.is_dir():
            raise ValueError(f'--repo must be a directory: {repo}')
        if fingerprint:
            report = build_gate_fingerprint(repo)
            code = 0
        else:
            paths = resolve_files(repo, names)
            report = run_gate(repo, paths, check_only=check_only)
            diagnostics = [
                outcome['diagnostics']
                for record in report['files']
                for outcome in record['tools']
                if outcome.get('diagnostics')
            ]
            code = int(bool(diagnostics) or report['deadline_exceeded'])
    except (OSError, ValueError) as error:
        report = {'error': str(error)}
        code = 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
    return code


if __name__ == '__main__':
    started = time.monotonic()
    status = 'fail'
    try:
        code = main()
    except SystemExit as exit_request:
        code = exit_request.code
        status = 'ok' if not exit_request.code else 'fail'
        raise
    else:
        status = 'ok' if not code else 'fail'
    finally:
        _record_run(status, started)
    sys.exit(code)
