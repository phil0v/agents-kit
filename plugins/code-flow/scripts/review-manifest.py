#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

MANIFEST_VERSION = 1
ROUND_KINDS = ('initial-correctness', 'targeted-correctness', 'final-style', 'post-exit-correctness')
MAX_GROUP_FILES = 12
MAX_GROUP_LINES = 1500
TOP_LEVEL_FIELDS = {'version', 'repository', 'round_kind', 'original_scope', 'round_scope',
                    'added_paths', 'excluded', 'scoped_untracked', 'constraints', 'groups',
                    'acceptance'}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def git(repo, *arguments):
    result = subprocess.run(
        ['git', '-C', str(repo), *arguments], capture_output=True,
        env=os.environ | {'GIT_OPTIONAL_LOCKS': '0'},
    )
    if result.returncode != 0:
        raise ValueError(f'git {arguments[0]}: {os.fsdecode(result.stderr).strip()}')
    return result.stdout


def repository_identity(repo):
    root = Path(os.fsdecode(git(repo, 'rev-parse', '--show-toplevel'))[:-1]).resolve(strict=True)
    git_dir = Path(os.fsdecode(git(repo, 'rev-parse', '--absolute-git-dir'))[:-1]).resolve(strict=True)
    return {'git_dir': str(git_dir), 'root': str(root)}


def valid_path(value):
    return isinstance(value, str) and bool(value) and '\0' not in value and all(
        part not in ('', '.', '..') for part in value.split('/')
    )


def require_path_list(value, label):
    if not isinstance(value, list) or not all(valid_path(path) for path in value):
        raise ValueError(f'invalid {label}: expected repository-relative paths')
    if len(set(value)) != len(value):
        raise ValueError(f'invalid {label}: duplicate paths')
    return set(value)


def load_manifest(path):
    with open(path, encoding='utf-8') as source:
        manifest = json.load(source)
    if not isinstance(manifest, dict):
        raise ValueError(f'invalid manifest: {path}')
    return manifest


def validate_scope(manifest):
    if set(manifest) - {'run'} != TOP_LEVEL_FIELDS or type(manifest['version']) is not int \
            or manifest['version'] != MANIFEST_VERSION:
        raise ValueError('invalid manifest schema')
    if 'run' in manifest and (not isinstance(manifest['run'], str) or not manifest['run']
                              or '\0' in manifest['run']):
        raise ValueError('invalid run id')
    original = require_path_list(manifest['original_scope'], 'original_scope')
    round_scope = require_path_list(manifest['round_scope'], 'round_scope')
    scoped_untracked = require_path_list(manifest['scoped_untracked'], 'scoped_untracked')
    excluded = require_path_list(manifest['excluded'], 'excluded')
    require_path_list(manifest['constraints'], 'constraints')
    if not original or not round_scope:
        raise ValueError('empty scope')
    added = manifest['added_paths']
    if not isinstance(added, list):
        raise ValueError('invalid added_paths')
    added_paths = set()
    for entry in added:
        if not isinstance(entry, dict) or set(entry) != {'path', 'reason'} \
                or not valid_path(entry['path']):
            raise ValueError('invalid added_paths entry')
        if not isinstance(entry['reason'], str) or not entry['reason'].strip():
            raise ValueError(f"added path {entry['path']}: reason required")
        if entry['path'] in added_paths:
            raise ValueError(f"duplicate added path: {entry['path']}")
        added_paths.add(entry['path'])
    if added_paths & original:
        raise ValueError('added path already in original scope')
    if excluded & added_paths:
        raise ValueError(f'excluded path also in added paths: {sorted(excluded & added_paths)}')
    if not excluded <= original:
        raise ValueError(f'excluded path outside the original scope: {sorted(excluded - original)}')
    if excluded & round_scope:
        raise ValueError(f'excluded path inside the round scope: {sorted(excluded & round_scope)}')
    if excluded & scoped_untracked:
        raise ValueError(
            f'excluded path in scoped untracked: {sorted(excluded & scoped_untracked)}')
    if not round_scope <= original | added_paths:
        raise ValueError('round scope exceeds original scope plus added paths')
    if not scoped_untracked <= round_scope:
        raise ValueError('scoped untracked path outside the round scope')
    return original, round_scope, scoped_untracked, added_paths


