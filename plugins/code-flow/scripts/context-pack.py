#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

import repository

MAX_OUTLINE_ENTRIES = 20
MAX_IMPORTS = 20
MAX_CALLER_NAMES = 10
MAX_HITS_PER_SYMBOL = 5
MAX_TESTS = 10
MAX_OUTPUT_BYTES = 65536
TRUNCATED = '... truncated'

DECLARATION_PATTERNS = {
    '.go': (
        r'^func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(',
        r'^(?:type|var|const)\s+(?P<name>[A-Za-z_]\w*)\b',
    ),
    '.md': (
        r'^#{1,6}\s+(?P<name>\S(?:.*?\S)?)(?:\s+#+)?\s*$',
    ),
    '.py': (
        r'^(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(',
        r'^class\s+(?P<name>[A-Za-z_]\w*)\b',
        r'^\s+(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)\s*\(',
    ),
    '.php': (
        r'^(?:final\s+|abstract\s+)?class\s+(?P<name>[A-Za-z_]\w*)\b',
        r'^(?:interface|trait|enum)\s+(?P<name>[A-Za-z_]\w*)\b',
        r'^\s*(?:(?:public|protected|private|static|final|abstract)\s+)*function\s+(?P<name>[A-Za-z_]\w*)\s*\(',
    ),
    '.sh': (
        r'^(?P<name>[A-Za-z_]\w*)\s*\(\s*\)\s*\{',
        r'^function\s+(?P<name>[A-Za-z_]\w*)\b',
    ),
    '.toml': (
        r'^\[(?P<name>[^\][]+)\]\s*$',
        r'^(?P<name>[A-Za-z_]\w*)\s*=',
    ),
}
TYPESCRIPT_DECLARATIONS = (
    r'^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_]\w*)',
    r'^(?:export\s+)?(?:abstract\s+)?class\s+(?P<name>[A-Za-z_]\w*)\b',
    r'^(?:export\s+)?(?:interface|type|enum)\s+(?P<name>[A-Za-z_]\w*)\b',
    r'^(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_]\w*)\b',
)
TYPESCRIPT_EXTENSIONS = ('.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs')
for extension in TYPESCRIPT_EXTENSIONS:
    DECLARATION_PATTERNS[extension] = TYPESCRIPT_DECLARATIONS
DECLARATION_PATTERNS['.bash'] = DECLARATION_PATTERNS['.sh']

IMPORT_PATTERNS = {
    '.go': (r'^import\s+(?:\w+\s+)?"(?P<module>[^"]+)"', r'^\s*(?:[._\w]+\s+)?"(?P<module>[^"]+)"'),
    '.md': (),
    '.py': (r'^import\s+(?P<module>[\w.]+)', r'^from\s+(?P<module>[\w.]+)\s+import\b'),
    '.php': (r'^use\s+(?P<module>[\\\w]+)\s*;', r'^require(?:_once)?\s+["\'](?P<module>[^"\']+)["\']'),
    '.sh': (r'^(?:source|\.)\s+(?P<module>\S+)',),
    '.toml': (),
}
TYPESCRIPT_IMPORTS = (
    r'^import\s+.*?from\s+["\'](?P<module>[^"\']+)["\']',
    r'^import\s+["\'](?P<module>[^"\']+)["\']',
    r'^\s*(?:const|let|var)\s+\w+\s*=\s*require\(["\'](?P<module>[^"\']+)["\']\)',
)
for extension in TYPESCRIPT_EXTENSIONS:
    IMPORT_PATTERNS[extension] = TYPESCRIPT_IMPORTS
IMPORT_PATTERNS['.bash'] = IMPORT_PATTERNS['.sh']

TEST_NAME_PATTERNS = (
    re.compile(r'^test_.+\.py$'),
    re.compile(r'^.+_test\.py$'),
    re.compile(r'^.+_test\.go$'),
    re.compile(r'^.+Test\.php$'),
    re.compile(r'^.+\.(?:test|spec)\.(?:ts|tsx|js|jsx|mjs|cjs)$'),
)
TEST_DIRECTORY_NAMES = {'test', 'tests', '__tests__', 'spec', 'specs'}
FENCE_DELIMITER = re.compile(r'^(?:`{3,}|~{3,})')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def outline(lines, patterns):
    compiled = [re.compile(pattern) for pattern in patterns]
    entries = []
    for number, line in enumerate(lines, start=1):
        for pattern in compiled:
            match = pattern.match(line)
            if match:
                entries.append({'name': match.group('name'), 'line': number})
                break
    return entries


