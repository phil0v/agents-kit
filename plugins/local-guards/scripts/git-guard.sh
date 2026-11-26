# shellcheck shell=bash
_GUARD_WORD_EDGE_BEFORE='(^|[^[:alnum:]_-])'
_GUARD_WORD_EDGE_AFTER='($|[^[:alnum:]_-])'
_GUARD_GIT_WORD_EDGE_BEFORE='(^|[^[:alnum:]_.-]|[^/]\.)'
_GUARD_HEREDOC_OPERATOR='^[0-9]?<<(-?)$'
_GUARD_HEREDOC_GLUED='^[0-9]?<<(-?)([^<-].*)$'
_GUARD_REDIRECT_ALONE='^([0-9]?(>|>>|<|>&)|<<<|&>|&>>)$'
_GUARD_REDIRECT_WORD='^([0-9]?[<>]|&>)'
_GUARD_REDIRECT_PREFIX='^([0-9]*[<>]?|[0-9]*<<|&>?)$'

_guard_resolve() {
    case $1 in
        /*) printf '%s' "$1" ;;
        *) printf '%s/%s' "$2" "$1" ;;
    esac
}

_guard_has_words() {
    [[ $1 =~ ${_GUARD_GIT_WORD_EDGE_BEFORE}git${_GUARD_WORD_EDGE_AFTER} ]] || return 1
    [[ $1 =~ ${_GUARD_WORD_EDGE_BEFORE}($2)${_GUARD_WORD_EDGE_AFTER} ]]
}

_guard_unquote() {
    local text=$1
    text=${text//\\$'\n'/}
    text=${text//\"/}
    text=${text//\'/}
    text=${text//\\/}
    text=${text//\$\{IFS\}/ }
    text=${text//\$IFS/ }
    printf '%s' "$text"
}

guard_prefilter() {
    local text
    text=$(_guard_unquote "$2")
    [[ $text =~ ${_GUARD_GIT_WORD_EDGE_BEFORE}git${_GUARD_WORD_EDGE_AFTER} ]] && return 0
    _guard_has_words "$text" "$1|xargs|alias" && return 0
    [[ $text =~ ${_GUARD_WORD_EDGE_BEFORE}git-($1)${_GUARD_WORD_EDGE_AFTER} ]] && return 0
    [[ $text == *[\$\`]* ]] && _guard_has_words "$text" git
}

guard_mentions() {
    local IFS=' '
    _guard_has_words "${GUARD_WORDS[*]+"${GUARD_WORDS[*]}"}" "$1"
}

guard_unparsed_reason() {
    local word
    if [ "${GUARD_UNPARSED_CAUSE-}" = evaluator ]; then
        printf 'an evaluator or command runner shares the command with git; run git as its own command'
        return 0
    fi
    for word in ${1//|/ }; do
        if guard_mentions "$word"; then
            printf 'git %s could not be parsed; run it in its plain form' "$word"
            return 0
        fi
    done
    printf 'the git subcommand could not be parsed; run it in its plain form'
}

_guard_note_git_env() {
    case ${1%%=*} in
        GIT_* | EDITOR | VISUAL | PAGER | LESS | SSH_ASKPASS) GUARD_GIT_ENV=1 ;;
    esac
}

_guard_strip() {
    local count=${#GUARD_WORDS[@]} word current="" duration=""
    _strip_index=0
    _strip_wrappers=" "
    _strip_split=""
    _strip_dir=$GUARD_CWD
    while [ "$_strip_index" -lt "$count" ]; do
        word=${GUARD_WORDS[$_strip_index]}
        if [[ $word =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            _guard_note_git_env "$word"
            _strip_index=$((_strip_index + 1))
            continue
        fi
        case ${word##*/} in
            command | env | sudo | nohup | time | exec | timeout | nice | xargs | eval | then | do | else | elif | if | while | until | '!' | '{')
                current=${word##*/}
                _strip_wrappers+="$current "
                [ "$current" != timeout ] || duration=1
                _strip_index=$((_strip_index + 1))
                continue
                ;;
        esac
        case $word in
            -*)
                if [ -n "$current" ]; then
                    case $current:$word in
                        sudo:-u | sudo:-g | sudo:-C | nice:-n | timeout:-s | timeout:-k | exec:-a | env:-u | time:-f | time:-o | \
                            xargs:-n | xargs:-I | xargs:-L | xargs:-P | xargs:-d | xargs:-a | xargs:-E | xargs:-s)
                            _strip_index=$((_strip_index + 2))
                            ;;
                        sudo:-D | env:-C)
                            if [ $((_strip_index + 1)) -lt "$count" ]; then
                                _strip_dir=$(_guard_resolve "${GUARD_WORDS[$((_strip_index + 1))]}" "$_strip_dir")
                            fi
                            _strip_index=$((_strip_index + 2))
                            ;;
                        *:--chdir=*)
                            _strip_dir=$(_guard_resolve "${word#*=}" "$_strip_dir")
                            _strip_index=$((_strip_index + 1))
                            ;;
                        env:-S | env:-S?* | env:--split-string | env:--split-string=* | env:-[!-]*S*)
                            _strip_split=1
                            _strip_index=$((_strip_index + 1))
                            ;;
                        command:-v | command:-V | command:-pv | command:-vp | command:-pV | command:-Vp)
                            _strip_index=$count
                            break
                            ;;
                        *) _strip_index=$((_strip_index + 1)) ;;
                    esac
                    continue
                fi
                ;;
        esac
        if [ -n "$duration" ]; then
            duration=""
            _strip_index=$((_strip_index + 1))
            continue
        fi
        break
    done
    [ "$_strip_index" -le "$count" ] || _strip_index=$count
}

