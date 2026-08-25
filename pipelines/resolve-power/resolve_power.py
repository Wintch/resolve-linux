#!/usr/bin/env python3
"""Pin CPU/GPU to full performance while DaVinci Resolve is actually running, on this rig.

Self-contained Python port of the same idea as the sibling `reverb-g2` project's
`vr-power-setup.sh` (performance/saver toggle) and `vr-power-watchdog.py` (auto-switch based
on what's actually running) -- not a fork of that code and not a dependency on it, since
that project has to keep working standalone regardless of anything here. The only place this
script talks to `reverb-g2` at all is via `systemctl` on `vr-power-watchdog.service`'s *unit
name* (a runtime bracket, the same pattern that repo's own `q2rtx-power-sweep.sh` already
uses for a standalone measurement) -- if that unit isn't installed, everything else here
still works, just without the bracket.

Why this exists: `reverb-g2`'s watchdog only treats a `monado-service` process or a live
Proton game tree as "active" -- DaVinci Resolve editing/rendering doesn't trigger it at all,
so this rig can sit in the watchdog's `saver` floor (GPU capped to ~40% of max watts, CPU
governor `powersave`, EPP `power`, boost off) for an entire Resolve session with nothing
noticing. Confirmed live (2026-08-25): GPU pinned at 100W of 250W max, P8, while Resolve was
actively open.

A same-day batch-export sweep (light cut, no grading) found no measurable *export-speed*
difference between saver and full power -- but export wall-time is the wrong thing to
optimize for and hides exactly the risk that matters: a governor ramping or GPU boosting
with delay mid-scrub or mid-playback is the same non-deterministic-latency problem
`reverb-g2`'s own `vr-power-setup.sh` was written to eliminate for VR ("anything that can
add latency non-deterministically...can produce [a dropped frame], and none of it is
visible in an average"). A batch job's total time doesn't care if one frame took 3x longer
while a governor spun up; a human scrubbing the timeline does, every time. **Default to
`--watch` for the whole time Resolve is open for real editing/grading, not just for
exports** -- `reverb-g2` separately found idle GPU draw is identical capped vs. uncapped, so
there's no real power being saved by staying capped during a session anyway. An unattended
batch-export-only session is the one case where the export-speed finding above still holds
on its own.

This is a manual, on-demand tool ("corremos esto solo a veces para validar"), not a systemd
service -- reverb-g2's watchdog keeps running independently the rest of the time.

Usage:
    ./resolve_power.py              report current state (no root needed, changes nothing)
    sudo ./resolve_power.py --apply     pin CPU+GPU to full performance right now
    sudo ./resolve_power.py --saver     drop back to minimum watts
    sudo ./resolve_power.py --restore   put back whatever was there before --apply
    sudo ./resolve_power.py --watch     bracket reverb-g2's watchdog (if active), wait for a
                                         `resolve` process, --apply while it runs, --restore
                                         (and restart the watchdog if it was running) once
                                         Resolve exits or on Ctrl+C

Does NOT touch NVIDIA persistence mode, in either direction -- same reason as reverb-g2's
script: toggling it has been observed to disturb this GPU's attached desktop monitor's
modeset, and this rig's only monitor lives on the same card Resolve renders with.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WATCHDOG_UNIT = "vr-power-watchdog.service"
STATE_DIR = Path("/var/lib/resolve-power")
STATE_FILE = STATE_DIR / "saved-state"
RESOLVE_PATTERN = "bin/resolve$"

C_OK = "\033[1;32m"
C_WARN = "\033[1;33m"
C_DIM = "\033[2m"
C_OFF = "\033[0m"


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _write(path: str, value: str) -> bool:
    try:
        Path(path).write_text(value + "\n")
        return True
    except OSError:
        return False


def _glob_write(pattern: str, value: str) -> int:
    n = 0
    for p in Path("/").glob(pattern.lstrip("/")):
        if _write(str(p), value):
            n += 1
    return n


def nvidia_smi_query(field: str) -> str | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={field}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, check=True,
        ).stdout.strip().splitlines()
        return out[0] if out else None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def gpu_max_w() -> int | None:
    v = nvidia_smi_query("power.max_limit")
    return int(float(v)) if v else None


def gpu_min_w() -> int | None:
    v = nvidia_smi_query("power.min_limit")
    return int(float(v)) if v else None


def gpu_cur_w() -> int | None:
    v = nvidia_smi_query("power.limit")
    return int(float(v)) if v else None


def need_root() -> None:
    if os.geteuid() != 0:
        sys.exit(f"this needs root: sudo {sys.argv[0]} {' '.join(sys.argv[1:])}")


def resolve_running() -> bool:
    r = subprocess.run(["pgrep", "-f", RESOLVE_PATTERN], capture_output=True, text=True)
    return r.returncode == 0


def watchdog_active() -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", "--quiet", WATCHDOG_UNIT], capture_output=True
    )
    return r.returncode == 0


def watchdog_present() -> bool:
    r = subprocess.run(
        ["systemctl", "cat", WATCHDOG_UNIT], capture_output=True
    )
    return r.returncode == 0


def watchdog_stop() -> None:
    subprocess.run(["systemctl", "stop", WATCHDOG_UNIT], capture_output=True)


def watchdog_start() -> None:
    subprocess.run(["systemctl", "start", WATCHDOG_UNIT], capture_output=True)


def report() -> None:
    print("=== power state (this rig, for DaVinci Resolve) ===")
    gov = _read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    epp = _read("/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference")
    boost = _read("/sys/devices/system/cpu/cpufreq/boost")
    aspm_raw = _read("/sys/module/pcie_aspm/parameters/policy") or ""
    aspm = aspm_raw[aspm_raw.find("[") + 1 : aspm_raw.find("]")] if "[" in aspm_raw else aspm_raw

    def line(label: str, value: str | None, good: bool) -> None:
        color = C_OK if good else C_WARN
        print(f"  {label:<14} {color}{value or '?'}{C_OFF}")

    line("cpu governor", gov, gov == "performance")
    line("cpu epp", epp, epp == "performance")
    line("cpu boost", boost, boost == "1")
    line("pcie aspm", aspm, aspm == "performance")

    cur, mx = gpu_cur_w(), gpu_max_w()
    if cur and mx:
        pct = round(cur * 100 / mx)
        print(f"  {'gpu power':<14} {C_OK if pct >= 90 else C_WARN}{cur} W of {mx} W max ({pct}%){C_OFF}")
        persist = nvidia_smi_query("persistence_mode")
        print(f"  {'gpu persist':<14} {persist}")

    print(f"  {'resolve':<14} {'running' if resolve_running() else 'not running'}")
    if watchdog_present():
        print(f"  {'vr watchdog':<14} {'active' if watchdog_active() else 'inactive'} ({WATCHDOG_UNIT})")
    else:
        print(f"  {'vr watchdog':<14} {C_DIM}not installed on this box{C_OFF}")

    if os.geteuid() != 0:
        print(f"\n  {C_DIM}--apply needs root. Nothing above has been changed.{C_OFF}")


def _save_state_once() -> None:
    if STATE_FILE.exists():
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        "\n".join([
            f"governor={_read('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor') or ''}",
            f"epp={_read('/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference') or ''}",
            f"aspm_raw={_read('/sys/module/pcie_aspm/parameters/policy') or ''}",
            f"gpu_w={gpu_cur_w() or ''}",
            "",
        ])
    )
    print(f"saved previous state -> {STATE_FILE}")


def apply(gpu_limit_pct: int | None = None) -> None:
    need_root()
    _save_state_once()
    _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor", "performance")
    _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference", "performance")
    _write("/sys/devices/system/cpu/cpufreq/boost", "1")
    _write("/sys/module/pcie_aspm/parameters/policy", "performance")

    subprocess.run(["nvidia-smi", "-pm", "1"], capture_output=True)
    target = gpu_max_w()
    if gpu_limit_pct is not None and target:
        mn = gpu_min_w() or 0
        target = max(mn, int(target * gpu_limit_pct / 100))
    if target:
        subprocess.run(["nvidia-smi", "-pl", str(target)], capture_output=True)

    print("applied.\n")
    report()


def saver() -> None:
    need_root()
    _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor", "powersave")
    _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference", "power")
    _write("/sys/devices/system/cpu/cpufreq/boost", "0")
    _write("/sys/module/pcie_aspm/parameters/policy", "powersave")
    mn = gpu_min_w()
    if mn:
        subprocess.run(["nvidia-smi", "-pl", str(mn)], capture_output=True)
    print("saver applied.\n")
    report()


def restore() -> None:
    need_root()
    if not STATE_FILE.exists():
        sys.exit(f"no saved state at {STATE_FILE} -- nothing to restore")
    state = dict(
        line.split("=", 1) for line in STATE_FILE.read_text().splitlines() if "=" in line
    )
    _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor", state.get("governor") or "powersave")
    epp = state.get("epp")
    if epp:
        _glob_write("/sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference", epp)
    aspm_raw = state.get("aspm_raw", "")
    if "[" in aspm_raw:
        aspm = aspm_raw[aspm_raw.find("[") + 1 : aspm_raw.find("]")]
        _write("/sys/module/pcie_aspm/parameters/policy", aspm)
    gpu_w = state.get("gpu_w")
    if gpu_w:
        subprocess.run(["nvidia-smi", "-pl", gpu_w], capture_output=True)
    STATE_FILE.unlink(missing_ok=True)
    print("restored.")


def watch(poll_interval: int = 10, gpu_limit_pct: int | None = None) -> None:
    """Bracket the reverb-g2 watchdog (if present) and hold full performance for exactly as
    long as a `resolve` process is alive. Manual, foreground tool -- Ctrl+C cleans up too."""
    need_root()
    was_active = watchdog_present() and watchdog_active()
    if was_active:
        print(f"{WATCHDOG_UNIT} is active -- stopping it for the duration.")
        watchdog_stop()

    cleaned_up = False

    def cleanup(*_a) -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        print("\nrestoring previous power state...")
        if STATE_FILE.exists():
            restore()
        else:
            saver()
        if was_active:
            print(f"restarting {WATCHDOG_UNIT}...")
            watchdog_start()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("waiting for a `resolve` process to start (Ctrl+C to stop watching)...")
    while not resolve_running():
        time.sleep(poll_interval)

    print("Resolve detected -> applying performance mode.")
    apply(gpu_limit_pct)

    while resolve_running():
        time.sleep(poll_interval)

    print("Resolve exited.")
    cleanup()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="pin CPU+GPU to full performance now")
    g.add_argument("--saver", action="store_true", help="drop to minimum watts now")
    g.add_argument("--restore", action="store_true", help="restore the state saved before --apply")
    g.add_argument("--watch", action="store_true", help="bracket the VR watchdog and track a running Resolve process")
    p.add_argument("--gpu-limit", type=int, default=None, metavar="PCT", help="cap GPU watts to this percent of max instead of 100%% (used by --apply/--watch)")
    p.add_argument("--poll-interval", type=int, default=10, help="seconds between checks in --watch (default: 10)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.apply:
        apply(args.gpu_limit)
    elif args.saver:
        saver()
    elif args.restore:
        restore()
    elif args.watch:
        watch(args.poll_interval, args.gpu_limit)
    else:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
