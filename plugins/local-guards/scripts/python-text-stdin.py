"""Separate a leading literal Python text-edit script from its shell suffix."""

import ast
import builtins
import importlib.machinery
import os
import re
import sys
import sysconfig

HEADER = re.compile(
    r"""[ \t]*python3[ \t]+-[ \t]+<<[ \t]*(?P<quote>['"])(?P<delimiter>[a-zA-Z_][a-zA-Z_0-9]*)(?P=quote)[ \t]*"""
)
DIFF_COMMAND = re.compile(
    r"git[ \t]+diff[ \t]+--check|diff[ \t]+-u(?:[ \t]+[a-zA-Z0-9./_][a-zA-Z0-9./_-]*){2}"
)
GUARDED_WORD = re.compile(
    r"(?<![\w-])(?:git-)?(?:push|send-pack|http-push|receive-pack|merge|rebase|branch)(?![\w-])"
)
NODES = {
    ast.Add, ast.Assert, ast.Assign, ast.Attribute, ast.AugAssign, ast.BinOp,
    ast.BoolOp, ast.Call, ast.Compare, ast.Constant, ast.Dict, ast.Eq, ast.Expr,
    ast.For, ast.FormattedValue, ast.FunctionDef, ast.GeneratorExp, ast.Global,
    ast.If, ast.IfExp, ast.Import, ast.ImportFrom, ast.In, ast.JoinedStr,
    ast.List, ast.ListComp, ast.Load, ast.Module, ast.Name, ast.Not, ast.NotEq,
    ast.NotIn, ast.Or, ast.And, ast.Return, ast.Slice, ast.Store, ast.Subscript,
    ast.Tuple, ast.UnaryOp, ast.alias, ast.arg, ast.arguments, ast.comprehension,
    ast.keyword,
}
CALLS = {"Path", "len", "print", "str", "range"}
METHODS = {
    "count", "exists", "find", "findall", "index", "join", "read_text",
    "replace", "split", "splitlines", "strip", "write_text",
}
ATTRIBUTES = METHODS | {"M", "MULTILINE", "Path"}
UNSAFE_BUILTINS = set(vars(builtins)) - CALLS
PYTHON_PATH_ENV = {
    "PYTHONCASEOK", "PYTHONHOME", "PYTHONINSPECT", "PYTHONPATH", "PYTHONPLATLIBDIR",
    "PYTHONPYCACHEPREFIX", "PYTHONSTARTUP", "PYTHONUSERBASE", "PYTHONWARNINGS",
}


def stdlib_can_be_shadowed(cwd):
    if any(os.environ.get(name) for name in PYTHON_PATH_ENV):
        return True

    finder = importlib.machinery.PathFinder
    stdlib_path = sysconfig.get_path("stdlib")
    stdlib_names = getattr(sys, "stdlib_module_names", set())
    for entry in os.listdir(cwd):
        name = entry.partition(".")[0]
        if not name or finder.find_spec(name, [cwd]) is None:
            continue
        if (name in stdlib_names or name in {"sitecustomize", "usercustomize"}
                or finder.find_spec(name, [stdlib_path]) is not None):
            return True
    return False


def text_edit_body(body):
    tree = ast.parse(body)
    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    for node in ast.walk(tree):
        if type(node) not in NODES:
            return False
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in UNSAFE_BUILTINS):
            return False
        if isinstance(node, ast.Import) and any(name.name not in ("pathlib", "re") for name in node.names):
            return False
        if isinstance(node, ast.ImportFrom) and (
            node.level or node.module != "pathlib" or any(name.name != "Path" for name in node.names)
        ):
            return False
        if isinstance(node, ast.Attribute) and node.attr not in ATTRIBUTES:
            return False
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name) and target.id in CALLS | functions:
                continue
            if isinstance(target, ast.Attribute) and target.attr in METHODS | {"Path"}:
                continue
            return False
    return True


def shell_suffix(command, cwd):
    header, _, remainder = command.partition("\n")
    match = HEADER.fullmatch(header)
    if match is None:
        return None

    lines = remainder.split("\n")
    delimiter = match["delimiter"]
    if delimiter not in lines:
        return None

    end = lines.index(delimiter)
    body = "\n".join(lines[:end])
    suffix = "\n".join(lines[end + 1:]).strip()
    if suffix and DIFF_COMMAND.fullmatch(suffix) is None:
        return None
    if GUARDED_WORD.search(body):
        return None
    try:
        if text_edit_body(body) and not stdlib_can_be_shadowed(cwd):
            return suffix
    except (OSError, SyntaxError, ValueError, RecursionError):
        pass
    return None


if __name__ == "__main__":
    suffix = shell_suffix(sys.stdin.read(), sys.argv[1])
    if suffix is None:
        sys.exit(1)
    sys.stdout.write(suffix)