_guard_evaluator_inline() {
    local cmd=$1 index=$2 count=${#GUARD_WORDS[@]} arg skip="" end_options=""
    case $cmd in
        python3 | python3.*) cmd=python ;;
    esac
    while [ "$index" -lt "$count" ]; do
        arg=${GUARD_WORDS[$index]}
        [ -z "${GUARD_EXPANDED[$index]-}" ] || return 0
        if [ -n "$skip" ]; then
            if [ "$skip" = module ]; then
                [ -n "$arg" ] && [ "$arg" != - ] && [[ $arg != -* ]] && return 1
                return 0
            fi
            skip=""
            index=$((index + 1))
            continue
        fi
        if [ -n "$end_options" ]; then
            [ -n "$arg" ] && [ "$arg" != - ] && return 1
            return 0
        fi
        case $arg in
            --) end_options=1 ;;
            "" | -) return 0 ;;
            -c | -c?* | -e | -E | -r | -p | -s | --eval | --eval=* | --print | --print=*) return 0 ;;
            *)
                case $cmd:$arg in
                    perl:-e?* | perl:-E?* | perl:-p?* | ruby:-e?* | ruby:-p?* | ruby:-r?* | node:-e?* | node:-p?* | node:-r?*) return 0 ;;
                    python:-m) skip=module ;;
                    python:-m?*)
                        arg=${arg#-m}
                        [ "$arg" != - ] && [[ $arg != -* ]] && return 1
                        return 0
                        ;;
                    fish:-C | fish:-C?* | fish:--command | fish:--command=* | fish:--init-command | fish:--init-command=* | \
                        node:--require | node:--require=* | \
                        node:--loader | node:--loader=* | node:--import | node:--import=* | \
                        node:--experimental-loader | node:--experimental-loader=* | \
                        node:--run | node:--run=*)
                        return 0
                        ;;
                    python:-W | python:-X | python:-Q | python:--check-hash-based-pycs | \
                        sh:-o | sh:+o | sh:+[!-]*o | bash:-o | bash:+o | bash:-O | bash:+O | bash:+[!-]*[oO] | \
                        bash:--rcfile | bash:--init-file | \
                        dash:-o | dash:+o | dash:+[!-]*o | zsh:-o | zsh:+o | zsh:+[!-]*o | ksh:-o | ksh:+o | ksh:+[!-]*o | \
                        fish:-d | fish:--debug | fish:-o | fish:--debug-output | fish:-f | fish:--features | \
                        perl:-I | perl:-x | ruby:-I | ruby:-C | ruby:-F | ruby:-K | \
                        node:--env-file | node:--env-file-if-exists | node:--conditions | node:-C | node:--title | \
                        node:--input-type | node:--redirect-warnings | node:--diagnostic-dir | node:--icu-data-dir | \
                        node:--openssl-config | node:--snapshot-blob | node:--build-snapshot-config | \
                        node:--experimental-config-file | node:--experimental-default-type | node:--experimental-sea-config | \
                        node:--experimental-test-isolation | node:--allow-fs-read | node:--allow-fs-write | \
                        node:--cpu-prof-dir | node:--cpu-prof-interval | node:--cpu-prof-name | \
                        node:--heap-prof-dir | node:--heap-prof-interval | node:--heap-prof-name | \
                        node:--heapsnapshot-near-heap-limit | node:--heapsnapshot-signal | \
                        node:--disable-proto | node:--disable-warning | node:--dns-result-order | \
                        node:--debug-port | node:--inspect-port | node:--inspect-publish-uid | \
                        node:--localstorage-file | node:--max-http-header-size | node:--max-old-space-size-percentage | \
                        node:--network-family-autoselection-attempt-timeout | \
                        node:--report-dir | node:--report-directory | node:--report-filename | node:--report-signal | \
                        node:--secure-heap | node:--secure-heap-min | \
                        node:--test-concurrency | node:--test-coverage-branches | node:--test-coverage-exclude | \
                        node:--test-coverage-functions | node:--test-coverage-include | node:--test-coverage-lines | \
                        node:--test-name-pattern | node:--test-reporter | node:--test-reporter-destination | \
                        node:--test-shard | node:--test-skip-pattern | node:--test-timeout | \
                        node:--tls-cipher-list | node:--tls-keylog | \
                        node:--trace-event-categories | node:--trace-event-file-pattern | node:--trace-require-module | \
                        node:--unhandled-rejections | node:--use-largepages | node:--v8-pool-size | \
                        node:--watch-kill-signal | node:--watch-path)
                        skip=arg
                        ;;
                    python:-W?* | python:-X?* | python:-Q?* | \
                        perl:-I?* | perl:-x?* | perl:-C?* | ruby:-I?* | ruby:-C?* | ruby:-F?* | ruby:-K?* | \
                        sh:+?* | bash:+?* | dash:+?* | zsh:+?* | ksh:+?*)
                        ;;
                    *:-[!-]*c* | sh:-[!-]*s* | bash:-[!-]*s* | dash:-[!-]*s* | zsh:-[!-]*s* | ksh:-[!-]*s* | \
                        fish:-[!-]*C* | perl:-[!-]*[eEp]* | ruby:-[!-]*[eEpr]* | node:-[!-]*[eEp]*)
                        return 0
                        ;;
                    sh:-[!-]*o | bash:-[!-]*[oO] | dash:-[!-]*o | zsh:-[!-]*o | ksh:-[!-]*o | \
                        python:-[!-]*[WXQ] | perl:-[!-]*[Ix] | ruby:-[!-]*[ICFK])
                        skip=arg
                        ;;
                    python:-[!-]*m) skip=module ;;
                    *:-*) ;;
                    *) return 1 ;;
                esac
                ;;
        esac
        index=$((index + 1))
    done
    return 0
}

