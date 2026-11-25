#!/usr/bin/env python3
import argparse
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

RECORDER_PACKAGE = 'usage-metrics'
USAGE_LOG_NAME = 'usage.jsonl'
OPTIONAL_EVENT_FIELDS = ('role', 'model', 'round_kind')


def agents_home():
    return Path(os.path.expanduser(os.environ.get('AGENTS_HOME') or '~/.agents'))


def usage_log_path():
    return agents_home() / 'metrics' / USAGE_LOG_NAME


def new_run_id():
    return uuid.uuid4().hex


def recording_enabled():
    return os.environ.get('AGENTS_METRICS') != 'off'


def resolve_recorder_path(package_root):
    explicit = os.environ.get('AGENTS_METRICS_MODULE')
    if explicit:
        explicit_path = Path(os.path.expanduser(explicit))
        return str(explicit_path) if explicit_path.is_file() else None
    adjacent = (Path(package_root).parent / RECORDER_PACKAGE /
                'scripts' / 'metrics.py')
    if adjacent.is_file():
        return str(adjacent)
    installed = (agents_home() / 'plugins' / RECORDER_PACKAGE /
                 'scripts' / 'metrics.py')
    if installed.is_file():
        return str(installed)
    return None


def record(plugin, event, status, seconds, **fields):
    if not recording_enabled():
        return False
    try:
        seconds = float(seconds)
        if not math.isfinite(seconds):
            return False
        entry = {
            'ts': datetime.now(timezone.utc).isoformat(),
            'run': os.environ.get('AGENTS_RUN_ID') or new_run_id(),
            'plugin': plugin,
            'event': event,
            'status': status,
            'seconds': seconds,
        }
        for field_name in OPTIONAL_EVENT_FIELDS:
            value = fields.get(field_name)
            if value is not None:
                entry[field_name] = value
        log_path = usage_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open('a', encoding='utf-8') as log_file:
            log_file.write(json.dumps(entry) + '\n')
    except Exception:
        return False
    return True


def build_parser():
    parser = argparse.ArgumentParser(prog='metrics.py')
    commands = parser.add_subparsers(dest='command', required=True)
    recorder = commands.add_parser('record')
    recorder.add_argument('--plugin', required=True)
    recorder.add_argument('--event', required=True)
    recorder.add_argument('--status', required=True, choices=('ok', 'fail'))
    recorder.add_argument('--seconds', required=True, type=float)
    recorder.add_argument('--role')
    recorder.add_argument('--model')
    recorder.add_argument('--round-kind')
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    record(args.plugin, args.event, args.status, args.seconds,
           role=args.role, model=args.model, round_kind=args.round_kind)
    return 0


if __name__ == '__main__':
    sys.exit(main())
