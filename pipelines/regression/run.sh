#!/usr/bin/env bash
# Regression suite for the Resolve + MCP rig: see README.md.
#
#   run.sh                                everything: t00 offline, one shared -nogui session for t10..t49, then t50+
#   run.sh --offline                      only the offline modules (t00..t09): no Resolve is started
#   run.sh --only t20,t40                 only these modules (t10 = ../mcp-benchmark/validate_client.py)
#   run.sh --offline --update-baselines   accept the current API surface: rewrite baselines/ (after a green run.sh)
#   run.sh --gui-xvfb                     passed to the modules that run outside the shared session (t50..t99)
#   run.sh --list                         list the modules found
#
# Module numbering: t00-t09 offline, t10-t49 live inside the shared session, t50-t99 outside it (they
# manage their own Resolve sessions). Exit: 0 all passed (WARN/SKIP allowed), 1 a FAIL, 2 refused to start,
# 130 interrupted.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bench="$(cd "${here}/../mcp-benchmark" && pwd)"
repo="${RESOLVE_MCP_COMMUNITY_REPO:-${HOME}/resolve-install/davinci-resolve-mcp}"
py="${repo}/venv/bin/python"
xvfb_display="${RESOLVE_XVFB_DISPLAY:-:99}"
module_timeout="${REGRESSION_MODULE_TIMEOUT:-1800}"

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"; }

offline=0
only=""
update_baselines=0
gui_xvfb=0
list=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --offline) offline=1 ;;
        --only) only="${2:?--only needs a list, e.g. t20,t40}"; shift ;;
        --only=*) only="${1#--only=}" ;;
        --update-baselines) update_baselines=1 ;;
        --gui-xvfb) gui_xvfb=1 ;;
        --list) list=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [[ ! -x "${py}" ]]; then
    echo "community server venv not found: ${py} (set RESOLVE_MCP_COMMUNITY_REPO)" >&2
    exit 2
fi
cd "${here}"
if [[ ${list} -eq 1 ]]; then
    exec "${py}" live.py --list
fi

# ---- which modules ---------------------------------------------------------------------------------
mapfile -t available < <("${py}" live.py --list | cut -d' ' -f1)
if [[ -n "${only}" ]]; then
    IFS=',' read -r -a selected <<< "${only}"
    for m in "${selected[@]}"; do
        [[ "${m}" =~ ^t[0-9][0-9]$ ]] || { echo "not a module id: ${m} (expected tNN)" >&2; exit 2; }
    done
else
    selected=("${available[@]}")