_guard_evaluates() {
    local count=${#GUARD_WORDS[@]} index=$_strip_index cmd
    if [ "$index" -ge "$count" ]; then
        [ -n "$_strip_split" ] && return 0
        return 1
    fi
    [ -z "${GUARD_EXPANDED[$index]-}" ] || return 0
    cmd=${GUARD_WORDS[$index]##*/}
    case $cmd in
        git) return 1 ;;
        source | . | alias | eval | ssh | su | watch | osascript) return 0 ;;
    esac
    [ -z "$_strip_split" ] || return 0
    [[ $_strip_wrappers != *" eval "* ]] || return 0
    case $cmd in
        sh | bash | dash | zsh | ksh | fish | python | python3 | perl | ruby | node) ;;
        python3.*) [[ $cmd =~ ^python3\.[0-9]+$ ]] || return 1 ;;
        *) return 1 ;;
    esac
    _guard_evaluator_inline "$cmd" $((index + 1))
}

_guard_spells_git_call() {
    local word base named="" triggered=""
    for word in ${GUARD_WORDS[@]+"${GUARD_WORDS[@]}"}; do
        base=${word##*/}
        case $base in
            git | git-*) [[ $base == git-*.* ]] || named=1 ;;
        esac
        [[ $word =~ ^($1)$ ]] && triggered=1
        [[ $base == git-* && ${base#git-} =~ ^($1)$ ]] && triggered=1
    done
    [ -n "$named" ] && [ -n "$triggered" ]
}

_guard_change_dir() {
    local count=${#GUARD_WORDS[@]} index=$((_strip_index + 1)) target
    [ -z "$GUARD_SUBSHELL" ] || return 0
    while [ "$index" -lt "$count" ]; do
        case ${GUARD_WORDS[$index]} in
            -L | -P | -e | -@) index=$((index + 1)) ;;
            *) break ;;
        esac
    done
    if [ "$index" -ge "$count" ]; then
        GUARD_CWD_LOST=1
        return 0
    fi
    target=${GUARD_WORDS[$index]}
    if [ "$target" = - ] || [ -n "${GUARD_EXPANDED[$index]-}" ]; then
        GUARD_CWD_LOST=1
        return 0
    fi
    GUARD_CWD=$(_guard_resolve "$target" "$GUARD_DIR")
}

_guard_export() {
    local index=$((_strip_index + 1))
    while [ "$index" -lt "${#GUARD_WORDS[@]}" ]; do
        case ${GUARD_WORDS[$index]} in
            -*) ;;
            *) _guard_note_git_env "${GUARD_WORDS[$index]}" ;;
        esac
        index=$((index + 1))
    done
}

_guard_lookup_var() {
    local name=$1 i=0 count=${#GUARD_VAR_NAMES[@]}
    while [ "$i" -lt "$count" ]; do
        if [ "${GUARD_VAR_NAMES[$i]}" = "$name" ]; then
            printf '%s' "${GUARD_VAR_VALUES[$i]}"
            return 0
        fi
        i=$((i + 1))
    done
    return 1
}

_guard_resolve_cmd() {
    local count=${#GUARD_WORDS[@]} word name resolved
    local bare_re='^[$]([A-Za-z_][A-Za-z0-9_]*)$'
    local braced_re='^[$][{]([A-Za-z_][A-Za-z0-9_]*)[}]$'
    [ "$_strip_index" -lt "$count" ] || return 1
    [[ $_strip_wrappers != *" eval "* ]] || return 1
    [ -n "${GUARD_EXPANDED[$_strip_index]-}" ] || return 1
    word=${GUARD_WORDS[$_strip_index]}
    if [[ $word =~ $bare_re ]] || [[ $word =~ $braced_re ]]; then
        name=${BASH_REMATCH[1]}
        resolved=$(_guard_lookup_var "$name") || return 1
        GUARD_WORDS[_strip_index]=$resolved
        GUARD_EXPANDED[_strip_index]=""
        return 0
    fi
    return 1
}

_guard_git_call() {
    local count=${#GUARD_WORDS[@]} word base arg dir j
    GUARD_ARGS=()
    GUARD_GIT_OPTIONS=()
    _guard_strip
    while _guard_resolve_cmd; do
        _guard_strip
    done
    GUARD_DIR=$_strip_dir
    if [ "$_strip_index" -ge "$count" ]; then
        if [ -n "$_strip_split" ]; then
            GUARD_UNPARSED_CAUSE=evaluator
            return 2
        fi
        return 1
    fi
    word=${GUARD_WORDS[$_strip_index]}
    base=${word##*/}
    [ -z "${GUARD_EXPANDED[$_strip_index]-}" ] || return 2

    case $word in
        cd)
            _guard_change_dir
            return 1
            ;;
        pushd | popd | builtin)
            GUARD_CWD_LOST=1
            return 1
            ;;
        export)
            _guard_export
            return 1
            ;;
    esac

    case $base in
        git | git-?*) ;;
        *)
            if _guard_evaluates; then
                GUARD_UNPARSED_CAUSE=evaluator
                return 2
            fi
            return 1
            ;;
    esac
    [[ $_strip_wrappers != *" xargs "* ]] || return 2

    dir=$GUARD_DIR
    j=$((_strip_index + 1))
    while [ "$j" -lt "$count" ]; do
        arg=${GUARD_WORDS[$j]}
        case $arg in
            -C)
                [ $((j + 1)) -lt "$count" ] || break
                dir=$(_guard_resolve "${GUARD_WORDS[$((j + 1))]}" "$dir")
                j=$((j + 2))
                ;;
            -C?*)
                dir=$(_guard_resolve "${arg#-C}" "$dir")
                j=$((j + 1))
                ;;
            -c* | --config-env | --config-env=*) return 2 ;;
            --git-dir | --work-tree | --namespace | --exec-path | --super-prefix | --attr-source | --shallow-file)
                [ $((j + 1)) -lt "$count" ] || return 2
                GUARD_GIT_OPTIONS+=("$arg" "${GUARD_WORDS[$((j + 1))]}")
                j=$((j + 2))
                ;;
            -*)
                GUARD_GIT_OPTIONS+=("$arg")
                j=$((j + 1))
                ;;
            *) break ;;
        esac
    done
    [ "$j" -le "$count" ] || j=$count
    GUARD_DIR=$dir
    GUARD_ARGS=(${GUARD_WORDS[@]+"${GUARD_WORDS[@]:$j}"})
    [ "$base" = git ] || GUARD_ARGS=("${base#git-}" ${GUARD_ARGS[@]+"${GUARD_ARGS[@]}"})
    while [ "$j" -gt "$_strip_index" ]; do
        [ -z "${GUARD_EXPANDED[$j]-}" ] || return 2
        j=$((j - 1))
    done
    return 0
}