def parse_diff_arguments(value, paths):
    if not isinstance(value, list) or not all(
        isinstance(part, str) and part and '\0' not in part for part in value
    ) or value[:2] != ['git', 'diff'] or '--' not in value[2:]:
        raise ValueError('expected git diff with explicit paths')
    separator = value.index('--')
    supplied_paths = value[separator + 1:]
    if len(supplied_paths) != len(paths) or set(supplied_paths) != set(paths):
        raise ValueError('diff paths must exactly match group paths')
    options = {
        '--find-renames', '-M', '--cached', '--staged', '--patch', '-p',
        '--no-ext-diff', '--no-textconv', '--no-color', '--no-relative',
    }
    revisions = []
    staged = False
    for part in value[2:separator]:
        if part in options:
            staged |= part in ('--cached', '--staged')
        elif not part.startswith(('-', '^')) and ':' not in part:
            revisions.append(part)
        else:
            raise ValueError('unsupported diff option or revision')
    if len(revisions) > (1 if staged else 2):
        raise ValueError('too many diff revisions')
    if any('..' in revision for revision in revisions):
        if staged or len(revisions) != 1 or not re.fullmatch(
            r'[^.]+(?:\.[^.]+)*\.{2,3}[^.]+(?:\.[^.]+)*', revisions[0]
        ):
            raise ValueError('invalid diff range')
    return staged, revisions


def build_protected_diff_argv(argv, paths, output_options=()):
    staged, revisions = parse_diff_arguments(argv, paths)
    return [
        'git', '--no-pager', '--literal-pathspecs', 'diff',
        '--no-ext-diff', '--no-textconv', '--no-color', '--no-relative',
        '--find-renames=50%', '--diff-algorithm=myers', '--indent-heuristic',
        '--ignore-submodules=none', '-l0', *output_options,
        *(['--cached'] if staged else []), *revisions, '--', *paths,
    ]


def validate_groups(manifest, round_scope):
    groups = manifest['groups']
    if not isinstance(groups, list) or not groups:
        raise ValueError('invalid groups')
    ids = set()
    assigned = []
    for group in groups:
        if not isinstance(group, dict) \
                or set(group) - {'context_pack'} != {'id', 'paths', 'original_diff', 'current_diff'}:
            raise ValueError('invalid group schema')
        if 'context_pack' in group:
            pack = group['context_pack']
            if not isinstance(pack, str) or not pack or '\0' in pack or not os.path.isabs(pack):
                raise ValueError(f"group {group.get('id')}: invalid context_pack path")
        if not isinstance(group['id'], str) or not group['id'].strip():
            raise ValueError('invalid group id')
        if group['id'] in ids:
            raise ValueError(f"duplicate group id: {group['id']}")
        ids.add(group['id'])
        paths = require_path_list(group['paths'], f"group {group['id']} paths")
        if not paths:
            raise ValueError(f"group {group['id']}: empty paths")
        assigned.extend(group['paths'])
        for key in ('original_diff', 'current_diff'):
            try:
                parse_diff_arguments(group[key], paths)
            except ValueError as error:
                raise ValueError(f"group {group['id']}: invalid {key} argv: {error}") from error
    if len(assigned) != len(set(assigned)):
        raise ValueError('path assigned to more than one group')
    assigned_set = set(assigned)
    unexpected = assigned_set - round_scope
    if unexpected:
        raise ValueError(f'assigned paths outside the round scope: {sorted(unexpected)}')
    missing = round_scope - assigned_set
    if missing:
        raise ValueError(f'scoped paths without a group assignment: {sorted(missing)}')
    return ids


