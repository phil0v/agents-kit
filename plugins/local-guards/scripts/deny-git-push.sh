#!/usr/bin/env bash
set -uo pipefail

blocked='git push is blocked by local-guards deny-git-push.sh'
trigger='push|send-pack|http-push|receive-pack'

metrics_start=$(date +%s 2>/dev/null) || metrics_start=0
metrics_status=ok

metrics_cache_recorder() {
    local candidate name part numeric key best
    recorder= best=
    for candidate in "$guard_dir/../../../usage-metrics/"*/scripts/metrics.py; do
        [ -f "$candidate" ] || continue
        name=${candidate%/scripts/metrics.py}
        name=${name##*/}
        numeric=yes key=
        IFS=. read -r -a parts <<< "$name"
        for part in "${parts[@]}"; do
            case $part in
                *[!0-9]*) numeric=no ;;
                *) key+=$(printf '%08d.' "$((10#$part))") ;;
            esac
        done
        if [ "$numeric" = yes ] && [ -n "$key" ] && [ "$key" \> "$best" ]; then
            recorder=$candidate
            best=$key
        fi
    done
}

metrics_record() {
    local now recorder home
    [ "${AGENTS_METRICS-}" != off ] || return 0
    if [ -n "${AGENTS_METRICS_MODULE-}" ]; then
        recorder=$AGENTS_METRICS_MODULE
        case $recorder in
            '~'*) recorder=$HOME${recorder#\~} ;;
        esac
        [ -f "$recorder" ] || return 0
    elif [ -f "$guard_dir/../../usage-metrics/scripts/metrics.py" ]; then
        recorder=$guard_dir/../../usage-metrics/scripts/metrics.py
    else
        metrics_cache_recorder
        if [ -z "$recorder" ]; then
            home=${AGENTS_HOME:-$HOME/.agents}
            recorder=$home/plugins/usage-metrics/scripts/metrics.py
        fi
        [ -f "$recorder" ] || return 0
    fi
    now=$(date +%s 2>/dev/null) || now=$metrics_start
    python3 "$recorder" record --plugin local-guards --event deny-git-push \
        --status "$metrics_status" --seconds $((now - metrics_start)) >/dev/null 2>&1 || :
}

GUARD_HOOK_FORMAT=${LOCAL_GUARDS_HOOK_FORMAT:-}
if [ "${1-}" = --antigravity ]; then
    GUARD_HOOK_FORMAT=antigravity
fi

guard_dir=.
[[ ${BASH_SOURCE[0]} != */* ]] || guard_dir=${BASH_SOURCE[0]%/*}
source "$guard_dir/git-guard.sh" || {
    metrics_status=fail
    metrics_record
    if [ "$GUARD_HOOK_FORMAT" = antigravity ]; then
        printf '{"decision":"deny","reason":"%s"}\n' "$blocked"
    else
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$blocked"
    fi
    exit 0
}

guard_bootstrap "$trigger" "$blocked" "$blocked" || {
    command -v jq >/dev/null 2>&1 || metrics_status=fail
    metrics_record
    exit 0
}

command=$GUARD_COMMAND
dir=$GUARD_DIR

deny() {
    metrics_status=ok
    emit_deny "$1"
    exit 0
}

metrics_trap() {
    local status=$1
    if [ "$status" -ne 0 ]; then
        emit_deny "$GUARD_HOOK_FAILED_REASON"
        metrics_status=fail
    fi
    metrics_record
    exit 0
}
trap 'metrics_trap $?' EXIT

check_segment() {
    guard_segment "$trigger"
    case $? in
        0)
            [[ ! ${GUARD_ARGS[0]-} =~ ^($trigger)$ ]] || deny "$blocked"
            [ -z "$GUARD_BARE_WORD" ] ||
                deny "git ${GUARD_ARGS[0]} carries the bare word $GUARD_BARE_WORD — the hook cannot tell it is not the subcommand; join it into a longer word or run the git command with plain options"
            ;;
        2) deny "$(guard_unparsed_reason "$trigger")" ;;
    esac
}

guard_scan check_segment "$command" "$dir"
emit_allow
metrics_record
trap - EXIT
exit 0
