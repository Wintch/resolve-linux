#!/usr/bin/env python3
"""Groups CR2 files into bursts based on EXIF timestamp (with sub-seconds).

Usage: tools/02_detect_bursts.py [--threshold 0.6]
Writes analysis/bursts.json and analysis/exif_timestamps.json
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "analysis"


def load_timestamps():
    cr2_files = sorted(ROOT.glob("*.CR2"))
    if not cr2_files:
        sys.exit("No .CR2 files found in " + str(ROOT))

    cmd = ["exiftool", "-j", "-DateTimeOriginal", "-SubSecTimeOriginal"] + [
        str(f) for f in cr2_files
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    records = json.loads(out)

    entries = []
    for rec in records:
        name = Path(rec["SourceFile"]).name
        dt_str = rec.get("DateTimeOriginal")
        subsec = str(rec.get("SubSecTimeOriginal", "0")).zfill(2)[:2]
        if not dt_str:
            continue
        dt = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
        dt = dt.replace(microsecond=int(subsec) * 10000)
        entries.append({"file": name, "timestamp": dt.isoformat()})

    entries.sort(key=lambda e: e["timestamp"])
    return entries


def group_bursts(entries, threshold):
    bursts = []
    current = []
    prev_dt = None

    for entry in entries:
        dt = datetime.fromisoformat(entry["timestamp"])
        if prev_dt is not None and (dt - prev_dt).total_seconds() > threshold:
            bursts.append(current)
            current = []
        current.append(entry)
        prev_dt = dt
    if current:
        bursts.append(current)

    result = []
    for i, group in enumerate(bursts):
        result.append(
            {
                "burst_id": i,
                "files": [g["file"] for g in group],
                "count": len(group),
                "is_burst": len(group) > 1,
                "start": group[0]["timestamp"],
                "end": group[-1]["timestamp"],
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.6,
        help="max seconds between consecutive shots to consider them the same burst",
    )
    args = parser.parse_args()

    ANALYSIS_DIR.mkdir(exist_ok=True)

    entries = load_timestamps()
    with open(ANALYSIS_DIR / "exif_timestamps.json", "w") as f:
        json.dump(entries, f, indent=2)

    bursts = group_bursts(entries, args.threshold)
    with open(ANALYSIS_DIR / "bursts.json", "w") as f:
        json.dump({"threshold_seconds": args.threshold, "bursts": bursts}, f, indent=2)

    n_burst_groups = sum(1 for b in bursts if b["is_burst"])
    n_singles = sum(1 for b in bursts if not b["is_burst"])
    n_burst_photos = sum(b["count"] for b in bursts if b["is_burst"])
    print(f"Total photos: {len(entries)}")
    print(f"Groups: {len(bursts)}  (bursts: {n_burst_groups}, singles: {n_singles})")
    print(f"Photos inside bursts: {n_burst_photos}")
    print(f"Threshold used: {args.threshold}s")
    print(f"-> {ANALYSIS_DIR / 'bursts.json'}")


if __name__ == "__main__":
    main()
