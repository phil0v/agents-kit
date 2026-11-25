#!/usr/bin/env python3
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

CAPTURE_WORKERS = 8


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def git(repo, *arguments, allowed=(0,)):
    result = subprocess.run(
        ['git', '-C', str(repo), *arguments], capture_output=True,
        env=os.environ | {'GIT_OPTIONAL_LOCKS': '0'},
    )
    if result.returncode not in allowed:
        raise ValueError(f'git {arguments[0]}: {os.fsdecode(result.stderr).strip()}')
    return result.stdout


def cached_diff(repo, visibility):
    records = git(repo, 'diff', '--cached', '--raw', '-z', '--no-abbrev',
                  '--no-renames', '--no-ext-diff', '--no-textconv', '--no-color',
                  '--no-relative', '--ignore-submodules=none',
                  f'--ita-{visibility}-in-index', '--').split(b'\0')[:-1]
    return dict(zip(records[1::2], records[::2]))


def repository_inputs(repo, scope=None):
    commit = git(repo, 'rev-parse', '--verify', '--quiet', 'HEAD', allowed=(0, 1)).strip()
    ref = git(repo, 'symbolic-ref', '--quiet', 'HEAD', allowed=(0, 1)).strip()
    visible = cached_diff(repo, 'visible')
    invisible = cached_diff(repo, 'invisible')
    index = []
    for entry in git(repo, 'ls-files', '-v', '--stage', '-z').split(b'\0')[:-1]:
        identity, path = entry.split(b'\t', 1)
        tag, mode, object_id, stage = identity.decode('ascii').split()
        index.append({'path': os.fsdecode(path), 'mode': mode,
                      'object': object_id, 'stage': int(stage),
                      'assume_unchanged': tag.islower(), 'skip_worktree': tag.upper() == 'S',
                      'intent_to_add': visible.get(path) != invisible.get(path)})
    if scope is not None:
        index = [entry for entry in index if entry['path'] in scope]
    tracked = sorted({entry['path'] for entry in index})
    untracked = sorted(os.fsdecode(path) for path in
                       git(repo, 'ls-files', '--others', '--exclude-standard', '-z').split(b'\0')[:-1])
    if scope is not None:
        untracked = [path for path in untracked if path in scope]
    return {
        'head': {'commit': os.fsdecode(commit) if commit else None,
                 'ref': os.fsdecode(ref) if ref else None},
        'index': sorted(index, key=lambda entry: (entry['path'], entry['stage'])),
        'tracked_paths': tracked, 'untracked_paths': untracked,
    }


def symlink_entry(directory, name, metadata):
    return {'type': 'symlink', 'mode': stat.S_IMODE(metadata.st_mode),
            'target': os.readlink(name, dir_fd=directory)}


def file_entry(directory, name):
    try:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return {'type': 'missing'}, 0
    if stat.S_ISLNK(metadata.st_mode):
        return symlink_entry(directory, name, metadata), 0
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f'unsupported file type: {name}')
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as source:
        opened = os.fstat(source.fileno())
        if (metadata.st_dev, metadata.st_ino, metadata.st_mode) != (opened.st_dev, opened.st_ino, opened.st_mode):
            raise ValueError(f'file changed during capture: {name}')
        digest = hashlib.sha256()
        hashed_bytes = 0
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            hashed_bytes += len(chunk)
        finished = os.fstat(source.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            finished.st_size, finished.st_mtime_ns, finished.st_ctime_ns
        ):
            raise ValueError(f'file changed during capture: {name}')
    return {'type': 'file', 'mode': stat.S_IMODE(metadata.st_mode),
            'sha256': digest.hexdigest()}, hashed_bytes


