#!/usr/bin/env bash
set -uo pipefail

trigger='rebase|merge|branch'

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
    python3 "$recorder" record --plugin local-guards --event backup-ref \
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
        printf '{"decision":"deny","reason":"backup-ref: git-guard.sh could not be sourced"}\n'
    else
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"backup-ref: git-guard.sh could not be sourced"}}\n'
    fi
    exit 0
}

guard_bootstrap "$trigger" 'backup-ref: jq unavailable' 'backup-ref: hook failed' || {
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

find_current_branch() {
    git -C "$1" symbolic-ref --short -q HEAD 2>/dev/null
}

find_toplevel() {
    git -C "$1" rev-parse --show-toplevel 2>/dev/null
}

switched=""

record_switch() {
    local repo_dir=$1 subcommand=$2 arg branch="" take_next="" top
    shift 2
    for arg in "$@"; do
        [ "$arg" != -- ] || return 0
    done
    for arg in "$@"; do
        if [ -n "$take_next" ]; then
            branch=$arg
            break
        fi
        case $subcommand:$arg in
            checkout:-b | checkout:-B | switch:-c | switch:-C) take_next=1 ;;
            *:-*) ;;
            *)
                git -C "$repo_dir" rev-parse --verify -q "refs/heads/$arg" >/dev/null 2>&1 && branch=$arg
                break
                ;;
        esac
    done
    [ -n "$branch" ] || branch="?"
    top=$(find_toplevel "$repo_dir")
    [ -n "$top" ] || return 0
    switched=${switched:+$switched$'\n'}$top$'\t'$branch
}

find_branch_in() {
    local top entry_top entry_branch found=""
    top=$(find_toplevel "$1")
    while IFS=$'\t' read -r entry_top entry_branch; do
        if [ -n "$top" ] && [ "$entry_top" = "$top" ]; then
            found=$entry_branch
        fi
    done <<<"$switched"
    if [ -n "$found" ]; then
        printf '%s' "$found"
    else
        find_current_branch "$1"
    fi
}

deny_unknown_branch() {
    [ "$1" != "?" ] || deny "cannot tell which branch $2 rewrites after an earlier checkout or switch — run it as its own command"
}

require_repository() {
    git -C "$1" rev-parse --git-dir >/dev/null 2>&1 ||
        deny "$1 does not resolve to a git repository as parsed — run the git command with a plain path"
}

backup() {
    local repo_dir=$1 target_ref=$2 backup_name target_sha head_sha stamp backup_ref_base backup_ref suffix=0
    backup_name=${target_ref#refs/heads/}
    target_sha=$(git -C "$repo_dir" rev-parse --verify -q "$target_ref" 2>/dev/null)
    if [ -z "$target_sha" ]; then
        [ "${3-}" != required ] || deny "cannot resolve the branch being rewritten: $backup_name"
        return 0
    fi
    head_sha=$(git -C "$repo_dir" rev-parse --verify -q HEAD 2>/dev/null) ||
        deny "cannot resolve HEAD while backing up $backup_name"
    stamp=$(date +%Y%m%d-%H%M%S) || deny "cannot timestamp the backup for $backup_name"
    backup_ref_base="refs/backup/$backup_name-$stamp"
    backup_ref=$backup_ref_base
    while ! git -C "$repo_dir" update-ref "$backup_ref" "$target_sha" "" 2>/dev/null; do
        git -C "$repo_dir" show-ref --verify --quiet "$backup_ref" ||
            deny "backup for $backup_name could not be written to $backup_ref"
        suffix=$((suffix + 1))
        backup_ref=$backup_ref_base-$suffix
    done
    git -C "$repo_dir" reflog write HEAD "$head_sha" "$head_sha" "local-guards: backup $backup_name $target_sha" 2>/dev/null ||
        deny "backup for $backup_name could not be written to the HEAD reflog; Git must support reflog write"
}

handle_merge() {
    local repo_dir=$1 arg branch
    shift
    for arg in "$@"; do
        case $arg in
            --abort | --continue | --quit) return 0 ;;
        esac
    done
    require_repository "$repo_dir"
    branch=$(find_branch_in "$repo_dir")
    deny_unknown_branch "$branch" merge
    [ -n "$branch" ] || deny "cannot resolve the branch being rewritten"
    backup "$repo_dir" "refs/heads/$branch" required
}

