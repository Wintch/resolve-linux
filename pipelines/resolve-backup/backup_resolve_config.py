#!/usr/bin/env python3
"""Timestamped snapshot backup of DaVinci Resolve's own project/config state.

A plain timestamped copy into a destination directory kept **outside any git repo** (the
source is a real user's project files and preferences, not something to publish), logging
what it found vs. skipped per item, with a warning if Resolve itself is running: Resolve
rewrites its config/database state on exit, so a snapshot taken while it is open can
capture a half-written state.

What gets backed up (small, typically a few tens of MB, cheap to snapshot often):
    - `Resolve Project Library/`  -- the project files (Disk Database mode; with the
      Postgres/multi-user database mode, back up the database instead)
    - `configs/`                  -- Resolve preferences
    - `Fusion/{Settings,Macros,Templates,Profiles,Layouts}/` -- user Fusion customizations
      (NOT `Fusion/Plugins`, `Fusion/Modules`, `Fusion/DiskCache`, etc. -- those are
      bundled/regenerable, not user state)
    - `Fairlight/Mixes/`          -- Fairlight mix state

Deliberately NOT backed up: source media (lives outside `~/.local/share/DaVinciResolve`),
`.LUT/` (by default only vendor-bundled LUT sets live there; add your own folder with
--extra), render cache/proxy media, logs, crash reports.

This is an **on-demand tool, not a cron/systemd job**; wiring it into a timer is a
deliberate choice left to the user. `../resolve-update/preflight_backup.sh` calls it before
an upgrade.

Usage:
    ./backup_resolve_config.py                  snapshot now, to the default destination
    ./backup_resolve_config.py --dest ~/somewhere
    ./backup_resolve_config.py --list            list existing snapshots and their sizes
    ./backup_resolve_config.py --keep 10         after snapshotting, delete older snapshots
                                                  beyond the most recent 10 (off by default)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

RESOLVE_DATA = Path.home() / ".local/share/DaVinciResolve"
DEFAULT_DEST = Path.home() / "resolve-backups"

ITEMS = {
    "project_library": RESOLVE_DATA / "Resolve Project Library",
    "configs": RESOLVE_DATA / "configs",
    "fusion_settings": RESOLVE_DATA / "Fusion" / "Settings",
    "fusion_macros": RESOLVE_DATA / "Fusion" / "Macros",
    "fusion_templates": RESOLVE_DATA / "Fusion" / "Templates",
    "fusion_profiles": RESOLVE_DATA / "Fusion" / "Profiles",
    "fusion_layouts": RESOLVE_DATA / "Fusion" / "Layouts",
    "fairlight_mixes": RESOLVE_DATA / "Fairlight" / "Mixes",
}


def resolve_running() -> bool:
    # same check as resolve_power.py -- pgrep -x "resolve" doesn't work: the process's
    # /proc/<pid>/comm is "GUI Thread", not "resolve"
    return subprocess.run(["pgrep", "-f", "bin/resolve$"], capture_output=True).returncode == 0


def du(path: Path) -> str:
    out = subprocess.run(["du", "-sh", str(path)], capture_output=True, text=True)
    return out.stdout.split()[0] if out.stdout else "?"


def snapshot(dest_root: Path, extra: list[str]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = dest_root / f"resolve-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)

    items = dict(ITEMS)
    for e in extra:
        items[f"extra_{Path(e).name}"] = Path(e).expanduser()

    for name, src in items.items():
        if not src.exists():
            print(f"  [absent] {name}: {src}")
            continue
        dst = dest / name
        shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
        print(f"  [ok]     {name}: {src} -> {dst}")

    return dest


def list_snapshots(dest_root: Path):
    if not dest_root.exists():
        print(f"no backups yet at {dest_root}")
        return
    snaps = sorted(p for p in dest_root.iterdir() if p.is_dir() and p.name.startswith("resolve-"))
    if not snaps:
        print(f"no backups yet at {dest_root}")
        return
    for s in snaps:
        print(f"{s.name}  {du(s)}")


def prune(dest_root: Path, keep: int):
    snaps = sorted(p for p in dest_root.iterdir() if p.is_dir() and p.name.startswith("resolve-"))
    for old in snaps[:-keep]:
        print(f"  pruning {old.name}")
        shutil.rmtree(old)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", type=int, default=None, help="after snapshotting, keep only the N most recent snapshots")
    ap.add_argument("--extra", action="append", default=[], help="extra path to include in the snapshot, repeatable")
    args = ap.parse_args()

    if args.list:
        list_snapshots(args.dest)
        return

    if resolve_running():
        print("WARNING: Resolve is currently running -- it may rewrite config/project "
              "state on exit, after this snapshot is taken. Close Resolve first for a "
              "guaranteed-consistent snapshot, or accept this one may be slightly stale.",
              file=sys.stderr)

    dest = snapshot(args.dest, args.extra)
    print(f"snapshot: {dest}  ({du(dest)})")

    if args.keep:
        prune(args.dest, args.keep)


if __name__ == "__main__":
    main()
