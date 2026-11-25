#!/usr/bin/env python3
import argparse
import os
import runpy
import stat
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
RoleProfiles = runpy.run_path(str(PACKAGE_ROOT / 'scripts/role-profile.py'))['RoleProfiles']
NATIVE_FORMS = runpy.run_path(str(PACKAGE_ROOT / 'tools/native_agent_forms.py'))


def validate_source(source):
    try:
        source_stat = source.stat()
    except OSError as error:
        return f'invalid source {source}: {error}'
    if not stat.S_ISREG(source_stat.st_mode):
        return f'source is not a regular file: {source}'
    try:
        with source.open('rb') as handle:
            handle.read(1)
    except OSError as error:
        return f'source is not readable {source}: {error}'
    return None


def inspect_destination(source, dest):
    try:
        dest_stat = dest.lstat()
    except FileNotFoundError:
        return True, None
    except OSError as error:
        return False, f'cannot inspect destination {dest}: {error}'
    if stat.S_ISLNK(dest_stat.st_mode):
        target = os.readlink(dest)
        if target == str(source):
            return False, None
        return False, f'conflicting symlink at {dest}: points to {target!r}, expected {str(source)!r}'
    if stat.S_ISDIR(dest_stat.st_mode):
        return False, f'conflicting directory at {dest}'
    return False, f'conflicting file at {dest}'


def plan_install(sources, empryo_dir, agents_dir):
    errors = []
    parent_error = False
    for directory in (empryo_dir, agents_dir):
        if (directory.exists() or directory.is_symlink()) and not directory.is_dir():
            errors.append(f'destination parent is not a directory: {directory}')
            parent_error = True
    to_create = []
    unchanged = 0
    for source in sources:
        error = validate_source(source)
        if error:
            errors.append(error)
        if parent_error:
            continue
        dest = agents_dir / source.name
        create, error = inspect_destination(source, dest)
        if error:
            errors.append(error)
        elif create:
            to_create.append((source, dest))
        else:
            unchanged += 1
    return to_create, unchanged, errors


def main():
    parser = argparse.ArgumentParser(
        description='Install symlinks for Empryo agent forms into ~/.empryo/agents.',
        allow_abbrev=False,
    )
    parser.parse_args()
    try:
        profiles = RoleProfiles(PACKAGE_ROOT)
        sources = NATIVE_FORMS['expected_forms'](PACKAGE_ROOT, 'empryo', profiles.roles)
    except (ValueError, OSError) as error:
        print(f'error: {error}', file=sys.stderr)
        return 1
    empryo_dir = Path.home() / '.empryo'
    agents_dir = empryo_dir / 'agents'
    to_create, unchanged, errors = plan_install(sources, empryo_dir, agents_dir)
    if errors:
        for error in errors:
            print(f'error: {error}', file=sys.stderr)
        return 1
    try:
        agents_dir.mkdir(parents=True, exist_ok=True)
        for source, dest in to_create:
            os.symlink(str(source), str(dest))
    except OSError as error:
        print(f'error: failed to install link: {error}', file=sys.stderr)
        return 1
    print(f'empryo: linked {len(to_create)} agents ({unchanged} unchanged) in {agents_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
