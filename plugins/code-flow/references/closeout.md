# Close-out procedure

Resolve `<package>` from the calling workflow's absolute package root. This reference owns the inspection and fix algorithm. Each rule is stated once, and other sections cite it. The wrap-up entrypoint owns scope selection, flags, and commit authorization. Load this reference when close-out starts. Complete its gates before the entrypoint's commit and report steps.

## Inspection groups and gates

Before each inspection round, define its review scope. Assign every file in it to one group. Use at most twelve files and 1,500 added/deleted lines per group. Count scoped untracked files by their full line count. A single file exceeding the line limit gets its own group. Keep related implementation and tests together if the limits permit. Name binary files for explicit inspection or a reported limitation. Recompute groups when fixes add paths or enlarge a group past a limit.

The routing threshold for the single-group fast path has two limits.

- At most 12 scoped files.
- At most 1,500 changed lines, with no single file exceeding 1,500 changed lines.

Both this threshold and the group limits count scoped untracked files by their full line count. Initial, fix-added, and step 4 follow-up rounds qualify for the no-manifest fast path when post-exclusion `round_scope` fits this threshold. Any single file exceeding that line limit stays on the manifest path so it receives a context pack. Leave the fast path only when the merged post-exclusion `round_scope` exceeds this threshold. A round scope within this threshold routes to one unpartitioned group covering the whole round scope. The group dispatches under the same coverage, acceptance, and mutation gates as any group. Dispatch it from a manifest or inline under step 1's no-manifest exception. See Run step 1 for inline prompt inputs. Step 4's final-round skip and widened-skip conditions apply unchanged. A first round that requires no fixes ends the loop with no final round.

A calling workflow may owe a verification pass over the scoped change before close-out. This is its caller verification duty. The duty is a pre-close-out duty, distinct from the post-exit verification duty that step 4's final round carries. If the fused initial round is supplied with an acceptance baseline, the duty rides that round. This applies to a single-group round and to a multi-group round. Each group's dispatch prompt carries the duty. The round's manifest acceptance routing also carries it. No new manifest schema field is introduced. On the no-manifest fast path, the prompt's acceptance checklist alone carries the duty. A completed initial round with the duty attached satisfies the duty. The calling workflow then records the round as its verification pass. If wrap-up edits code after Round 1, attach the caller verification duty to the post-fix verification round. That post-fix round satisfies the duty if two conditions hold. First, all affected acceptance criteria verify clean against the supplied baseline. Second, no `openspec` change artifacts changed after Round 1. The calling workflow repeats its verification pass in only two cases. Either `openspec` change artifacts changed after Round 1, or an OpenSpec artifact check stays unverified. The manifest's per-group acceptance routing already assigns every criterion. Thus no routing precondition remains.

A scoped path that is terminal evidence never enters any round's review scope. The terminal-evidence path set has these members.

- An archived plan under `plans/archive/`.
- A ticket grouping's archive directory under `plans`.
- An archived spec artifact under the `openspec/changes/archive/` directory of an `openspec` repository.

A terminal-evidence path stays in the round manifest's original scope. It is excluded from every round scope and group. Thus no reviewer reads it. No findings or edits are made against its body. If a terminal-evidence path arrives as a scoped untracked file, drop it from `scoped_untracked` as well. The manifest requires every scoped untracked path to lie inside the round scope. The coordinator verifies each excluded path mechanically instead. The verification passes in either case below.

- The archived body is byte-identical to its pre-archive source in the same scoped diff.
- The archived body matches the recorded archive receipt.

Record the verification in the round evidence. An unverifiable archive move is a blocker reported with its evidence, not a review finding. The pre-round style gate's complete scoped file list excludes the terminal-evidence paths too. Thus no formatter or linter runs against an archived body, and byte-identical move verification stays sound.

A scoped path in the generated-path set never enters any round's review scope. The generated-path set has these members.

- A built-in lockfile name carried by the lockfile consistency table in `<package>/references/checks.md`, plus `flake.lock`.
- A path declared by a `## Generated paths` section in the repository's instruction files. Discover these sections in the same order `<package>/references/checks.md` reads `## Checks` sections.

Initialize the round manifest with `--exclude` carrying the scoped generated paths and the scoped terminal-evidence paths. Thus each path stays in the manifest's `original_scope` and `excluded` fields. Each path never enters any item below.

- `round_scope`.
- Any group.
- `scoped_untracked`.
- The pre-round style gate's complete scoped file list.
- The single-group fast path's file and line counting.

