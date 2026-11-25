#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
TARGET_DIR = PACKAGE_ROOT / "standards"
SOURCE_PACKAGE = "plugins/code-quality"
HEADER_MARKER = "<!-- flow:header -->"
HEADER_LINE = re.compile(r"^" + re.escape(HEADER_MARKER) + r"\n", re.MULTILINE)
ANY_MARKER = re.compile(r"<!-- /?flow:[a-z-]+ -->")
STEP_HEADING = re.compile(r"^(\*\*Step )(\d+)( — )", re.MULTILINE)
SIBLING_LINK = re.compile(r"\]\(\.\./\.\./\.\./references/([A-Za-z0-9_.-]+\.md)\)")
PROTOCOL = "`<package>/standards/review-protocol.md`"
SCOPE_STEP = (
    "**Step 1 — Resolve scope**\n\n"
    f"Follow the scope and comparison rules in {PROTOCOL}."
)
FLOW_HEADER = f"Read {PROTOCOL} before this reference. Resolve `<package>` from the dispatch context.\n\n## Routing\n\n"

SPECS = {
    "agent-config.md": {
        "source": f"{SOURCE_PACKAGE}/skills/code-reviewer/references/agent-config.md",
        "header": (
            f"{FLOW_HEADER}"
            "Use when the diff is agent configuration: `SKILL.md`, subagent definitions, "
            "slash-command and prompt markdown, `CLAUDE.md`, `AGENTS.md`, and the harness settings "
            "driving them (`settings.json` permissions, hooks, model and effort keys). Hand off "
            "application code to `code-reviewer`, `symfony` or `frontend` by stack; a diff mixing "
            "config with code belongs wholly to the stack skill, these files included.\n"
        ),
        "step_offset": 1,
        "alt": {
            "comments": (
                f"Comment findings follow the policy in {PROTOCOL}. Under this reference they are "
                "banded as Minor in the `## Bands` table."
            ),
            "scope": SCOPE_STEP,
            "conventions-style": "`<package>/standards/code-style.md`",
        },
    },
    "code-design.md": {"source": f"{SOURCE_PACKAGE}/references/code-design.md"},
    "code-style.md": {"source": f"{SOURCE_PACKAGE}/references/code-style.md"},
    "frontend.md": {
        "source": f"{SOURCE_PACKAGE}/skills/code-reviewer/references/frontend.md",
        "header": (
            f"{FLOW_HEADER}"
            "React, Next.js, Vue, Nuxt and other frontend/UI code; `package.json`, build config "
            "(`vite.config.js`, `next.config.js`, `nuxt.config.ts`) and frontend CI; CSS, design "
            "systems and component libraries. Hand off: Symfony PHP → `symfony`; pure backend "
            "(Python, Go, Rust, Java) → `code-reviewer`; a diff that is nothing but AI agent "
            "configuration (`SKILL.md`, subagent, command and prompt markdown, `CLAUDE.md`/`AGENTS.md`, "
            "harness settings such as `settings.json`) → `agent-config`, though mixed with frontend "
            "code it stays here.\n\n"
            "**Verify the project first:** `package.json` with `react`, `next`, `vue` or `nuxt`, plus "
            "`pages/`, `app/` or `components/` holding `.tsx`, `.jsx` or `.vue`. If none, stop and say "
            "this reference only applies to frontend projects.\n"
        ),
        "step_offset": 1,
        "alt": {
            "comments": f"Comment findings follow the policy in {PROTOCOL}. Here they take 🔵 Style.",
            "scope": SCOPE_STEP,
            "band-style": "; fixed in the same run, never a reason for another pass.",
            "band-link": "`<package>/standards/code-style.md`",
        },
    },
    "golang.md": {"source": f"{SOURCE_PACKAGE}/references/golang.md"},
    "symfony.md": {
        "source": f"{SOURCE_PACKAGE}/skills/code-reviewer/references/symfony.md",
        "header": (
            f"{FLOW_HEADER}"
            "Symfony PHP applications, and the TypeScript that ships alongside them in the same "
            "repository. Hand off: a React/Next/Vue/Nuxt frontend of its own → `frontend`; Python, Go, "
            "Rust, Java, C# → `code-reviewer`; a diff that is nothing but AI agent configuration "
            "(`SKILL.md`, subagent, command and prompt markdown, `CLAUDE.md`/`AGENTS.md`, harness "
            "settings such as `settings.json`) → `agent-config`, though mixed with PHP it stays here.\n\n"
            "**Verify the project first:** `composer.json` with `symfony/symfony` or `symfony/flex`, "
            "plus `config/`, `bin/console` or `symfony.lock`. If none, stop and say this reference only "
            "applies to Symfony projects.\n"
        ),
        "step_offset": 1,
        "alt": {
            "comments": f"Comment findings follow the policy in {PROTOCOL}. Here they take 🔵 Style.",
            "scope": SCOPE_STEP,
            "band-style": "; fixed in the same run, never a reason for another pass.",
            "band-link": "`<package>/standards/code-style.md`",
        },
    },
    "test-quality.md": {
        "source": f"{SOURCE_PACKAGE}/skills/code-reviewer/references/test-quality.md",
        "header": "# Test quality\n\n",
        "step_offset": 0,
        "alt": {},
    },
}


