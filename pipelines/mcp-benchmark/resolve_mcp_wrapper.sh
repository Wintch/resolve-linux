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
# Usage: resolve_mcp_wrapper.sh native|community [server args...]
set -euo pipefail

backend="${1:?usage: $0 native|community [server args...]}"
shift

export DISPLAY="${DISPLAY:-:0}"
if [[ -z "${XAUTHORITY:-}" || ! -f "${XAUTHORITY}" ]]; then
    unset XAUTHORITY
    runtime_dir="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    # newest first: a stale cookie from a crashed session can sit next to the live one
    cookie="$(ls -t "${runtime_dir}"/.mutter-Xwaylandauth.* 2>/dev/null | head -n 1 || true)"
    if [[ -n "${cookie}" ]]; then
        export XAUTHORITY="${cookie}"
    elif [[ -f "${HOME}/.Xauthority" ]]; then
        export XAUTHORITY="${HOME}/.Xauthority"
    fi
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
    *)
        echo "unknown backend '${backend}' (want native|community)" >&2
        exit 2
        ;;
esac