def validate_acceptance(manifest, group_ids):
    acceptance = manifest['acceptance']
    if not isinstance(acceptance, list):
        raise ValueError('invalid acceptance')
    ids = set()
    for criterion in acceptance:
        if not isinstance(criterion, dict) or set(criterion) != {'id', 'text', 'source', 'group', 'evidence'}:
            raise ValueError('invalid acceptance criterion schema')
        if not isinstance(criterion['id'], str) or not criterion['id'].strip() \
                or not isinstance(criterion['text'], str) or not criterion['text'].strip() \
                or not isinstance(criterion['source'], str) or not criterion['source'].strip():
            raise ValueError('invalid acceptance criterion identity')
        if criterion['id'] in ids:
            raise ValueError(f"duplicate criterion id: {criterion['id']}")
        ids.add(criterion['id'])
        owner, evidence = criterion['group'], criterion['evidence']
        if owner is not None and (not isinstance(owner, str) or owner not in group_ids):
            raise ValueError(f"criterion {criterion['id']}: unknown owning group")
        if not isinstance(evidence, list) or not all(
            isinstance(reference, str) and reference.strip() for reference in evidence
        ):
            raise ValueError(f"criterion {criterion['id']}: invalid evidence references")
        if owner is not None and evidence:
            raise ValueError(f"criterion {criterion['id']}: contradictory assignment and carried evidence")
        if owner is None and not evidence:
            raise ValueError(f"criterion {criterion['id']}: unowned criterion without carried evidence")
    round_kind = manifest['round_kind']
    if round_kind == 'initial-correctness':
        unassigned = [criterion['id'] for criterion in acceptance if criterion['group'] is None]
        if unassigned:
            raise ValueError(f'initial correctness round must assign every criterion: {sorted(unassigned)}')
    if round_kind == 'final-style':
        assigned = [criterion['id'] for criterion in acceptance if criterion['group'] is not None]
        if assigned:
            raise ValueError(f'final style round assigns no correctness criteria: {sorted(assigned)}')


def changed_line_counts(repo, argv, paths):
    staged, revisions = parse_diff_arguments(argv, paths)
    resolved = []
    for revision in revisions:
        endpoints = re.split(r'(\.{2,3})', revision)
        object_type = 'commit' if '...' in endpoints else 'tree'
        for index in range(0, len(endpoints), 2):
            endpoints[index] = os.fsdecode(git(
                repo, 'rev-parse', '--verify', '--end-of-options',
                endpoints[index] + '^{' + object_type + '}',
            )).strip()
        resolved.append(''.join(endpoints))
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            'GIT_EXTERNAL_DIFF', 'GIT_DIFF_OPTS', 'GIT_LITERAL_PATHSPECS',
            'GIT_GLOB_PATHSPECS', 'GIT_NOGLOB_PATHSPECS', 'GIT_ICASE_PATHSPECS',
        }
    }
    result = subprocess.run(
        build_protected_diff_argv(
            ['git', 'diff', *(['--cached'] if staged else []), *resolved, '--', *paths],
            paths, ('--numstat', '-z'),
        ),
        cwd=repo, capture_output=True,
        env=environment | {'GIT_OPTIONAL_LOCKS': '0'},
    )
    if result.returncode != 0:
        raise ValueError(f"git diff: {os.fsdecode(result.stderr).strip()}")
    counts = {}
    records = iter(result.stdout.split(b'\0')[:-1])
    for record in records:
        added, deleted, name = record.split(b'\t', 2)
        if not name:
            old_name, name = next(records), next(records)
            if os.fsdecode(old_name) not in paths or os.fsdecode(name) not in paths:
                raise ValueError('rename diff paths must belong to the group')
        path = os.fsdecode(name)
        if path not in paths:
            raise ValueError(f'diff returned path outside the group: {path}')
        counts[path] = 0 if added == b'-' else int(added) + int(deleted)
    return counts


def untracked_line_count(root, path):
    full = os.path.join(root, path)
    if os.path.islink(full):
        return 0
    with open(full, 'rb') as source:
        return len(source.read().splitlines())


def group_line_counts(repo, root, scoped_untracked, group):
    return per_path_line_counts(repo, root, group['current_diff'], scoped_untracked, group['paths'])


def validate_group_limits(manifest, repo, scoped_untracked):
    root = repository_identity(repo)['root']
    for group in manifest['groups']:
        if len(group['paths']) > MAX_GROUP_FILES:
            raise ValueError(f"group {group['id']}: more than {MAX_GROUP_FILES} files")
        per_path = group_line_counts(repo, root, scoped_untracked, group)
        total = sum(per_path.values())
        if total > MAX_GROUP_LINES:
            single = len(group['paths']) == 1 and per_path[group['paths'][0]] > MAX_GROUP_LINES
            if not single:
                raise ValueError(
                    f"group {group['id']}: {total} changed lines exceed {MAX_GROUP_LINES}")