class SyncError(ValueError):
    pass


def split_header(text, source):
    markers = HEADER_LINE.findall(text)
    if len(markers) != 1:
        raise SyncError(f"{source}: expected exactly one {HEADER_MARKER} marker line, found {len(markers)}")
    match = HEADER_LINE.search(text)
    return text[:match.start()], text[match.end():]


def replace_region(text, identifier, replacement, source):
    pattern = re.compile(
        r"<!-- flow:" + re.escape(identifier) + r" -->(.*?)<!-- /flow:" + re.escape(identifier) + r" -->",
        re.DOTALL,
    )
    found = len(pattern.findall(text))
    if found != 1:
        raise SyncError(f"{source}: expected exactly one flow:{identifier} region, found {found}")
    return pattern.sub(lambda _match: replacement, text)


def offset_steps(text, offset):
    return STEP_HEADING.sub(lambda match: f"{match[1]}{int(match[2]) + offset}{match[3]}", text)


def render(name, spec, repo_root):
    source = repo_root / spec["source"]
    text = source.read_text(encoding="utf-8")
    if "header" in spec:
        text = spec["header"] + split_header(text, source.relative_to(repo_root))[1]
    text = offset_steps(text, spec.get("step_offset", 0))
    for identifier, replacement in spec.get("alt", {}).items():
        text = replace_region(text, identifier, replacement, source.relative_to(repo_root))
    text = SIBLING_LINK.sub(r"](\1)", text)
    leftover = ANY_MARKER.search(text)
    if leftover is not None:
        raise SyncError(f"{source.relative_to(repo_root)}: unresolved marker {leftover.group(0)} in {name}")
    return text


def render_all(repo_root=REPO_ROOT):
    if not (repo_root / SOURCE_PACKAGE).is_dir():
        raise SyncError(f"source package not found: {repo_root / SOURCE_PACKAGE}")
    return {name: render(name, spec, repo_root) for name, spec in sorted(SPECS.items())}


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate review standards from code-quality or check them without writing.",
        allow_abbrev=False,
    )
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    try:
        rendered = render_all()
    except (ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        sys.exit(2)
    stale = []
    if check:
        for filename, text in sorted(rendered.items()):
            target = TARGET_DIR / filename
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(str(target.relative_to(PACKAGE_ROOT)))
    else:
        TARGET_DIR.mkdir(parents=True, exist_ok=True)
        for filename, text in sorted(rendered.items()):
            (TARGET_DIR / filename).write_text(text, encoding="utf-8")
        print(f"standards: wrote {len(rendered)} files")
    if stale:
        print("stale standards:", *stale, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