If a generated path arrives as a scoped untracked file, drop it from `scoped_untracked` as well. The coordinator verifies each excluded path mechanically instead. If a lockfile consistency check exists, the coordinator runs it under the execution and reuse rules of `<package>/references/checks.md`. That reference selects the check. If no such command exists, the coordinator records the exclusion and the path hash as a reported limitation. The coordinator records the verification in the round evidence. If exclusions empty the scope, every scoped path is generated. Then `review-manifest.py init` refuses with a named error, and no round manifest exists. The coordinator records the per-path mechanical verification and dispatches no inspection round. The coverage gate is satisfied vacuously. The checks and commit gates are unchanged.

Each group gets a fresh agent dispatched from the round's validated manifest. The single-group fast path is excepted. Step 1 defines its inline dispatch. The agent receives the manifest path and its group ID. The manifest supplies these inputs.

- The assigned files.
- The complete scoped file list.
- The exact diff commands.
- The scoped untracked files.
- The constraints.
- The acceptance routing for reviewers.

Agents may read across groups to check dependencies. But findings and the `read:` list are anchored in the assigned group. Dispatch concurrency and rate-limit retries follow the Run section. A correctness reviewer pass means one round across all reviewer groups. Thus the three-pass limit is independent of the number of groups. The final style pass never counts against that limit. A final round's pass accounting follows step 4's budget rule.

Each capture runs over one single source. The source is the round scope after terminal-evidence exclusion. This scope is the complete scoped file list minus the terminal-evidence paths that this section excludes. Scoped untracked files are included. The complete scoped file list comes from the manifest. On the no-manifest fast path, it comes from the inline prompt. Write the scope to a JSON file outside the repository worktree as a JSON array of repository-relative paths. The pre-round style gate's file list uses the same shape. Write that scope file with or without a manifest. Thus capture and the mutation gate it feeds run unchanged on the inline path. Write the scope file once per round, before the opening capture. Never rewrite it while that round's captures compare. A scope-list rewrite between captures causes a scope mismatch.

Before the first inspection round, capture `python3 "<package>/scripts/review-snapshot.py" --repo "<repository>" --scope-from <scope.json>` to a unique JSON file outside the repository. After every result and replacement call in that round has returned, capture again over the same round scope. Write that capture to another external file. Then run `python3 "<package>/scripts/review-snapshot.py" compare "<before.json>" "<after.json>"`. If the pre-round style gate's file list is identical to the round scope, reuse it as the capture scope. Under `--gate` before approval, no gate list exists. In that case, use the round scope defined above. The capture that closes a round is the opening capture of the next one. If no edit or check execution intervenes between rounds, reuse it as the baseline instead of capturing again. A round that follows coordinator edits or check executions opens with a fresh capture.

Capture failure or comparison exit 2 is a blocker. A scope mismatch is a blocker. Exit 0 confirms unchanged inputs. Exit 1 reports changed paths and whether HEAD changed. The evidence chain is HEAD, index, scoped paths and scoped untracked paths. Compare content evidence including file types/modes, with capture duration and hashed-byte metrics excluded from identity. Keep porcelain for human-readable scope discovery.

A mismatch invalidates the affected inspection evidence. When a mismatch occurs, take these steps.

- Pause reconciliation and edits.
- Report the concurrent mutation and changed paths. Do not guess its writer.
- Establish stable inputs before the re-review of affected groups and dependencies.

A HEAD change or an impact that cannot be bounded invalidates the whole round. Establish a fresh baseline with two consecutive unchanged captures. Then apply the same before/after gate to the replacement inspection. Mutation invalidation preserves the existing review-pass and replacement-call limits. Exhausted limits stop the run. Apply this gate to every inspection round, including final style and post-exit verification. Mutations to paths outside the round scope are outside the run's evidence chain. They do not invalidate inspection evidence. Capture errors, unsupported file types and unreadable inputs remain blockers.

For each result, normalize repository-relative paths. Use a tool call to compare the assigned file set with the returned `read:` set. Compute the missing paths and the unexpected paths. The gate fails in each case below, even when the verdict says clean.

- Either set is nonempty.
- The `read:` list is absent.
- The `read:` list is malformed.

Retry once for missing files or a corrected list. Retain earlier findings and valid assigned-file coverage during that retry. Merge the valid coverage and correct the list. Then both missing and unexpected sets must be empty. Every explicit blocker must also be resolved. Otherwise stop and report the gap. Apply this gate to every agent on every round, including the final style pass.

Reviewers return one acceptance status per checklist item in their review scope. They preserve the status spellings and attach `method` and verification `source` from `<package>/roles/reviewer.toml`. The initial correctness pass covers every criterion. On targeted follow-up rounds, carry forward prior evidence for criteria unaffected by the changed files. Reassess the criteria that those files could affect. Consolidate evidence across relevant groups and rounds before marking each criterion met. An unmet item produces a Major finding for the fix loop. It also prevents a clean close-out. The rules below apply to items that are blocked or not reached.