fi
offline_mods=() live_mods=() outside_mods=()
for m in "${selected[@]}"; do
    n=$((10#${m#t}))
    if (( n < 10 )); then offline_mods+=("${m}")
    elif (( n < 50 )); then live_mods+=("${m}")
    else outside_mods+=("${m}")
    fi
done
if [[ ${offline} -eq 1 ]]; then
    if [[ -n "${only}" && ${#offline_mods[@]} -eq 0 ]]; then
        echo "--offline runs only t00..t09, and --only ${only} selects none of them" >&2
        exit 2
    fi
    live_mods=() outside_mods=()
fi
if [[ ${update_baselines} -eq 1 ]]; then
    # Accepting a new API surface is a separate, deliberate step: run it after a green ./run.sh, never in the
    # same run as the live checks (they would not have had a say).
    if [[ ${offline} -eq 0 ]]; then
        echo "--update-baselines only together with --offline (after a green ./run.sh): ./run.sh --offline --update-baselines" >&2
        exit 2
    fi
    if [[ ! " ${offline_mods[*]} " == *" t00 "* ]]; then
        echo "--update-baselines needs t00 (the module that owns baselines/) in the selection" >&2
        exit 2
    fi
fi
needs_resolve=$(( ${#live_mods[@]} + ${#outside_mods[@]} ))

# Every DaVinci Resolve, found by executable and by command line (rlib.resolve_processes), one per line.
resolve_running() {
    "${py}" -c 'import rlib
for p in rlib.resolve_processes():
    print(p["pid"], p["cmd"])' || echo "cannot list processes (rlib.resolve_processes failed)"
}
# Xvfb on the shared session's display and on :99 (the display t50 uses).
xvfb_running() {
    "${py}" - "${xvfb_display}" <<'EOF' || echo "cannot list processes (rlib.xvfb_pids failed)"
import sys, rlib
for d in dict.fromkeys([sys.argv[1], ":99"]):
    for pid in rlib.xvfb_pids(d):
        print(pid, "Xvfb", d)
EOF
}
wait_clear() {   # up to 60 s for the last session to finish quitting Resolve and its Xvfb
    for _ in $(seq 60); do
        [[ -z "$(resolve_running)$(xvfb_running)" ]] && return 0
        sleep 1
    done
    return 1
}

# ---- (a) never start on top of a running Resolve ----------------------------------------------------
if (( needs_resolve > 0 )); then
    running="$(resolve_running)"
    if [[ -n "${running}" ]]; then
        echo "REFUSE: DaVinci Resolve is already running (a person's session or a leftover):" >&2
        echo "${running}" >&2
        echo "Quit it first; the suite starts and stops its own -nogui instance." >&2
        exit 2
    fi
    if [[ -n "$(xvfb_running)" ]]; then
        echo "note: an idle Xvfb is up ($(xvfb_running | tr '\n' ' ')): the shared session adopts one on ${xvfb_display}; t50 fails its precondition on :99"
    fi
fi

# ---- (b) scripting environment (the same discovery as the wrapper and bench_lib) ---------------------
if ! envsh="$("${py}" - "${bench}" <<'EOF'
import shlex, sys
sys.path.insert(0, sys.argv[1])
import bench_lib
e = bench_lib.resolve_env()
for k in ("DISPLAY", "XAUTHORITY", "RESOLVE_SCRIPT_API", "RESOLVE_SCRIPT_LIB", "PYTHONPATH"):
    if k in e:
        print(f"export {k}={shlex.quote(e[k])}")
EOF
)"; then
    echo "REFUSE: bench_lib.resolve_env() failed: no scripting environment" >&2
    exit 2
fi
eval "${envsh}"
if [[ -z "${RESOLVE_SCRIPT_API:-}" || -z "${RESOLVE_SCRIPT_LIB:-}" ]]; then
    echo "REFUSE: bench_lib.resolve_env() gave no RESOLVE_SCRIPT_API / RESOLVE_SCRIPT_LIB" >&2
    exit 2
fi
export RESOLVE_MCP_COMMUNITY_REPO="${repo}"

reports="$("${py}" -c 'import rlib; print(rlib.REPORTS)')"
mkdir -p "${reports}"
# only this suite's report names, never every *.json (REGRESSION_REPORTS may point anywhere)
rm -f "${reports}"/t[0-9][0-9]*.json "${reports}"/t[0-9][0-9]_*.log "${reports}"/live_cleanup.json "${reports}"/live_session.json \
      "${reports}"/post_run.json "${reports}"/missing_reports.json "${reports}"/interrupted.json \
      "${reports}/.live_started" "${reports}/.live_child"

# the report names that must exist at the end: a step that never finished is a FAIL, not a quiet pass
expected=("${offline_mods[@]}" "${live_mods[@]}" "${outside_mods[@]}")
(( ${#live_mods[@]} > 0 )) && expected+=(live_cleanup)
(( needs_resolve > 0 )) && expected+=(post_run)

post_run_check() {
    wait_clear || true
    local left_resolve left_xvfb
    left_resolve="$(resolve_running | tr '\n' ';')"
    left_xvfb="$(xvfb_running | tr '\n' ';')"
    {
        if [[ -n "${left_resolve}" ]]; then
            printf 'FAIL\tno Resolve left running\t%s\n' "${left_resolve}"
        else
            printf 'OK\tno Resolve left running\tnone\n'
        fi
        if [[ -n "${left_xvfb}" ]]; then
            printf 'FAIL\tno Xvfb left\t%s\n' "${left_xvfb}"
        else
            printf 'OK\tno Xvfb left\tnone on %s or :99\n' "${xvfb_display}"
        fi
    } | "${py}" live.py --record post_run || true
}

block_pid=""
on_interrupt() {
    trap '' INT TERM HUP
    echo "### interrupted: stopping the running module and checking what is left" >&2
    local pid
    if [[ -n "${block_pid}" ]]; then
        # live.py stops its module, sweeps the fixture (live_cleanup) and exits; then the shim quits Resolve
        pid="$(cat "${reports}/.live_started" 2>/dev/null || true)"
        [[ "${pid}" =~ ^[0-9]+$ ]] && kill -TERM "${pid}" 2>/dev/null || true
        for _ in $(seq 600); do
            kill -0 "${block_pid}" 2>/dev/null || break
            sleep 1
        done
        kill -TERM "${block_pid}" 2>/dev/null || true
        wait "${block_pid}" 2>/dev/null || true
    elif [[ -f "${reports}/.live_child" ]]; then   # an offline or outside module runs in its own process group
        pid="$(cat "${reports}/.live_child" 2>/dev/null || true)"
        [[ "${pid}" =~ ^[0-9]+$ ]] && kill -TERM -- "-${pid}" 2>/dev/null || true
    fi
    printf 'FAIL\trun.sh\tinterrupted: reports may be missing; the next run sweeps leftover zz_reg_ objects (live_cleanup)\n' \
        | "${py}" live.py --record interrupted || true
    (( needs_resolve > 0 )) && post_run_check
    "${py}" live.py --summary "${expected[@]}" || true
    exit 130
}
trap on_interrupt INT TERM HUP

# ---- (c) offline ------------------------------------------------------------------------------------
offline_args=()
[[ ${update_baselines} -eq 1 ]] && offline_args+=(--update-baselines)
for m in "${offline_mods[@]}"; do
    "${py}" live.py --outside "${m}" -- "${offline_args[@]}" || true
done

# ---- (d) live block: one shared -nogui session for all live modules ----------------------------------
refused=0
if (( ${#live_mods[@]} > 0 )); then
    echo
    echo "### starting the shared -nogui session for: ${live_mods[*]}"
    rc=0
    # bounded: every module has its own timeout, this covers the shim's lock wait, the boot and live_cleanup
    block_timeout=$(( (${#live_mods[@]} + 1) * ${module_timeout%.*} + 600 ))
    # in the background (timeout puts it in its own process group, so a Ctrl-C reaches only this script, whose
    # trap stops live.py first and lets it clean up), then waited for
    RESOLVE_XVFB_NOGUI=1 timeout --signal=TERM --kill-after=120 "${block_timeout}" \
        "${py}" "${bench}/resolve_xvfb_stdio.py" "${repo}" -- "${py}" "${here}/live.py" "${live_mods[@]}" &
    block_pid=$!
    wait "${block_pid}" || rc=$?
    block_pid=""
    if [[ ! -f "${reports}/.live_started" ]]; then
        refused=1
        printf 'FAIL\tshared -nogui session\tdid not start or live.py refused (exit %s): see the [resolve_xvfb] lines above\n' "${rc}" \
            | "${py}" live.py --record live_session || true
    elif [[ ${rc} -eq 124 || ${rc} -eq 137 ]]; then
        printf 'FAIL\tshared -nogui session\tthe live block ran past %ss and was stopped (exit %s)\n' "${block_timeout}" "${rc}" \
            | "${py}" live.py --record live_session || true
    fi
    rm -f "${reports}/.live_started" "${reports}/.live_child"
fi

# ---- (e) modules that manage their own session --------------------------------------------------------
outside_args=()
[[ ${gui_xvfb} -eq 1 ]] && outside_args+=(--gui-xvfb)
for m in "${outside_mods[@]}"; do
    if ! wait_clear && [[ -n "$(resolve_running)" ]]; then
        printf 'FAIL\tstart %s\tnot run: a Resolve is still running (%s)\n' "${m}" "$(resolve_running | tr '\n' ';')" \
            | "${py}" live.py --record "${m}_not_run" || true
        continue
    fi
    "${py}" live.py --outside "${m}" -- "${outside_args[@]}" || true
done

# ---- (f) nothing may be left running -------------------------------------------------------------------
(( needs_resolve > 0 )) && post_run_check

# ---- (g) summary ----------------------------------------------------------------------------------------
trap - INT TERM HUP
rc=0
"${py}" live.py --summary "${expected[@]}" || rc=$?
if [[ ${refused} -eq 1 ]]; then
    exit 2
fi
exit "${rc}"
