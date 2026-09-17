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
import asyncio
import glob
import json
import os
import sys
import time
from contextlib import asynccontextmanager, nullcontext
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Overridable because this rig has no working sudo: anything run from the
# operator's root shell sees "~" as /root, not the desktop user's home.
COMMUNITY_REPO = os.environ.get("RESOLVE_MCP_COMMUNITY_REPO") or os.path.expanduser(
    "~/resolve-install/davinci-resolve-mcp"
)
COMMUNITY_PYTHON = os.path.join(COMMUNITY_REPO, "venv", "bin", "python")
NATIVE_BINARY = os.environ.get("RESOLVE_MCP_NATIVE_BINARY", "/opt/resolve/bin/ResolveMCP")

# Both servers log every JSON-RPC message to stderr at DEBUG level -- useful
# once, pure noise on every run after that.
_DEBUG = bool(os.environ.get("RESOLVE_MCP_DEBUG"))


def _find_xauthority():
    """This rig's default session is Wayland (mutter) with Xwayland rootless.
    Resolve's GUI needs a real X11 auth cookie -- DISPLAY alone crashes it
    with SIGABRT ("Authorization required, but no authorization protocol
    specified"), found live while building this benchmark.

    An XAUTHORITY already exported and pointing at a real file wins (the
    Xorg/KDE session case, see README's 2026-09-07 update). Otherwise take
    the newest mutter cookie -- a stale one from a crashed session can linger
    next to the live one. Returns None rather than "" when nothing is found:
    an empty XAUTHORITY is worse than an unset one."""
    current = os.environ.get("XAUTHORITY")
    if current and os.path.isfile(current):
        return current
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    matches = glob.glob(os.path.join(runtime_dir, ".mutter-Xwaylandauth.*"))
    if matches:
        return max(matches, key=os.path.getmtime)
    fallback = os.path.expanduser("~/.Xauthority")
    return fallback if os.path.isfile(fallback) else None


def resolve_env() -> dict:
    env = dict(os.environ)
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"
    xauthority = _find_xauthority()
    if xauthority:
        env["XAUTHORITY"] = xauthority
    else:
        env.pop("XAUTHORITY", None)
    env["RESOLVE_SCRIPT_API"] = "/opt/resolve/Developer/Scripting"
    env["RESOLVE_SCRIPT_LIB"] = "/opt/resolve/libs/Fusion/fusionscript.so"
    modules_path = "/opt/resolve/Developer/Scripting/Modules/"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{existing}:{modules_path}" if existing else modules_path
    return env


def _errlog():
    """Server stderr: passed through with RESOLVE_MCP_DEBUG=1, dropped otherwise."""
    return nullcontext(sys.stderr) if _DEBUG else open(os.devnull, "w")


@asynccontextmanager
async def _session(params: StdioServerParameters):
    """One initialized MCP session. The SDK runs each session inside two
    nested anyio task groups, which re-raise anything crossing them as
    ExceptionGroup(ExceptionGroup(exc)) -- so an `except FixtureError` around
    a session never matched and the real message sat at the bottom of a
    60-line traceback. A group holding exactly one exception is unwrapped
    back into that exception; a genuine multi-error group is left alone."""
    try:
        with _errlog() as errlog:
            async with stdio_client(params, errlog=errlog) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    yield session
    except BaseExceptionGroup as group:
        leaf = group
        while isinstance(leaf, BaseExceptionGroup) and len(leaf.exceptions) == 1:
            leaf = leaf.exceptions[0]
        if leaf is group:
            raise
        raise leaf from None


@asynccontextmanager
async def native_session():
    params = StdioServerParameters(command=NATIVE_BINARY, args=[], env=resolve_env())
    async with _session(params) as session:
        yield session


@asynccontextmanager
async def community_session(mode: str = "compound"):
    args = [os.path.join(COMMUNITY_REPO, "src", "server.py")]
    if mode == "granular":
        args.append("--full")
    params = StdioServerParameters(
        command=COMMUNITY_PYTHON, args=args, env=resolve_env(), cwd=COMMUNITY_REPO
    )
    async with _session(params) as session:
        yield session


def _flatten_text(content) -> str:
    return "".join(getattr(c, "text", "") for c in content)


