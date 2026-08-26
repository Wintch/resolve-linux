#!/usr/bin/env python3
"""Synthetic I/O profile comparison via `fio`: gaming-shaped access vs editing-shaped access.

Built to answer a real question raised this session: this rig is a dual-boot gaming/VR
box (`reverb-g2`) as well as a Resolve editing box, sharing the same NVMe -- does that
NVMe (a DRAM-less Kingston NV1, see README.md/PERFORMANCE.md section 1a for its known
reliability caveats) behave meaningfully differently under the two workload shapes, or
is throughput throughput regardless of access pattern? `TestIO` (used elsewhere in this
repo, see `pipelines/effect-benchmark/`) only measures sequential-ish throughput; `fio`
is the right tool for a real IOPS/latency comparison at different block sizes and queue
depths, which is what actually distinguishes these two workload shapes.

Two profiles, deliberately simplified caricatures of each workload (not a claim these
exactly match either real workload, just plausible representative access patterns):
    - **editing**: sequential read AND write, large block size (4MB), low queue depth,
      single job -- matches streaming a video file off disk for playback/render, the
      shape this repo's `TestIO`/`render_benchmark.py` numbers already represent.
    - **gaming**: random read, small block size (4KB, matching typical NTFS/game-asset
      cluster size), higher queue depth, more jobs -- matches loading many small
      game/texture assets rather than streaming one big file.

Usage:
    ./io_profile_benchmark.py --path /mnt/resolve_test --list-jobs
    ./io_profile_benchmark.py --path /mnt/resolve_test                    # both profiles
    ./io_profile_benchmark.py --path /mnt/resolve_test --profile editing
    ./io_profile_benchmark.py --path /mnt/resolve_test --size 4G          # default 2G

Requires `fio` (`sudo apt install fio` -- not installed by this script; a system package
install is a deliberate, explicit action, not something to do silently as a side effect).
Uses `--direct=1` (bypass page cache, real hardware) by default -- see this repo's own
`TestIO` findings (README.md) for why cached numbers are misleading on this rig.

Results append to --out (default: io_profile_results.jsonl next to this script), one
JSON record per profile per run, same convention as the other benchmark tools here.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent / "io_profile_results.jsonl"

PROFILES = {
    "editing": {
        "rw": "rw",              # mixed sequential read+write, matches read-write TestIO row
        "bs": "4M",
        "iodepth": 4,
        "numjobs": 1,
        "rwmixread": 70,
    },
    "gaming": {
        "rw": "randread",
        "bs": "4k",
        "iodepth": 32,
        "numjobs": 4,
        "rwmixread": 100,
    },
    "manyfiles": {
        # Many small discrete files, not random offsets within one big file -- the real
        # access shape of game-asset loading OR a CinemaDNG/image-sequence clip (one file
        # per frame). Deliberately distinct from "gaming" above: that profile isolates
        # raw random-4K IOPS: this one isolates per-file-open overhead, which matters a
        # lot more on NTFS-3G (FUSE round-trip per syscall) than on native ext4 -- see the
        # CinemaDNG discussion in README.md's session log for why this was added.
        "rw": "read",
        "bs": "256k",           # ~one DNG-frame-sized read per file, not the whole file
        "iodepth": 16,
        "numjobs": 4,
        "rwmixread": 100,
        "nrfiles": 500,
        "filesize": "4M",        # 500 files x 4M = 2G total, plausible per-frame RAW size
    },
}


def run_fio(path: Path, profile_name: str, cfg: dict, size: str, direct: bool) -> dict:
    testfile = path / f"fio_{profile_name}.bin"
    cmd = [
        "fio",
        f"--name={profile_name}",
        f"--rw={cfg['rw']}",
        f"--bs={cfg['bs']}",
        f"--iodepth={cfg['iodepth']}",
        f"--numjobs={cfg['numjobs']}",
        f"--direct={1 if direct else 0}",
        "--ioengine=libaio",
        "--group_reporting",
        "--time_based=0",
        "--output-format=json",
    ]
    if "nrfiles" in cfg:
        # many-small-files mode: fio manages a directory of nrfiles x filesize each,
        # instead of one big --filename/--size file
        cmd += [f"--directory={path}", f"--nrfiles={cfg['nrfiles']}", f"--filesize={cfg['filesize']}"]
    else:
        cmd += [f"--filename={testfile}", f"--size={size}"]
    if cfg["rw"] == "rw":
        cmd.append(f"--rwmixread={cfg['rwmixread']}")

    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    wall_s = time.monotonic() - t0

    if "nrfiles" in cfg:
        for f in path.glob(f"{profile_name}.*.*"):
            f.unlink(missing_ok=True)
    else:
        testfile.unlink(missing_ok=True)

    record = {"profile": profile_name, "path": str(path), "wall_s": round(wall_s, 2), "config": cfg}
    if proc.returncode != 0:
        record["error"] = proc.stderr.strip()[-2000:]
        return record

    def _pct(lat_ns: dict, p: str):
        val = (lat_ns.get("percentile") or {}).get(p)
        return round(val / 1e6, 3) if val is not None else None

    try:
        data = json.loads(proc.stdout)
        job = data["jobs"][0]
        read, write = job.get("read", {}), job.get("write", {})
        record.update({
            "read_iops": read.get("iops"),
            "read_bw_mb_s": round(read.get("bw", 0) / 1024, 2),
            "read_lat_avg_ms": round(read.get("lat_ns", {}).get("mean", 0) / 1e6, 3) if read.get("lat_ns") else None,
            "read_lat_p99_ms": _pct(read.get("clat_ns", {}), "99.000000") if read.get("clat_ns") else None,
            "write_iops": write.get("iops"),
            "write_bw_mb_s": round(write.get("bw", 0) / 1024, 2),
            "write_lat_avg_ms": round(write.get("lat_ns", {}).get("mean", 0) / 1e6, 3) if write.get("lat_ns") else None,
            "write_lat_p99_ms": _pct(write.get("clat_ns", {}), "99.000000") if write.get("clat_ns") else None,
        })
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        record["parse_error"] = str(exc)
        record["raw_stdout_tail"] = proc.stdout[-2000:]

    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", type=Path, required=True, help="directory to run the I/O test in")
    ap.add_argument("--profile", choices=list(PROFILES), action="append", dest="profiles")
    ap.add_argument("--list-jobs", action="store_true")
    ap.add_argument("--size", default="2G")
    ap.add_argument("--no-direct", action="store_true", help="allow OS page cache (default is --direct=1, real hardware)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if args.list_jobs:
        for name, cfg in PROFILES.items():
            print(name, cfg)
        return

    if subprocess.run(["which", "fio"], capture_output=True).returncode != 0:
        print("fio not found -- install with: sudo apt install fio", file=sys.stderr)
        sys.exit(1)

    if not args.path.exists():
        print(f"{args.path} does not exist", file=sys.stderr)
        sys.exit(1)

    profiles = args.profiles or list(PROFILES)
    with args.out.open("a") as f:
        for name in profiles:
            record = run_fio(args.path, name, PROFILES[name], args.size, direct=not args.no_direct)
            record["ts"] = time.time()
            print(json.dumps(record, indent=2))
            f.write(json.dumps(record) + "\n")
            f.flush()


if __name__ == "__main__":
    main()