def path_entry(root, path):
    parts = path.split('/')
    directory = os.dup(root)
    try:
        for position, part in enumerate(parts[:-1]):
            try:
                metadata = os.stat(part, dir_fd=directory, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                return {'type': 'missing'}, 0
            if stat.S_ISLNK(metadata.st_mode):
                entry = symlink_entry(directory, part, metadata)
                return entry | {'type': 'symlink-parent', 'ancestor': '/'.join(parts[:position + 1])}, 0
            if not stat.S_ISDIR(metadata.st_mode):
                return {'type': 'missing'}, 0
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        return file_entry(directory, parts[-1])
    finally:
        os.close(directory)


def capture(repo, scope=None):
    started = time.perf_counter()
    root = Path(os.fsdecode(git(repo, 'rev-parse', '--show-toplevel'))[:-1]).resolve(strict=True)
    scope_set = set(scope) if scope is not None else None
    inputs = repository_inputs(root, scope_set)
    paths = sorted(set(inputs['tracked_paths']) | set(inputs['untracked_paths']))
    for path in paths:
        if not valid_path(path):
            raise ValueError(f'unsupported Git path: {path!r}')
    worktree = {}
    hashed_bytes = 0
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        with ThreadPoolExecutor(max_workers=CAPTURE_WORKERS) as pool:
            for path, (entry, size) in zip(paths, pool.map(lambda item: path_entry(descriptor, item), paths)):
                worktree[path] = entry
                hashed_bytes += size
    finally:
        os.close(descriptor)
    if repository_inputs(root, scope_set) != inputs:
        raise ValueError('repository inputs changed during capture')
    return {
        'snapshot': {
            'version': 1, 'repo': str(root),
            'scope': sorted(scope_set) if scope_set is not None else None,
            **inputs, 'worktree': worktree,
        },
        'metrics': {'elapsed_seconds': time.perf_counter() - started, 'hashed_bytes': hashed_bytes},
    }


def load_scope(path):
    with open(path, encoding='utf-8') as source:
        try:
            scope = json.load(source)
        except json.JSONDecodeError as error:
            raise ValueError(f'invalid scope: {path}') from error
    if not isinstance(scope, list) or not all(valid_path(name) for name in scope):
        raise ValueError(f'invalid scope: {path}')
    return set(scope)


def valid_path(value):
    return isinstance(value, str) and bool(value) and '\0' not in value and all(
        part not in ('', '.', '..') for part in value.split('/')
    )


def valid_path_list(value):
    return isinstance(value, list) and all(valid_path(name) for name in value) and value == sorted(set(value))


def valid_object(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value) is not None


def validate_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError('invalid worktree entry')
    kind = entry.get('type')
    if kind == 'missing' and set(entry) == {'type'}:
        return
    if type(entry.get('mode')) is not int or not 0 <= entry['mode'] <= 0o7777:
        raise ValueError('invalid worktree mode')
    if kind == 'file' and set(entry) == {'type', 'mode', 'sha256'} and isinstance(entry['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']):
        return
    if kind in ('symlink', 'symlink-parent') and isinstance(entry.get('target'), str) and '\0' not in entry['target']:
        expected = {'type', 'mode', 'target'}
        if kind == 'symlink-parent':
            expected.add('ancestor')
            if not valid_path(entry.get('ancestor')):
                raise ValueError('invalid symlink ancestor')
        if set(entry) == expected:
            return
    raise ValueError('invalid worktree entry')


def load_snapshot(path):
    with open(path, encoding='utf-8') as source:
        data = json.load(source)
    if not isinstance(data, dict) or set(data) != {'snapshot', 'metrics'}:
        raise ValueError(f'invalid snapshot: {path}')
    snapshot, metrics = data['snapshot'], data['metrics']
    fields = {'version', 'repo', 'scope', 'head', 'index', 'tracked_paths', 'untracked_paths', 'worktree'}
    if not isinstance(snapshot, dict) or set(snapshot) != fields or type(snapshot['version']) is not int or snapshot['version'] != 1:
        raise ValueError(f'invalid snapshot schema: {path}')
    if not isinstance(snapshot['repo'], str) or not os.path.isabs(snapshot['repo']) or '\0' in snapshot['repo']:
        raise ValueError(f'invalid repository identity: {path}')
    if snapshot['scope'] is not None and not valid_path_list(snapshot['scope']):
        raise ValueError(f'invalid scope: {path}')
    if not isinstance(metrics, dict) or set(metrics) != {'elapsed_seconds', 'hashed_bytes'} or type(metrics['hashed_bytes']) is not int or metrics['hashed_bytes'] < 0:
        raise ValueError(f'invalid snapshot metrics: {path}')
    elapsed = metrics['elapsed_seconds']
    if type(elapsed) not in (int, float) or elapsed < 0 or (isinstance(elapsed, float) and not math.isfinite(elapsed)):
        raise ValueError(f'invalid capture duration: {path}')
    head = snapshot['head']
    if not isinstance(head, dict) or set(head) != {'commit', 'ref'} or (head['commit'] is not None and not valid_object(head['commit'])):
        raise ValueError(f'invalid HEAD identity: {path}')
    if head['ref'] is not None and (not isinstance(head['ref'], str) or not head['ref'].startswith('refs/heads/')):
        raise ValueError(f'invalid HEAD ref: {path}')
    for key in ('tracked_paths', 'untracked_paths'):
        if not valid_path_list(snapshot[key]):
            raise ValueError(f'invalid {key}: {path}')
    tracked, untracked = set(snapshot['tracked_paths']), set(snapshot['untracked_paths'])
    if tracked & untracked:
        raise ValueError(f'overlapping tracked and untracked paths: {path}')
    index = snapshot['index']
    if not isinstance(index, list):
        raise ValueError(f'invalid index: {path}')
    for entry in index:
        if not isinstance(entry, dict) or set(entry) != {'path', 'mode', 'object', 'stage', 'assume_unchanged', 'skip_worktree', 'intent_to_add'} or not valid_path(entry['path']):
            raise ValueError(f'invalid index entry: {path}')
        if entry['mode'] not in ('100644', '100755', '120000', '160000') or not valid_object(entry['object']) or type(entry['stage']) is not int or entry['stage'] not in (0, 1, 2, 3):
            raise ValueError(f'invalid index identity: {path}')
        if not all(isinstance(entry[flag], bool) for flag in ('assume_unchanged', 'skip_worktree', 'intent_to_add')):
            raise ValueError(f'invalid index flags: {path}')
    identities = [(entry['path'], entry['stage']) for entry in index]
    if identities != sorted(set(identities)) or {entry['path'] for entry in index} != tracked:
        raise ValueError(f'inconsistent index paths: {path}')
    worktree = snapshot['worktree']
    if not isinstance(worktree, dict) or set(worktree) != tracked | untracked:
        raise ValueError(f'inconsistent worktree paths: {path}')
    for name, entry in worktree.items():
        validate_entry(entry)
        if entry['type'] == 'symlink-parent' and not name.startswith(entry['ancestor'] + '/'):
            raise ValueError(f'inconsistent symlink ancestor: {path}')
    return snapshot


def compare(before, after):
    if before['repo'] != after['repo']:
        raise ValueError('snapshots belong to different repositories')
    if before['scope'] != after['scope']:
        raise ValueError('snapshots have different scopes')
    before_index = {}
    after_index = {}
    for entry in before['index']:
        before_index.setdefault(entry['path'], []).append(entry)
    for entry in after['index']:
        after_index.setdefault(entry['path'], []).append(entry)
    paths = set(before['worktree']) | set(after['worktree'])
    changed = sorted(path for path in paths if
                     before['worktree'].get(path) != after['worktree'].get(path) or
                     before_index.get(path) != after_index.get(path))
    head_changed = before['head'] != after['head']
    return {'changed': bool(changed) or head_changed,
            'changed_paths': changed, 'head_changed': head_changed}


def _version_key(path):
    parts = path.parents[1].name.split('.')
    if not all(part.isdigit() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def _cache_recorder():
    candidates = Path(__file__).resolve().parents[3].glob('usage-metrics/*/scripts/metrics.py')
    return max(candidates, key=_version_key, default=None)


def _recorder_module():
    explicit = os.environ.get('AGENTS_METRICS_MODULE')
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_file():
            return None
    else:
        source = Path(__file__).resolve().parents[2] / 'usage-metrics' / 'scripts' / 'metrics.py'
        if not source.is_file():
            source = _cache_recorder()
            if source is None:
                home = Path(os.environ.get('AGENTS_HOME') or str(Path.home() / '.agents'))
                source = home / 'plugins' / 'usage-metrics' / 'scripts' / 'metrics.py'
            if not source.is_file():
                return None
    try:
        specification = importlib.util.spec_from_file_location('usage_metrics_recorder', source)
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
            module.record('code-flow', 'review-snapshot', status, time.monotonic() - started)
    except Exception:
        pass


def main():
    parser = Parser(description='Capture review input content, or compare two saved snapshots.')
    parser.add_argument('--repo', type=Path)
    parser.add_argument('--scope-from', type=Path)
    parser.add_argument('operation', nargs='?', choices=('compare',))
    parser.add_argument('files', nargs='*', type=Path)
    try:
        args = parser.parse_args()
        if args.operation == 'compare':
            if args.repo is not None or args.scope_from is not None or len(args.files) != 2:
                raise ValueError('compare requires exactly two snapshot files and no --repo or --scope-from')
            result = compare(load_snapshot(args.files[0]), load_snapshot(args.files[1]))
            code = int(result['changed'])
        else:
            if args.repo is None or args.files:
                raise ValueError('capture requires --repo DIR')
            scope = load_scope(args.scope_from) if args.scope_from is not None else None
            result = capture(args.repo, scope)
            code = 0
    except (OSError, ValueError, NotImplementedError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True, sort_keys=True))
        return 2, 'fail'
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return code, 'ok'


if __name__ == '__main__':
    started = time.monotonic()
    status = 'fail'
    try:
        code, status = main()
    except SystemExit as exit_request:
        code = exit_request.code
        status = 'ok' if not exit_request.code else 'fail'
        raise
    finally:
        _record_run(status, started)
    sys.exit(code)