def validate_identity(manifest, repo):
    identity = manifest['repository']
    if not isinstance(identity, dict) or set(identity) != {'root', 'git_dir'} \
            or not all(isinstance(identity[key], str) and os.path.isabs(identity[key])
                       and '\0' not in identity[key] for key in ('root', 'git_dir')):
        raise ValueError('invalid repository identity')
    resolved = repository_identity(repo)
    if identity != resolved:
        raise ValueError('manifest repository identity does not match --repo')


def validate_manifest(manifest, repo=None):
    _, round_scope, scoped_untracked, _ = validate_scope(manifest)
    if manifest['round_kind'] not in ROUND_KINDS:
        raise ValueError(f"invalid round_kind: {manifest.get('round_kind')!r}")
    group_ids = validate_groups(manifest, round_scope)
    validate_acceptance(manifest, group_ids)
    if repo is not None:
        validate_identity(manifest, repo)
        validate_group_limits(manifest, repo, scoped_untracked)


def render_group(manifest, group_id, manifest_path):
    groups = {group['id']: group for group in manifest['groups']}
    if group_id not in groups:
        raise ValueError(f'unknown group: {group_id}')
    group = groups[group_id]
    packet = {
        'group_id': group_id,
        'manifest_path': str(manifest_path),
        'round_kind': manifest['round_kind'],
        'assigned_paths': group['paths'],
        'original_diff': build_protected_diff_argv(group['original_diff'], group['paths']),
        'current_diff': build_protected_diff_argv(group['current_diff'], group['paths']),
        'scoped_untracked': [path for path in manifest['scoped_untracked'] if path in set(group['paths'])],
        'constraints': manifest['constraints'],
        'criteria': [criterion for criterion in manifest['acceptance'] if criterion['group'] == group_id],
        'carried_evidence': [criterion for criterion in manifest['acceptance'] if criterion['group'] is None],
    }
    if 'context_pack' in group:
        packet['context_pack'] = group['context_pack']
    return packet


def json_argument(value, flag):
    origin = ''
    if value.startswith('@'):
        path = value[1:]
        origin = f' in {path}'
        try:
            with open(path, encoding='utf-8') as source:
                value = source.read().strip()
        except OSError as error:
            raise ValueError(
                f'cannot read {flag} file {path}: {error.strerror or error}') from error
        except UnicodeError as error:
            raise ValueError(
                f'cannot read {flag} file {path}: not valid UTF-8') from error
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f'invalid {flag} JSON{origin}: {error.msg}') from error


def added_entries(value):
    if not isinstance(value, dict):
        raise ValueError('invalid --added: expected an object mapping paths to reasons')
    entries = []
    for path, reason in value.items():
        if not valid_path(path) or not isinstance(reason, str) or not reason.strip():
            raise ValueError(f'invalid --added entry: {path}')
        entries.append({'path': path, 'reason': reason})
    return sorted(entries, key=lambda entry: entry['path'])


def listed_paths(repo, *arguments):
    listing = git(repo, *arguments, '-z')
    return {path for path in (os.fsdecode(part) for part in listing.split(b'\0')) if path}


def untracked_paths(repo):
    return listed_paths(repo, 'ls-files', '--others', '--exclude-standard')


def head_tree_paths(repo):
    try:
        return listed_paths(repo, 'ls-tree', '-r', '--name-only', 'HEAD')
    except ValueError:
        return set()


def require_resolvable_paths(repo, root, paths, untracked):
    known = head_tree_paths(repo) | listed_paths(repo, 'ls-files') | untracked
    for path in paths:
        if path not in known and not os.path.exists(os.path.join(root, path)):
            raise ValueError(f'scope path does not exist in the repository: {path}')


def per_path_line_counts(repo, root, argv, scoped_untracked, paths):
    tracked = changed_line_counts(repo, argv, paths)
    return {
        path: untracked_line_count(root, path) if path in scoped_untracked else tracked.get(path, 0)
        for path in paths
    }


