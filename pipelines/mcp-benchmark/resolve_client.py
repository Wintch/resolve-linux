#!/usr/bin/env python3
"""
Robust, backend-agnostic DaVinci Resolve automation client.

Wraps the native (Blackmagic, bundled since 21.1) and community
(samuelgursky/davinci-resolve-mcp) MCP servers behind one interface.
Callers never pick a backend -- this module routes each call to whichever
side bench_01/bench_02 (see ../mcp-benchmark and the README's "Native MCP
server arrives in Resolve 21.1" section) showed is actually better for that
kind of operation:

  - single lookups (version, project/timeline name)        -> community
    (~5.5ms/call vs native's ~66ms flat `run_script` tax)
  - compound/batch edits (rename N clips, any multi-item
    loop), and anything native-only (LUT generation, DCTL,
    Fusion/UI Manager API, arbitrary scripts)               -> native

Also folds in the two startup failure modes found live while building the
benchmarks:
  - missing XAUTHORITY crashes Resolve's Qt init under this rig's Wayland
    (mutter + rootless Xwayland) session -- DISPLAY alone isn't enough.
  - Right after `launch_resolve`, Resolve idles on the Project Manager
    screen, not inside a loaded project: GetCurrentProject() still returns
    a usable "Untitled Project" object, but GetCurrentPage() is None and
    every Media Pool write silently no-ops. Fixed by explicitly
    creating/loading a *named* project, which is what actually dismisses
    the Project Manager screen.

Usage:
    async with ResolveClient() as client:
        await client.ensure_running()
        version = await client.get_version()
        await client.rename_clips(["A", "B"])
"""
import asyncio
from contextlib import AsyncExitStack

from bench_lib import community_session, native_session, timed_call


def _flat(content) -> str:
    return "".join(getattr(c, "text", "") for c in content)


class ResolveClient:
    def __init__(self, default_project: str = "MCP-Benchmark"):
        self.default_project = default_project
        self._stack = None
        self.native = None
        self.community = None

    async def __aenter__(self):
        self._stack = AsyncExitStack()
        self.native = await self._stack.enter_async_context(native_session())
        self.community = await self._stack.enter_async_context(community_session("compound"))
        return self

    async def __aexit__(self, *exc_info):
        await self._stack.aclose()

    # ---- robustness: launch + escape the Project Manager screen --------

    async def ensure_running(self, project: str = None) -> dict:
        status = await timed_call(self.native, "get_resolve_status", {}, label="get_resolve_status")
        if '"running": true' not in status["result_preview"]:
            launch = await timed_call(self.native, "launch_resolve", {}, label="launch_resolve")
            if launch["is_error"]:
                raise RuntimeError(f"launch_resolve failed: {launch['result_preview']}")
        return await self.ensure_project(project or self.default_project)

    async def ensure_project(self, name: str) -> dict:
        script = f"""
pm = resolve.GetProjectManager()
existing = pm.GetProjectListInCurrentFolder() or []
if "{name}" in existing:
    proj = pm.LoadProject("{name}")
else:
    proj = pm.CreateProject("{name}")
result = {{"project": proj.GetName() if proj else None, "page": resolve.GetCurrentPage()}}
"""
        r = await timed_call(self.native, "run_script", {"script": script}, label="ensure_project")
        if r["is_error"] or '"page": null' in r["result_preview"]:
            raise RuntimeError(f"ensure_project failed to reach an editable page: {r['result_preview']}")
        return r

    # ---- fast paths: single lookups go to community ---------------------

    async def get_version(self) -> str:
        r = await self.community.call_tool("resolve_control", {"action": "get_version"})
        return _flat(r.content)

    async def get_project_name(self) -> str:
        r = await self.community.call_tool("project_manager", {"action": "get_current"})
        return _flat(r.content)

    async def get_current_timeline_name(self) -> str:
        r = await self.community.call_tool("timeline", {"action": "get_current"})
        return _flat(r.content)

    # ---- compound/batch paths: one native run_script loop ----------------

    async def rename_clips(self, names: list, track_type: str = "video", track_index: int = 1) -> dict:
        script = f"""
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
items = tl.GetItemListInTrack("{track_type}", {track_index})
names = {names!r}
applied = []
for item, name in zip(items, names):
    item.SetName(name)
    applied.append(item.GetName())
result = applied
"""
        return await timed_call(self.native, "run_script", {"script": script}, label="rename_clips")

    async def run_script(self, script: str, unsafe: bool = False) -> dict:
        tool = "run_script_unsafe" if unsafe else "run_script"
        return await timed_call(self.native, tool, {"script": script}, label=tool)

    # ---- native-only capability -------------------------------------------

    async def generate_lut(self, path: str, size: int, transform: str) -> dict:
        return await timed_call(
            self.native, "generate_lut", {"path": path, "size": size, "transform": transform}, label="generate_lut"
        )

    async def list_luts(self) -> dict:
        return await timed_call(self.native, "list_luts", {}, label="list_luts")