def imports(lines, patterns):
    compiled = [re.compile(pattern) for pattern in patterns]
    modules = []
    for line in lines:
        for pattern in compiled:
            match = pattern.match(line)
            if match:
                modules.append(match.group('module'))
                break
    return modules


def related_contexts(root, analyses):
    contexts = {}
    for entry, _ in analyses:
        if not entry['supported']:
            continue
        relative = entry['path']
        unique_names = list(dict.fromkeys(symbol['name'] for symbol in entry['outline']))
        pattern = re.compile(r'\b(?:' + '|'.join(re.escape(name) for name in unique_names) + r')\b') \
            if unique_names else None
        contexts[relative] = {
            'stem': Path(relative).stem,
            'pattern': pattern,
            'hits': {name: [] for name in unique_names},
            'candidates': [],
        }
    if not contexts:
        return contexts
    for relpath, lines in repository.iter_text_lines(root):
        basename = os.path.basename(relpath)
        base_stem = Path(basename).stem
        test_named = any(item.match(basename) for item in TEST_NAME_PATTERNS)
        test_located = bool(set(Path(relpath).parts[:-1]) & TEST_DIRECTORY_NAMES)
        for relative, context in contexts.items():
            stem = context['stem']
            if (test_named or test_located) and (stem == base_stem or stem in basename):
                context['candidates'].append(relpath)
            pattern = context['pattern']
            if pattern is None or relpath == relative:
                continue
            for number, line in enumerate(lines, start=1):
                for name in {match.group() for match in pattern.finditer(line)}:
                    context['hits'][name].append(f'{relpath}:{number}')
    return {relative: (context['hits'], context['candidates'])
            for relative, context in contexts.items()}


def blank_fenced_lines(lines):
    blanked = []
    fenced = False
    for line in lines:
        if FENCE_DELIMITER.match(line.strip()):
            fenced = not fenced
            blanked.append('')
        elif fenced:
            blanked.append('')
        else:
            blanked.append(line)
    return blanked


def analyze(root, relative):
    path = os.path.join(root, relative)
    extension = Path(relative).suffix
    entry = {'path': relative, 'language': extension or None}
    if os.path.islink(path):
        entry['supported'] = False
        entry['symlink_target'] = os.readlink(path)
        return entry, {}
    if extension not in DECLARATION_PATTERNS:
        entry['supported'] = False
        return entry, {}
    entry['supported'] = True
    with open(path, 'rb') as source:
        content = source.read()
    lines = [os.fsdecode(line) for line in content.splitlines()]
    if extension == '.md':
        lines = blank_fenced_lines(lines)
    symbols = outline(lines, DECLARATION_PATTERNS[extension])
    truncated = {}
    if len(symbols) > MAX_OUTLINE_ENTRIES:
        symbols = symbols[:MAX_OUTLINE_ENTRIES]
        truncated['outline'] = True
    modules = imports(lines, IMPORT_PATTERNS[extension])
    if len(modules) > MAX_IMPORTS:
        modules = modules[:MAX_IMPORTS]
        truncated['imports'] = True
    entry['outline'] = symbols
    entry['imports'] = modules
    return entry, truncated


def bound_related(entry, truncated, hits_by_name, candidates):
    references = {name: hits for name, hits in hits_by_name.items() if hits}
    tests = sorted(candidates)
    if len(references) > MAX_CALLER_NAMES:
        references = dict(list(references.items())[:MAX_CALLER_NAMES])
        truncated['callers'] = True
    for name, hits in references.items():
        if len(hits) > MAX_HITS_PER_SYMBOL:
            references[name] = hits[:MAX_HITS_PER_SYMBOL]
            truncated.setdefault('callers', True)
    if len(tests) > MAX_TESTS:
        tests = tests[:MAX_TESTS]
        truncated['tests'] = True
    entry['callers'] = references
    entry['tests'] = tests
    if truncated:
        entry['truncated'] = sorted(truncated)


