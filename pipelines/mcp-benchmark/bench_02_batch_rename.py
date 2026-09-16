#!/usr/bin/env python3
"""
Benchmark 2 -- a real compound edit task, native vs community MCP: rename
every clip on bench_tl to "Clip 01".."Clip NN".

This is the case Benchmark 1 undersells. `timeline.get_items` on the
community server already batches name+start+end+duration into one call
(good design on their part) -- so a *read* of all clip info is one round
trip either way. What still forces multiple round trips on the community
side is any per-item *write*: there is no batch "rename these N items"
action, only `timeline_item` (action=set_name) for one item at a time. So a
rename pass costs:

  - community: 1 call to list items (get their ids) + N calls to rename
    each one = N+1 round trips.
  - native: 1 `run_script` call with a for-loop = 1 round trip, regardless
    of N.

Benchmark 1 already measured that a single native round trip costs ~12x a
single community round trip (community's compound-tool design has no fixed
sandbox-spinup tax, native's run_script does). This benchmark measures where
that per-call tax stops mattering: once a task needs enough per-item calls,
native's "one script, N items" approach wins on wall-clock despite its
higher fixed cost per call.

Resets clip names back to "Solid Color" at the end so re-running this (or
bench_01/setup_bench_project.py) starts from a known state.

Run: venv/bin/python bench_02_batch_rename.py
Requires: setup_bench_project.py already run once.
"""
import asyncio
import json
import statistics
import time
from pathlib import Path

from bench_lib import community_session, native_session, timed_call

REPS = 5
RESULTS_FILE = Path(__file__).parent / "bench_02_results.jsonl"

RENAME_SCRIPT = """
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
items = tl.GetItemListInTrack("video", 1)
names = []
for i, item in enumerate(items, start=1):
    item.SetName(f"Clip {i:02d}")
    names.append(item.GetName())
result = names
"""

RESET_SCRIPT = """
tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline()
items = tl.GetItemListInTrack("video", 1)
for item in items:
    item.SetName("Solid Color")
result = len(items)
"""


def _flatten(content):
    return "".join(getattr(c, "text", "") for c in content)


async def rename_pass_native(session):
    r = await timed_call(session, "run_script", {"script": RENAME_SCRIPT}, label="batch_rename")
    r["backend"] = "native"
    r["round_trips"] = 1
    return r


async def rename_pass_community(session):
    t0 = time.perf_counter()
    round_trips = 0
    is_error = False
    try:
        res = await session.call_tool(
            "timeline", {"action": "get_items", "params": {"track_type": "video", "index": 1}}
        )
        round_trips += 1
        items = json.loads(_flatten(res.content))["items"]
        for i, item in enumerate(items, start=1):
            r = await session.call_tool(
                "timeline_item",
                {"action": "set_name", "params": {"id": item["id"], "name": f"Clip {i:02d}"}},
            )
            round_trips += 1
            if getattr(r, "isError", False):
                is_error = True
    except Exception:
        is_error = True
    elapsed = time.perf_counter() - t0
    return {
        "label": "batch_rename",
        "backend": "community-compound",
        "seconds": elapsed,
        "round_trips": round_trips,
        "is_error": is_error,
    }


async def main():
    results = []

    async with native_session() as session:
        for _ in range(REPS):
            results.append(await rename_pass_native(session))

    async with community_session("compound") as session:
        for _ in range(REPS):
            results.append(await rename_pass_community(session))
        # leave the fixture in a known state for the next run
        await session.call_tool(
            "timeline", {"action": "get_items", "params": {"track_type": "video", "index": 1}}
        )

    # reset via native (one call, cheap) regardless of which backend ran last
    async with native_session() as session:
        await session.call_tool("run_script", {"script": RESET_SCRIPT})

    with RESULTS_FILE.open("w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    by_backend = {}
    for r in results:
        by_backend.setdefault(r["backend"], []).append(r)

    print(f"\n{'backend':<20} {'median_ms':>10} {'mean_ms':>10} {'round_trips':>12}")
    for backend, rows in sorted(by_backend.items()):
        secs = [r["seconds"] for r in rows]
        print(
            f"{backend:<20} {statistics.median(secs) * 1000:>10.1f} "
            f"{statistics.mean(secs) * 1000:>10.1f} {rows[0]['round_trips']:>12}"
        )

    errors = [r for r in results if r["is_error"]]
    if errors:
        print(f"\n{len(errors)} error(s) -- see {RESULTS_FILE}")

    print(f"\nRaw results: {RESULTS_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