def stripped_test_basename(name):
    stem, separator, extension = name.rpartition('.')
    if not separator:
        stem, extension = name, ''
    if stem.startswith('test_'):
        stripped = stem[len('test_'):]
    elif stem.endswith('_test'):
        stripped = stem[:-len('_test')]
    else:
        return None
    if not stripped:
        return None
    return f'{stripped}.{extension}' if extension else stripped


def partition_paths(paths, counts):
    groups = []
    current = []
    current_lines = 0
    for path in paths:
        path_lines = counts[path]
        if current and (len(current) == MAX_GROUP_FILES or current_lines + path_lines > MAX_GROUP_LINES):
            groups.append(current)
            current, current_lines = [], 0
        if path_lines > MAX_GROUP_LINES:
            groups.append([path])
            continue
        current.append(path)
        current_lines += path_lines
    if current:
        groups.append(current)
    return groups


def auto_group_paths(round_scope, counts):
    basenames = {path: path.rsplit('/', 1)[-1] for path in round_scope}
    stripped = {path: stripped_test_basename(basenames[path]) for path in round_scope}
    tests_by_implementation = {}
    for path in round_scope:
        if stripped[path] is None:
            continue
        candidates = sorted(
            other for other in round_scope
            if stripped[other] is None and basenames[other] == stripped[path]
        )
        if len(candidates) > 1:
            raise ValueError(f'test file {path} names multiple implementation files: {candidates}')
        if candidates:
            tests_by_implementation.setdefault(candidates[0], []).append(path)
    units = [
        sorted([implementation, *tests])
        for implementation, tests in sorted(tests_by_implementation.items())
    ]
    paired = {path for unit in units for path in unit}
    by_directory = {}
    for path in round_scope:
        if path not in paired:
            by_directory.setdefault(path.rpartition('/')[0], []).append(path)
    groups = []
    for unit in units:
        groups.extend(partition_paths(unit, counts))
    for directory in sorted(by_directory):
        groups.extend(partition_paths(sorted(by_directory[directory]), counts))
    return groups


def explicit_group_paths(specifications):
    grouped = []
    for specification in specifications:
        identifier, separator, paths = specification.partition('=')
        if not separator or not identifier.strip() or not paths.strip():
            raise ValueError(f'invalid --group specification: {specification}')
        parts = [part.strip() for part in paths.split(',')]
        if any(not part for part in parts):
            raise ValueError(f'invalid --group specification: {specification}')
        grouped.append((identifier, parts))
    return grouped


def owning_group(identifier, paths, owner_by_path):
    if not isinstance(paths, list) or not paths:
        raise ValueError(f'criterion {identifier}: expected a nonempty paths list')
    owners = set()
    for path in paths:
        if not valid_path(path) or path not in owner_by_path:
            raise ValueError(f'criterion {identifier}: path outside every group: {path}')
        owners.add(owner_by_path[path])
    if len(owners) != 1:
        raise ValueError(f'criterion {identifier}: paths span more than one group')
    return owners.pop()


def acceptance_entries(value, grouped):
    if not isinstance(value, list):
        raise ValueError('invalid --acceptance: expected an array of criteria')
    owner_by_path = {path: identifier for identifier, paths in grouped for path in paths}
    criteria = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError('invalid acceptance criterion')
        criterion = {key: entry.get(key) for key in ('id', 'text', 'source')}
        if any(not isinstance(part, str) or not part.strip() for part in criterion.values()):
            raise ValueError('invalid acceptance criterion identity')
        has_group = entry.get('group') is not None
        has_paths = 'paths' in entry
        has_evidence = 'evidence' in entry and entry['evidence'] != []
        bindings = [
            key
            for key, active in (('group', has_group), ('paths', has_paths), ('evidence', has_evidence))
            if active
        ]
        if not bindings and ('evidence' in entry or 'group' in entry):
            bindings.append('evidence' if 'evidence' in entry else 'group')
        if len(bindings) != 1:
            raise ValueError(f"criterion {criterion['id']}: expected one of group, paths, or evidence")
        criterion['group'] = None
        criterion['evidence'] = []
        binding = bindings[0]
        if binding == 'group':
            criterion['group'] = entry['group']
        elif binding == 'paths':
            criterion['group'] = owning_group(criterion['id'], entry['paths'], owner_by_path)
        else:
            criterion['evidence'] = entry['evidence']
        criteria.append(criterion)
    return criteria