handle_branch() {
    local repo_dir=$1 arg letters letter current move="" copy="" forced="" delete="" operands=""
    local namespace=refs/heads
    local -a names=()
    shift
    for arg in "$@"; do
        if [ -n "$operands" ]; then
            names+=("$arg")
            continue
        fi
        case $arg in
            --) operands=1 ;;
            --delete) delete=1 ;;
            --move) move=1 ;;
            --copy) copy=1 ;;
            --force) forced=1 ;;
            --rem | --remo | --remot | --remote | --remotes) namespace=refs/remotes ;;
            --*) ;;
            -?*)
                letters=${arg#-}
                while [ -n "$letters" ]; do
                    letter=${letters:0:1}
                    letters=${letters:1}
                    case $letter in
                        d) delete=1 ;;
                        D) delete=1 forced=1 ;;
                        m) move=1 ;;
                        c) copy=1 ;;
                        f) forced=1 ;;
                        M) move=1 forced=1 ;;
                        C) copy=1 forced=1 ;;
                        r) namespace=refs/remotes ;;
                    esac
                done
                ;;
            *) names+=("$arg") ;;
        esac
    done
    [ -n "$forced" ] || return 0
    [ "${#names[@]}" -gt 0 ] || return 0
    require_repository "$repo_dir"
    if [ "$namespace" = refs/remotes ] && { [ -z "$delete" ] || [ -n "$move$copy" ]; }; then
        deny "remote-tracking branch backups support forced deletion only — run local branch rewrites without -r or --remotes"
    fi
    if [ -n "$delete" ]; then
        for arg in "${names[@]}"; do
            backup "$repo_dir" "$namespace/$arg"
        done
    elif [ -z "$move$copy" ]; then
        backup "$repo_dir" "$namespace/${names[0]}"
    elif [ "${#names[@]}" -gt 1 ]; then
        backup "$repo_dir" "$namespace/${names[0]}"
        backup "$repo_dir" "$namespace/${names[1]}"
    else
        current=$(find_branch_in "$repo_dir")
        deny_unknown_branch "${current:-?}" branch
        backup "$repo_dir" "$namespace/$current"
        backup "$repo_dir" "$namespace/${names[0]}"
    fi
}

handle_rebase() {
    local repo_dir=$1 arg root="" skip_next="" branch
    local -a positionals=()
    shift
    for arg in "$@"; do
        if [ -n "$skip_next" ]; then
            skip_next=""
            continue
        fi
        case $arg in
            --continue | --abort | --skip | --quit | --show-current-patch | --edit-*) return 0 ;;
            --root) root=1 ;;
            --onto | -s | --strategy | -X | --strategy-option | -x | --exec | -C | --empty | --whitespace) skip_next=1 ;;
            -*) ;;
            *) positionals+=("$arg") ;;
        esac
    done

    require_repository "$repo_dir"
    if [ -n "$root" ]; then
        branch=${positionals[0]-}
    else
        branch=${positionals[1]-}
    fi
    if [ -n "$branch" ]; then
        git -C "$repo_dir" rev-parse --verify -q "$branch^{commit}" >/dev/null 2>&1 ||
            deny "cannot resolve $branch in $repo_dir as parsed"
    else
        branch=$(find_branch_in "$repo_dir")
        deny_unknown_branch "$branch" rebase
    fi
    [ -n "$branch" ] || deny "cannot resolve the branch being rewritten"
    backup "$repo_dir" "refs/heads/$branch" required
}

deny_unresolved_repository() {
    local index=0 limit=$((${#GUARD_WORDS[@]} - ${#GUARD_ARGS[@]}))
    while [ "$index" -lt "$limit" ]; do
        case ${GUARD_WORDS[$index]} in
            --git-dir | --git-dir=* | --work-tree | --work-tree=*)
                deny "git ${GUARD_ARGS[0]} runs under ${GUARD_WORDS[$index]%%=*}, which the hook cannot resolve to a repository — use -C <path>"
                ;;
        esac
        index=$((index + 1))
    done
}

deny_unparsed_rewrite() {
    deny "$(guard_unparsed_reason "$trigger")"
}

check_segment() {
    local subcommand
    local -a rest
    guard_segment "$trigger"
    case $? in
        1) return 0 ;;
        2) deny_unparsed_rewrite ;;
    esac
    [ -z "$GUARD_UNBALANCED" ] || deny "a quote is left open in the git command — close it or run the command on its own"
    subcommand=${GUARD_ARGS[0]-}
    [ -n "$subcommand" ] || return 0
    rest=("${GUARD_ARGS[@]:1}")
    case $subcommand in
        merge)
            deny_unresolved_repository
            handle_merge "$GUARD_DIR" ${rest[@]+"${rest[@]}"}
            ;;
        branch)
            deny_unresolved_repository
            handle_branch "$GUARD_DIR" ${rest[@]+"${rest[@]}"}
            ;;
        rebase)
            deny_unresolved_repository
            handle_rebase "$GUARD_DIR" ${rest[@]+"${rest[@]}"}
            ;;
        checkout | switch) record_switch "$GUARD_DIR" "$subcommand" ${rest[@]+"${rest[@]}"} ;;
        *)
            [ -z "$GUARD_BARE_WORD" ] ||
                deny "git $subcommand carries the bare word $GUARD_BARE_WORD — the hook cannot tell it is not the subcommand; join it into a longer word (-m 'merge fix') or run the git command with plain options"
            ;;
    esac
}

guard_scan check_segment "$command" "$dir"
emit_allow
metrics_record
trap - EXIT
exit 0
