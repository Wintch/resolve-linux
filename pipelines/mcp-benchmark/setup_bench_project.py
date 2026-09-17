#!/usr/bin/env python3
"""
Idempotent setup for the MCP benchmark fixtures: a project with a timeline
called "bench_tl" holding 8 "Solid Color" generator clips. Uses generators
instead of imported media on purpose -- this makes the benchmark fully
self-contained (no source footage dependency) and reproducible on any rig
with Resolve installed.

Run standalone: ~/resolve-install/davinci-resolve-mcp/venv/bin/python setup_bench_project.py

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

from bench_lib import native_session, timed_call

CLIP_COUNT = 8
PROJECT_NAME = "MCP-Benchmark"

SETUP_SCRIPT = f"""
pm = resolve.GetProjectManager()
current = pm.GetCurrentProject()
if current and current.GetName() == {PROJECT_NAME!r} and resolve.GetCurrentPage():
    proj = current
elif {PROJECT_NAME!r} in (pm.GetProjectListInCurrentFolder() or []):
    proj = pm.LoadProject({PROJECT_NAME!r})
else:
    proj = pm.CreateProject({PROJECT_NAME!r})
if proj is None:
    raise RuntimeError("could not load or create {PROJECT_NAME}")

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
    if not tl.InsertGeneratorIntoTimeline("Solid Color"):
        raise RuntimeError(f"InsertGeneratorIntoTimeline failed at {{len(items)}} clips")
    items = tl.GetItemListInTrack("video", 1) or []
    added += 1

result = {{
    "project": proj.GetName(),
    "timeline": tl.GetName(),
    "clip_count": len(items),
    "clips_added_this_run": added,
}}
"""


async def main() -> int:
    async with native_session() as session:
        r = await timed_call(session, "run_script", {"script": SETUP_SCRIPT, "timeout": 60}, timeout=70)
    print(r["result_text"])
    fixture = (r["result_json"] or {}).get("result") or {}
    if r["is_error"] or fixture.get("clip_count") != CLIP_COUNT:
        print(f"FIXTURE SETUP FAILED (wanted {CLIP_COUNT} clips on bench_tl)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
