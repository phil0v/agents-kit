#!/usr/bin/env python3
import hashlib
import importlib.util
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _load_repository_helper():
    spec = importlib.util.spec_from_file_location(
        "code_flow_repository_helper", PACKAGE_ROOT / "scripts" / "repository.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_repository = _load_repository_helper()
git_toplevel = _repository.git_toplevel
is_openspec_repo = _repository.is_openspec_repo


OPENSPEC_REFUSAL = (
    "repository contains an openspec directory; the native OpenSpec workflow owns "
    f"planning there — use it or {PACKAGE_ROOT / 'skills' / 'spec-flow' / 'SKILL.md'}"
)


REQUIRED_SECTIONS = (
    "Context",
    "Summary",
    "Steps",
    "Acceptance",
    "Verification",
)

SECTION_ORDER = (
    "Context",
    "Summary",
    "Steps",
    "Acceptance",
    "Decisions",
    "Out of scope",
    "Open questions",
    "Verification",
)

SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$")
STEP_HEADING = re.compile(r"^(\d+)\.\s+(.*)$")
STEP_CHECKBOX = re.compile(r"^\[([ xX])\]\s*(.*)$")
ACCEPTANCE_ITEM = re.compile(r"^\s*[-*+]\s*(A\d+)\.\s*(.*)$")
OPEN_QUESTION = re.compile(r"^\s*[-*+]\s+(.*)$")
STEP_ATTACHMENT = re.compile(r"\bstep (\d+)\b")
BACKTICK_TOKEN = re.compile(r"`([^`\n]+)`")
ANCHOR = re.compile(r"`([^`\n]+):(\d+)`\s*(?:\"([^\"]+)\"|'([^']+)')")
CHECKBOX_TICK = re.compile(r"^(\s*[-*+]\s*\[)[xX](\])")
HEADING_TICK = re.compile(r"^(\d+\.\s*\[)[xX](\])")
ANCHOR_LINE_NUMBER = re.compile(r"`([^`\n]+):\d+`")
FENCE = re.compile(r"^\s*```")
CONTRACT_PREFIX = re.compile(r"^(depends on|acceptance|creates|done when|test):\s*(.*)$")
BULLET = re.compile(r"^[-*+]\s+")
PATH_TRAILING_LINE = re.compile(r":\d+$")
ACCEPTANCE_ID = re.compile(r"^A\d+$")
TEST_EXEMPTION = re.compile(r"^none\s+(--|—)", re.IGNORECASE)


@dataclass
class Diagnostic:
    code: str
    message: str
    step: Optional[int] = None
    path: Optional[str] = None


def _mask_fences(lines):
    masked = []
    in_fence = False
    for line in lines:
        if FENCE.match(line):
            in_fence = not in_fence
            masked.append(None)
        elif in_fence:
            masked.append(None)
        else:
            masked.append(line)
    return masked


def parse_plan(path):
    with open(path, "r", encoding="utf-8-sig") as handle:
        text = handle.read()
    lines = _mask_fences(text.splitlines())
    title = None
    sections = {}
    duplicates = []
    current = None
    for number, line in enumerate(lines, start=1):
        if line is None:
            continue
        heading = SECTION_HEADING.match(line)
        if heading:
            current = heading.group(1)
            if current in sections:
                duplicates.append(current)
            sections.setdefault(current, [])
            continue
        if current is None:
            if title is None and line.startswith("# "):
                title = line[2:].strip()
            continue
        sections[current].append((number, line))
    steps = []
    orphans = []
    for number, line in sections.get("Steps", []):
        heading = STEP_HEADING.match(line)
        if heading:
            step_title = heading.group(2).strip()
            checkbox = STEP_CHECKBOX.match(step_title)
            done = False
            if checkbox:
                done = checkbox.group(1) != " "
                step_title = checkbox.group(2).strip()
            steps.append(
                {
                    "id": int(heading.group(1)),
                    "title": step_title,
                    "checkbox": checkbox is not None,
                    "done": done,
                    "line": number,
                    "body": [],
                    "contracts": {},
                    "depends_on": [],
                    "malformed_dependencies": [],
                    "acceptance": [],
                    "creates": [],
                    "done_when": "",
                    "test": "",
                }
            )
            continue
        if steps:
            steps[-1]["body"].append(line)
        elif line.strip():
            orphans.append(number)
    for step in steps:
        _read_contracts(step)
    acceptance = []
    for _number, line in sections.get("Acceptance", []):
        item = ACCEPTANCE_ITEM.match(line)
        if item:
            acceptance.append(item.group(1))
    open_questions = []
    for _number, line in sections.get("Open questions", []):
        item = OPEN_QUESTION.match(line)
        if not item:
            continue
        question = item.group(1)
        resolved = question.startswith("[resolved]")
        if resolved:
            question = question[len("[resolved]"):].strip()
        open_questions.append(
            {
                "text": question,
                "resolved": resolved,
                "steps": [int(n) for n in STEP_ATTACHMENT.findall(question)],
            }
        )
    return {
        "path": path,
        "title": title,
        "raw": text,
        "sections": sections,
        "duplicate_sections": duplicates,
        "steps_section_orphans": orphans,
        "steps": steps,
        "acceptance": acceptance,
        "open_questions": open_questions,
    }


def _read_contracts(step):
    for raw in step["body"]:
        content = BULLET.sub("", raw.strip())
        match = CONTRACT_PREFIX.match(content)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        step["contracts"][key] = value
        if key == "depends on":
            if value.lower() == "none":
                step["depends_on"] = []
                step["malformed_dependencies"] = []
            else:
                parsed, malformed = [], []
                for token in value.split(","):
                    token = token.strip()
                    if token.isdigit():
                        parsed.append(int(token))
                    else:
                        malformed.append(token)
                step["depends_on"] = parsed
                step["malformed_dependencies"] = malformed
        elif key == "acceptance":
            step["acceptance"] = [t.strip() for t in value.split(",") if t.strip()]
        elif key == "creates":
            step["creates"] = [t.strip() for t in value.split(",") if t.strip()]
        elif key == "done when":
            step["done_when"] = value
        elif key == "test":
            step["test"] = value


def _parse_steps_arg(steps_arg, plan_steps):
    step_ids = []
    valid_ids = {s["id"] for s in plan_steps}
    for token in steps_arg.split(","):
        token = token.strip()
        if not token.isdigit():
            return None, _fail("usage", f"--steps expects comma-separated step IDs, got `{token}`")
        step_id = int(token)
        if step_id not in valid_ids:
            return None, _fail("usage", f"step {step_id} is not defined in the plan")
        step_ids.append(step_id)
    return step_ids, None


def _split_steps_args(rest):
    if len(rest) == 1:
        return rest[0], None, None
    if len(rest) == 3 and rest[1] == "--steps":
        return rest[0], rest[2], None
    return None, None, _fail("usage", f"unexpected argument `{rest[1] if len(rest) > 1 else rest}`")


def _dedup(diagnostics):
    seen = set()
    unique = []
    for diagnostic in diagnostics:
        key = (diagnostic.code, diagnostic.message, diagnostic.step, diagnostic.path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(diagnostic)
    return unique


def _path_tokens(plan):
    tokens = []
    for name, lines in plan["sections"].items():
        for number, line in lines:
            for match in BACKTICK_TOKEN.finditer(line):
                token = match.group(1)
                if "/" in token and not any(c.isspace() for c in token):
                    tokens.append((name, number, PATH_TRAILING_LINE.sub("", token)))
    for step in plan["steps"]:
        for created in step["creates"]:
            tokens.append(("Steps", step["line"], created))
    return tokens


def _check_path(token, repository, created, diagnostics, step=None):
    location = f"step {step}" if step is not None else "plan"
    if token.startswith(("/", "~")):
        diagnostics.append(Diagnostic("absolute_path", f"{location}: `{token}` is not repository-relative", step=step, path=token))
        return
    if ".." in token.split("/"):
        diagnostics.append(Diagnostic("escaping_path", f"{location}: `{token}` leaves the repository", step=step, path=token))
        return
    repository_real = os.path.realpath(repository)
    target_real = os.path.realpath(os.path.join(repository, token))
    if target_real != repository_real and not target_real.startswith(repository_real + os.sep):
        diagnostics.append(Diagnostic("symlink_escape", f"{location}: `{token}` resolves outside the repository", step=step, path=token))
        return
    if not os.path.exists(os.path.join(repository, token)) and token not in created:
        diagnostics.append(Diagnostic("missing_file", f"{location}: `{token}` does not exist and is not declared as a creation", step=step, path=token))


def _cycle_members(steps):
    ids = {s["id"] for s in steps}
    deps = {s["id"]: [d for d in s["depends_on"] if d in ids] for s in steps}
    state = {}
    cycle = set()

    def visit(node, trail):
        state[node] = 1
        for dep in deps.get(node, []):
            if state.get(dep) == 1:
                cycle.update(trail[trail.index(dep):] + [dep] if dep in trail else [dep])
            elif state.get(dep) != 2:
                visit(dep, trail + [dep])
        state[node] = 2

    for node in ids:
        if state.get(node) is None:
            visit(node, [node])
    return cycle


def validate_plan(plan, repository, selected_steps=None):
    diagnostics = []
    for name in REQUIRED_SECTIONS:
        if name not in plan["sections"]:
            diagnostics.append(Diagnostic("missing_section", f"## {name} section is missing"))
    for name in plan["duplicate_sections"]:
        diagnostics.append(Diagnostic("duplicate_section", f"## {name} section appears more than once"))
    if "Checklist" in plan["sections"]:
        diagnostics.append(Diagnostic("obsolete_section", "## Checklist section is obsolete; tick step headings instead"))
    present_order = [name for name in plan["sections"] if name in SECTION_ORDER]
    expected_order = [name for name in SECTION_ORDER if name in plan["sections"]]
    if present_order != expected_order:
        diagnostics.append(
            Diagnostic("section_order", "sections must appear in the canonical order: " + ", ".join(SECTION_ORDER))
        )
    for number in plan["steps_section_orphans"]:
        diagnostics.append(Diagnostic("orphan_step_content", f"line {number} in ## Steps is not part of any step"))
    steps = plan["steps"]
    ids = [s["id"] for s in steps]
    id_set = set(ids)
    missing = set(range(1, max(ids) + 1)) - id_set if ids else set()
    for number in sorted(missing):
        diagnostics.append(Diagnostic("missing_step_id", f"step {number} is missing from the numbering"))
    if not ids:
        diagnostics.append(Diagnostic("missing_step_id", "## Steps contains no steps"))
    seen = set()
    for step_id in ids:
        if step_id in seen:
            diagnostics.append(Diagnostic("duplicate_step_id", f"step {step_id} is declared more than once", step=step_id))
        seen.add(step_id)
    defined_acceptance = set(plan["acceptance"])
    created = {c for s in steps for c in s["creates"]}
    for step in steps:
        if not step["checkbox"]:
            diagnostics.append(
                Diagnostic("missing_step_checkbox", f"step {step['id']}: heading needs a checkbox: `N. [ ] <title>` or `N. [x] <title>`", step=step["id"])
            )
        for token in step["malformed_dependencies"]:
            diagnostics.append(Diagnostic("malformed_dependency", f"step {step['id']}: `depends on: {token}` is not a step ID", step=step["id"]))
        for dep in step["depends_on"]:
            if dep not in id_set:
                diagnostics.append(Diagnostic("unknown_dependency", f"step {step['id']}: depends on unknown step {dep}", step=step["id"]))
            if dep == step["id"]:
                diagnostics.append(Diagnostic("self_dependency", f"step {step['id']}: depends on itself", step=step["id"]))
        for contract in ("depends on", "done when", "test"):
            if contract not in step["contracts"]:
                diagnostics.append(Diagnostic("missing_step_contract", f"step {step['id']}: missing `{contract}:` line", step=step["id"]))
        if step["contracts"].get("done when", "").strip() == "" and "done when" in step["contracts"]:
            diagnostics.append(Diagnostic("missing_step_contract", f"step {step['id']}: `done when:` is empty", step=step["id"]))
        if step["contracts"].get("test", "").strip() == "" and "test" in step["contracts"]:
            diagnostics.append(Diagnostic("missing_step_contract", f"step {step['id']}: `test:` is empty", step=step["id"]))
        test_value = step["test"]
        if test_value.lower().startswith("none") and not TEST_EXEMPTION.match(test_value):
            diagnostics.append(Diagnostic("missing_step_contract", f"step {step['id']}: `test:` must name a command or `none -- <reason>`", step=step["id"]))
        if not step["acceptance"]:
            diagnostics.append(Diagnostic("missing_acceptance_reference", f"step {step['id']}: names no acceptance criteria", step=step["id"]))
        for criterion in step["acceptance"]:
            if not ACCEPTANCE_ID.match(criterion):
                diagnostics.append(Diagnostic("unknown_acceptance_reference", f"step {step['id']}: `{criterion}` is not an acceptance ID", step=step["id"]))
            elif criterion not in defined_acceptance:
                diagnostics.append(Diagnostic("unknown_acceptance_reference", f"step {step['id']}: `{criterion}` is not defined in ## Acceptance", step=step["id"]))
    for member in sorted(_cycle_members(steps)):
        diagnostics.append(Diagnostic("cyclic_dependency", f"step {member}: dependency cycle detected", step=member))
    selected = set(selected_steps) if selected_steps is not None else id_set
    for question in plan["open_questions"]:
        if question["resolved"]:
            continue
        attached = [n for n in question["steps"] if n in selected]
        for number in attached:
            diagnostics.append(Diagnostic("open_question_unresolved", f"step {number}: open question is unresolved: {question['text']}", step=number))
    for name, number, token in _path_tokens(plan):
        step = None
        if name == "Steps":
            for candidate in steps:
                if candidate["line"] <= number:
                    step = candidate["id"]
        _check_path(token, repository, created, diagnostics, step=step)
    return [asdict(d) for d in _dedup(diagnostics)]


def dependency_order(plan):
    steps = plan["steps"]
    ids = {s["id"] for s in steps}
    deps = {s["id"]: sorted({d for d in s["depends_on"] if d in ids}) for s in steps}
    order = []
    done = set()
    while len(done) < len(ids):
        progressed = False
        for step in sorted(ids):
            if step in done:
                continue
            if all(dep in done for dep in deps[step]):
                order.append(step)
                done.add(step)
                progressed = True
        if not progressed:
            return None
    return order


def plan_digest(plan):
    normalized = []
    for line in plan["raw"].splitlines():
        line = CHECKBOX_TICK.sub(r"\1 \2", line)
        line = HEADING_TICK.sub(r"\1 \2", line)
        line = ANCHOR_LINE_NUMBER.sub(r"`\1`", line)
        normalized.append(line)
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def _anchor_quote(match):
    return match.group(3) if match.group(3) is not None else match.group(4)


def _collect_line_anchors(anchors, step_id, line):
    for match in ANCHOR.finditer(line):
        end = match.end()
        cut = end < len(line) and line[end].isalnum()
        anchors.append((step_id, match.group(1), int(match.group(2)), _anchor_quote(match), cut))


def _plan_anchors(plan, step_ids):
    anchors = []
    wanted = set(step_ids)
    for name, lines in plan["sections"].items():
        if name != "Context":
            continue
        for _number, line in lines:
            _collect_line_anchors(anchors, None, line)
    for step in plan["steps"]:
        if step["id"] not in wanted:
            continue
        for raw in step["body"]:
            _collect_line_anchors(anchors, step["id"], raw)
    return anchors


def check_anchors(plan, repository, step_ids):
    diagnostics = []
    cache = {}
    for step_id, path, line_number, quote, cut in _plan_anchors(plan, step_ids):
        label = f"step {step_id}" if step_id is not None else "plan"
        if cut:
            diagnostics.append(
                Diagnostic(
                    "anchor_unparsable",
                    f"{label}: anchor quote in `{path}` appears cut by an inner quote; use the other quote form or a neighbouring line: {quote}",
                    step=step_id,
                    path=path,
                )
            )
            continue
        full = os.path.join(repository, path)
        if full not in cache:
            if not os.path.exists(full):
                cache[full] = ("missing", None)
            else:
                try:
                    with open(full, "r", encoding="utf-8") as handle:
                        cache[full] = ("ok", [line.strip() for line in handle.read().splitlines()])
                except OSError as exc:
                    cache[full] = ("io_error", exc)
                except UnicodeDecodeError as exc:
                    cache[full] = ("decode_error", exc)
        status, payload = cache[full]
        if status == "missing":
            diagnostics.append(Diagnostic("anchor_source_missing", f"{label}: `{path}` does not exist", step=step_id, path=path))
            continue
        if status == "io_error":
            diagnostics.append(Diagnostic("io_error", f"{label}: cannot read `{path}`: {payload}", step=step_id, path=path))
            continue
        if status == "decode_error":
            diagnostics.append(Diagnostic("io_error", f"{label}: cannot decode `{path}`: {payload}", step=step_id, path=path))
            continue
        stripped = payload
        wanted = quote.strip()
        index = line_number - 1
        if 0 <= index < len(stripped) and stripped[index] == wanted:
            continue
        occurrences = [i + 1 for i, line in enumerate(stripped) if line == wanted]
        if len(occurrences) == 1:
            continue
        if not occurrences:
            diagnostics.append(Diagnostic("anchor_missing", f"{label}: quote not found in `{path}`: {quote}", step=step_id, path=path))
        else:
            diagnostics.append(Diagnostic("anchor_ambiguous", f"{label}: quote matches several lines in `{path}`: {quote}", step=step_id, path=path))
    return [asdict(d) for d in _dedup(diagnostics)]


def _openspec_diagnostics(repository):
    if not is_openspec_repo(repository):
        return []
    openspec_path = os.path.join(os.path.realpath(repository), "openspec")
    return [asdict(Diagnostic("openspec_project", OPENSPEC_REFUSAL, path=openspec_path))]


def _emit(payload):
    print(json.dumps(payload, indent=2))


def _fail(code, message):
    print(json.dumps({"error": {"code": code, "message": message}}), file=sys.stderr)
    return 2


def _load(plan_path):
    try:
        return parse_plan(plan_path), None
    except OSError as exc:
        return None, _fail("io_error", f"cannot read {plan_path}: {exc}")
    except UnicodeDecodeError as exc:
        return None, _fail("io_error", f"cannot decode {plan_path}: {exc}")


def cmd_validate(plan_path, steps_arg):
    parsed, exit_code = _load(plan_path)
    if exit_code is not None:
        return exit_code
    repository = git_toplevel(os.path.dirname(os.path.abspath(plan_path)))
    selected_steps = None
    if steps_arg is not None:
        selected_steps, exit_code = _parse_steps_arg(steps_arg, parsed["steps"])
        if exit_code is not None:
            return exit_code
    diagnostics = _openspec_diagnostics(repository)
    diagnostics.extend(validate_plan(parsed, repository, selected_steps))
    _emit({"plan": plan_path, "digest": plan_digest(parsed), "diagnostics": diagnostics})
    return 0 if not diagnostics else 1


def cmd_inspect(plan_path):
    parsed, exit_code = _load(plan_path)
    if exit_code is not None:
        return exit_code
    repository = git_toplevel(os.path.dirname(os.path.abspath(plan_path)))
    diagnostics = _openspec_diagnostics(repository)
    diagnostics.extend(validate_plan(parsed, repository))
    order = dependency_order(parsed)
    positions = {step_id: index for index, step_id in enumerate(order)} if order else {}
    steps = [
        {
            "id": step["id"],
            "title": step["title"],
            "done": step["done"],
            "depends_on": step["depends_on"],
            "acceptance": step["acceptance"],
            "creates": step["creates"],
            "done_when": step["done_when"],
            "test": step["test"],
            "order": positions.get(step["id"]),
        }
        for step in parsed["steps"]
    ]
    _emit(
        {
            "plan": plan_path,
            "title": parsed["title"],
            "digest": plan_digest(parsed),
            "steps": steps,
            "acceptance": parsed["acceptance"],
            "diagnostics": diagnostics,
        }
    )
    return 0 if not diagnostics else 1


def cmd_select(directory):
    repository = git_toplevel(os.path.abspath(directory))
    diagnostics = _openspec_diagnostics(repository)
    candidates = []
    plans_dir = os.path.join(directory, "plans")
    if os.path.isdir(plans_dir):
        for name in sorted(os.listdir(plans_dir)):
            if name.endswith(".md") and os.path.isfile(os.path.join(plans_dir, name)):
                candidates.append(f"plans/{name}")
        for ticket in sorted(os.listdir(plans_dir)):
            ticket_dir = os.path.join(plans_dir, ticket)
            if not os.path.isdir(ticket_dir) or ticket.startswith(".") or ticket == "archive":
                continue
            for name in sorted(os.listdir(ticket_dir)):
                if name.endswith(".md") and os.path.isfile(os.path.join(ticket_dir, name)):
                    candidates.append(f"plans/{ticket}/{name}")
    candidates.sort()
    selected = candidates[0] if len(candidates) == 1 else None
    _emit({"directory": directory, "candidates": candidates, "selected": selected, "diagnostics": diagnostics})
    return 0 if selected is not None and not diagnostics else 1


def cmd_anchors(plan_path, steps_arg):
    parsed, exit_code = _load(plan_path)
    if exit_code is not None:
        return exit_code
    if steps_arg is None:
        step_ids = [s["id"] for s in parsed["steps"]]
    else:
        step_ids, exit_code = _parse_steps_arg(steps_arg, parsed["steps"])
        if exit_code is not None:
            return exit_code
    repository = git_toplevel(os.path.dirname(os.path.abspath(plan_path)))
    diagnostics = _openspec_diagnostics(repository)
    diagnostics.extend(check_anchors(parsed, repository, step_ids))
    _emit({"plan": plan_path, "steps": step_ids, "diagnostics": diagnostics})
    return 0 if not diagnostics else 1


def cmd_preflight(plan_path, steps_arg):
    parsed, exit_code = _load(plan_path)
    if exit_code is not None:
        return exit_code
    repository = git_toplevel(os.path.dirname(os.path.abspath(plan_path)))
    selected_steps = None
    if steps_arg is not None:
        selected_steps, exit_code = _parse_steps_arg(steps_arg, parsed["steps"])
        if exit_code is not None:
            return exit_code
    step_ids = selected_steps if selected_steps is not None else [s["id"] for s in parsed["steps"]]
    stages = []
    openspec = _openspec_diagnostics(repository)
    stages.append({"stage": "openspec", "diagnostics": openspec})
    if not openspec:
        validation = validate_plan(parsed, repository, selected_steps)
        stages.append({"stage": "validate", "diagnostics": validation})
        if not validation:
            stages.append({"stage": "anchors", "diagnostics": check_anchors(parsed, repository, step_ids)})
    _emit({"plan": plan_path, "digest": plan_digest(parsed), "stages": stages})
    return 1 if any(stage["diagnostics"] for stage in stages) else 0


def main(argv):
    if not argv:
        return _fail("usage", "a command is required: validate, inspect, select, anchors, preflight")
    command, rest = argv[0], argv[1:]
    if command == "validate" and len(rest) in (1, 3):
        plan_path, steps_arg, exit_code = _split_steps_args(rest)
        if exit_code is not None:
            return exit_code
        return cmd_validate(plan_path, steps_arg)
    if command == "inspect" and len(rest) == 1:
        return cmd_inspect(rest[0])
    if command == "select" and len(rest) <= 1:
        return cmd_select(rest[0] if rest else os.getcwd())
    if command == "anchors" and len(rest) in (1, 3):
        plan_path, steps_arg, exit_code = _split_steps_args(rest)
        if exit_code is not None:
            return exit_code
        return cmd_anchors(plan_path, steps_arg)
    if command == "preflight" and len(rest) in (1, 3):
        plan_path, steps_arg, exit_code = _split_steps_args(rest)
        if exit_code is not None:
            return exit_code
        return cmd_preflight(plan_path, steps_arg)
    return _fail("usage", f"unknown usage: plan.py {' '.join(argv)}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
