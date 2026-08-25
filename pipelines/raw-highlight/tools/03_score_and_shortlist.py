#!/usr/bin/env python3
"""Scores each photo (sharpness + exposure) using the AVIF proxies, picks a
representative per burst group, decides whether the group is a "single"
(standalone photo) or a "sequence" (long burst for a motion micro-sequence),
and builds a contact sheet + shortlist.json for the user to review before
moving on to the grading stage.

Usage: tools/03_score_and_shortlist.py [--sequence-min 5] [--reject-percentile 10]
"""
import argparse
import io
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy.ndimage import laplace

ROOT = Path(__file__).resolve().parent.parent
PROXY_DIR = ROOT / "proxies_avif"
ANALYSIS_DIR = ROOT / "analysis"
PREVIEW_SIZE = 640  # working resolution for scoring (more than enough)


def decode_preview(avif_path):
    p = subprocess.run(
        ["magick", str(avif_path), "-resize", f"{PREVIEW_SIZE}x{PREVIEW_SIZE}", "ppm:-"],
        capture_output=True,
        check=True,
    )
    return Image.open(io.BytesIO(p.stdout)).convert("RGB")


def score_image(avif_path):
    im = decode_preview(avif_path)
    gray = np.asarray(im.convert("L"), dtype=np.float64)

    sharpness = float(laplace(gray).var())

    total = gray.size
    shadow_clip = float((gray < 5).sum()) / total
    highlight_clip = float((gray > 250).sum()) / total
    clip_fraction = shadow_clip + highlight_clip

    return {"sharpness": sharpness, "clip_fraction": clip_fraction}, im


def build_contact_sheet(entries, out_path, columns=8, tile=(220, 150)):
    rows = (len(entries) + columns - 1) // columns
    pad = 6
    label_h = 34
    cell_w, cell_h = tile[0] + pad, tile[1] + label_h + pad
    sheet = Image.new("RGB", (cell_w * columns, cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        font = ImageFont.load_default()

    for i, e in enumerate(entries):
        col, row = i % columns, i // columns
        x, y = col * cell_w, row * cell_h
        thumb = e["_thumb"].copy()
        thumb.thumbnail(tile)
        sheet.paste(thumb, (x, y))
        status = "SEQ" if e["type"] == "sequence" else "single"
        keep = "keep" if e["suggested_keep"] else "REJECT"
        label = f"b{e['burst_id']} {status} x{e['count']} score={e['score']:.0f} {keep}"
        draw.rectangle([x, y + tile[1], x + tile[0], y + tile[1] + label_h], fill="black")
        draw.text((x + 3, y + tile[1] + 3), label, fill="white", font=font)

    sheet.save(out_path, quality=90)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sequence-min", type=int, default=5,
                         help="minimum burst size to treat it as a motion micro-sequence")
    parser.add_argument("--reject-percentile", type=float, default=10,
                         help="score percentile below which a group is suggested for rejection")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    bursts = json.loads((ANALYSIS_DIR / "bursts.json").read_text())["bursts"]

    all_files = [f for b in bursts for f in b["files"]]
    avif_paths = {f: PROXY_DIR / f"{Path(f).stem}.avif" for f in all_files}
    missing = [f for f, p in avif_paths.items() if not p.exists()]
    if missing:
        raise SystemExit(f"Missing {len(missing)} AVIF proxies (e.g.: {missing[:3]}). "
                          f"Run tools/01_make_proxies_avif.sh first.")

    print(f"Scoring {len(all_files)} photos ({args.workers} workers)...")
    scores = {}
    thumbs = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fname, (metrics, im) in zip(all_files, ex.map(lambda f: score_image(avif_paths[f]), all_files)):
            scores[fname] = metrics
            thumbs[fname] = im

    # normalize sharpness 0..100 over the whole dataset so it's comparable
    sharp_vals = np.array([scores[f]["sharpness"] for f in all_files])
    s_min, s_max = sharp_vals.min(), sharp_vals.max()
    for f in all_files:
        s = scores[f]["sharpness"]
        norm_sharp = 100.0 * (s - s_min) / (s_max - s_min + 1e-9)
        clip_penalty = 100.0 * scores[f]["clip_fraction"]
        scores[f]["score"] = norm_sharp - clip_penalty

    groups = []
    for b in bursts:
        files = b["files"]
        best = max(files, key=lambda f: scores[f]["score"])
        gtype = "sequence" if b["count"] >= args.sequence_min else "single"
        groups.append({
            "burst_id": b["burst_id"],
            "type": gtype,
            "count": b["count"],
            "files": files,
            "representative": best,
            "score": scores[best]["score"],
            "sharpness": scores[best]["sharpness"],
            "clip_fraction": scores[best]["clip_fraction"],
            "start": b["start"],
            "end": b["end"],
        })

    all_scores = np.array([g["score"] for g in groups])
    threshold = np.percentile(all_scores, args.reject_percentile)
    for g in groups:
        g["suggested_keep"] = bool(g["score"] >= threshold)

    groups.sort(key=lambda g: g["score"], reverse=True)

    ANALYSIS_DIR.mkdir(exist_ok=True)
    shortlist_path = ANALYSIS_DIR / "shortlist.json"
    shortlist_path.write_text(json.dumps({
        "sequence_min": args.sequence_min,
        "reject_percentile": args.reject_percentile,
        "score_threshold": float(threshold),
        "groups": [{k: v for k, v in g.items()} for g in groups],
    }, indent=2))

    for g in groups:
        g["_thumb"] = thumbs[g["representative"]]
    contact_sheet_path = ANALYSIS_DIR / "shortlist_contact_sheet.jpg"
    build_contact_sheet(groups, contact_sheet_path)

    n_keep = sum(1 for g in groups if g["suggested_keep"])
    n_seq = sum(1 for g in groups if g["type"] == "sequence")
    print(f"Groups: {len(groups)}  (suggested keep: {n_keep}, sequences: {n_seq})")
    print(f"-> {shortlist_path}")
    print(f"-> {contact_sheet_path}")


if __name__ == "__main__":
    main()
