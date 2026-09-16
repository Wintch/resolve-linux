#!/usr/bin/env python3
"""
Shared connection + timing helpers for benchmarking the two DaVinci Resolve
MCP servers installed on this rig against each other:

  - "native"    -- /opt/resolve/bin/ResolveMCP, bundled with Resolve Studio
                   21.1+. 14 tools, code-execution model (run_script against
                   the full scripting API).
  - "community" -- samuelgursky/davinci-resolve-mcp, cloned to
                   ~/resolve-install/davinci-resolve-mcp. One MCP tool per
                   grouped action (compound, 36 tools) or per API method
                   (granular, 353 tools).

Both speak standard MCP over stdio, so both are driven with the same
mcp.client.stdio machinery -- no protocol-specific code needed per server.
"""
import glob
import os
import time
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

COMMUNITY_REPO = os.path.expanduser("~/resolve-install/davinci-resolve-mcp")
COMMUNITY_PYTHON = os.path.join(COMMUNITY_REPO, "venv", "bin", "python")
NATIVE_BINARY = "/opt/resolve/bin/ResolveMCP"


def _find_xauthority() -> str:
    """This rig runs a Wayland session (mutter) with Xwayland rootless.
    Resolve's GUI needs a real X11 auth cookie -- DISPLAY alone crashes it
    with SIGABRT ("Authorization required, but no authorization protocol
    specified"), found live while building this benchmark."""
    matches = glob.glob("/run/user/1000/.mutter-Xwaylandauth.*")
    if matches:
        return matches[0]
    return os.environ.get("XAUTHORITY", "")


def resolve_env() -> dict:
    env = dict(os.environ)
    env["DISPLAY"] = ":0"
    env["XAUTHORITY"] = _find_xauthority()
    env["RESOLVE_SCRIPT_API"] = "/opt/resolve/Developer/Scripting"
    env["RESOLVE_SCRIPT_LIB"] = "/opt/resolve/libs/Fusion/fusionscript.so"
    modules_path = "/opt/resolve/Developer/Scripting/Modules/"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{existing}:{modules_path}" if existing else modules_path
    return env


@asynccontextmanager
async def native_session():
    params = StdioServerParameters(command=NATIVE_BINARY, args=[], env=resolve_env())
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def community_session(mode: str = "compound"):
    args = [os.path.join(COMMUNITY_REPO, "src", "server.py")]
    if mode == "granular":
        args.append("--full")
    params = StdioServerParameters(
        command=COMMUNITY_PYTHON, args=args, env=resolve_env(), cwd=COMMUNITY_REPO
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _flatten_text(content) -> str:
    return "".join(getattr(c, "text", "") for c in content)


async def timed_call(session: ClientSession, tool: str, arguments: dict, label: str = None) -> dict:
    """Call one MCP tool, return timing + result, never raise on tool-level errors
    (a script error inside run_script is a normal benchmark data point, not a
    harness failure)."""
    label = label or tool
    t0 = time.perf_counter()
    try:
        result = await session.call_tool(tool, arguments)
        elapsed = time.perf_counter() - t0
        return {
            "label": label,
            "seconds": elapsed,
            "is_error": bool(getattr(result, "isError", False)),
            "result_preview": _flatten_text(result.content)[:300],
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {"label": label, "seconds": elapsed, "is_error": True, "result_preview": repr(exc)[:300]}


async def run_native_script(session: ClientSession, script: str, label: str = "run_script") -> dict:
    return await timed_call(session, "run_script", {"script": script}, label=label)