- An item blocked on missing evidence gets one targeted reviewer retry with that evidence. If it is still uncovered, stop the run.
- An item blocked on a user decision stops the run immediately with that question.
- An item can be marked not reached by every group assigned to assess it. That item gets one targeted reviewer retry with the necessary context. If it is still uncovered, stop the run.

| method | owner | source |
| --- | --- | --- |
| `test-inspection` | reviewer | Test file, case and relevant assertion; no execution claimed. |
| `executed-test` | coordinator | Inspected case plus matching command, scope/revision, exit status and saved log. |
| `mechanical` | reviewer/coordinator | Named validation, its result and the documentation/metadata it establishes. |
| `manual` | reviewer/coordinator | Inspected file:line and a documented observation establishing the criterion. |
| `unavailable` | reviewer/coordinator | Required evidence or decision that is missing or unreadable. |

Keep `met, untested` distinct from `proven by test`. Documentation-only and trivial metadata criteria can be met by appropriate mechanical validation or documented manual inspection. This is acceptable even when test tooling exists. Do not generate artificial tests for those criteria. Changed observable executable behavior still needs meaningful regression coverage under the applicable review rules. Missing coverage remains a finding. Manual or mechanical evidence does not waive it. If those rules exempt unavailable test tooling, report the limitation without claiming a test pass. Required evidence that is unavailable remains blocked.

Reviewers remain read-only and never run checks. A reviewer's `proven by test` with `method: test-inspection` establishes coverage only. The coordinator alone reports `method: executed-test`. Before that report, the coordinator joins the inspected case to an actually executed successful check. That check must cover the same case and the current relevant inputs. Preserve the status. Record the command, exit status and saved log with the case. None of the items below can support a passing-test claim.

- Stale results.
- Unrun commands.
- Unrelated passing suites.
- Failed checks.
- Timeouts.

Run outstanding checks through the normal checks step after the inspection round. Do not edit or run checks during inspection. A complete checklist may contain unmet items. The fix loop must address them. Acceptance and coverage retries belong to the current round. They keep that round's worktree unchanged.

## Round evidence

Round evidence applies the evidence-by-reference contract in `<package>/references/dispatch.md`. Under that contract, these items travel as referenced files.

- Coverage lists.
- Finding evidence excerpts.
- Fix verification output beyond the bound.
- The pre-executed diff artifacts attached under step 1's dispatch rule.

The round records each dispatched group's artifact by path, SHA-256, and line count. Thus a group's diff reading is anchored to the exact artifact that its prompt carried. The reading is not anchored to a fresh execution of the diff argv. The coordinator also maintains a `dispositions.json` ledger. The ledger lives in the shared run artifacts directory that the Shared run artifacts section governs. The ledger has one entry per finding. Each entry records its id, severity, file:line, root cause, fix classification, disposition, and round index. Step 2 records each fix classification there with its finding disposition. Step 4's narrowing reads the classifications from it. Every dispatch prompt references the ledger by path and SHA-256. This reference uses the same evidence-by-reference contract that the pre-executed diff artifacts use. Thus a dispatch carries a ledger reference instead of inline disposition history. Coverage and acceptance gates apply to the referenced content, never to its summary. Inline text carries only findings and dispositions. The conversation's per-result reports and round summaries under the Run section stay unchanged. The ledger is durable evidence alongside them, not a replacement for them.

## Shared run artifacts

At the start of close-out, create one unique temporary directory outside the repository worktree. The directory must be accessible to the coordinator and delegated agents. For example, use `mktemp -d /tmp/code-flow-wrap-up.XXXXXXXX`. Artifact durability is owned by the evidence-by-reference contract in `<package>/references/dispatch.md`. The run directory created above satisfies it for every artifact another agent must read. Store review manifests, snapshots, style-gate reports, check-result JSON, and check logs there for the full run. Set `TMPDIR` to this directory for `run-check.py` invocations so their logs survive the terminal invocation. Before relying on a delegated artifact, have the receiver verify its path and SHA-256 under the evidence-by-reference contract. If the path is unavailable, send a validated bounded packet inline when permitted. Otherwise report unavailable evidence. Remove temporary artifacts only after all rounds and reports that depend on them finish.

## Gate

Under `--gate` the run reviews without fixing or executing project tools. Dispatch the step 1 inspection round without running the style gate, formatters, linters, or project checks. Supply no gate report. The style reviewers inspect every rule family. Verify each finding and its stated root cause against the code and constraints. Then present every surviving finding with its severity, location, and root cause. Ask whether to fix. Before approval, do not delegate fixes, stage files, or enter any fix loop. Until approval, keep worktree bytes and the index untouched. A declined gate ends the run with the report. The report carries the findings unedited. Nothing is committed. After approval, run the normal mutating pre-round style gate and its fix loop. Then continue the standard run from fix delegation for the surviving review findings. These edits invalidate affected review evidence and require the normal follow-up checks and review. The approval covers the fixes, and the commit stays governed by the recorded authorization.

