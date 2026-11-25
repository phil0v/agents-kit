#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

REQUIRED_EVENT_FIELDS = ('ts', 'run', 'plugin', 'event', 'status', 'seconds')
NON_EMPTY_TEXT_FIELDS = ('ts', 'run', 'plugin', 'event')
OPTIONAL_TEXT_FIELDS = ('role', 'model', 'round_kind')
EVENT_STATUSES = ('ok', 'fail')


def default_usage_log_path():
    home = os.path.expanduser(os.environ.get('AGENTS_HOME') or '~/.agents')
    return Path(home) / 'metrics' / 'usage.jsonl'


def parse_timestamp(value):
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'
    return datetime.fromisoformat(value)


def event_error(entry):
    if not isinstance(entry, dict):
        return 'not a JSON object'
    for field in REQUIRED_EVENT_FIELDS:
        if field not in entry:
            return 'missing field ' + field
    for field in NON_EMPTY_TEXT_FIELDS:
        if not isinstance(entry[field], str) or not entry[field]:
            return field + ' is not a non-empty string'
    if entry['status'] not in EVENT_STATUSES:
        return 'status is not ok or fail'
    seconds = entry['seconds']
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return 'seconds is not a number'
    try:
        finite = math.isfinite(seconds)
    except OverflowError:
        finite = False
    if not finite:
        return 'seconds is not finite'
    if seconds < 0:
        return 'seconds is negative'
    for field in OPTIONAL_TEXT_FIELDS:
        if field in entry and (not isinstance(entry[field], str)
                               or not entry[field]):
            return field + ' is not a non-empty string'
    try:
        timestamp = parse_timestamp(entry['ts'])
    except ValueError:
        return 'ts is not an ISO-8601 timestamp'
    if timestamp.utcoffset() is None:
        return 'ts carries no UTC offset'
    return None


def nearest_rank(sorted_values, percent):
    rank = -(-percent * len(sorted_values) // 100)
    return sorted_values[rank - 1]


def collect_events(log_path):
    events = []
    skipped_lines = 0
    total_lines = 0
    with log_path.open('r', encoding='utf-8', errors='replace') as log_file:
        for raw_line in log_file:
            total_lines += 1
            try:
                entry = json.loads(raw_line)
            except ValueError:
                skipped_lines += 1
                continue
            if event_error(entry):
                skipped_lines += 1
                continue
            events.append(entry)
    return events, skipped_lines, total_lines


def event_timestamp(entry):
    return parse_timestamp(entry['ts'])


def build_usage_report(events, skipped_lines, total_lines):
    plugin_counts = {}
    surface_durations = {}
    rounds_by_run = {}
    dispatches_by_run = {}
    for entry in events:
        plugin = entry['plugin']
        plugin_counts[plugin] = plugin_counts.get(plugin, 0) + 1
        durations = surface_durations.setdefault(
            (plugin, entry['event']), [])
        durations.append(entry['seconds'])
        if 'round_kind' in entry:
            rounds_by_run.setdefault(entry['run'], []).append(entry)
        if 'role' in entry or 'model' in entry:
            dispatches_by_run.setdefault(entry['run'], []).append(entry)
    for grouped in (rounds_by_run, dispatches_by_run):
        for entries in grouped.values():
            entries.sort(key=event_timestamp)
    return {
        'total_lines': total_lines,
        'valid_events': len(events),
        'skipped_lines': skipped_lines,
        'plugin_counts': plugin_counts,
        'surface_durations': surface_durations,
        'rounds_by_run': rounds_by_run,
        'dispatches_by_run': dispatches_by_run,
    }


def render_usage_report(log_path, usage_report):
    lines = [
        f'usage log: {log_path}',
        f"lines read: {usage_report['total_lines']}",
        f"valid events: {usage_report['valid_events']}",
        f"skipped lines: {usage_report['skipped_lines']}",
        '',
        'invocations by plugin',
    ]
    for plugin, count in sorted(usage_report['plugin_counts'].items()):
        lines.append(f'  {plugin}: {count}')
    lines.append('')
    lines.append('invocations by surface: count, p50 seconds, p95 seconds')
    for (plugin, event), durations in sorted(
            usage_report['surface_durations'].items()):
        ordered = sorted(durations)
        p50 = nearest_rank(ordered, 50)
        p95 = nearest_rank(ordered, 95)
        lines.append(f'  {plugin} / {event}: {len(ordered)}, '
                     f'p50 {p50:.3f}, p95 {p95:.3f}')
    lines.append('')
    lines.append('rounds per run')
    for run, rounds in usage_report['rounds_by_run'].items():
        kinds = ', '.join(entry['round_kind'] for entry in rounds)
        lines.append(f'  {run}: {len(rounds)} ({kinds})')
    lines.append('')
    lines.append('dispatches per run')
    for run, dispatches in usage_report['dispatches_by_run'].items():
        pairs = ', '.join(
            f"{entry.get('role', '-')}={entry.get('model', '-')}"
            for entry in dispatches)
        lines.append(f'  {run}: {pairs}')
    return '\n'.join(lines)


def build_parser():
    parser = argparse.ArgumentParser(prog='metrics-report.py')
    parser.add_argument('usage_log', nargs='?', help=(
        'usage.jsonl to read; defaults to $AGENTS_HOME/metrics/usage.jsonl'))
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    log_path = (Path(args.usage_log) if args.usage_log
                else default_usage_log_path())
    if not log_path.is_file():
        print(f'usage log not found: {log_path}', file=sys.stderr)
        return 1
    events, skipped_lines, total_lines = collect_events(log_path)
    usage_report = build_usage_report(events, skipped_lines, total_lines)
    print(render_usage_report(log_path, usage_report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
