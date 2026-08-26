#!/usr/bin/env python3
"""Render-profile benchmark: same timeline, multiple render presets, one comparable record each.

Companion to `effect_benchmark.py` (per-effect GPU/timing) -- this one measures the other
half of a real Studio workflow: actually exporting the current timeline through several
delivery presets/codecs, sampling `nvidia-smi` throughout each render via the same
subprocess-based `GpuPoller` (see that file's docstring for *why* it has to be a subprocess,
not a Python thread -- the scripting API's blocking calls don't release the GIL). Reuses
`GpuPoller` from `effect_benchmark.py` rather than duplicating it.

Built to answer a concrete ask this session: the user hand-applied a different real
grading effect to each of 4 clips on a new timeline (Noise Reduction x2, Film Grain, Analog
Damage -- see README.md's effect-benchmark session update for which clip has which),
wanted the actual render measured, and wanted it compared across more than one delivery
profile -- "todo mapeo completo para estudio", i.e. build out the render-side half of the
capability/performance map this repo has been assembling for Resolve Studio on this rig.

Usage:
    ./render_benchmark.py --list-presets
    ./render_benchmark.py --preset "H.264 Master" --preset "H.265 Master" --preset "ProRes 422 HQ"
    ./render_benchmark.py --preset "H.264 Master" --keep    # don't delete the output file after

Renders go to --outdir (default: a subdir of the one registered Media Storage volume on
this rig, /home/iam/Videos -- an arbitrary path fails per README.md's "Render output path
must be inside a registered Media Storage volume" finding). Deleted after `ffprobe`
verification unless --keep, same convention as every other throwaway render artifact in
this repo. Results append to --out (default: render_benchmark_results.jsonl next to this
script), same convention as effect_benchmark.py.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.append("/opt/resolve/Developer/Scripting/Modules/")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from effect_benchmark import GpuPoller  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parent / "render_benchmark_results.jsonl"
DEFAULT_OUTDIR = Path("/home/iam/Videos/render_benchmark_tmp")

TERMINAL_STATUSES = {"Complete", "Cancelled", "Failed"}


def connect():
    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise RuntimeError("scripting API connect failed -- is Resolve running and not mid-playback?")
    return resolve


def probe_output(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_name,width,height",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"ffprobe_error": out.stderr.strip()}


def bench_one_preset(proj, preset: str, outdir: Path, poll_s: float) -> dict:
    record = {"preset": preset}
    if not proj.LoadRenderPreset(preset):
        record["error"] = "LoadRenderPreset failed"
        return record

    outdir.mkdir(parents=True, exist_ok=True)
    stamp = f"benchmark_{preset.replace(' ', '_').replace('/', '-')}"
    ok = proj.SetRenderSettings({
        "SelectAllFrames": True,
        "TargetDir": str(outdir),
        "CustomName": stamp,
    })
    record["set_render_settings_ok"] = bool(ok)

    job_id = proj.AddRenderJob()
    if not job_id:
        record["error"] = "AddRenderJob returned no job_id"
        return record
    record["job_id"] = job_id

    t0 = time.monotonic()
    with GpuPoller() as poller:
        proj.StartRendering([job_id], False)
        status = {}
        while True:
            status = proj.GetRenderJobStatus(job_id)
            if status.get("JobStatus") in TERMINAL_STATUSES:
                break
            time.sleep(poll_s)
    wall_s = time.monotonic() - t0

    samples = poller.samples
    record.update({
        "wall_s": round(wall_s, 3),
        "job_status": status.get("JobStatus"),
        "resolve_time_taken_ms": status.get("TimeTakenToRenderInMs"),
        "gpu_samples": len(samples),
        "gpu_util_max_pct": max((s["util_pct"] for s in samples), default=None),
        "gpu_util_avg_pct": round(sum(s["util_pct"] for s in samples) / len(samples), 1) if samples else None,
        "gpu_power_max_w": max((s["power_w"] for s in samples), default=None),
        "gpu_sm_clock_max_mhz": max((s["sm_clock_mhz"] for s in samples), default=None),
    })

    out_files = sorted(outdir.glob(f"{stamp}*"))
    if out_files:
        f = out_files[0]
        record["output_file_size_mb"] = round(f.stat().st_size / 1e6, 2)
        record["ffprobe"] = probe_output(f)
    else:
        record["output_file_size_mb"] = None

    return record, out_files


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", action="append", dest="presets", default=[],
                     help="render preset name, repeatable (default: H.264 Master, H.265 Master, ProRes 422 HQ)")
    ap.add_argument("--list-presets", action="store_true")
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--poll-s", type=float, default=1.0)
    ap.add_argument("--keep", action="store_true", help="don't delete render output after ffprobe verification")
    args = ap.parse_args()

    resolve = connect()
    proj = resolve.GetProjectManager().GetCurrentProject()
    tl = proj.GetCurrentTimeline()

    if args.list_presets:
        print("\n".join(proj.GetRenderPresetList()))
        return

    presets = args.presets or ["H.264 Master", "H.265 Master", "ProRes 422 HQ"]

    print(f"# timeline: {tl.GetName()}", file=sys.stderr)
    with args.out.open("a") as f:
        for preset in presets:
            # clear any stale queued jobs so StartRendering only runs this one
            for jid in [j["JobId"] for j in proj.GetRenderJobList()]:
                proj.DeleteRenderJob(jid)
            record, out_files = bench_one_preset(proj, preset, args.outdir, args.poll_s)
            record["ts"] = time.time()
            record["timeline"] = tl.GetName()
            print(json.dumps(record))
            f.write(json.dumps(record) + "\n")
            f.flush()
            if not args.keep:
                for of in out_files:
                    of.unlink()


if __name__ == "__main__":
    main()
