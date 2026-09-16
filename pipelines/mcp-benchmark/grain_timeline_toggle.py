#!/usr/bin/env python3
"""
Validates timeline-level (not per-clip) grade control on a REAL project:
test1 / "Timeline 1" already has "OFX: Film Grain" applied to node 1 of its
*timeline* node graph (`Timeline.GetNodeGraph()` -- confirmed distinct from
any per-clip graph, see README's MCP section). This toggles that grain off
and back on via `Graph.SetNodeEnabled`, run through native `run_script`
(Resolve's own scripting API first, per instruction) -- and community's
Gallery `grab_and_export` for proof, since native has no stills/Gallery tool
of its own.

Why proof-by-still, not just trusting the bool SetNodeEnabled returns:
`GetToolsInNode()` keeps listing a tool even when its node is disabled (a
known, already-documented limitation of this API -- see README), and there
is no `GetNodeEnabled()` getter to read the toggle back either. The only
trustworthy evidence a grade change actually rendered differently is a real
frame grab (community's own gallery_stills docstring calls this the
"WYSIWYG PROOF RULE"). Grabs a still in each state and diffs them with numpy
(the community venv already carries opencv/PIL as installed AI extras).

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

Run: venv/bin/python grain_timeline_toggle.py
"""
import asyncio
import base64
import io
import json
import tempfile

import numpy as np
from PIL import Image

from bench_lib import community_session, native_session, timed_call

PROJECT = "test1"
TIMELINE = "Timeline 1"
GRAIN_NODE_INDEX = 1

OPEN_SCRIPT = f"""
pm = resolve.GetProjectManager()
proj = pm.LoadProject({PROJECT!r})
tl = None
for i in range(1, proj.GetTimelineCount() + 1):
    t = proj.GetTimelineByIndex(i)
    if t.GetName() == {TIMELINE!r}:
        tl = t
        break
proj.SetCurrentTimeline(tl)
resolve.OpenPage("color")
tl.SetCurrentTimecode(tl.GetStartTimecode())
result = {{"opened": tl.GetName(), "page": resolve.GetCurrentPage()}}
"""

def set_enabled_script(enabled: bool) -> str:
    return f"""
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
ng = tl.GetNodeGraph()
result = {{"set_enabled_returned": ng.SetNodeEnabled({GRAIN_NODE_INDEX}, {enabled})}}
"""

RESTORE_SCRIPT = """
pm = resolve.GetProjectManager()
pm.LoadProject("MCP-Benchmark")
result = {"restored_project": "MCP-Benchmark"}
"""


def _flat(content) -> str:
    return "".join(getattr(c, "text", "") for c in content)


async def nudge_playhead(native, label: str):
    """Color page node caching can serve a stale frame to GrabStill if the
    playhead hasn't moved since a node's enabled state changed -- jump away
    and back to force a genuine re-render before each grab."""
    script = """
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
start_tc = tl.GetStartTimecode()
away_tc = start_tc[:-2] + "05"  # a few frames later, same hour/min/sec digits
tl.SetCurrentTimecode(away_tc)
tl.SetCurrentTimecode(start_tc)
result = {"start_tc": start_tc, "away_tc": away_tc}
"""
    return await timed_call(native, "run_script", {"script": script}, label=f"nudge_{label}")


async def grab_still(community, label: str) -> bytes:
    """grab_and_export always emits a companion .drx (plain-text XML under a
    "data" key) alongside the requested format -- the actual PNG comes back
    as a separate `files` entry named "<prefix>....png" with its bytes under
    "data_base64", not "data". Found by inspecting the real response instead
    of guessing a single "the image is base64 somewhere" regex."""
    with tempfile.TemporaryDirectory() as tmp:
        res = await community.call_tool(
            "gallery_stills",
            {"action": "grab_and_export", "params": {"folder_path": tmp, "prefix": label, "format": "png"}},
        )
        obj = json.loads(_flat(res.content))
        if "files" not in obj:
            raise RuntimeError(f"grab_and_export({label}) failed: {json.dumps(obj)[:300]}")
        png_entries = [f for f in obj["files"] if f["name"].endswith(".png")]
        if not png_entries:
            raise RuntimeError(f"grab_and_export({label}) returned no .png entry: {[f['name'] for f in obj['files']]}")
        return base64.b64decode(png_entries[0]["data_base64"])


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


async def main():
    async with native_session() as native, community_session("compound") as community:
        opened = await timed_call(native, "run_script", {"script": OPEN_SCRIPT}, label="open_test1")
        print("open:", opened["result_preview"])
        if opened["is_error"] or '"page": null' in opened["result_preview"]:
            raise RuntimeError(f"failed to open {PROJECT}/{TIMELINE}: {opened['result_preview']}")

        try:
            await nudge_playhead(native, "on_before")
            on_before_arr = luma_array(await grab_still(community, "grain_on_before"))

            off = await timed_call(
                native, "run_script",
                {"script": set_enabled_script(False)},
                label="disable_grain",
            )
            print("disable_grain:", off["result_preview"])

            await nudge_playhead(native, "off")
            off_arr = luma_array(await grab_still(community, "grain_off"))

        finally:
            on = await timed_call(
                native, "run_script",
                {"script": set_enabled_script(True)},
                label="restore_grain",
            )
            print("restore_grain:", on["result_preview"])

            await nudge_playhead(native, "on_after")
            on_after_arr = luma_array(await grab_still(community, "grain_on_after"))

            await native.call_tool("run_script", {"script": RESTORE_SCRIPT})

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


if __name__ == "__main__":
    asyncio.run(main())
