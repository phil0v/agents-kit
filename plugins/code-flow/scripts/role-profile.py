#!/usr/bin/env python3
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import time

try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _tomli as tomllib

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
ROUND_KINDS = runpy.run_path(
    str(PACKAGE_ROOT / 'scripts' / 'review-manifest.py')
)['ROUND_KINDS']
EFFORTS = {
    'codex': {'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'},
    'antigravity': set(),
    'claude': {'low', 'medium', 'high', 'max'},
    'delta': {'low', 'medium', 'high', 'xhigh', 'max'},
    'pi': {'off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'},
}


def require_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}: expected a nonempty string')
    return value


class RoleProfiles:
    def __init__(self, package_root=PACKAGE_ROOT):
        self.roles = {}
        for path in sorted((package_root / 'roles').glob('*.toml')):
            role = tomllib.loads(path.read_text())
            for key in ('name', 'description', 'model', 'model_reasoning_effort', 'developer_instructions'):
                require_text(role.get(key), f'{path.name}.{key}')
            if role['name'] != path.stem:
                raise ValueError(f'role name mismatch: {path}')
            if role['model_reasoning_effort'] not in EFFORTS['codex']:
                raise ValueError(f'{path.name}: invalid Codex effort')
            self.roles[path.stem] = role
        if not self.roles:
            raise ValueError('no canonical roles found')
        self.harnesses = json.loads((package_root / 'harnesses/mapping.json').read_text())
        if not isinstance(self.harnesses, dict) or set(self.harnesses) != {'antigravity', 'claude', 'delta', 'pi'}:
            raise ValueError('harness mapping must declare antigravity, claude, delta, and pi')
        self.profiles = {'codex': {}, 'agentic': {}}
        for name, role in self.roles.items():
            shared_profile = {
                'name': name,
                'description': role['description'],
                'developer_instructions': role['developer_instructions'],
            }
            self.profiles['codex'][name] = shared_profile | {
                'model': role['model'],
                'effort': role['model_reasoning_effort'],
                'effort_policy': 'explicit',
            }
            self.profiles['agentic'][name] = shared_profile | {
                'model': 'runtime-default',
                'effort': None,
                'effort_policy': 'unsupported',
            }
        for harness, config in self.harnesses.items():
            self.profiles[harness] = self.resolve_mapping(harness, config)

    def resolve_mapping(self, harness, config, provider=None):
        if not isinstance(config, dict) or not isinstance(config.get('roles'), dict):
            raise ValueError(f'{harness}: expected a role mapping')
        missing = set(self.roles) - set(config['roles'])
        extra = set(config['roles']) - set(self.roles)
        if missing or extra:
            raise ValueError(f'{harness}: mapping mismatch missing={sorted(missing)} extra={sorted(extra)}')
        tool_names = config.get('tool_names', {})
        if not isinstance(tool_names, dict):
            raise ValueError(f'{harness}: expected tool name mapping')
        for tool, native_name in tool_names.items():
            require_text(tool, f'{harness}.tool_names key')
            require_text(native_name, f'{harness}.tool_names.{tool}')
        defaults = config.get('frontmatter_defaults', {})
        if not isinstance(defaults, dict) or any(
            key not in {'inheritProjectContext', 'inheritGlobalContext', 'inheritSkills'}
            or not isinstance(value, bool) for key, value in defaults.items()
        ):
            raise ValueError(f'{harness}: invalid frontmatter defaults')
        profiles = {}
        for name, mapping in config['roles'].items():
            profiles[name] = self.resolve_role(harness, name, mapping, tool_names)
        self.apply_provider(harness, config, profiles, provider)
        return profiles

    def resolve_role(self, harness, name, mapping, tool_names):
        label = f'{harness}.{name}'
        if not isinstance(mapping, dict):
            raise ValueError(f'{label}: expected role settings')
        model = require_text(mapping.get('model'), f'{label}.model')
        policy = mapping.get('effort_policy')
        effort = mapping.get('effort')
        if policy not in ('explicit', 'unsupported') or 'effort' not in mapping:
            raise ValueError(f'{label}: expected explicit effort and effort_policy')
        if policy == 'unsupported' and effort is not None:
            raise ValueError(f'{label}: unsupported effort must be null')
        if policy == 'explicit' and (not isinstance(effort, str) or effort not in EFFORTS[harness]):
            raise ValueError(f'{label}: invalid explicit effort')
        profile = {
            'name': name,
            'description': self.roles[name]['description'],
            'model': model,
            'effort': effort,
            'effort_policy': policy,
            'developer_instructions': self.roles[name]['developer_instructions'],
        }
        if 'tools' in mapping:
            tools = mapping['tools']
            if not isinstance(tools, list) or not tools or any(
                not isinstance(tool, str) or tool not in tool_names for tool in tools
            ):
                raise ValueError(f'{label}: invalid tool mapping')
            profile['tools'] = list(dict.fromkeys(tool_names[tool] for tool in tools))
        return profile

    def validate_provider_table(self, harness, provider_id, table, profiles):
        label = f'{harness}.{provider_id}'
        if not isinstance(table, dict):
            raise ValueError(f'{label}: expected a provider table')
        missing = set(self.roles) - set(table)
        extra = set(table) - set(self.roles)
        if missing or extra:
            raise ValueError(f'{label}: table mismatch missing={sorted(missing)} extra={sorted(extra)}')
        entries = {}
        for name, entry in table.items():
            entry_label = f'{label}.{name}'
            if isinstance(entry, str):
                require_text(entry, f'{entry_label}.model')
                entries[name] = {'model': entry}
                continue
            if not isinstance(entry, dict):
                raise ValueError(f'{entry_label}: invalid provider entry')
            unknown = set(entry) - {'model', 'effort'}
            if unknown:
                raise ValueError(f'{entry_label}: unknown entry keys {sorted(unknown)}')
            require_text(entry.get('model'), f'{entry_label}.model')
            fields = {'model': entry['model']}
            if 'effort' in entry:
                if profiles[name]['effort_policy'] == 'unsupported':
                    raise ValueError(f'{entry_label}: unsupported effort must be null')
                if not isinstance(entry['effort'], str) or entry['effort'] not in EFFORTS[harness]:
                    raise ValueError(f'{entry_label}: invalid entry effort')
                fields['effort'] = entry['effort']
            entries[name] = fields
        return entries

    def apply_provider(self, harness, config, profiles, override):
        if 'providers' not in config and 'provider' not in config:
            if override is not None:
                raise ValueError(f'{harness}: no providers')
            return
        if 'providers' not in config:
            raise ValueError(f'{harness}: provider requires providers')
        if 'provider' not in config:
            raise ValueError(f'{harness}: providers require provider')
        raw_tables = config['providers']
        if not isinstance(raw_tables, dict):
            raise ValueError(f'{harness}: expected a provider mapping')
        provider = require_text(config['provider'], f'{harness}.provider')
        tables = {}
        for provider_id, table in raw_tables.items():
            require_text(provider_id, f'{harness}.providers key')
            tables[provider_id] = self.validate_provider_table(harness, provider_id, table, profiles)
        selected = require_text(override, f'{harness}.--provider') if override is not None else provider
        if selected not in tables:
            raise ValueError(f'{harness}: unknown provider: {selected}')
        for name, fields in tables[selected].items():
            profiles[name] = profiles[name] | fields

    def resolve(self, harness, role, provider=None):
        if harness not in self.profiles:
            raise ValueError(f'unknown harness: {harness}')
        if provider is None:
            if role not in self.profiles[harness]:
                raise ValueError(f'unknown role: {role}')
            return self.profiles[harness][role]
        if harness not in self.harnesses:
            raise ValueError(f'{harness}: no providers')
        profiles = self.resolve_mapping(harness, self.harnesses[harness], provider)
        if role not in profiles:
            raise ValueError(f'unknown role: {role}')
        return profiles[role]


