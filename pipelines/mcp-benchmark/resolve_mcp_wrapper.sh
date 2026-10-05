#!/usr/bin/env bash
# Launch one of the two DaVinci Resolve MCP servers with the environment
# Resolve's GUI actually needs, for MCP clients whose config can only carry a
# static env block (.mcp.json, Claude Desktop, ...).
#
# Static env isn't enough on this rig: under the default Wayland (mutter +
# rootless Xwayland) session the X11 auth cookie is
# $XDG_RUNTIME_DIR/.mutter-Xwaylandauth.<random>, a new name every login, and
# without it `launch_resolve` aborts Resolve's Qt init ("Authorization
# required, but no authorization protocol specified"). Same logic as
# bench_lib.resolve_env() -- keep the two in sync.
#
# Usage: resolve_mcp_wrapper.sh native|community|headless [server args...]
#
# `headless` (2026-10-01): same community backend, but wrapped in
# davinci-resolve-mcp's own scripts/resolve_headless.py -- `guard` (refuse
# to start on top of an interactive session already open on this rig),
# `start` (boot -nogui, wait until scriptable), `run`, then `stop` (but
# only if it was the one that started Resolve). Built for an unattended
# remote caller (an AI agent's MCP client, over SSH, no one at the
# keyboard to notice a crash or a stolen session) -- `native`/`community`
# assume a human already has Resolve open or is at the console to deal
# with it, which doesn't hold for that caller. Uses the same
# DISPLAY/XAUTHORITY discovery above (an SSH-invoked process has neither
# by default -- confirmed the hard way: resolve_headless.py called bare
# over SSH aborts Resolve's Qt init with SIGABRT in QApplication::init,
# the same failure this script's own header already describes, just
# surfacing through a different unprivileged caller). See README.md,
# "Session update (2026-10-01): headless mode for a remote MCP caller".
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend="${1:?usage: $0 native|community|headless [server args...]}"
shift

# DISPLAY: a rootless Xwayland picks its number per login (:0, :1, ...), so a
# hardcoded :0 pairs the right cookie with the wrong server ("Invalid
# MIT-MAGIC-COOKIE-1 key", Resolve dies in Qt init). Resolved below from the
# Xwayland process that owns the cookie; :0 only as a last resort.
if [[ -z "${XAUTHORITY:-}" || ! -f "${XAUTHORITY}" ]]; then
    unset XAUTHORITY
    runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    # newest first: a stale cookie from a crashed session can sit next to the live one
    cookie="$(ls -t "${runtime_dir}"/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1 || true)"
    # Native X11 session (operator switched this rig from Wayland to X11 on
    # 2026-10-01, specifically to work around the Wayland/XWayland cookie
    # trouble this script already existed for): sddm/lightdm-spawned X
    # sessions keep their per-session cookie at /tmp/xauth_<random>, a
    # DIFFERENT random name and location than the greeter's own
    # /run/sddm/xauth_* -- that one authenticates the login screen, not the
    # user's actual session, so it must not be picked here.
    if [[ -z "${cookie}" ]]; then
        cookie="$(ls -t /tmp/xauth_* 2>/dev/null | head -n 1 || true)"
    fi
    if [[ -n "${cookie}" ]]; then
        export XAUTHORITY="${cookie}"
    elif [[ -f "${HOME}/.Xauthority" ]]; then
        export XAUTHORITY="${HOME}/.Xauthority"
    fi
fi

if [[ -z "${DISPLAY:-}" ]]; then
    if [[ -n "${XAUTHORITY:-}" ]]; then
        DISPLAY="$(ps -eo args | sed -n "s|^[^ ]*Xwayland \(:[0-9]*\) .*-auth ${XAUTHORITY} .*|\1|p" | head -n 1)"
    fi
    export DISPLAY="${DISPLAY:-:0}"
fi

export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}/opt/resolve/Developer/Scripting/Modules/"

case "${backend}" in
    native)
        exec "${RESOLVE_MCP_NATIVE_BINARY:-/opt/resolve/bin/ResolveMCP}" "$@"
        ;;
    community)
        repo="${RESOLVE_MCP_COMMUNITY_REPO:-${HOME}/resolve-install/davinci-resolve-mcp}"
        cd "${repo}"
        exec "${repo}/venv/bin/python" "${repo}/src/server.py" "$@"
        ;;
    headless)
        repo="${RESOLVE_MCP_COMMUNITY_REPO:-${HOME}/resolve-install/davinci-resolve-mcp}"
        cd "${repo}"
        # via the shim, not resolve_headless.py directly: upstream prints its
        # status lines to stdout, which corrupts the MCP stdio stream.
        exec "${repo}/venv/bin/python" "${script_dir}/resolve_headless_stdio.py" \
            "${repo}" -- "${repo}/venv/bin/python" "${repo}/src/server.py" "$@"
        ;;
    *)
        echo "unknown backend '${backend}' (want native|community|headless)" >&2
        exit 2
        ;;
esac
