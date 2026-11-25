#!/usr/bin/env python3
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def execute(command, cwd, timeout, result):
    with tempfile.NamedTemporaryFile(prefix='code-flow-check-', suffix='.log', delete=False) as log:
        result['log_path'] = log.name
        process = subprocess.Popen(
            command, cwd=cwd, env=os.environ | {
                'CI': '1', 'NO_COLOR': '1', 'TERM': 'dumb', 'PAGER': 'cat',
            }, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=os.name == 'posix',
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result['timed_out'] = True
            if os.name != 'posix':
                process.kill()
        finally:
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if result['timed_out']:
            process.wait()
        result['exit_status'] = process.returncode


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
            module.record('code-flow', 'run-check', status, time.monotonic() - started)
    except Exception:
        pass


def main():
    parser = Parser(description='Run a bounded check once and save its combined output.')
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    result = {'command': None, 'cwd': None, 'exit_status': None,
              'timed_out': False, 'log_path': None}
    try:
        args = parser.parse_args()
        if not args.command or args.command[0] != '--' or len(args.command) < 2:
            raise ValueError('a command argv is required after --')
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError('--timeout must be a finite positive number of seconds')
        cwd = args.cwd.resolve(strict=True)
        if not cwd.is_dir():
            raise ValueError(f'--cwd must be a directory: {cwd}')
        command = args.command[1:]
        result.update(command=command, cwd=str(cwd))
        execute(command, cwd, args.timeout, result)
        code = int(result['timed_out'] or result['exit_status'] != 0)
    except (OSError, ValueError) as error:
        result['error'] = str(error)
        code = 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
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
