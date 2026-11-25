---
name: setup
description: Check dependencies and duplicate local hooks before enabling or troubleshooting the local-guards plugin.
argument-hint: "[<repository-path>]"
---

# Set up local guards

Check `command -v bash git jq python3`. All four commands are required; report missing commands before continuing. Missing dependencies at startup can prevent hook execution, so do not report the protections active until these checks pass. Backup and push protection also need Git 2.52 or newer, which provides `git reflog write`: from inside the target repository, probe it with `git reflog write -h 2>&1 | grep -q "reflog write"` and stop setup with a clear message naming the installed Git version when the probe fails.

Resolve this setup skill's directory from the absolute path used to load this `SKILL.md`. Run `python3 "<absolute-setup-directory>/scripts/check.py"`, substituting that resolved directory. The skill may run from any working directory; `PLUGIN_ROOT` is supplied to hooks and is not assumed in skill shell calls. The check reads dependencies and existing hook configuration without changing files. A nonzero result stops setup until the reported dependency or duplicate is resolved.

The check looks for matching hooks in `hooks.json` and inline `config.toml` under `CODEX_HOME`, falling back to `~/.codex`. Existing local hooks and plugin hooks run together. Before enabling the plugin, explain the duplicate entries and let the user decide whether to disable those specific local hooks. Preserve unrelated hooks such as terminal status notifications. Do not delete files or deactivate hooks silently.

After dependencies and duplicates are resolved, enable the plugin through the client's plugin controls and review/trust its hooks through `/hooks`. Trust is a separate local decision; installation does not grant it. Report any remaining activation step instead of bypassing trust.

Push protection and backups inspect supported shell commands, not arbitrary script contents. Git aliases and command-line configuration overrides are rejected; run the plain builtin command instead. Backups cover supported merge, rebase, and forced branch commands, including forced deletion of multiple branches. Before each operation, the hook creates a durable `refs/backup/<backup-name>-YYYYMMDD-HHMMSS` ref. Atomic creation preserves existing backups, adding a numeric suffix on collisions. It also writes `local-guards: backup <backup-name> <backup-sha>` to the repository's `HEAD` reflog. Local backups use the short branch name as `<backup-name>`; remote backups use the full source ref. The entry's old and new commit IDs remain the current HEAD, even when backing up another branch. Git must support `git reflog write`; an unsupported command or a failed ref or reflog write blocks the operation.

Forced remote-tracking deletion (`git branch -r -D origin/topic`, including combined flags and `--remotes --delete --force`) backs up `refs/remotes/origin/topic`, not a same-named local branch. For example, its backup ref is `refs/backup/refs/remotes/origin/topic-YYYYMMDD-HHMMSS`. Forced moves, copies, and other non-deletion commands with `-r` or `--remotes` are rejected; omit the remote flag when rewriting local branches.

Find backups with `git for-each-ref --format='%(refname) %(objectname)' refs/backup/` and recover with `git branch recovered-feature <backup-ref>`, substituting the selected full ref name. Backup refs retain their commits through reflog expiration and garbage collection until explicitly deleted with `git update-ref -d <backup-ref>`; prune them only after confirming recovery is unnecessary. Reflog messages remain useful for finding the saved SHA, but `HEAD@{n}` identifies HEAD, not a non-current branch's backup. Other branch rewrites and stacked branches affected by `--update-refs` still require manual backups. The duplicate scan also reports a local hook that calls `edit-checks.py`: edit checks belong to the `code-flow` plugin, and a local copy would run alongside them.
