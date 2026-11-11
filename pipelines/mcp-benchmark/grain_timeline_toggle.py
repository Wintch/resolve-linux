#!/usr/bin/env python3
"""
Validates timeline-level (not per-clip) grade control on a real project.

Point it at a project whose timeline has "OFX: Film Grain" (or another effect)
on a node of its *timeline* node graph (`Timeline.GetNodeGraph()`, distinct
from any per-clip graph). It toggles that node off and back on via
`Graph.SetNodeEnabled`, run through native `run_script`, with proof stills
from `ResolveClient.grab_still`.

Stills are native-only: `Timeline.GrabStill` + `GalleryStillAlbum.ExportStills`
are in the scripting API (native has no stills tool of its own, but
`run_script_unsafe` can read the exported PNG back); community is only the
client's fallback. GrabStill returns its GalleryStill before the image behind
it exists, and ExportStills called straight after exports stale content with
no error, so the client waits (resolve_client.STILL_SETTLE_S) between the two.

Why proof-by-still, not just trusting the bool SetNodeEnabled returns:
`GetToolsInNode()` keeps listing a tool even when its node is disabled, and
there is no `GetNodeEnabled()` getter to read the toggle back. The only
trustworthy evidence a grade change rendered differently is a real frame
grab (community's gallery_stills docstring calls this the "WYSIWYG PROOF
RULE"). Grabs a still in each state and diffs them with numpy.

Metric: an exact per-pixel diff (any nonzero, reproducible delta counts), not
whole-frame luma stddev. A low Film Grain amount can shift only ~0.16 % of a
1080p frame's pixels by 1 luma level: real and reproducible, but invisible in
a global statistic (stddev on/off differed by less than noise).

Run with care on a real project: always re-enables the node before exiting,
including on error (try/finally), and switches back to MCP-Benchmark as the
current project when done either way.

Run: <community venv>/bin/python grain_timeline_toggle.py
     --project <name> [--timeline "Timeline 1"] [--node 1]
(--project MCP-Benchmark --timeline bench_tl_media exercises the code path
without touching a real project -- no grain there, so expect "on vs off" = 0)
"""
import argparse
import asyncio
import io

import numpy as np
from PIL import Image

from resolve_client import ResolveClient, ResolveError

RETURN_TO_PROJECT = "MCP-Benchmark"


def open_script(project: str, timeline: str) -> str:
    return f"""
pm = resolve.GetProjectManager()
current = pm.GetCurrentProject()
proj = current if current and current.GetName() == {project!r} else pm.LoadProject({project!r})
if proj is None:
    raise RuntimeError("could not load project " + {project!r})
tl = next((proj.GetTimelineByIndex(i) for i in range(1, proj.GetTimelineCount() + 1)
           if proj.GetTimelineByIndex(i).GetName() == {timeline!r}), None)
if tl is None:
    raise RuntimeError("no timeline named " + {timeline!r})
proj.SetCurrentTimeline(tl)
resolve.OpenPage("color")
tl.SetCurrentTimecode(tl.GetStartTimecode())
result = {{"opened": tl.GetName(), "page": resolve.GetCurrentPage(), "nodes": tl.GetNodeGraph().GetNumNodes()}}
"""


def set_enabled_script(node_index: int, enabled: bool) -> str:
    return f"""
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
result = {{"set_enabled_returned": tl.GetNodeGraph().SetNodeEnabled({int(node_index)}, {bool(enabled)})}}
"""


NUDGE_SCRIPT = """
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
start_tc = tl.GetStartTimecode()
away_tc = start_tc[:-2] + "05"  # a few frames later, same hour/min/sec digits
tl.SetCurrentTimecode(away_tc)
tl.SetCurrentTimecode(start_tc)
result = {"start_tc": start_tc, "away_tc": away_tc}
"""


async def grab(client: ResolveClient, label: str) -> np.ndarray:
    """Color page node caching can serve a stale frame to GrabStill if the
    playhead hasn't moved since a node's enabled state changed -- jump away
    and back to force a genuine re-render before each grab."""
    await client.run_script(NUDGE_SCRIPT, label=f"nudge_{label}")
    return luma_array(await client.grab_still(label))


def luma_array(png_bytes: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("L"), dtype=np.int16)


def pixel_diff_stats(a: np.ndarray, b: np.ndarray) -> dict:
    """Global stddev turned out too coarse to catch this project's actual
    grain amount (see below) -- an exact per-pixel diff is what actually
    proves whether SetNodeEnabled changed the render, regardless of how
    subtle."""
    diff = np.abs(a.astype(np.int16) - b.astype(np.int16))
    return {
        "changed_pixels": int((diff > 0).sum()),
        "total_pixels": int(diff.size),
        "max_diff": int(diff.max()),
        "mean_diff": float(diff.mean()),
    }


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", required=True, help="project whose timeline graph carries the effect")
    ap.add_argument("--timeline", default="Timeline 1")
    ap.add_argument("--node", type=int, default=1, help="timeline-graph node index carrying the effect")
    args = ap.parse_args()

    async with ResolveClient() as client:
        opened = await client.run_script(open_script(args.project, args.timeline), label="open", timeout=60)
        print("open:", opened)
        if opened["page"] != "color":
            raise ResolveError(f"failed to reach the Color page on {args.project}/{args.timeline}: {opened}")

        off_arr = on_after_arr = None
        try:
            on_before_arr = await grab(client, "grain_on_before")
            print("disable:", await client.run_script(set_enabled_script(args.node, False), label="disable"))
            off_arr = await grab(client, "grain_off")
        finally:
            # always leave the node enabled and hand the session back, error or not
            try:
                print("restore:", await client.run_script(set_enabled_script(args.node, True), label="restore"))
                on_after_arr = await grab(client, "grain_on_after")
            finally:
                await client.ensure_project(RETURN_TO_PROJECT)

    effect_stats = pixel_diff_stats(on_before_arr, off_arr)
    restore_stats = pixel_diff_stats(on_before_arr, on_after_arr)

    print(f"\non vs off:      {effect_stats}")
    print(f"on vs restored: {restore_stats}")

    # Global stddev turned out useless here: an early cut compared whole-
    # frame stddev between on/off and saw no significant difference,
    # which looked like "the toggle has no visual effect" -- but an
    # exact pixel diff showed it does change the render, just subtly
    # (this project's grain amount is low): ~3200/2,073,600 pixels
    # (0.16%) shift by exactly 1 luma level. So the bar for "the toggle
    # did something real" is *any* nonzero, reproducible pixel change --
    # not a stddev swing sized for a much heavier effect.
    grain_effect_confirmed = effect_stats["changed_pixels"] > 0
    restore_confirmed = restore_stats["changed_pixels"] == 0
    print(f"\nGRAIN EFFECT CONFIRMED: {grain_effect_confirmed}")
    print(f"RESTORE CONFIRMED (pixel-exact): {restore_confirmed}")
    return 0 if grain_effect_confirmed and restore_confirmed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
