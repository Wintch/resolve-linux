#!/usr/bin/env python3
"""
Benchmark 1 -- simple read-only operations, native vs community MCP.

Four everyday asks, run REPS times each against both servers:
  1. Resolve version
  2. current project name
  3. timeline count
  4. current timeline name

Native has no per-action tool for any of these -- everything goes through
one `run_script` call. Community's compound server has a dedicated tool+
action per ask. This benchmark measures the per-call round-trip cost of
each approach for the simplest possible asks, where the community model's
one-call-per-answer design should be at its best relative to native.

Run: ~/resolve-install/davinci-resolve-mcp/venv/bin/python bench_01_simple.py
Requires: setup_bench_project.py already run once, Resolve already running
(this repo's own resolve_power.py / launch_resolve can start it).
"""
import asyncio
import statistics
from pathlib import Path

from bench_lib import community_session, native_session, select_fixture_timeline, timed_call, write_results

REPS = 10
RESULTS_FILE = Path(__file__).parent / "bench_01_results.jsonl"

NATIVE_OPS = {
    "resolve_version": "result = resolve.GetVersionString()",
    "project_name": "result = resolve.GetProjectManager().GetCurrentProject().GetName()",
    "timeline_count": "result = resolve.GetProjectManager().GetCurrentProject().GetTimelineCount()",
    "current_timeline_name": (
        "tl = resolve.GetProjectManager().GetCurrentProject().GetCurrentTimeline(); "
        "result = tl.GetName() if tl else None"
    ),
}

COMMUNITY_OPS = {
    "resolve_version": ("resolve_control", {"action": "get_version"}),
    "project_name": ("project_manager", {"action": "get_current"}),
    "timeline_count": ("timeline", {"action": "list"}),
    "current_timeline_name": ("timeline", {"action": "get_current"}),
}


async def bench_native():
    results = []
    async with native_session() as session:
        await select_fixture_timeline(session)
        for op_name, script in NATIVE_OPS.items():
            for _ in range(REPS):
                r = await timed_call(session, "run_script", {"script": script}, label=op_name)
                r["backend"] = "native"
                results.append(r)
    return results


async def bench_community(mode="compound"):
    results = []
    async with community_session(mode) as session:
        for op_name, (tool, args) in COMMUNITY_OPS.items():
            for _ in range(REPS):
                r = await timed_call(session, tool, args, label=op_name)
                r["backend"] = f"community-{mode}"
                results.append(r)
    return results


def summarize(results):
    by_key = {}
    for r in results:
        key = (r["backend"], r["label"])
        by_key.setdefault(key, []).append(r["seconds"])
    print(f"\n{'backend':<20} {'op':<24} {'median_ms':>10} {'mean_ms':>10} {'min_ms':>9} {'max_ms':>9}")
    for (backend, label), secs in sorted(by_key.items()):
        print(
            f"{backend:<20} {label:<24} "
            f"{statistics.median(secs) * 1000:>10.1f} "
            f"{statistics.mean(secs) * 1000:>10.1f} "
            f"{min(secs) * 1000:>9.1f} "
            f"{max(secs) * 1000:>9.1f}"
        )


async def main() -> int:
    all_results = []
    all_results += await bench_native()
    all_results += await bench_community("compound")

    write_results(RESULTS_FILE, all_results)

    errors = [r for r in all_results if r["is_error"]]
    if errors:
        print(f"\n{len(errors)} call(s) returned an error -- check {RESULTS_FILE}")
        for e in errors[:5]:
            print(" ", e["backend"], e["label"], e["result_preview"])

    summarize(all_results)
    print(f"\nRaw results: {RESULTS_FILE}")
    return 1 if errors else 0  # a timing taken over failed calls isn't a result


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
