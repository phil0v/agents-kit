import os
import subprocess

EXCLUDED_DIRECTORY_NAMES = frozenset({
    '.git', '.hg', '.svn', '.tox', '.venv', '__pycache__', 'build', 'dist',
    'node_modules', 'vendor',
})


def is_openspec_repo(root):
    return os.path.isdir(os.path.join(os.path.realpath(root), "openspec"))


def git_toplevel(cwd):
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return cwd
    if result.returncode != 0:
        return cwd
    path = result.stdout.strip()
    return path if path else cwd


def _git_iter_files(root):
    if not os.path.exists(os.path.join(root, '.git')):
        return None
    try:
        result = subprocess.run(
            ['git', '-C', root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=10,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    directories = {}
    for entry in result.stdout.split(b'\0'):
        if not entry:
            continue
        parts = os.fsdecode(entry).split('/')
        parents = parts[:-1]
        name = parts[-1]
        if not name or not EXCLUDED_DIRECTORY_NAMES.isdisjoint(parents):
            continue
        for index in range(len(parents)):
            prefix = tuple(parents[:index])
            directories.setdefault(prefix, (set(), set()))[1].add(parents[index])
        directories.setdefault(tuple(parents), (set(), set()))[0].add(name)
    if not directories:
        return []
    collected = []
    stack = [()]
    while stack:
        prefix = stack.pop()
        files, subdirs = directories[prefix]
        for name in sorted(files):
            collected.append(os.path.join(root, *prefix, name))
        stack.extend(prefix + (subdir,) for subdir in sorted(subdirs, reverse=True))
    return collected


def iter_files(root):
    paths = _git_iter_files(root)
    if paths is not None:
        yield from paths
        return
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(
            name for name in subdirectories if name not in EXCLUDED_DIRECTORY_NAMES)
        for name in sorted(names):
            yield os.path.join(directory, name)


def iter_text_lines(root):
    for path in iter_files(root):
        try:
            with open(path, 'rb') as source:
                content = source.read()
        except OSError:
            continue
        if b'\0' in content:
            continue
        yield os.path.relpath(path, root), [os.fsdecode(line) for line in content.splitlines()]

