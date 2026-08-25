#!/usr/bin/env python3
"""Generates a chronologically-ordered (by burst_id) contact sheet for manually
reviewing which photos are rotated, since the camera used for this session
didn't record the EXIF orientation tag on any photo.

Usage: tools/04b_review_rotation.py
Output: analysis/rotation_review.jpg
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from importlib import import_module
score_mod = import_module("03_score_and_shortlist")

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "analysis"
PROXY_DIR = ROOT / "proxies_avif"


def main():
    shortlist = json.loads((ANALYSIS_DIR / "shortlist.json").read_text())
    groups = sorted(shortlist["groups"], key=lambda g: g["burst_id"])

    entries = []
    for g in groups:
        avif_path = PROXY_DIR / f"{Path(g['representative']).stem}.avif"
        thumb = score_mod.decode_preview(avif_path)
        entries.append({
            "burst_id": g["burst_id"],
            "type": g["type"],
            "count": g["count"],
            "score": g["score"],
            "suggested_keep": g["suggested_keep"],
            "_thumb": thumb,
        })

    out_path = ANALYSIS_DIR / "rotation_review.jpg"
    score_mod.build_contact_sheet(entries, out_path, columns=8, tile=(220, 150))
    print(f"-> {out_path} ({len(entries)} groups, chronological order)")


if __name__ == "__main__":
    main()