def clean_outcome(outcome):
    if not isinstance(outcome, dict):
        return False
    return (
        outcome.get('exit_status') == 0
        and not outcome.get('mutated')
        and not outcome.get('skipped')
        and not outcome.get('timed_out')
        and not outcome.get('diagnostics')
    )


def clean_record(record, repo):
    if not isinstance(record, dict) or record.get('mutated'):
        return False
    path = record.get('path')
    sha256 = record.get('sha256')
    tools = record.get('tools')
    if not isinstance(path, str) or not isinstance(sha256, str) \
            or not isinstance(tools, list) or not tools:
        return False
    try:
        current = hashlib.sha256((repo / path).read_bytes()).hexdigest()
    except OSError:
        return False
    return current == sha256 and all(clean_outcome(outcome) for outcome in tools)


def gate_report_verified(report_path, repo):
    try:
        report = json.loads(report_path.read_text())
    except OSError:
        return False
    except json.JSONDecodeError as error:
        raise ValueError(f'malformed gate report: {error}') from error
    if not isinstance(report, dict):
        raise ValueError('malformed gate report: expected a JSON object')
    files = report.get('files')
    if report.get('deadline_exceeded') or not isinstance(files, list) or not files:
        return False
    return all(clean_record(record, repo) for record in files)


def apply_gate_report(profile, role, gate_report, repo):
    if gate_report is None or role != 'style-reviewer' or profile['effort_policy'] != 'explicit':
        return profile
    if repo is None:
        raise ValueError('--gate-report requires --repo for hash verification')
    if gate_report_verified(gate_report, repo):
        profile = profile | {'effort': 'low'}
    return profile