_guard_alias_safe() {
    local subcommand=${GUARD_ARGS[0]-} status
    [ -n "$subcommand" ] || return 0
    if [ -z "$GUARD_BUILTINS" ]; then
        GUARD_BUILTINS=$(git --list-cmds=builtins 2>/dev/null) || return 1
    fi
    case $'\n'$GUARD_BUILTINS$'\n' in
        *$'\n'"$subcommand"$'\n'*) return 0 ;;
    esac
    [ -z "$GUARD_CWD_LOST" ] || return 1
    git -C "$GUARD_DIR" ${GUARD_GIT_OPTIONS[@]+"${GUARD_GIT_OPTIONS[@]}"} \
        config --get "alias.$subcommand" >/dev/null 2>&1
    status=$?
    [ "$status" -eq 1 ]
}

_guard_config_writes() {
    local arg skip="" names=0
    case ${1-} in
        get | list) return 1 ;;
        set | unset | edit | rename-section | remove-section) return 0 ;;
    esac
    for arg in "$@"; do
        if [ -n "$skip" ]; then
            skip=""
            continue
        fi
        case $arg in
            --get | --get-all | --get-regexp | --get-urlmatch | --list | -l) return 1 ;;
            --add | --replace-all | --unset | --unset-all | --rename-section | --remove-section | --edit | -e) return 0 ;;
            --file | -f | --blob | --type | --default) skip=1 ;;
            -*) ;;
            *) names=$((names + 1)) ;;
        esac
    done
    [ "$names" -ne 1 ]
}

_guard_bare_word() {
    local arg from=1
    [ "${GUARD_ARGS[0]-}" != stash ] || from=2
    for arg in ${GUARD_ARGS[@]+"${GUARD_ARGS[@]:$from}"}; do
        if [[ $arg =~ ^($1)$ ]]; then
            printf '%s' "$arg"
            return 0
        fi
    done
}

_guard_helper_triggers() {
    case ${GUARD_ARGS[0]-} in
        subtree | submodule | stash) [ -n "$GUARD_BARE_WORD" ] ;;
        *) return 1 ;;
    esac
}

_guard_runs_handed_command() {
    local subcommand=${GUARD_ARGS[0]-} arg
    for arg in ${GUARD_ARGS[@]+"${GUARD_ARGS[@]:1}"}; do
        case $subcommand:$arg in
            submodule:foreach | bisect:run | rebase:-x | rebase:--exec | rebase:--exec=* | difftool:-x | difftool:--extcmd | difftool:--extcmd=*) return 0 ;;
        esac
    done
    [ "$subcommand" = filter-branch ]
}

_guard_call_literal() {
    local subcommand=${GUARD_ARGS[0]-}
    if [[ $subcommand =~ ^($1)$ ]]; then
        [ -z "$GUARD_CWD_LOST" ] || return 1
    fi
    if [ "$subcommand" = config ]; then
        ! _guard_config_writes ${GUARD_ARGS[@]+"${GUARD_ARGS[@]:1}"} || return 1
    fi
    ! _guard_helper_triggers || return 1
    ! _guard_runs_handed_command
}

guard_segment() {
    local outcome
    GUARD_BARE_WORD=""
    GUARD_UNPARSED_CAUSE=""
    [ -z "$GUARD_DATA" ] || return 1
    _guard_git_call
    outcome=$?
    [ "$outcome" -ne 0 ] || GUARD_BARE_WORD=$(_guard_bare_word "$1")
    [ -z "$GUARD_GIT_ENV" ] || return 2
    if [ -n "$GUARD_UNBALANCED" ] && guard_mentions "$1"; then
        return 2
    fi
    case $outcome in
        0)
            _guard_alias_safe || return 2
            _guard_call_literal "$1" && return 0
            return 2
            ;;
        1) _guard_spells_git_call "$1" && return 2 ;;
        2) return 2 ;;
    esac
    return 1
}

_guard_add_heredoc() {
    _scan_heredoc_delims+=("$1")
    _scan_heredoc_strips+=("$2")
    _scan_heredoc_states+=(pending)
    if [ -n "$_scan_quoted" ]; then
        _scan_heredoc_expands+=("")
    else
        _scan_heredoc_expands+=(1)
    fi
}