## Pre-round style gate

Without `--gate`, reuse a verified existing style-gate report from the current run if all conditions below hold:

- `deadline_exceeded` is `false`.
- `gate_fingerprint` equals the current output of `python3 "<package>/scripts/style-gate.py" --fingerprint --repo "<repository>"`.
- Every non-excluded scoped file appears with a matching SHA-256, `mutated: false`, and clean tool outcomes. Clean outcomes require `exit_status` `0`, `timed_out: false`, no `diagnostics`, and no `skipped` outcome.

Do not compare the `uncovered` list in this check. Report each entry of the saved `uncovered` list as a limitation. Otherwise, run `python3 "<package>/scripts/style-gate.py" --repo "<repository>" --files-from <json>` before the first inspection round's dispatch. Report each entry of the report's `uncovered` list as a limitation. The gate runs over the complete scoped file list. Write the file list outside the repository worktree as a JSON array of repository-relative paths. Under `--gate`, defer this execution until approval. Exit 2, deadline exhaustion, tool timeout, or launch failure is a blocker reported with the evidence. Store logs and tool caches outside the worktree. Use the shared run artifacts directory and the tools' cache environment settings. A repository may pin containerized gate tools in a `.style-gate.json` `"tools"` config at its root. A configured entry replaces same-named discovered tools. A malformed config fails the gate with exit 2.

For explicitly authorized diagnostic runs, the script also offers `--check-only`. This option selects non-writing formatter checks. It also disables configured fixes in Ruff and golangci-lint. Do not use it before `--gate` approval. It is not a sandbox for untrusted project tools. A formatting diff from Go's check commands is a diagnostic even when the tool exits 0. Report unexpected mutations as blockers. A failed check, formatting diff, timeout, or unexpected mutation never establishes clean coverage.

Without `--gate`, or after its approval, record every formatter mutation the normal report marks as a mechanical edit. Record the file's post-format `sha256` as its new hash. These mutations are gate output, not review findings. Delegate lint diagnostics to `worker` with the report excerpt. Carry every check the task names per the exact-check-argv rule in `<package>/references/dispatch.md`. Rerun the gate after fixes. Rerun affected checks until the gate exits 0. This loop consumes no review pass and no replacement call. Without `--gate`, this loop completes before the first round's manifest is written. On the no-manifest fast path, it completes before the fast path's scope file is written. With `--gate`, it starts only after approval. A diagnostic that cannot be fixed within scope stops the run with the evidence.

Verify the gate report before relying on it. Recompute each listed file's SHA-256. Compare it with the report's `sha256`. Any mismatch or edit since the gate run makes the report stale. Rerun over the full scoped list only if tool execution is authorized. Each `style-reviewer` dispatch receives the current verified report when one exists. Each fused inspector, initial round or final round, receives it when one exists. The report arrives alongside the full review brief. Refresh stale evidence after fixes. Only the rule families covered by clean tools in that report are skipped. Without a report, every family stays in scope. The report's `rule_family_coverage` lists the covered families; they are the entries of the `RULE_FAMILY_COVERAGE` table in `<package>/scripts/style-gate.py`.

The inspection section's exclusions apply first. A round scope is then documentation-only if it contains no path with a configured tool in this gate's coverage. The verified report's tool set is the classification's single mechanical source. No file-extension or naming heuristic substitutes for the report. The classification is recomputed for each dispatched round against the current verified report. A documentation-only round carries no style dispatch duty. Thus the fused initial dispatch omits its style section. A final round over a still-documentation-only scope records its style pass as reused. Record that reuse in the close-out report and the round evidence. The round's correctness, acceptance, and fused verification duties are unchanged. The run's checks follow the documentation-only rule `<package>/references/checks.md` owns. Under `--gate`, the first inspection round dispatches before any gate run and carries no report. Thus its fused dispatch keeps its style section. The omission first applies to a round dispatched after approval's gate run.

## Run

1. Dispatch one review round over the scoped change.
   - Use one fused inspector per group, covering correctness and style together.
   - Each fused inspector dispatches under the `reviewer` profile with `round_kind` set to `initial-correctness`, from a manifest or inline on the single-group fast path.
   - Thus the model-and-effort routing in `<package>/references/dispatch.md` applies unchanged.
   - Second-contract rule. Carry `<package>/roles/style-reviewer.toml` as a second pinned resource beside `<package>/roles/reviewer.toml`. This second contract adds the style duty. It changes neither the profile, the round kind, nor the routing.
   - Before dispatch, write the round's manifest to a temporary file outside the repository worktree.
   - Validate it with `python3 "<package>/scripts/review-manifest.py" validate --repo "<repository>" --manifest <path>`.
   - A failed validation stops the round before any dispatch.
   - The canonical `init` invocation follows.

