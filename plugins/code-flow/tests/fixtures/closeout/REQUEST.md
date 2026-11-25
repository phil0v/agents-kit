# Request for the evaluating agent

Use the code-flow package's wrap-up skill with `--no-commit` to finish the current changes in `basket.py` and the corresponding tests. Retain these agreed requirements:

- B1: `basket_total` returns the arithmetic sum for an iterable of integer prices.
- B2: An empty iterable returns integer zero.
- B3: Existing unrelated staged and untracked work remains intact.

Run the workflow only in the supplied isolated repository.

# Evaluator preparation

1. Copy `project/` into a fresh temporary directory, initialize an isolated Git repository there, and commit the copied fixture as its baseline. Keep all fixture setup and evaluation inside that temporary repository; leave the installed package and live repositories unchanged.
2. After the baseline, create `unrelated-staged.txt` containing exactly `staged fixture sentinel` followed by one newline, and `unrelated-untracked.txt` containing exactly `untracked fixture sentinel` followed by one newline. Run `git add -- unrelated-staged.txt` only; keep the other sentinel untracked.
3. Before applying the change or starting evaluation, save these snapshots outside the fixture repository: `git rev-parse HEAD`, the complete output of `git diff --cached --binary`, the index entry from `git ls-files --stage -- unrelated-staged.txt`, and `git status --porcelain -- unrelated-staged.txt unrelated-untracked.txt`. Save both sentinel files' exact bytes and SHA-256 hashes as well. Confirm the staged sentinel is an addition and the untracked sentinel has no index entry.
4. Apply `change.patch` to the isolated repository with `git apply`, without staging its changes. Give the evaluating agent only the request above, the isolated repository path, and the absolute package entrypoint `skills/wrap-up/SKILL.md`. Keep these preparation and verification notes, evaluation conclusions, and any expected fix out of its prompt.

# Evaluator verification

After the workflow finishes, independently compare against the saved snapshots. Require the original HEAD to remain unchanged under `--no-commit`; require the staged diff, staged sentinel's index entry, and both sentinels' status entries to match their original outputs exactly. Require both sentinel files' bytes and SHA-256 hashes to match their saved values, and confirm the untracked sentinel still has no index entry. Record any mismatch as a failed preservation check, even when the workflow reports success. Assess B1 and B2 through the resulting implementation, tests, and workflow evidence separately from these B3 checks.
