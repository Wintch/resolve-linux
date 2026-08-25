#!/usr/bin/env python3
"""Takes the approved shortlist (analysis/shortlist.json), decodes each CR2
with darktable-cli (base correction: demosaic, white balance, exposure),
applies denoise (nlmeans via ffmpeg) and the chosen color preset, and leaves
the large result in graded/.

Usage: tools/04_grade_export.py [--preset concert] [--denoise nlmeans|hqdn3d|none] [--workers 4]

With --denoise nlmeans (default) the denoise strength adjusts itself based on
each photo's real ISO (read from the AVIF proxy's EXIF, already copied there
by 01_make_proxies): higher ISO, more sensor noise, more strength. Pass
--denoise hqdn3d/none, or --no-iso-denoise, to use a fixed filter on all of them.
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_preset import load_preset, build_filter_chain  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "analysis"
GRADED_DIR = ROOT / "graded"
BASE_DIR = GRADED_DIR / "_base"
PROXY_DIR = ROOT / "proxies_avif"

DENOISE_FILTERS = {
    "nlmeans": "nlmeans=s=3.0:p=5:r=7",
    "hqdn3d": "hqdn3d=4:3:6:4",
    "none": None,
}

# ISO-based denoise tiers, calibrated against a specific camera+lens combo
# (night event, one batch's dark frames). Eyeballed validation: below each
# tier's floor the noise already shows and more strength helps; no need to
# touch --denoise-iso-max unless the body/sensor changes.
ISO_DENOISE_BUCKETS = [
    (0, 400, "nlmeans=s=1.5:p=5:r=7"),
    (400, 1600, "nlmeans=s=3.0:p=5:r=7"),
    (1600, 6400, "nlmeans=s=4.5:p=7:r=9"),
    (6400, None, "nlmeans=s=6.5:p=7:r=11"),
]


def denoise_for_iso(iso):
    for lo, hi, filt in ISO_DENOISE_BUCKETS:
        if iso >= lo and (hi is None or iso < hi):
            return filt
    return ISO_DENOISE_BUCKETS[-1][2]


def read_iso_map(cr2_names):
    """ISO per file, read from the AVIF proxy's EXIF (01_make_proxies copies
    it over from the original CR2). If there's no proxy or exiftool can't
    find an ISO, the file is left out of the map and the caller falls back
    to the fixed default filter."""
    stems = [Path(f).stem for f in cr2_names]
    proxy_paths = [PROXY_DIR / f"{s}.avif" for s in stems]
    existing = [(s, p) for s, p in zip(stems, proxy_paths) if p.exists()]
    if not existing:
        return {}
    proc = subprocess.run(
        ["exiftool", "-ISO", "-T", "-s3"] + [str(p) for _, p in existing],
        capture_output=True, text=True,
    )
    values = proc.stdout.splitlines()
    iso_map = {}
    for (stem, _), val in zip(existing, values):
        val = val.strip()
        if val.lstrip("-").isdigit():
            iso_map[stem] = int(val)
    return iso_map


ROTATE_FILTERS = {0: None, 90: "transpose=1", -90: "transpose=2", 180: "transpose=1,transpose=1"}


def select_files(shortlist):
    """Returns {cr2_name: rotation_in_degrees}."""
    files = {}
    for g in shortlist["groups"]:
        approved = g.get("approved", g["suggested_keep"])
        if not approved:
            continue
        rotation = g.get("rotation", 0)
        group_files = g["files"] if g["type"] == "sequence" else [g["representative"]]
        for f in group_files:
            files[f] = rotation
    return dict(sorted(files.items()))


def export_base(cr2_name):
    stem = Path(cr2_name).stem
    out = BASE_DIR / f"{stem}.tif"
    graded_out = GRADED_DIR / f"{stem}.tif"
    if graded_out.exists() or out.exists():
        return out, None
    cr2_path = ROOT / cr2_name
    with tempfile.TemporaryDirectory(prefix="dt_cfg_") as cfgdir:
        proc = subprocess.run(
            [
                "darktable-cli", str(cr2_path), str(out),
                "--core", "--configdir", cfgdir,
                "--conf", "plugins/imageio/format/tiff/bpp=16",
            ],
            capture_output=True, text=True,
        )
    if proc.returncode != 0 or not out.exists():
        return None, f"darktable-cli failed on {cr2_name}: {proc.stderr[-300:]}"
    return out, None


def apply_look(base_tif, denoise_filter, look_filter, rotation=0):
    stem = base_tif.stem
    out = GRADED_DIR / f"{stem}.tif"
    if out.exists():
        return out, None
    rotate_filter = ROTATE_FILTERS.get(rotation)
    filters = [f for f in (rotate_filter, denoise_filter, look_filter) if f]
    vf = ",".join(filters)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(base_tif)]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-update", "1", "-frames:v", "1", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        if out.exists():
            out.unlink()  # don't leave truncated half-written files (e.g. disk full)
        return None, f"ffmpeg failed on {stem}: {proc.stderr[-300:]}"
    base_tif.unlink()  # the 16-bit base TIFF is no longer needed once graded
    return out, None


def process_one(cr2_name, denoise_filter, look_filter, rotation):
    base_out, err = export_base(cr2_name)
    if err:
        return None, err
    return apply_look(base_out, denoise_filter, look_filter, rotation)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", default="concert", help="color preset in tools/presets/look/")
    parser.add_argument("--denoise", default="nlmeans", choices=list(DENOISE_FILTERS))
    parser.add_argument("--no-iso-denoise", action="store_true",
                         help="use the fixed --denoise filter on every photo, without scaling by ISO")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--shortlist", default=str(ANALYSIS_DIR / "shortlist.json"))
    args = parser.parse_args()

    shortlist_path = Path(args.shortlist)
    if not shortlist_path.exists():
        raise SystemExit(f"{shortlist_path} doesn't exist. Run 03_score_and_shortlist.py and review the contact sheet first.")
    shortlist = json.loads(shortlist_path.read_text())

    file_rotations = select_files(shortlist)
    files = list(file_rotations.keys())
    if not files:
        raise SystemExit("The shortlist has no approved groups (approved/suggested_keep).")

    preset = load_preset(args.preset, category="look")
    look_filter = build_filter_chain(preset)
    fixed_denoise_filter = DENOISE_FILTERS[args.denoise]

    use_iso_denoise = args.denoise == "nlmeans" and not args.no_iso_denoise
    iso_map = read_iso_map(files) if use_iso_denoise else {}

    def denoise_for(cr2_name):
        if use_iso_denoise:
            iso = iso_map.get(Path(cr2_name).stem)
            if iso is not None:
                return denoise_for_iso(iso)
        return fixed_denoise_filter

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    GRADED_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Color preset: {preset['name']} - {preset['description']}")
    if use_iso_denoise:
        print(f"Denoise: ISO-adaptive ({len(iso_map)}/{len(files)} photos with EXIF available)")
    else:
        print(f"Denoise: {args.denoise} (fixed)")
    print(f"Photos to process: {len(files)}")

    # Each photo goes through export_base -> apply_look -> deleting the
    # intermediate end to end before starting the next one (instead of two
    # separate full passes). That way the _base/ peak stays bounded to ~workers
    # files in flight, not to the N photos of the whole batch.
    print("Processing (demosaic + denoise + color, per photo) ...")
    graded_results = {}
    errors = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = ex.map(
            lambda f: process_one(f, denoise_for(f), look_filter, file_rotations[f]),
            files,
        )
        for fname, (out, err) in zip(files, futures):
            if err:
                errors.append(err)
            else:
                graded_results[fname] = out

    print(f"  ok: {len(graded_results)}/{len(files)}")
    for e in errors:
        print(f"  ERROR: {e}", file=sys.stderr)

    manifest = {
        "preset": preset["name"],
        "denoise": "iso-adaptive" if use_iso_denoise else args.denoise,
        "files": sorted(str(p.name) for p in graded_results.values()),
    }
    (ANALYSIS_DIR / "graded_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"-> {GRADED_DIR} ({len(graded_results)} files)")
    print(f"-> {ANALYSIS_DIR / 'graded_manifest.json'}")


if __name__ == "__main__":
    main()