```sh
python3 "<package>/scripts/review-manifest.py" init \
  --repo "<repository>" \
  --scope-from @/path/to/prior-round-scope.json \
  --added '{"<added-path>": "<reason>"}' \
  --round-kind post-exit-correctness \
  --constraints '["AGENTS.md"]' \
  --acceptance '[{"id": "A1", "text": "…", "source": "…", "group": "g1"},
                 {"id": "A2", "text": "…", "source": "…", "evidence": ["round 1: …"]}]' \
  --group g1=<path>,<path> > <manifest-path>
```

- The CLI's input shapes differ from the stored manifest.
   - `@<path>` reads a file argument.
   - `--added` is a JSON object that maps added paths to reasons. Its paths must NOT also appear in `--scope-from`.
   - Init's original scope is the prior round's scope, and added paths extend the eligible post-exclusion scope.
   - Follow-up rounds narrow `round_scope` through `--group` or `--round-scope`.
   - When `--round-scope` is omitted on a follow-up round and `--group` is supplied within the eligible scope, `init` narrows `round_scope` to the `--group` path union.
   - `initial-correctness` rounds cannot narrow `--round-scope` and must assign every post-exclusion `round_scope` path to a group.
   - Each `--acceptance` entry carries `id`/`text`/`source` plus one active binding of `group`, `paths`, or `evidence`. Stored-manifest objects with inactive `group: null` or `evidence: []` are accepted. The stored manifest holds all five fields.
- Validate the result with the `validate` form above.
- Rules for every inspection round:
   - Validate the round's manifest once before dispatch, and reuse it for replacement calls within that round.
   - Revalidate only when the coordinator regenerates the manifest, including on new paths.
- Run each `validate` and `snapshot compare` invocation as its own command. Capture its exit status before any chained parsing or display.
- The single-group fast path, counted after the exclusions in Inspection groups and gates, is this rule's one inline exception.
   - Initial, fix-added, and step 4 follow-up rounds may dispatch inline without writing or validating a manifest file. This applies when their post-exclusion `round_scope` meets the fast-path threshold in Inspection groups and gates.
   - The post-exclusion `round_scope` serves as the assigned files, and the prompt carries the complete scoped file list, the exact diff command, scoped untracked files, any constraint paths, and the acceptance checklist.
   - Inline follow-up dispatches also pass `round_kind` (`post-exit-correctness`), carried prior acceptance evidence for unaffected criteria, and reassessed criteria.
   - These inputs form the no-manifest input path in `<package>/roles/reviewer.toml` and `<package>/roles/style-reviewer.toml`. They replace the manifest path and group ID. Replacement calls within the round reuse them.
   - Coverage, acceptance, mutation gates, and scope-file snapshot capture apply unchanged.
   - Leave the fast path only when the merged post-exclusion `round_scope` exceeds that threshold. Then regenerate through `review-manifest.py init` and validate before dispatch.
- Each group receives the repository, manifest path, group ID, and the inputs defined in Inspection groups and gates.
   - A group may also carry an optional `context_pack` path over its existing assigned files. Render it into the group packet when present. Omit it if all assigned files in the group are deleted.
- The coordinator generates the round's per-group packs concurrently by passing only existing assigned files in each group to `context-pack.py`.
   - Generation runs after the pre-round style gate exits 0 and finishes before dispatch. Thus every pack describes post-format files. A pack regenerated for a fix-added path runs alone.
   - Under `--gate`, the first inspection round dispatches before any gate run and carries no packs. Packs first apply to the round dispatched after approval.
- Once the round's inputs are stable, execute each group's protected `current_diff` argv once immediately before its dispatch. Store the output in the shared run artifacts directory.
   - Add the artifact's path, SHA-256, and line count to the dispatch prompt per the evidence-by-reference contract in `<package>/references/dispatch.md`.
   - Inspectors consume that pre-executed diff instead of running the argv. The packet keeps the diff argv for spot verification and gains no schema field.
   - This rule covers every inspection round. For the first `--gate` round, compute the diff after its manifest is written. For inline fast-path dispatches, compute it from the prompt's exact diff command.
- Each fused inspector receives the applicable review reference selected by repository instructions or bundled review routing in `<package>/references/dispatch.md`. It reads that reference with `<package>/standards/code-design.md` and `<package>/standards/code-style.md` to cover both contracts' read duties.
   - A missing required review resource stops the run.