def resolve_round_scope(arguments, eligible_scope, excluded_set):
    if arguments.round_scope is None:
        return sorted(eligible_scope)
    if arguments.round_kind == 'initial-correctness':
        raise ValueError('initial correctness round cannot narrow --round-scope')
    requested = require_path_list(
        json_argument(arguments.round_scope, '--round-scope'), '--round-scope')
    if not requested:
        raise ValueError('empty round scope')
    excluded_overlap = sorted(requested & excluded_set)
    if excluded_overlap:
        raise ValueError(f'excluded path inside --round-scope: {excluded_overlap}')
    outside_eligible = sorted(requested - eligible_scope)
    if outside_eligible:
        raise ValueError(f'--round-scope path outside eligible scope: {outside_eligible}')
    return sorted(requested)


def init_manifest(arguments):
    scope = sorted(require_path_list(json_argument(arguments.scope_from, '--scope-from'), '--scope-from'))
    added = added_entries(json_argument(arguments.added, '--added'))
    excluded = sorted(require_path_list(json_argument(arguments.exclude, '--exclude'), '--exclude'))
    constraints = sorted(
        require_path_list(json_argument(arguments.constraints, '--constraints'), '--constraints')
    )
    acceptance_input = json_argument(arguments.acceptance, '--acceptance')
    excluded_set = set(excluded)
    added_paths = {entry['path'] for entry in added}
    if not scope and not added:
        raise ValueError('empty scope')
    if excluded_set & added_paths:
        raise ValueError(f'excluded path also in added paths: {sorted(excluded_set & added_paths)}')
    outside_scope = excluded_set - set(scope)
    if outside_scope:
        raise ValueError(f'excluded path outside --scope-from: {sorted(outside_scope)}')
    eligible_scope = (set(scope) | added_paths) - excluded_set
    if not eligible_scope:
        raise ValueError('exclusions empty the scope: every scoped path is generated')
    round_scope = resolve_round_scope(arguments, eligible_scope, excluded_set)
    identity = repository_identity(arguments.repo)
    untracked = untracked_paths(arguments.repo)
    require_resolvable_paths(
        arguments.repo, identity['root'], sorted(set(scope) | added_paths), untracked)
    if arguments.auto_groups and arguments.group:
        raise ValueError('expected --group specifications or --auto-groups, not both')
    if arguments.auto_groups:
        scoped_untracked = set(round_scope) & untracked
        counts = per_path_line_counts(
            arguments.repo, identity['root'],
            ['git', 'diff', arguments.baseline, '--', *round_scope], scoped_untracked, round_scope,
        )
        grouped = [
            (f'g{index}', paths)
            for index, paths in enumerate(auto_group_paths(round_scope, counts), start=1)
        ]
    elif arguments.group:
        grouped = explicit_group_paths(arguments.group)
        if arguments.round_kind != 'initial-correctness' and arguments.round_scope is None:
            group_union = {path for _, paths in grouped for path in paths}
            if group_union and group_union <= eligible_scope:
                round_scope = sorted(group_union)
        scoped_untracked = set(round_scope) & untracked
    else:
        raise ValueError('expected --group specifications or --auto-groups')
    groups = [
        {
            'id': identifier,
            'paths': list(paths),
            'original_diff': ['git', 'diff', arguments.baseline, '--', *paths],
            'current_diff': ['git', 'diff', arguments.baseline, '--', *paths],
        }
        for identifier, paths in grouped
    ]
    manifest = {
        'version': MANIFEST_VERSION,
        'repository': identity,
        'round_kind': arguments.round_kind,
        'original_scope': scope,
        'round_scope': round_scope,
        'added_paths': added,
        'excluded': excluded,
        'scoped_untracked': sorted(scoped_untracked),
        'constraints': constraints,
        'groups': groups,
        'acceptance': acceptance_entries(acceptance_input, grouped),
    }
    validate_manifest(manifest, arguments.repo)
    return manifest


