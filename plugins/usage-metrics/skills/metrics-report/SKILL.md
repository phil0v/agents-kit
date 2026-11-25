---
name: metrics-report
description: Analyze the local usage-metrics log with the package report script and read its output. Use for requests to analyze or report usage metrics, plugin invocations, durations, review rounds, or dispatch lists, or to inspect what a workflow run recorded.
argument-hint: "[<path-to-usage.jsonl>]"
---

# Analyze usage metrics

## Run the report

Run the report script with `python3`: resolve this `SKILL.md` to its real path and run `../../scripts/metrics-report.py` from there. Pass the requested log path as the only argument; without an argument the script reads `$AGENTS_HOME/metrics/usage.jsonl` (default `~/.agents/metrics/usage.jsonl`). Run the script as the only command of its terminal call, so the tool output contains nothing but the script's own output. The script only reads the log; never write to the log.

## Reply

On exit 0, quote the report verbatim in one fenced code block, then add a short reading of it as bullets: the surfaces with the highest `p95` seconds, the runs that stand out by round or dispatch count, and how many lines the log skipped. Four bullets at most. On a nonzero exit, quote the script's stderr line as the blocker and stop.
