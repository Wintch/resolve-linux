#!/usr/bin/env python3
"""
Validates timeline-level (not per-clip) grade control on a REAL project:
test1 / "Timeline 1" already has "OFX: Film Grain" applied to node 1 of its
*timeline* node graph (`Timeline.GetNodeGraph()` -- confirmed distinct from
any per-clip graph, see README's MCP section). This toggles that grain off
and back on via `Graph.SetNodeEnabled`, run through native `run_script`
(Resolve's own scripting API first, per instruction), with proof stills from
`ResolveClient.grab_still`.

**Correction (2026-09-17)**: this originally grabbed its stills through
community's `gallery_stills.grab_and_export` "since native has no
stills/Gallery tool of its own". True of native's *tool list*, wrong as a
conclusion: `Timeline.GrabStill` + `GalleryStillAlbum.ExportStills` are in the
scripting API and `run_script_unsafe` can read the exported PNG back, so the
whole thing runs native-only now (verified live; community is only the
client's fallback).

Why proof-by-still, not just trusting the bool SetNodeEnabled returns:
`GetToolsInNode()` keeps listing a tool even when its node is disabled (a
known, already-documented limitation of this API -- see README), and there
is no `GetNodeEnabled()` getter to read the toggle back either. The only
trustworthy evidence a grade change actually rendered differently is a real
frame grab (community's gallery_stills docstring calls this the "WYSIWYG
PROOF RULE"). Grabs a still in each state and diffs them with numpy (the
community venv already carries numpy/PIL as installed AI extras).

**Whole-frame luma stddev turned out to be the wrong metric here** -- a
first cut compared it on/off and saw ~8.120 vs ~8.128, indistinguishable
from noise, which looked exactly like "the toggle has no real effect." An
exact per-pixel diff told the true story: this project's Film Grain node has
a genuinely low amount set -- only ~3,200 of 2,073,600 pixels (0.16%) shift,
and only by 1 luma level -- real and reproducible, just too subtle for a
single global-average statistic to surface. Swapped to an exact pixel-diff
check (any nonzero, reproducible delta counts) instead of a stddev
threshold. (An even earlier attempt used ffmpeg's `signalstats` filter for
this -- it has no stddev output at all, only min/low/avg/high/max
percentile-style fields -- so that was dropped in favor of decoding the PNG
with numpy directly.)

This is a real, working project (the same 16172-frame timeline this repo's
render-pacing benchmarks used) -- run with care. Always re-enables the node
before exiting, including on error (try/finally), and switches back to
MCP-Benchmark as the current project when done either way.

Run: ~/resolve-install/davinci-resolve-mcp/venv/bin/python grain_timeline_toggle.py
     [--project test1] [--timeline "Timeline 1"] [--node 1]
(point it at MCP-Benchmark/bench_tl_media to exercise the code path without
touching a real project -- no grain there, so expect "on vs off" = 0 pixels)
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
    ap.add_argument("--project", default="test1")
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
