#!/usr/bin/env python3
import argparse
import json
import runpy
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FORMS_DIR = PACKAGE_ROOT / "harnesses"
FORM_FIELDS = {"claude": "effort", "pi": "thinking"}
RoleProfiles = runpy.run_path(str(PACKAGE_ROOT / 'scripts/role-profile.py'))['RoleProfiles']
NATIVE_FORMS = runpy.run_path(str(PACKAGE_ROOT / 'tools/native_agent_forms.py'))


def scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return json.dumps(value, ensure_ascii=False)


def render_frontmatter(fields, body):
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in fields]
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip("\n") + "\n"


def render_role(profile, harness, config):
    fields = [
        ("name", scalar(profile["name"])),
        ("description", scalar(profile["description"])),
        ("model", scalar(profile["model"])),
    ]
    if profile['effort_policy'] == 'explicit':
        fields.append((FORM_FIELDS[harness], scalar(profile['effort'])))
    if 'tools' in profile:
        fields.append(("tools", ", ".join(profile['tools'])))
    for key, value in config.get("frontmatter_defaults", {}).items():
        fields.append((key, scalar(value)))
    return render_frontmatter(fields, profile["developer_instructions"])


def render_antigravity(profile):
    fields = [(key, scalar(value)) for key, value in NATIVE_FORMS['antigravity_fields'](profile)]
    return render_frontmatter(fields, profile["developer_instructions"])


def render_empryo(role, capabilities):
    fields = [
        (key, ", ".join(value) if key == "tools" else scalar(value))
        for key, value in NATIVE_FORMS['empryo_fields'](role, capabilities)
    ]
    return render_frontmatter(fields, role["developer_instructions"])


def sync_directory(target_dir, label, rendered, check, is_managed):
    stale = []
    if not check:
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename, text in sorted(rendered.items()):
            (target_dir / filename).write_text(text)
        print(f"{label}: wrote {len(rendered)} forms")
    else:
        for filename, text in sorted(rendered.items()):
            target = target_dir / filename
            if not target.is_file() or target.read_text() != text:
                stale.append(str(target.relative_to(PACKAGE_ROOT)))
    if target_dir.is_dir():
        for target in sorted(target_dir.iterdir()):
            if target.is_file() and is_managed(target) and target.name not in rendered:
                if check:
                    stale.append(str(target.relative_to(PACKAGE_ROOT)))
                else:
                    target.unlink()
                    print(f"removed unmatched form: {target.relative_to(PACKAGE_ROOT)}", file=sys.stderr)
    return stale


def main():
    parser = argparse.ArgumentParser(
        description="Regenerate agent forms or check them without writing.",
        allow_abbrev=False,
    )
    parser.add_argument("--check", action="store_true")
    check = parser.parse_args().check
    try:
        profiles = RoleProfiles()
        capabilities = NATIVE_FORMS['role_capabilities'](profiles)
        targets = []
        for harness in sorted(FORM_FIELDS):
            config = profiles.harnesses[harness]
            rendered = {
                f"{name}.md": render_role(profiles.resolve(harness, name), harness, config)
                for name in profiles.roles
            }
            targets.append((FORMS_DIR / harness, harness, rendered, lambda path: path.suffix == ".md"))
        for client, renderer in (
            ('antigravity', lambda name: render_antigravity(profiles.resolve('antigravity', name))),
            ('empryo', lambda name: render_empryo(profiles.roles[name], capabilities[name])),
        ):
            rendered = {
                NATIVE_FORMS['form_filename'](name): renderer(name)
                for name in profiles.roles
            }
            targets.append((
                NATIVE_FORMS['form_dir'](PACKAGE_ROOT, client),
                client,
                rendered,
                NATIVE_FORMS['is_managed_form'],
            ))
    except (ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        sys.exit(2)
    stale = []
    for target_dir, label, rendered, is_managed in targets:
        stale.extend(sync_directory(target_dir, label, rendered, check, is_managed))
    if stale:
        print("stale agent forms:", *stale, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