- Each fused inspector receives the pre-round style gate's current verified report when one exists, subject to Pre-round style gate rules.
- The fused inspector judges every assigned hunk once and returns one report with two clearly separated sections.
   - First, the correctness section carries Critical, Major, and Minor verdict counts. Each finding lists severity, repository-relative `file:line`, what breaks, triggering input or path, root cause, and suggested fix. The acceptance checklist carries `status`, `method`, and `source` per `<package>/roles/reviewer.toml`.
   - Second, the style section appears unless Pre-round style gate's documentation-only classification omits it. Each item lists repository-relative `file:line`, standard section, concrete cost, root cause, and suggested replacement.
   - One `read:` list serves both sections. Each group's one replacement call per round covers gaps from both sections together.
- Dispatch groups in waves of at most four concurrent agents, starting the next wave as calls return. A rate-limited or failed call switches remaining dispatches to batches of at most two, retaining successful results. Retry only failed or incomplete calls within the replacement allowance.
   - Waves are not rounds. Coverage, acceptance, and mutation gates still close per round, not per wave.
- Budget rules:
   - A fused initial round counts as one correctness reviewer pass against the three-pass limit.
   - Its style section is a completed style pass that seeds the final style pass's changed-since-last-style-pass scope.
   - A documentation-only round completes no style pass. Record the final style pass as reused per Pre-round style gate.
- Wait for every result, and pass the coverage and acceptance gates for every agent before editing.

Every review-round script invocation runs with `AGENTS_RUN_ID` set for the review's lifetime. The first manifest-generating `init` opens the run id and echoes it as `run` in its JSON result. The coordinator exports that run id into later script invocations. Pack-generation invocations pass `--role` and `--model` for the dispatch each pack serves.

2. Verify each finding and its stated root cause against the code and constraints.
   - The root cause is the decision, rule, or pattern that produced the finding.
   - It is not just the reported symptom.
   - Correct a wrong root cause or supply a missing one before delegation.
   - Decline a finding that does not survive verification, with its reason.
   - Delegate every surviving finding, Critical, Major, and Minor, to `worker`.
   - Carry its root cause.
   - Carry every check the task names per the exact-check-argv rule in `<package>/references/dispatch.md`.
   - Also carry the formatter and linter checks that the pre-round gate's verified report leaves uncovered over the touched files.
   - Combine findings with the same root cause into one task.
   - Preserve each finding's disposition.
   - Keep independent causes separate.
   - The fix must eliminate each cause rather than patch the symptom.
- Each surviving fix returns with a `mechanical` or `semantic` classification per the return contract in `<package>/roles/worker.toml`.
   - The coordinator records that classification with the finding disposition in the Round evidence section's dispositions ledger.
   - The recorded classifications drive step 4's mechanical scope narrowing.
- Partition the surviving findings by the file named in each finding's file:line location.
   - Send findings whose named files are disjoint to parallel workers.
   - Dispatch them under step 1's dispatch rule.
   - Combine findings that share a named file into one worker task.
   - Order that task by file.
- The disable-fail-restore evidence rules follow.
   - If a fix adds or reworks a behavioral regression test, the worker task requires live `disable-fail-restore` execution per test.
      - Disable the fix, observe the test fail for the expected reason, and restore the fix.
   - Fixes without a new or reworked behavioral regression test skip the `disable-fail-restore` cycle.
   - If the live sequence is genuinely undemonstrable, the worker says so in one line.
   - It does so instead of claiming verification.
   - This reference owns the disable-fail-restore evidence standard.
   - The worker role's standing instructions defer to it.
- If a fix legitimately rewords prose that a test pins, updating that pin is part of the same worker task.
   - A pin is an exact-sentence or fragment assertion.
   - Report the update as "pin updated".
   - A pin broken without such a report is a finding against the fix batch.
- If a fix needs another path, the worker reports the path and its reason.
   - Recompute the merged post-exclusion `round_scope`, group and line limits, exact diffs, scoped-untracked treatment, affected checks, and next-round review ownership.
   - Stay on the no-manifest fast path while merged post-exclusion `round_scope` meets the fast-path threshold in Inspection groups and gates. Leave it only when the scope exceeds that threshold.
   - When on or moving to the manifest path, only the coordinator produces a manifest through `review-manifest.py init` over the merged scope.
   - The added path and its reason ride `--added`.
   - Follow-up rounds narrow `round_scope` through `--group` or `--round-scope` when not reviewing the full merged scope.
   - Carried-evidence entries ride `init`'s inputs such as `--acceptance`.
   - No manifest JSON is hand-edited anywhere in the procedure.
   - Acceptance ownership is manifest-local. Reassign criteria that the changed files could affect to the new manifest's group ids. Remaining criteria carry prior evidence with the `group` key removed. An entry keeps `group` or `evidence`, never both. A previous round's group id does not exist in the new manifest.
   - `validate` must pass on the regenerated manifest before the next dispatch.
   - Exclude and preserve unrelated paths and edits.