INIT_EPILOG = '''acceptance criteria:
  every criterion has exactly the fields
  id, text, source, group, evidence: id, text, and source are nonempty
  strings, id unique across the manifest; group is null or the id of a
  manifest group; evidence is a list of nonempty reference strings.
  A criterion either names an owning group or carries evidence from an
  earlier round; both at once, or neither, is rejected. A --acceptance
  entry states the owner as a group id, or as paths belonging to
  exactly one group.
  initial-correctness assigns every criterion to an owning group;
  final-style assigns none, carrying every criterion on its evidence.'''


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


def _manifest_run_id(module):
    if module is None:
        return None
    inherited = os.environ.get('AGENTS_RUN_ID')
    if inherited:
        return inherited
    return module.new_run_id()


def _record_run(status, started, round_kind=None):
    if os.environ.get('AGENTS_METRICS') == 'off':
        return
    try:
        module = _recorder_module()
        if module is not None:
            module.record('code-flow', 'review-manifest', status,
                          time.monotonic() - started, round_kind=round_kind)
    except Exception:
        pass


def main():
    started = time.monotonic()
    status = 'fail'
    round_kind = None
    parser = Parser(
        description='Validate or initialize a review-round manifest, or render one group packet.')
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('--repo', required=True, type=Path)
    validate.add_argument('--manifest', required=True, type=Path)
    render = commands.add_parser('render')
    render.add_argument('--manifest', required=True, type=Path)
    render.add_argument('--group', required=True)
    initialize = commands.add_parser(
        'init', description='Write a review-round manifest to stdout.',
        epilog=INIT_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    initialize.add_argument('--repo', required=True, type=Path)
    initialize.add_argument('--scope-from', required=True,
                           help='JSON array of repository-relative scoped paths; '
                                '@<path> reads the JSON from a file')
    initialize.add_argument('--round-scope', default=None,
                           help='JSON array of repository-relative round-scoped paths '
                                'for follow-up rounds; @<path> reads the JSON from a file')
    initialize.add_argument('--added', default='{}',
                           help='JSON object mapping added paths to reasons; '
                                '@<path> reads the JSON from a file')
    initialize.add_argument('--exclude', default='[]',
                           help='JSON array of scoped excluded paths - generated-path '
                                'set paths and terminal-evidence paths - kept in '
                                'original_scope but excluded from the round scope; '
                                '@<path> reads the JSON from a file')
    initialize.add_argument('--round-kind', required=True, choices=ROUND_KINDS)
    initialize.add_argument('--baseline', default='HEAD')
    initialize.add_argument('--group', action='append', metavar='ID=PATH[,PATH...]',
                           help='explicit group specification, repeatable')
    initialize.add_argument('--auto-groups', action='store_true',
                           help='partition the scope by the file and line limits')
    initialize.add_argument('--constraints', default='[]',
                           help='JSON array of constraint paths; '
                                '@<path> reads the JSON from a file')
    initialize.add_argument('--acceptance', default='[]',
                           help='JSON array of acceptance criteria; '
                                '@<path> reads the JSON from a file')
    try:
        try:
            args = parser.parse_args()
            if args.command == 'validate':
                manifest = load_manifest(args.manifest)
                validate_manifest(manifest, args.repo)
                result = {'valid': True, 'manifest': str(args.manifest)}
            elif args.command == 'init':
                round_kind = args.round_kind
                result = init_manifest(args)
                run_id = _manifest_run_id(_recorder_module())
                if run_id is not None:
                    result['run'] = run_id
                    os.environ['AGENTS_RUN_ID'] = run_id
            else:
                manifest = load_manifest(args.manifest)
                validate_manifest(manifest)
                result = render_group(manifest, args.group, args.manifest)
        except SystemExit as exit_request:
            status = 'ok' if not exit_request.code else 'fail'
            raise
        except (OSError, ValueError) as error:
            print(json.dumps({'error': str(error)}, ensure_ascii=True, sort_keys=True))
            return 2
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        status = 'ok'
        return 0
    finally:
        _record_run(status, started, round_kind)


if __name__ == '__main__':
    sys.exit(main())
