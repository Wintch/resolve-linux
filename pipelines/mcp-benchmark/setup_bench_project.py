#!/usr/bin/env python3
"""
Idempotent setup for the MCP benchmark fixtures: a project with a timeline
called "bench_tl" holding 8 "Solid Color" generator clips. Uses generators
instead of imported media on purpose -- this makes the benchmark fully
self-contained (no source footage dependency) and reproducible on any rig
with Resolve installed.

Run standalone: venv/bin/python setup_bench_project.py

Found live while building this: right after `launch_resolve`, Resolve is
sitting on the **Project Manager** screen, not inside a loaded project --
`GetCurrentProject()` still returns a usable "Untitled Project" object (its
name is readable) but `GetCurrentPage()` returns None and any Media Pool
write (e.g. `CreateEmptyTimeline`) silently returns None and does nothing,
with zero exception or error signal. The fix is to explicitly create/load a
*named* project via the Project Manager first -- that's what actually
dismisses the Project Manager screen and drops you into the Cut page.
"""
import asyncio

from bench_lib import native_session

CLIP_COUNT = 8
PROJECT_NAME = "MCP-Benchmark"

SETUP_SCRIPT = f"""
pm = resolve.GetProjectManager()
existing_projects = pm.GetProjectListInCurrentFolder() or []
if "{PROJECT_NAME}" in existing_projects:
    proj = pm.LoadProject("{PROJECT_NAME}")
else:
    proj = pm.CreateProject("{PROJECT_NAME}")

mp = proj.GetMediaPool()
tl = proj.GetCurrentTimeline()

if tl is None or tl.GetName() != "bench_tl":
    existing = None
    for i in range(1, proj.GetTimelineCount() + 1):
        t = proj.GetTimelineByIndex(i)
        if t.GetName() == "bench_tl":
            existing = t
            break
    tl = existing or mp.CreateEmptyTimeline("bench_tl")
    proj.SetCurrentTimeline(tl)

items = tl.GetItemListInTrack("video", 1) or []
added = 0
while len(items) < {CLIP_COUNT}:
    tl.InsertGeneratorIntoTimeline("Solid Color")
    items = tl.GetItemListInTrack("video", 1) or []
    added += 1

result = {{
    "project": proj.GetName(),
    "timeline": tl.GetName(),
    "clip_count": len(items),
    "clips_added_this_run": added,
}}
"""


async def main():
    async with native_session() as session:
        res = await session.call_tool("run_script", {"script": SETUP_SCRIPT})
        text = "".join(getattr(c, "text", "") for c in res.content)
        print(text)


if __name__ == "__main__":
    asyncio.run(main())
