#!/usr/bin/env python3
"""Per-effect GPU/timing benchmark harness for DaVinci Resolve's scriptable timeline-item effects.

Seeded from a single manual measurement in an earlier session (`Stabilize()` on a compound
clip, timed by hand while polling `nvidia-smi` in a loop -- see README.md's "Effect-by-effect
API + performance map" section). This turns that one-off into a repeatable tool: connect via
the scripting API, call one or more named effects on a real timeline item, sample
`nvidia-smi` throughout each call, and append one JSON-lines record per effect per run to a
results file -- so results accumulate across sessions instead of living only in prose.

Effects that need a human first (Magic Mask needs clicks on the Color page before
`CreateMagicMask()` will do anything; Film Grain and other OFX-only effects have no
scripting-API entry point at all -- see README.md) are still invoked so the *call itself* and
its immediate return value are on record, but are expected to report `success: false` /
`needs_hitl: true` rather than real GPU work -- don't read a `false` here as this tool being
broken.

Usage:
    ./effect_benchmark.py --list
    ./effect_benchmark.py --effect stabilize --track video --index 1
    ./effect_benchmark.py --effect smart_reframe --track video --index 1
    ./effect_benchmark.py --effect voice_isolation_get --track audio --index 1
    ./effect_benchmark.py --effect voice_isolation_set --track audio --index 1 --amount 60
    ./effect_benchmark.py --effect magic_mask_create --track video --index 1 --mode F
    ./effect_benchmark.py --all --track video --index 1   # runs every video-track effect below

Results append to --out (default: effect_benchmark_results.jsonl next to this script) --
never overwritten, so a benchmark harness run today is directly comparable to one run after a
future driver/Resolve update.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.append("/opt/resolve/Developer/Scripting/Modules/")

DEFAULT_OUT = Path(__file__).resolve().parent / "effect_benchmark_results.jsonl"

NVIDIA_SMI_QUERY = "utilization.gpu,power.draw,clocks.sm"


def sample_gpu():
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5,
    )
    util, power, clock = (p.strip() for p in out.stdout.strip().split(","))
    return {"util_pct": float(util), "power_w": float(power), "sm_clock_mhz": float(clock)}


class GpuPoller:
    """Samples nvidia-smi at interval_ms via nvidia-smi's own `-lms` loop, in a real OS
    subprocess -- NOT a Python thread. A Python thread was tried first and silently produced
    only 1 sample regardless of the effect's real duration (1 sample for a 1.2s call, 1
    sample for a 19s call): the scripting API's blocking calls (`item.Stabilize()` etc.) go
    through `fusionscript.so`'s socket RPC, which does not release the GIL during its
    blocking receive, so a same-process sampling thread never gets scheduled until the call
    already returned. A subprocess's own internal timing isn't subject to our interpreter's
    GIL at all, so this is the reliable way to sample GPU state *during* a scripting-API call.
    """

    def __init__(self, interval_ms: int = 500):
        self.interval_ms = interval_ms
        self.samples = []
        self._proc = None

    def __enter__(self):
        self._proc = subprocess.Popen(
            ["nvidia-smi", f"--query-gpu={NVIDIA_SMI_QUERY}", "--format=csv,noheader,nounits",
             "-lms", str(self.interval_ms)],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        return self

    def __exit__(self, *exc):
        self._proc.terminate()
        try:
            out, _ = self._proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            out, _ = self._proc.communicate()
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 3:
                continue
            util, power, clock = parts
            try:
                self.samples.append({"util_pct": float(util), "power_w": float(power), "sm_clock_mhz": float(clock)})
            except ValueError:
                pass


def connect():
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("scripting API connect failed -- is Resolve running and not mid-playback?")
    return resolve


def get_item(resolve, track_type: str, track_index: int, item_index: int):
    proj = resolve.GetProjectManager().GetCurrentProject()
    if proj is None:
        raise RuntimeError("no current project open")
    tl = proj.GetCurrentTimeline()
    if tl is None:
        raise RuntimeError("no current timeline open")
    items = tl.GetItemListInTrack(track_type, track_index)
    if not items or item_index >= len(items):
        raise RuntimeError(f"no item at {track_type} track {track_index} index {item_index}")
    return tl, items[item_index]


def run_effect(name: str, item, amount: int, mode: str):
    """Returns (success_or_result, note) -- note explains a non-GPU / HITL outcome."""
    if name == "stabilize":
        return bool(item.Stabilize()), None
    if name == "smart_reframe":
        return bool(item.SmartReframe()), None
    if name == "magic_mask_create":
        ok = bool(item.CreateMagicMask(mode))
        note = None if ok else "needs_hitl: no Magic Mask clicks placed on Color page yet"
        return ok, note
    if name == "magic_mask_regenerate":
        return bool(item.RegenerateMagicMask()), None
    if name == "voice_isolation_get":
        if not hasattr(item, "GetVoiceIsolationState"):
            return None, "GetVoiceIsolationState not present (needs Resolve 20.1+)"
        return item.GetVoiceIsolationState(), None
    if name == "voice_isolation_set":
        if not hasattr(item, "SetVoiceIsolationState"):
            return None, "SetVoiceIsolationState not present (needs Resolve 20.1+)"
        state = {"isEnabled": True, "amount": amount}
        return bool(item.SetVoiceIsolationState(state)), None
    raise ValueError(f"unknown effect {name!r}")


EFFECTS = [
    "stabilize", "smart_reframe", "magic_mask_create", "magic_mask_regenerate",
    "voice_isolation_get", "voice_isolation_set",
]


def bench_one(resolve, effect: str, track_type: str, track_index: int, item_index: int,
              amount: int, mode: str) -> dict:
    tl, item = get_item(resolve, track_type, track_index, item_index)
    record = {
        "effect": effect,
        "item": item.GetName(),
        "track": f"{track_type}{track_index}",
    }
    t0 = time.monotonic()
    try:
        with GpuPoller() as poller:
            result, note = run_effect(effect, item, amount, mode)
        wall_s = time.monotonic() - t0
        samples = poller.samples
        record.update({
            "wall_s": round(wall_s, 3),
            "result": result,
            "note": note,
            "gpu_samples": len(samples),
            "gpu_util_max_pct": max((s["util_pct"] for s in samples), default=None),
            "gpu_power_max_w": max((s["power_w"] for s in samples), default=None),
            "gpu_sm_clock_max_mhz": max((s["sm_clock_mhz"] for s in samples), default=None),
        })
    except Exception as exc:
        record.update({"wall_s": round(time.monotonic() - t0, 3), "error": str(exc)})
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--effect", choices=EFFECTS)
    ap.add_argument("--all", action="store_true", help="run every effect in EFFECTS against the same item")
    ap.add_argument("--list", action="store_true", help="print known effects and exit")
    ap.add_argument("--track", default="video", choices=["video", "audio"])
    ap.add_argument("--index", type=int, default=1, help="1-based track index (Resolve convention)")
    ap.add_argument("--item", type=int, default=0, help="0-based item index within the track")
    ap.add_argument("--amount", type=int, default=60, help="voice_isolation_set amount, 0-100")
    ap.add_argument("--mode", default="F", help="magic_mask_create mode: F/B/BI")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if args.list:
        print("\n".join(EFFECTS))
        return

    if not args.effect and not args.all:
        ap.error("pass --effect NAME or --all")

    resolve = connect()
    effects = EFFECTS if args.all else [args.effect]
    with args.out.open("a") as f:
        for effect in effects:
            record = bench_one(resolve, effect, args.track, args.index, args.item, args.amount, args.mode)
            record["ts"] = time.time()
            print(json.dumps(record))
            f.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    main()
