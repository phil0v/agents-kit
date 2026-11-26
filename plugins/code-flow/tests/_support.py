import importlib.util
import re
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

try:
    import tomllib
except ModuleNotFoundError:
    sys.path.insert(0, str(ROOT / 'scripts'))
    import _tomli as tomllib
ROLES = ROOT / "roles"
HARNESSES = ROOT / "harnesses"
SYNC = ROOT / "tools" / "sync-agents.py"
SCRIPT = ROOT / "skills" / "plan-new" / "scripts" / "plan.py"
RUN_STATE_SCRIPT = ROOT / "skills" / "plan-new" / "scripts" / "run_state.py"
RESOURCE = re.compile(r"<package>/([A-Za-z0-9_./-]+)")


def load_role(path):
    return tomllib.loads(path.read_text())


def copy_package(directory, name='package'):
    relocated = Path(directory) / name
    relocated.mkdir()
    for entry in ('roles', 'harnesses', 'standards', 'scripts', 'tools'):
        shutil.copytree(ROOT / entry, relocated / entry, ignore=shutil.ignore_patterns('__pycache__'))
    forms_package = ROOT.parent / 'agy-agents'
    shutil.copytree(forms_package, Path(directory) / forms_package.name,
                    ignore=shutil.ignore_patterns('__pycache__'))
    return relocated


def load_plan_module():
    return load_script_module(SCRIPT, "plan_script")


def load_run_state_module():
    return load_script_module(RUN_STATE_SCRIPT, "run_state_script")


def load_script_module(script, name):
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def split_frontmatter(path):
    text = path.read_text()
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"unterminated frontmatter: {path}")
    data = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data, text[end + 5 :]


def frontmatter(path):
    data, _ = split_frontmatter(path)
    return data