_guard_mark_expansion() {
    [ "$_scan_expand_at" -ge 0 ] || _scan_expand_at=${#_scan_word}
    _scan_expand_last=${#_scan_word}
}

_guard_flush_word() {
    local text=$_scan_word expanded="" name_quoted=$_scan_name_quoted
    [ -n "$_scan_in_word" ] || return 0
    _scan_in_word=""
    _scan_word=""
    _scan_name_quoted=""
    _scan_dollar_at=-1
    case $text in
        "\${HOME}"*) [ "$_scan_expand_last" -lt 7 ] || expanded=1 ;;
        "\$HOME"*) [ "$_scan_expand_last" -lt 5 ] || expanded=1 ;;
        *) [ "$_scan_expand_at" -lt 0 ] || expanded=1 ;;
    esac
    _scan_expand_at=-1
    _scan_expand_last=-1
    if [ -n "$_scan_heredoc_next" ]; then
        _guard_add_heredoc "$text" "${_scan_heredoc_next/#plain/}"
        _scan_heredoc_next=""
        _scan_quoted=""
        return 0
    fi
    if [ -n "$_scan_redirect" ]; then
        _scan_redirect=""
        _scan_quoted=""
        return 0
    fi
    if [[ -z $_scan_quoted && $text == *[\<\>]* ]]; then
        if [[ $text =~ $_GUARD_HEREDOC_OPERATOR ]]; then
            _scan_heredoc_next=plain
            [ -z "${BASH_REMATCH[1]}" ] || _scan_heredoc_next='strip'
            return 0
        fi
        if [[ $text =~ $_GUARD_HEREDOC_GLUED ]]; then
            _guard_add_heredoc "${BASH_REMATCH[2]}" "${BASH_REMATCH[1]:+1}"
            return 0
        fi
        if [[ $text =~ $_GUARD_REDIRECT_ALONE ]]; then
            _scan_redirect=1
            return 0
        fi
        [[ $text =~ $_GUARD_REDIRECT_WORD ]] && return 0
    fi
    _scan_quoted=""
    case $text in
        [~] | [~]/*) text=${HOME:-}${text:1} ;;
        "\${HOME}"*) text=${HOME:-}${text#"\${HOME}"} ;;
        "\$HOME"*) text=${HOME:-}${text#"\$HOME"} ;;
    esac
    [ "${#_scan_words[@]}" -gt 0 ] || _scan_first_quoted_name=$name_quoted
    _scan_words+=("$text")
    _scan_expanded+=("$expanded")
}

_guard_settle_heredocs() {
    local index=$_scan_heredoc_head state
    [[ " ${_scan_heredoc_states[*]+"${_scan_heredoc_states[*]}"} " == *" pending "* ]] || return 0
    GUARD_WORDS=(${_scan_words[@]+"${_scan_words[@]}"})
    GUARD_EXPANDED=(${_scan_expanded[@]+"${_scan_expanded[@]}"})
    _guard_strip
    while _guard_resolve_cmd; do
        _guard_strip
    done
    if _guard_evaluates; then
        state=run
    elif [ "$1" = '|' ]; then
        return 0
    else
        state=skip
    fi
    while [ "$index" -lt "${#_scan_heredoc_states[@]}" ]; do
        [ "${_scan_heredoc_states[$index]}" != pending ] || _scan_heredoc_states[index]=$state
        index=$((index + 1))
    done
}

_guard_end_segment() {
    local separator=${1-}
    _guard_flush_word
    _scan_redirect=""
    _guard_settle_heredocs "$separator"
    if [ "${#_scan_words[@]}" -eq 0 ]; then
        case $separator in
            '|')
                _scan_after_pipe=1
                _scan_prev_sep='|'
                ;;
            '&&' | '||' | '&') _scan_prev_sep=$separator ;;
        esac
        return 0
    fi
    GUARD_WORDS=(${_scan_words[@]+"${_scan_words[@]}"})
    GUARD_EXPANDED=(${_scan_expanded[@]+"${_scan_expanded[@]}"})
    GUARD_SUBSHELL=$_scan_after_pipe
    case $separator in
        '|' | '&') GUARD_SUBSHELL=1 ;;
    esac
    GUARD_PREV_SEP=$_scan_prev_sep
    GUARD_FIRST_QUOTED_NAME=$_scan_first_quoted_name
    _scan_after_pipe=""
    [ "$separator" != '|' ] || _scan_after_pipe=1
    _scan_prev_sep=$separator
    _scan_first_quoted_name=""
    _scan_words=()
    _scan_expanded=()
    "$_scan_callback" "$separator"
}

_guard_end_line() {
    _guard_end_segment
    [ "$_scan_prev_sep" != '&' ] || _scan_prev_sep=""
    [ -n "$_scan_body" ] || _guard_consume_heredocs
}

_guard_skip_comment() {
    local char
    while IFS= read -r -n1 -u 3 char && [ -n "$char" ]; do :; done
    _scan_line=""
    _guard_end_line
}

_guard_strip_tabs() {
    _heredoc_line=$1
    [ -z "${_scan_heredoc_strips[$_scan_heredoc_head]}" ] || _heredoc_line=${_heredoc_line#"${_heredoc_line%%[!$'\t']*}"}
}

_guard_consume_heredocs() {
    local count=${#_scan_heredoc_delims[@]} head line
    while [ "$_scan_heredoc_head" -lt "$count" ]; do
        head=$_scan_heredoc_head
        if [ "${_scan_heredoc_states[$head]}" = run ] || [ -n "${_scan_heredoc_expands[$head]}" ]; then
            _scan_body=1
            _scan_line=""
            _scan_body_literal=${_scan_heredoc_expands[$head]}
            GUARD_DATA=""
            [ "${_scan_heredoc_states[$head]}" = run ] || GUARD_DATA=1
            return 0
        fi
        while IFS= read -r -u 3 line; do
            _guard_strip_tabs "$line"
            [ "$_heredoc_line" != "${_scan_heredoc_delims[$head]}" ] || break
        done
        _scan_heredoc_head=$((head + 1))
    done
    _scan_heredoc_delims=()
    _scan_heredoc_strips=()
    _scan_heredoc_states=()
    _scan_heredoc_expands=()
    _scan_heredoc_head=0
}

_guard_body_ends() {
    _guard_strip_tabs "$_scan_line"
    [ "$_heredoc_line" = "${_scan_heredoc_delims[$_scan_heredoc_head]}" ] || return 1
    _scan_words=()
    _scan_expanded=()
    _scan_word=""
    _scan_in_word=""
    _scan_quoted=""
    _scan_name_quoted=""
    _scan_first_quoted_name=""
    _scan_redirect=""
    _scan_quote=""
    _scan_line=""
    _scan_body=""
    _scan_body_literal=""
    GUARD_DATA=""
    _scan_heredoc_head=$((_scan_heredoc_head + 1))
    _guard_consume_heredocs
}

_guard_substitute() {
    local saved_cwd=$GUARD_CWD saved_lost=$GUARD_CWD_LOST saved_data=$GUARD_DATA
    [ "$1" != ')' ] || _scan_word=${_scan_word%\$}
    GUARD_DATA=""
    _guard_scan_stream "$_scan_callback" "$1"
    GUARD_CWD=$saved_cwd
    GUARD_CWD_LOST=$saved_lost
    GUARD_DATA=$saved_data
    _scan_dollar_at=-1
    _guard_mark_expansion
    _scan_word+="\$()"
    _scan_in_word=1
}

_guard_enter_subshell() {
    _scan_cwds+=("$GUARD_CWD")
    _scan_losts+=("$GUARD_CWD_LOST")
}

_guard_leave_subshell() {
    local last=$((${#_scan_cwds[@]} - 1))
    [ "$last" -ge 0 ] || return 0
    GUARD_CWD=${_scan_cwds[$last]}
    GUARD_CWD_LOST=${_scan_losts[$last]}
    _scan_cwds=(${_scan_cwds[@]+"${_scan_cwds[@]:0:$last}"})
    _scan_losts=(${_scan_losts[@]+"${_scan_losts[@]:0:$last}"})
}

_guard_split_ifs() {
    _scan_word=${_scan_word:0:$_scan_dollar_at}
    if [ "$_scan_expand_at" -ge "$_scan_dollar_at" ]; then
        _scan_expand_at=-1
        _scan_expand_last=-1
    elif [ "$_scan_expand_last" -ge "$_scan_dollar_at" ]; then
        _scan_expand_last=$_scan_expand_at
    fi
    _scan_dollar_at=-1
    if [ -n "$_scan_word" ] || [ -n "$_scan_quoted" ]; then
        _guard_flush_word
    else
        _scan_in_word=""
    fi
}

_guard_split_operator() {
    if [[ -z $_scan_quoted && $_scan_word =~ $_GUARD_HEREDOC_OPERATOR ]]; then
        _guard_flush_word
    fi
}

_guard_scan_stream() {
    local _scan_callback=$1 _scan_end=$2
    local _scan_words=() _scan_expanded=() _scan_word="" _scan_in_word="" _scan_quoted="" _scan_redirect=""
    local _scan_name_quoted="" _scan_first_quoted_name="" _scan_prev_sep=""
    local _scan_ampersand="" _scan_pipe="" _scan_after_pipe="" _scan_quote="" _scan_cwds=() _scan_losts=()
    local _scan_depth=0 _scan_dollar="" _scan_was_dollar="" _scan_dollar_at=-1 _scan_char="" _scan_escaped=""
    local _scan_expand_at=-1 _scan_expand_last=-1
    local _scan_heredoc_next="" _scan_heredoc_delims=() _scan_heredoc_strips=() _scan_heredoc_states=()
    local _scan_heredoc_expands=() _scan_heredoc_head=0
    local _scan_body="" _scan_body_literal="" _scan_line=""

    while IFS= read -r -n1 -u 3 _scan_char; do
        [[ $_scan_char ]] || _scan_char=$'\n'
        _scan_was_dollar=$_scan_dollar
        _scan_dollar=""
        if [ -n "$_scan_body" ]; then
            if [ "$_scan_char" = $'\n' ]; then
                if _guard_body_ends; then continue; fi
                _scan_line=""
            else
                _scan_line+=$_scan_char
            fi
        fi
        if [ -n "$_scan_ampersand" ]; then
            _scan_ampersand=""
            case $_scan_char in
                '&')
                    _guard_end_segment '&&'
                    continue
                    ;;
                '>')
                    _guard_flush_word
                    _scan_word+="&"
                    _scan_in_word=1
                    ;;
                *) _guard_end_segment '&' ;;
            esac
        fi
        if [ -n "$_scan_pipe" ]; then
            _scan_pipe=""
            case $_scan_char in
                '|')
                    _guard_end_segment '||'
                    continue
                    ;;
                '&')
                    _guard_end_segment '|'
                    continue
                    ;;
                *) _guard_end_segment '|' ;;
            esac
        fi
        case $_scan_quote in
            "'")
                if [ "$_scan_char" = "'" ]; then _scan_quote=""; else _scan_word+=$_scan_char; fi
                continue
                ;;
            '"')
                case $_scan_char in
                    '"') _scan_quote="" ;;
                    "\\")
                        IFS= read -r -n1 -u 3 _scan_escaped
                        case $_scan_escaped in
                            '"' | "\\" | '$' | '`') _scan_word+=$_scan_escaped ;;
                            *) _scan_word+=$_scan_char$_scan_escaped ;;
                        esac
                        ;;
                    '$')
                        _scan_dollar=1
                        _guard_mark_expansion
                        _scan_word+=$_scan_char
                        ;;
                    '(')
                        if [ -n "$_scan_was_dollar" ]; then _guard_substitute ')'; else _scan_word+=$_scan_char; fi
                        ;;
                    '`') _guard_substitute '`' ;;
                    *) _scan_word+=$_scan_char ;;
                esac
                continue
                ;;
        esac
        if [ "$_scan_dollar_at" -ge 0 ] && [[ $_scan_char != [A-Za-z0-9_] ]] && [ "${_scan_word:$_scan_dollar_at}" = "\$IFS" ]; then
            _guard_split_ifs
        fi
        case $_scan_char in
            "'" | '"')
                if [ -n "$_scan_body_literal" ]; then
                    _scan_word+=$_scan_char
                    _scan_in_word=1
                else
                    [[ $_scan_word == *=* ]] || _scan_name_quoted=1
                    _guard_split_operator
                    _scan_quote=$_scan_char
                    _scan_in_word=1
                    _scan_quoted=1
                fi
                ;;
            "\\")
                IFS= read -r -n1 -u 3 _scan_escaped
                [[ $_scan_word == *=* ]] || _scan_name_quoted=1
                _guard_split_operator
                _scan_word+=$_scan_escaped
                _scan_in_word=1
                _scan_quoted=1
                ;;
            ' ' | $'\t') _guard_flush_word ;;
            '#')
                if [ -n "$_scan_in_word" ] || [ -n "$_scan_body_literal" ]; then
                    _scan_word+=$_scan_char
                    _scan_in_word=1
                else
                    _guard_skip_comment
                fi
                ;;
            '(')
                if [ -n "$_scan_was_dollar" ]; then
                    _guard_substitute ')'
                elif [[ -z $_scan_quoted && $_scan_word == [\<\>] ]]; then
                    _scan_word=""
                    _guard_substitute ')'
                else
                    _guard_end_segment
                    _guard_enter_subshell
                    _scan_prev_sep=""
                    [ "$_scan_end" != ')' ] || _scan_depth=$((_scan_depth + 1))
                fi
                ;;
            ')')
                _guard_end_segment
                if [ "$_scan_end" = ')' ]; then
                    [ "$_scan_depth" -gt 0 ] || return 0
                    _scan_depth=$((_scan_depth - 1))
                fi
                _guard_leave_subshell
                _scan_prev_sep=""
                ;;
            '`')
                if [ "$_scan_end" = '`' ]; then
                    _guard_end_segment
                    return 0
                fi
                _guard_substitute '`'
                ;;
            ';')
                _guard_end_segment
                _scan_prev_sep=""
                ;;
            '|') _scan_pipe=1 ;;
            $'\n') _guard_end_line ;;
            '&')
                if [ -n "$_scan_in_word" ] && [[ $_scan_word == *[\<\>] ]]; then
                    _scan_word+="&"
                else
                    _scan_ampersand=1
                fi
                ;;
            '$')
                _scan_dollar=1
                _scan_dollar_at=${#_scan_word}
                _guard_mark_expansion
                _scan_word+=$_scan_char
                _scan_in_word=1
                ;;
            '<' | '>')
                if [ -n "$_scan_in_word" ] && [[ ! $_scan_word =~ $_GUARD_REDIRECT_PREFIX ]]; then
                    _guard_flush_word
                fi
                _scan_word+=$_scan_char
                _scan_in_word=1
                ;;
            *)
                case $_scan_char in
                    '{' | '*' | '?' | '[') _guard_mark_expansion ;;
                esac
                _scan_word+=$_scan_char
                _scan_in_word=1
                if [ "$_scan_char" = '}' ] && [ "$_scan_dollar_at" -ge 0 ] && [ "${_scan_word:$_scan_dollar_at}" = "\${IFS}" ]; then
                    _guard_split_ifs
                fi
                ;;
        esac
    done

    [ -z "$_scan_ampersand" ] || _guard_end_segment '&'
    [ -z "$_scan_pipe" ] || _guard_end_segment '|'
    if [ -n "$_scan_quote" ] || [ -n "$_scan_end" ]; then
        GUARD_UNBALANCED=1
    fi
    _guard_end_segment
    GUARD_UNBALANCED=""
}

_guard_collect_segment() {
    local word name val i count
    local assign_re='^([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_.+/-]+)$'
    [ -z "$GUARD_DATA" ] || return 0
    if [ -n "$GUARD_UNBALANCED" ]; then
        _collect_unbalanced=1
        return 0
    fi
    for word in ${GUARD_WORDS[@]+"${GUARD_WORDS[@]}"}; do
        case $word in
            'if' | 'then' | 'else' | 'elif' | 'fi' | 'case' | 'esac' | 'while' | 'until' | 'for' | 'select' | 'do' | 'done' | 'function' | '{' | '}')
                _collect_forbidden=1
                return 0
                ;;
        esac
    done
    [ "${#GUARD_WORDS[@]}" -eq 1 ] || return 0
    word=${GUARD_WORDS[0]}
    [[ $word =~ $assign_re ]] || return 0
    name=${BASH_REMATCH[1]}
    val=${BASH_REMATCH[2]}

    i=0
    count=${#_collect_names[@]}
    while [ "$i" -lt "$count" ]; do
        if [ "${_collect_names[$i]}" = "$name" ]; then
            _collect_bad[i]=1
            return 0
        fi
        i=$((i + 1))
    done

    if [ -n "$_scan_end" ] || [ -n "$_scan_body" ] || [ "${#_scan_cwds[@]}" -gt 0 ] || [ -n "$GUARD_SUBSHELL" ] ||
        [ -n "$GUARD_PREV_SEP" ] || [ -n "${GUARD_EXPANDED[0]-}" ] || [ -n "$GUARD_FIRST_QUOTED_NAME" ]; then
        _collect_names+=("$name")
        _collect_values+=("")
        _collect_bad+=(1)
        return 0
    fi

    _collect_names+=("$name")
    _collect_values+=("$val")
    _collect_bad+=("")
}

_guard_var_valid() {
    local name=$1 rest=$2 text=$2
    local regex="(^|[^A-Za-z0-9_])${name}([^A-Za-z0-9_]|$)"
    local m before after prefix name_offset advance
    local assign_seen="" backslashes idx

    while [[ $rest =~ $regex ]]; do
        m=${BASH_REMATCH[0]}
        before=${BASH_REMATCH[1]}
        after=${BASH_REMATCH[2]}
        prefix=${rest%%"$m"*}
        name_offset=$((${#text} - ${#rest} + ${#prefix} + ${#before}))
        advance=$((${#prefix} + ${#before} + ${#name}))
        rest=${rest:$advance}

        if [ "$after" = '=' ] && [ "$before" != '$' ] && [ "$before" != '{' ]; then
            [ -z "$assign_seen" ] || return 1
            case $before in
                "" | " " | $'\t' | $'\n' | ";") ;;
                *) return 1 ;;
            esac
            assign_seen=1
            continue
        fi

        [ -n "$assign_seen" ] || return 1

        if [ "$before" = '$' ]; then
            idx=$((name_offset - 2))
        elif [ "$before" = '{' ] && [ "$after" = '}' ] && [ "$name_offset" -ge 2 ] &&
            [ "${text:$((name_offset - 2)):1}" = '$' ]; then
            idx=$((name_offset - 3))
        else
            return 1
        fi

        if [ "$idx" -ge 0 ] && [ "${text:$idx:1}" = '$' ]; then
            return 1
        fi
        backslashes=0
        while [ "$idx" -ge 0 ] && [ "${text:$idx:1}" = "\\" ]; do
            backslashes=$((backslashes + 1))
            idx=$((idx - 1))
        done
        [ $((backslashes % 2)) -eq 0 ] || return 1
    done

    [ -n "$assign_seen" ]
}

_guard_finalize_vars() {
    local text=$1 i=0 count=${#_collect_names[@]}
    local fn_re='(^|[^A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*[[:blank:]]*\([[:blank:]]*\)'
    local ifs_re='(^|[^A-Za-z0-9_])IFS([^A-Za-z0-9_]|$)'
    [ -z "$_collect_unbalanced" ] || return 0
    [ -z "$_collect_forbidden" ] || return 0
    [ "$count" -gt 0 ] || return 0
    if [[ $text =~ $fn_re ]] || [[ $text =~ $ifs_re ]]; then
        return 0
    fi
    while [ "$i" -lt "$count" ]; do
        if [ -z "${_collect_bad[$i]}" ] && _guard_var_valid "${_collect_names[$i]}" "$text"; then
            GUARD_VAR_NAMES+=("${_collect_names[$i]}")
            GUARD_VAR_VALUES+=("${_collect_values[$i]}")
        fi
        i=$((i + 1))
    done
}

_guard_reset_scan_state() {
    GUARD_WORDS=()
    GUARD_EXPANDED=()
    GUARD_ARGS=()
    GUARD_BARE_WORD=""
    GUARD_UNBALANCED=""
    GUARD_UNPARSED_CAUSE=""
    GUARD_DATA=""
    GUARD_DIR=""
    GUARD_CWD=$1
    GUARD_CWD_LOST=""
    GUARD_GIT_ENV=""
    GUARD_BUILTINS=""
    GUARD_SUBSHELL=""
    GUARD_PREV_SEP=""
    GUARD_FIRST_QUOTED_NAME=""
}

guard_scan() {
    local text=$2 suffix
    local _collect_names=() _collect_values=() _collect_bad=() _collect_forbidden="" _collect_unbalanced=""
    if [[ $text =~ ^[[:blank:]]*python3[[:blank:]] ]] &&
        suffix=$(python3 -I -S "${BASH_SOURCE[0]%/*}/python-text-stdin.py" "$3" <<<"$text"); then
        text=$suffix
    fi
    GUARD_VAR_NAMES=()
    GUARD_VAR_VALUES=()
    text=${text//\\$'\n'/}
    _guard_reset_scan_state "$3"
    _guard_scan_stream _guard_collect_segment "" 3<<<"$text"
    _guard_finalize_vars "$text"
    _guard_reset_scan_state "$3"
    _guard_scan_stream "$1" "" 3<<<"$text"
}

emit_allow() {
    if [ "${GUARD_HOOK_FORMAT-}" = antigravity ]; then
        printf '{"decision":"allow"}\n'
    fi
}

emit_deny() {
    if [ "${GUARD_HOOK_FORMAT-}" = antigravity ]; then
        jq -nc --arg reason "$1" '{decision: "deny", reason: $reason}' 2>/dev/null ||
            printf '{"decision":"deny","reason":"%s"}\n' "$1"
        return 0
    fi
    jq -nc --arg reason "$1" \
        '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}' 2>/dev/null ||
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
}

may_trigger() {
    [[ $2 =~ ${_GUARD_GIT_WORD_EDGE_BEFORE}git-($1)${_GUARD_WORD_EDGE_AFTER} ]] && return 0
    [[ $2 =~ ${_GUARD_GIT_WORD_EDGE_BEFORE}git${_GUARD_WORD_EDGE_AFTER} ]]
}

unescape_json() {
    local text=$1
    text=${text//'\\n'/ }
    text=${text//'\\t'/ }
    text=${text//'\\r'/ }
    text=${text//'\\\/'/ }
    printf '%s' "$text"
}

deny() {
    emit_deny "$1"
    trap - EXIT
    exit 0
}

guard_bootstrap() {
    local trigger=$1 no_jq_reason=$2 hook_failed_reason=$3
    local payload command dir jq_query='.tool_input.command // ""'
    if [ "${GUARD_HOOK_FORMAT-}" = antigravity ]; then
        jq_query='.toolCall.args.CommandLine // ""'
    fi
    payload=$(cat)
    if ! command=$(jq -r "$jq_query" <<<"$payload" 2>/dev/null); then
        if may_trigger "$trigger" "$(unescape_json "$payload")"; then
            emit_deny "$no_jq_reason"
        else
            emit_allow
        fi
        return 1
    fi
    if [ -z "$command" ]; then
        emit_allow
        return 1
    fi
    if ! guard_prefilter "$trigger" "$command"; then
        emit_allow
        return 1
    fi
    GUARD_HOOK_FAILED_REASON=$hook_failed_reason
    trap 'status=$?; if [ "$status" -ne 0 ]; then emit_deny "$GUARD_HOOK_FAILED_REASON"; exit 0; fi' EXIT
    dir=$(jq -r '.cwd // ""' <<<"$payload" 2>/dev/null)
    [ -n "$dir" ] || dir=$PWD
    # shellcheck disable=SC2034
    GUARD_COMMAND=$command
    GUARD_DIR=$dir
}