def parse_result(text: str):
    """Both servers return structured results as one JSON text block. Parse
    it rather than substring-matching it: `'"page": null' in preview` style
    checks break on any whitespace change and on anything past a truncated
    preview. Returns None when the text isn't JSON (plain error strings)."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _is_error_envelope(parsed) -> bool:
    """native's run_script reports a script exception as a *successful* MCP
    result -- isError stays false and the traceback arrives as
    {"error": "Traceback ..."} (a normal run is {"output": ..., "result": ...}).
    Found live: trusting isError alone meant no script failure was ever
    detected. community signals its failures with a top-level "error" key too."""
    return isinstance(parsed, dict) and "error" in parsed and "result" not in parsed


async def timed_call(
    session: ClientSession, tool: str, arguments: dict, label: str = None, timeout: float = None
) -> dict:
    """Call one MCP tool, return timing + result, never raise on tool-level errors
    (a script error inside run_script is a normal benchmark data point, not a
    harness failure). Callers that need a failure to be loud must check
    `is_error` themselves -- see resolve_client.ResolveClient for the raising
    wrapper.

    `timeout` (seconds) bounds the whole round trip client-side. It exists
    because a scripting call made while Resolve's GUI is mid-playback never
    returns at all (README's known-limitations table)."""
    label = label or tool
    read_timeout = timedelta(seconds=timeout) if timeout else None
    t0 = time.perf_counter()
    try:
        result = await session.call_tool(tool, arguments, read_timeout_seconds=read_timeout)
        elapsed = time.perf_counter() - t0
        text = _flatten_text(result.content)
        parsed = parse_result(text)
        return {
            "label": label,
            "seconds": elapsed,
            "is_error": bool(getattr(result, "isError", False)) or _is_error_envelope(parsed),
            "result_preview": text[:300],
            "result_text": text,
            "result_json": parsed,
        }
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return {
            "label": label,
            "seconds": elapsed,
            "is_error": True,
            "result_preview": repr(exc)[:300],
            "result_text": repr(exc),
            "result_json": None,
        }


FIXTURE_PROJECT = "MCP-Benchmark"


class FixtureError(RuntimeError):
    pass


def run_main(main) -> None:
    """asyncio.run(main()) as the process exit code, with a fixture problem
    reported as one line instead of a traceback."""
    try:
        code = asyncio.run(main())
    except FixtureError as exc:
        raise SystemExit(f"fixture check failed: {exc}") from None
    raise SystemExit(code)

# Prepended to any script that mutates "the current project": these fixtures
# rename clips, import media and create timelines, and whatever project the
# operator last had open (a real one, e.g. test1) is what "current" means.
REQUIRE_FIXTURE_PROJECT = f"""
_current = resolve.GetProjectManager().GetCurrentProject()
if _current is None or _current.GetName() != {FIXTURE_PROJECT!r}:
    raise RuntimeError("current project is " + repr(_current.GetName() if _current else None)
                       + ", refusing to touch anything but " + {FIXTURE_PROJECT!r} + " -- run setup_bench_project.py")
"""


async def select_fixture_timeline(session: ClientSession, name: str = "bench_tl", clip_count: int = 8) -> None:
    """Make `name` the current timeline and check it still holds the fixture.
    Every benchmark script addresses "the current timeline", so without this
    they silently measure (and rename clips on) whatever was left selected --
    caught live when bench_02 reported 2 round trips instead of 9 because a
    1-clip timeline happened to be current.

    Also pins the GUI to the Edit page. The active page is part of what gets
    measured: the same 8-clip native rename takes ~73ms on Edit/Color and
    ~166ms on Deliver (reproducible; Deliver redraws more per timeline edit),
    which is enough to flip bench_02's native-vs-community verdict depending
    on nothing but where the operator last left the GUI."""
    script = REQUIRE_FIXTURE_PROJECT + f"""
proj = resolve.GetProjectManager().GetCurrentProject()
tl = next((proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)
           if proj.GetTimelineByIndex(i).GetName() == {name!r}), None)
if tl is None or not proj.SetCurrentTimeline(tl):
    raise RuntimeError("fixture timeline " + {name!r} + " not found in " + proj.GetName() + " -- run setup_bench_project.py")
clips = len(tl.GetItemListInTrack("video", 1) or [])
if clips != {int(clip_count)}:
    raise RuntimeError(f"{{clips}} clips on " + {name!r} + ", fixture wants {int(clip_count)}")
if not resolve.OpenPage("edit"):
    raise RuntimeError("could not switch to the Edit page")
result = clips
"""
    r = await timed_call(session, "run_script", {"script": script}, label="select_fixture_timeline", timeout=20)
    if r["is_error"]:
        error = (r["result_json"] or {}).get("error") or r["result_text"]
        raise FixtureError(error.strip().splitlines()[-1])


def write_results(path, rows) -> None:
    """One JSON object per line, without the full result payloads -- the
    committed .jsonl files are timing records, `result_preview` is enough."""
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps({k: v for k, v in row.items() if k not in ("result_text", "result_json")}) + "\n")


async def run_native_script(session: ClientSession, script: str, label: str = "run_script") -> dict:
    return await timed_call(session, "run_script", {"script": script}, label=label)