def apply_round_kind(profile, role, round_kind):
    if round_kind is None or role != 'reviewer' \
            or round_kind != 'post-exit-correctness' \
            or profile['effort_policy'] != 'explicit':
        return profile
    return profile | {'effort': 'medium'}


def apply_dispatch_routing(profile, role, args):
    profile = apply_gate_report(profile, role, args.gate_report, args.repo)
    return apply_round_kind(profile, role, args.round_kind)


def parse_roles(raw_role):
    if ',' not in raw_role:
        return None
    roles = [part.strip() for part in raw_role.split(',')]
    if any(not name for name in roles) or len(set(roles)) != len(roles):
        raise ValueError('invalid --role list: expected unique nonempty role names')
    return roles


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


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


def _record_run(status, started, role=None, model=None):
    if os.environ.get('AGENTS_METRICS') == 'off':
        return
    try:
        module = _recorder_module()
        if module is not None:
            module.record('code-flow', 'role-profile', status,
                          time.monotonic() - started, role=role, model=model)
    except Exception:
        pass


def main():
    started = time.monotonic()
    status = 'fail'
    role = model = None
    recorded = True
    parser = Parser(description='Resolve the effective bundled role contract for a harness.')
    parser.add_argument('--list-harnesses', action='store_true',
                        help='emit the accepted --harness ids as JSON and exit 0; takes no '
                             '--harness or --role and records no metrics event')
    parser.add_argument('--harness')
    parser.add_argument('--role')
    parser.add_argument('--gate-report', type=Path, metavar='PATH',
                        help='style-gate report carried by the dispatch; for style-reviewer a '
                             'verified report resolves effort to low, any other outcome keeps '
                             'the default effort')
    parser.add_argument('--repo', type=Path, metavar='PATH',
                        help='repository root the gate report file hashes are recomputed under; '
                             'required with --gate-report for style-reviewer')
    parser.add_argument('--round-kind', choices=ROUND_KINDS, metavar='KIND',
                        help='review-round kind carried by the dispatch; for reviewer a '
                             'post-exit-correctness round resolves effort to medium, any other '
                             'role or kind keeps the default effort')
    parser.add_argument('--provider', metavar='ID',
                        help='provider table id that overrides the mapping provider selection '
                             'for this one resolution; affects model and entry effort only and '
                             'is never persisted')
    try:
        try:
            args = parser.parse_args()
            if args.list_harnesses:
                recorded = False
                print(json.dumps({'harnesses': sorted(RoleProfiles().profiles)}, ensure_ascii=False))
                return 0
            if args.harness is None or args.role is None:
                raise ValueError('--harness and --role are required')
            role = args.role
            roles = parse_roles(args.role)
            resolver = RoleProfiles()
            if roles is None:
                profile = resolver.resolve(args.harness, role, args.provider)
                model = profile['model']
                output = apply_dispatch_routing(profile, role, args)
            else:
                role = ','.join(roles)
                profiles = {
                    name: apply_dispatch_routing(
                        resolver.resolve(args.harness, name, args.provider),
                        name,
                        args,
                    )
                    for name in roles
                }
                model = ','.join(profile['model'] for profile in profiles.values())
                output = {'profiles': profiles}
        except SystemExit as exit_request:
            status = 'ok' if not exit_request.code else 'fail'
            raise
        except (ValueError, OSError) as error:
            print(json.dumps({'error': str(error)}))
            return 2
        print(json.dumps(output, ensure_ascii=False))
        status = 'ok'
        return 0
    finally:
        if recorded:
            _record_run(status, started, role, model)


if __name__ == '__main__':
    sys.exit(main())