3. Run the checks selected per `<package>/references/checks.md` for the final scoped changes from the relevant repository root.
   - Discover and execute them following that reference.
   - Reuse a still-valid passing result recorded in this run instead of rerunning it. Rerun only checks whose evidence is missing, stale, or invalidated.
   - Keep long output in a temporary log, and record exit status.
   - Delegate failures caused by the change to `worker`, and rerun affected checks.
   - A failure whose cause cannot be resolved within scope stops the run with the evidence.
   - Fix loops rerun only the checks covering the fixed files, per `<package>/references/checks.md` selection.
   - The full-suite and integration tier follows the once-only rule in `<package>/references/checks.md`. See Selection item 1 in `<package>/references/checks.md` for which runs leave out the full-suite and integration tier.
   - If no checks exist, report that limitation.
   - Run the affected checks concurrently with the next round's manifest preparation. That next round is the final round on the clean path, or the fused fallback round after a fallback. Neither waits for the other, and both must finish before the next dispatch.

4. Dispatch one fused final round over the files changed since the first style-review round and their directly affected dependencies.
   - Scope each fused fallback round to files changed since the last completed pass and their directly affected dependencies.
   - Use one fused inspector per group for the fused final round and each fused fallback round. Each inspector performs post-exit correctness verification and the final style pass over the same changed files.
   - Dispatch each under the `reviewer` profile with `round_kind` set to `post-exit-correctness`.
   - Stay on the no-manifest fast path when the post-exclusion `round_scope` meets the fast-path threshold in Inspection groups and gates. Pass step 1's inline follow-up inputs on that path. Leave the fast path only when the merged post-exclusion `round_scope` exceeds that threshold.
   - When using a manifest, narrow `round_scope` through `--group` or `--round-scope`, and that kind's validation permits assigned acceptance criteria.
   - The routing conditional in `<package>/references/dispatch.md` keys on that exact kind value.
   - Apply step 1's second-contract rule on the fused final round and each fused fallback round. Omit the style section only when a style-skip rule below applies.
   - No new round kind is introduced, and the `final-style` kind and its validation stay untouched.
- The fused inspector returns one report with two clearly separated sections.
   - The verification section comes first and carries the post-exit verification duty. It confirms every fix eliminated its stated root cause and checks changed files for regressions. It also retains prior review evidence for untouched files and reassesses any acceptance criterion the fixes could affect.
   - The style section comes second.
   - One `read:` list serves both sections, and the coverage gate applies to the single agent per group.
- The budget and fallback rules follow.
   - A fused final round after a fix round counts as one correctness reviewer pass against the three-pass limit. Each fused fallback round also counts as one correctness pass.
   - The last pass stays reserved for verification. Route its findings through the exit branches in step 5 without another fix round.
   - The final style pass never counts against the correctness budget.
   - New Critical or Major findings from the verification section send the run back to step 2's fix loop while correctness passes remain.
   - After step 2's fixes and step 3's checks, dispatch a fused fallback round with `round_kind` set to `post-exit-correctness`. Apply step 1's second-contract rule.
   - Style findings, coverage, and acceptance evidence for files untouched by the fallback fixes carry forward.
   - If a fused fallback round or the fused final round reports no Critical or Major findings, close correctness verification. Route surviving `<package>/standards/code-design.md`, `<package>/standards/code-style.md`, or Minor findings directly to step 5.
   - Close verification and final style immediately only if no Critical, Major, `<package>/standards/code-design.md`, `<package>/standards/code-style.md`, or Minor findings remain.
- If no files changed since the first style-review round, reuse that round's complete coverage and findings. In that case, skip the final round entirely. If the first inspection round required no fixes, the loop ends with no final round.
- If files changed, the last completed style pass's findings may all have been carried or declined without edits.
   - In that case, the initial final round dispatches only post-exit correctness verification. Record the style pass as reused in the close-out report and the round evidence.
   - This widened skip covers only the no-style-edit case on the initial final round.
   - A fused fallback round still applies step 1's second-contract rule unless the scope is documentation-only under Pre-round style gate.
   - If style fixes changed files, the style pass still runs, and post-exit correctness verification is never skipped.
- If the scope is documentation-only under Pre-round style gate, omit the style section. Record the style pass as reused in the close-out report and the round evidence. Run post-exit correctness verification unchanged.
- The classifications recorded with the finding dispositions in step 2 narrow the final or fused fallback round's scope per file.
   - The last completed pass is the most recent completed inspection pass that covers a file, correctness or style.
   - If a file's only changes since the last completed pass are `mechanical`, drop it from the round. It leaves both verification and final style together.
   - Verify `mechanical` status by hash equality against the gate report's post-format `sha256` or recorded tool log hash.
   - The removal narrows the changed-since-last-style-pass scoping of the style pass. It does not replace that scoping.
   - The excluded file's fix confirmation is the rerun of the affected checks exiting 0. Record it in the round evidence and in the acceptance table with the `mechanical` method.
   - A missing classification or a hash mismatch keeps the file in scope. A file with any `semantic` change since that pass keeps full post-exit verification.
   - If the narrowing empties the round's scope, dispatch no final or fused fallback round. Each excluded file's recorded check evidence then closes its verification.
