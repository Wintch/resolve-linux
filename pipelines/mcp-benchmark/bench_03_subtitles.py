#!/usr/bin/env python3
"""
Validates DaVinci Resolve's own built-in auto-subtitle feature
(`Timeline.CreateSubtitlesFromAudio`) end-to-end -- per the instruction to
always reach for Resolve's own tooling first, this uses the raw scripting
API method directly via native `run_script`, not community's `timeline_ai`
wrapper around the same call.

First attempt (recorded live): calling it on `bench_tl` (8 silent "Solid
Color" generator clips, no audio at all) returned `False` and created no
subtitle track -- a real, correct result, not a silent no-op: the feature
genuinely needs actual audio to transcribe. So this fixture needs real
speech, which the benchmark project deliberately has none of (by design, to
avoid needing source footage).

Fix: synthesize a short spoken-word WAV with `espeak-ng` (already on this
system, no extra install) at runtime -- never commit the .wav itself, per
this repo's no-binary-blobs policy, and delete it again once the run is
done. Imports it into a *separate* timeline ("bench_tl_subs") so
bench_01/bench_02's assumptions about bench_tl's 8 silent clips stay
untouched.

**Second real finding, live**: writing the WAV to `/tmp` made `ImportMedia`
return an empty list with zero error signal -- looked exactly like another
silent no-op. Root cause: `resolve.GetMediaStorage().GetMountedVolumeList()`
shows Resolve only accepts source media from *registered Media Storage
volumes* (`/home/iam/Videos`, `/mnt/videos` on this rig) -- confirms this
repo's earlier render-output-path finding from the MCP-CAPABILITIES.md
workflow test also applies to plain source import, not just render targets.
Fixed by synthesizing into `/home/iam/Videos/` instead.

**Third finding, same debugging pass**: even from inside a registered volume,
`MediaPool.ImportMedia()` still silently returned an empty list for this exact
file -- `MediaStorage.AddItemListToMediaPool()` (the same call the Media
Storage browser panel uses) imported it correctly on the first try. Same
input, same registered volume, different result depending on which of two
documented import methods you call. Worked around by using
`AddItemListToMediaPool` here; `ImportMedia`'s failure on a plain valid WAV
is worth reporting upstream rather than treating as expected behavior.

**Fourth finding**: re-running against the *same* file path then failed too
-- looked like the fix from finding 3 had stopped working. It hadn't:
`AddItemListToMediaPool` dedups by path against what's already in this
project's Media Pool, and returns an empty list (not the existing item, not
an error) the second time you hand it a path it already imported, even
though the file on disk had been deleted and regenerated in between. Fixed
by giving each run's synthesized WAV a unique filename (uuid4 suffix) so it
never collides with a prior run's import.

Run: venv/bin/python bench_03_subtitles.py
Requires: setup_bench_project.py already run once, `espeak-ng` on PATH.
"""
import asyncio
import subprocess
import uuid
from pathlib import Path

SCRATCH_DIR = Path.home() / "Videos" / "mcp-bench-scratch"

from bench_lib import native_session, timed_call

SPEECH_TEXT = (
    "This is a synthetic test clip used to validate automatic subtitle "
    "generation in DaVinci Resolve."
)

SETUP_AND_RUN_SCRIPT = """
pm = resolve.GetProjectManager()
proj = pm.GetCurrentProject()
mp = proj.GetMediaPool()

existing = None
for i in range(1, proj.GetTimelineCount() + 1):
    t = proj.GetTimelineByIndex(i)
    if t.GetName() == "bench_tl_subs":
        existing = t
        break
tl = existing or mp.CreateEmptyTimeline("bench_tl_subs")
proj.SetCurrentTimeline(tl)

ms = resolve.GetMediaStorage()
items = ms.AddItemListToMediaPool([%(wav_path)r])
if not items:
    result = {"error": "AddItemListToMediaPool returned no items"}
else:
    appended = mp.AppendToTimeline([{"mediaPoolItem": items[0]}])
    track_before = tl.GetTrackCount("subtitle")
    ok = tl.CreateSubtitlesFromAudio()
    track_after = tl.GetTrackCount("subtitle")
    sub_items = tl.GetItemListInTrack("subtitle", 1) if track_after > 0 else []
    result = {
        "imported": bool(items),
        "appended_to_timeline": bool(appended),
        "create_subtitles_returned": ok,
        "subtitle_tracks_before": track_before,
        "subtitle_tracks_after": track_after,
        "subtitle_item_count": len(sub_items or []),
        "subtitle_item_names": [i.GetName() for i in (sub_items or [])],
    }
"""


def synthesize_speech(text: str) -> Path:
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = SCRATCH_DIR / f"subtitle_test_{uuid.uuid4().hex[:8]}.wav"
    subprocess.run(["espeak-ng", "-w", str(wav_path), text], check=True, capture_output=True)
    return wav_path


async def main():
    wav_path = synthesize_speech(SPEECH_TEXT)
    print(f"Synthesized speech: {wav_path} ({wav_path.stat().st_size} bytes)")

    try:
        script = SETUP_AND_RUN_SCRIPT % {"wav_path": str(wav_path)}
        async with native_session() as session:
            r = await timed_call(session, "run_script", {"script": script, "timeout": 30}, label="create_subtitles")

        print(f"\n{'ok':<5} {r['seconds']*1000:.1f}ms  {r['result_preview']}")
        if r["is_error"]:
            raise SystemExit("run_script errored -- see output above")
    finally:
        wav_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
