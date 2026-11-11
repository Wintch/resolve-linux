#!/usr/bin/env bash
# Launch one of the two DaVinci Resolve MCP servers with the environment
# Resolve's GUI actually needs, for MCP clients whose config can only carry a
# static env block (.mcp.json, Claude Desktop, ...).
#
# Static env isn't enough under a Wayland (mutter + rootless Xwayland)
# session: the X11 auth cookie is $XDG_RUNTIME_DIR/.mutter-Xwaylandauth.<random>,
# a new name every login, and without it `launch_resolve` aborts Resolve's Qt
# init ("Authorization required, but no authorization protocol specified").
# Same logic as bench_lib.resolve_env() -- keep the two in sync.
#
# Usage: resolve_mcp_wrapper.sh native|community|headless|xvfb|native-xvfb [server args...]
#
#   native       the vendor server (/opt/resolve/bin/ResolveMCP) against a Resolve someone already has open
#   community    davinci-resolve-mcp against a Resolve someone already has open
#   headless     community server wrapped in its own scripts/resolve_headless.py: `guard` (refuse to start on
#                top of an interactive session), `start` (boot -nogui, wait until scriptable), `run`, then
#                `stop` (only if it started Resolve). For an unattended remote caller (an MCP client over SSH,
#                nobody at the keyboard). -nogui renders but has no pages and blocks on any dialog: use xvfb
#                when a page (Color: stills) or a visible dialog is needed.
#   xvfb         community server; Resolve runs WITH its GUI inside a private Xvfb (resolve_xvfb_stdio.py),
#                which is what makes the render queue work
#   native-xvfb  vendor server against that same Xvfb session, through mcp_filter.py (run_script_unsafe removed)
#
# An SSH-invoked process has neither DISPLAY nor XAUTHORITY: without the
# discovery below Resolve aborts in QApplication::init (SIGABRT).
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend="${1:?usage: $0 native|community|headless|xvfb|native-xvfb [server args...]}"
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
    # Native X11 session: sddm/lightdm-spawned X sessions keep their
    # per-session cookie at /tmp/xauth_<random>, a different name and place
    # than the greeter's own /run/sddm/xauth_* -- that one authenticates the
    # login screen, not the user's session, so it must not be picked here.
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
    xvfb)
        # Resolve WITH its GUI in a private Xvfb (no -nogui): the render queue works there.
        repo="${RESOLVE_MCP_COMMUNITY_REPO:-${HOME}/resolve-install/davinci-resolve-mcp}"
        cd "${repo}"
        exec "${repo}/venv/bin/python" "${script_dir}/resolve_xvfb_stdio.py" \
            "${repo}" -- "${repo}/venv/bin/python" "${repo}/src/server.py" "$@"
        ;;
    native-xvfb)
        # the vendor MCP (/opt/resolve/bin/ResolveMCP) against the Resolve that lives in the private Xvfb;
        # it shares the session with the community server (the last one out quits Resolve)
        repo="${RESOLVE_MCP_COMMUNITY_REPO:-${HOME}/resolve-install/davinci-resolve-mcp}"
        cd "${repo}"
        exec "${repo}/venv/bin/python" "${script_dir}/resolve_xvfb_stdio.py" \
            "${repo}" -- "${repo}/venv/bin/python" "${script_dir}/mcp_filter.py" --block "${RESOLVE_MCP_NATIVE_BLOCK:-run_script_unsafe}" -- "${RESOLVE_MCP_NATIVE_BINARY:-/opt/resolve/bin/ResolveMCP}" "$@"
        ;;
    *)
        echo "unknown backend '${backend}' (want native|community|headless|xvfb|native-xvfb)" >&2
        exit 2
        ;;
esac