- Pass the coverage gate and acceptance gate for every agent before dispositioning or exiting to step 5.
   - Disposition surviving `<package>/standards/code-style.md`, `<package>/standards/code-design.md`, and Minor findings without delegating edits before step 5.
   - Verify each finding and its stated root cause against the code and constraints, and decline any finding that does not survive verification, with its reason.
   - A surviving breach of `<package>/standards/code-design.md` is a required fix for step 5. An unresolved or declined breach prevents a clean close-out and the commit.
   - Findings under `<package>/standards/code-style.md` are advisory behavior-preserving improvements. Route supported ones to step 5, and carry the rest into the report.
   - Route surviving Minor findings from the verification section to step 5, and carry any declined or unfixable one into the report.
   - A coverage or agent-result gap that survives the round's replacement call stops the run under the coverage gate. If the gap is confined to the style section of a returned result, do not stop the run. Instead, record each such gap as a reported limitation naming the unread files or failed operation.
   - A fused final-round or fused fallback-round dispatch that cannot be started or returns no result at all still stops the run.

5. Exit the final or fused fallback round through one branch.
   - Evaluate the unresolved Critical or Major stop gate before delegating combined exit fixes.
   - An unresolved Critical or Major finding ends the run. Analyze and report its root cause with evidence telling why it exists. Also report why it cannot be resolved within the agreed scope. It prevents a clean close-out and the commit, with no further edits.
   - Otherwise, no Critical or Major findings remain.
   - If no surviving `<package>/standards/code-design.md`, supported `<package>/standards/code-style.md`, or Minor findings remain, skip combined exit fixes and step 6.
   - Otherwise, delegate surviving `<package>/standards/code-design.md`, supported `<package>/standards/code-style.md`, and Minor fixes in one combined exit fixes pass to `worker`. Follow step 2's root-cause grouping, exact-check-argv, file-partitioning, `disable-fail-restore`, and path-addition rules.
   - Record each fix's `mechanical` or `semantic` return classification with its finding disposition in the dispositions ledger.
   - Run the affected checks once after those combined exit fixes before step 6, following step 3's check selection and reuse rules.
   - A failed check whose cause cannot be resolved within scope stops the run with the evidence.
   - An unresolved or declined `<package>/standards/code-design.md` breach prevents a clean close-out and the commit.
   - Carry any declined or unfixable `<package>/standards/code-style.md` or Minor finding into the report, and continue to step 6.

6. Apply this step when step 5's combined exit fixes changed files.
   - Apply step 4's `mechanical` hash-verified exclusion rule to the files changed by the combined exit fixes.
   - Skip step 6 if the combined exit fixes changed no files. Also skip step 6 if every changed file is excluded under that rule with affected checks passing. Step 4's verification pass and each excluded file's recorded check evidence then close post-exit verification.
   - Otherwise, keep every file with any `semantic` change, missing classification, or hash mismatch in scope. Review only those files and their directly affected dependencies.
   - Dispatch one fresh grouped `reviewer` round over those files after the combined exit fixes and their affected checks pass.
   - Its Critical or Major findings end the run with their root cause reported and no fix round follows.
   - Fix its Minor findings and rerun the affected checks. Those fixes are terminal with no further review round.

As each inspection result returns within a round, the coordinator reports it in the conversation before the next dispatch.

- The report carries the group and role.
- It carries the verdict counts.
- It carries each finding with its severity and its stated root cause.
- It carries the round's cumulative Critical, Major, and Minor counts.

After every inspection round completes, the coordinator prints a round summary in the conversation. This includes the first correctness pass, verification rounds, the final style pass, and the post-exit verification. Print the summary before the next round or the close-out report. The summary carries this content.

- The round number and kind.
- The round's scope and group count.
- Findings found, declined, and fixed with their root causes.
- Acceptance-criteria movement.
- Check results.
- The next step.

A round that stops the run still prints its summary ahead of the stop report.

Dispatch every role using the effective profile selected through `<package>/references/dispatch.md`. If delegation or that profile's model or supported effort is unavailable, stop. Report which required implementation or independent inspection could not run.

A blocked, failed, or incomplete agent result is not a pass. Use the current round's retry allowance to supply missing context, recover a failed call, or fill coverage and acceptance gaps. Each group gets at most one replacement call per round. That single call must address every gap the group's result reported together. Such gaps are coverage, list correction, and blocked or unreached acceptance items. If it still falls short, stop and report the gap. Keep the worktree unchanged while an inspection is running.