def analyze_files(root, relative_paths):
    analyses = [analyze(root, relative) for relative in relative_paths]
    contexts = related_contexts(root, analyses)
    for entry, truncated in analyses:
        if not entry['supported']:
            continue
        hits_by_name, candidates = contexts[entry['path']]
        bound_related(entry, truncated, hits_by_name, candidates)
    return [entry for entry, _ in analyses]


def render_text(result):
    lines = [f"repository: {result['repository']}"]
    for entry in result['files']:
        lines.append(f"== {entry['path']}")
        if entry.get('symlink_target') is not None:
            lines.append(f"  symlink: {entry['symlink_target']}")
            continue
        if not entry.get('supported'):
            lines.append('  unsupported file type')
            continue
        lines.append('  outline:')
        for symbol in entry['outline']:
            lines.append(f"    {symbol['line']}: {symbol['name']}")
        lines.append('  imports:')
        for module in entry['imports']:
            lines.append(f'    {module}')
        lines.append('  callers:')
        for name, hits in entry['callers'].items():
            lines.append(f"    {name}: {', '.join(hits)}")
        lines.append('  tests:')
        for test in entry['tests']:
            lines.append(f'    {test}')
        if entry.get('truncated'):
            lines.append(f"  {TRUNCATED}: {', '.join(entry['truncated'])}")
    text = '\n'.join(lines) + '\n'
    if len(text.encode()) > MAX_OUTPUT_BYTES:
        text = text.encode()[:MAX_OUTPUT_BYTES].decode(errors='ignore')
        text += f'\n{TRUNCATED}: output\n'
    return text


def bounded_json(result):
    payload = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
    if len(payload.encode()) < MAX_OUTPUT_BYTES:
        return payload
    omitted = []
    result['omitted_files'] = []
    result['omitted_count'] = 0
    while len(payload.encode()) >= MAX_OUTPUT_BYTES and result['files']:
        dropped = result['files'].pop()
        omitted.append(dropped['path'])
        result['omitted_count'] = len(omitted)
        payload = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
    for path in omitted:
        result['omitted_files'].append(path)
        candidate = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
        if len(candidate.encode()) >= MAX_OUTPUT_BYTES:
            result['omitted_files'].pop()
            break
        payload = candidate
    return payload


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
            module.record('code-flow', 'context-pack', status,
                          time.monotonic() - started, role=role, model=model)
    except Exception:
        pass


def main():
    started = time.monotonic()
    status = 'fail'
    role = model = None
    parser = Parser(description='Emit a bounded context map for a set of repository files.')
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--files-from', type=Path)
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    parser.add_argument('--role')
    parser.add_argument('--model')
    parser.add_argument('files', nargs='*')
    try:
        try:
            args = parser.parse_args()
            role, model = args.role, args.model
            root = os.path.realpath(args.repo)
            if not os.path.isdir(root):
                raise ValueError(f'not a directory: {args.repo}')
            relative_paths = list(args.files)
            if args.files_from is not None:
                with open(args.files_from, encoding='utf-8') as source:
                    listed = json.load(source)
                if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
                    raise ValueError('invalid --files-from: expected a JSON list of paths')
                relative_paths.extend(listed)
            if not relative_paths:
                raise ValueError('no files given')
            for path in relative_paths:
                full = os.path.join(root, path)
                if not os.path.isfile(full) and not os.path.islink(full):
                    raise ValueError(f'no such file: {path}')
            result = {
                'repository': root,
                'files': analyze_files(root, relative_paths),
            }
        except SystemExit as exit_request:
            status = 'ok' if not exit_request.code else 'fail'
            raise
        except (OSError, ValueError) as error:
            print(json.dumps({'error': str(error)}, ensure_ascii=True, sort_keys=True))
            return 2
        if args.format == 'json':
            print(bounded_json(result))
        else:
            sys.stdout.write(render_text(result))
        status = 'ok'
        return 0
    finally:
        _record_run(status, started, role, model)


if __name__ == '__main__':
    sys.exit(main())
