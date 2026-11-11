#!/usr/bin/env python3
"""Hold CPU/GPU at full performance while DaVinci Resolve is running, then put back what was there.

Desktop power policies (a `powersave` governor, low EPP, boost off, a capped GPU power
limit, or a service that manages those) don't know Resolve exists, so a machine can sit at
its power floor for an entire editing session. This tool pins the CPU governor/EPP/boost,
PCIe ASPM and the NVIDIA power limit to full performance while a `resolve` process is
alive, and restores the previous values when it exits. If another service manages power
(a power-profile daemon, a custom watchdog), name it with `--pause-unit` so it is stopped
for the duration and restarted afterwards (only if it was active).

Why not only for exports: a light batch export showed no measurable speed difference
between capped and full power, but export wall-time hides the cost that matters during
editing -- a governor ramping or a GPU boosting late in the middle of scrubbing or
playback adds latency non-deterministically, and an average never shows it. Idle GPU draw
is the same capped or uncapped, so holding full power while Resolve is open costs little.
**Default to `--watch` for the whole time Resolve is open for real editing/grading.** An
unattended export-only session is the one case where staying capped is fine.

Manual, foreground, on-demand tool; not a systemd service.

Usage:
    ./resolve_power.py              report current state (no root needed, changes nothing)
    sudo ./resolve_power.py --apply     pin CPU+GPU to full performance right now
    sudo ./resolve_power.py --saver     drop back to minimum watts
    sudo ./resolve_power.py --restore   put back whatever was there before --apply
    sudo ./resolve_power.py --watch [--pause-unit NAME ...]
                                         wait for a `resolve` process, --apply while it
                                         runs, --restore once Resolve exits or on Ctrl+C;
                                         each --pause-unit is stopped for the duration and
                                         restarted afterwards if it was active
    sudo ./resolve_power.py --watch --adaptive
                                         same, but only go full power while a known-heavy
                                         OFX/ResolveFX effect (or a render) is on the
                                         current timeline, and drop back to the previous
                                         state the moment it isn't. See
                                         scan_timeline_for_heavy_effects() for the method
                                         and its blind spots before trusting it.

NVIDIA persistence mode: --apply turns it on (`nvidia-smi -pm 1`) so the power limit
sticks; nothing here ever turns it off, because toggling it can disturb the modeset of a
monitor attached to the same card.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path("/var/lib/resolve-power")
STATE_FILE = STATE_DIR / "saved-state"
RESOLVE_PATTERN = "bin/resolve$"

# OFX/ResolveFX tools measured (pipelines/effect-benchmark/) to cost real GPU time on an
# 8 GB mid-range card.
HEAVY_EFFECT_KEYWORDS = ("Relight", "Super Scale", "Speed Warp", "Noise Reduction", "Magic Mask")

RESOLVE_SCRIPT_API = "/opt/resolve/Developer/Scripting"
RESOLVE_SCRIPT_LIB = "/opt/resolve/libs/Fusion/fusionscript.so"

_TIMELINE_SCAN_SCRIPT = '''
import sys, json
sys.path.append("/opt/resolve/Developer/Scripting/Modules/")
try:
    import DaVinciResolveScript as dvr
except ImportError:
    print(json.dumps({"error": "no DaVinciResolveScript module"})); sys.exit(0)
r = dvr.scriptapp("Resolve")
if r is None:
    print(json.dumps({"error": "no connection"})); sys.exit(0)
proj = r.GetProjectManager().GetCurrentProject()
if proj is None:
    print(json.dumps({"error": "no project"})); sys.exit(0)
rendering = proj.IsRenderingInProgress()
tl = proj.GetCurrentTimeline()
if tl is None:
    print(json.dumps({"error": "no timeline", "rendering": rendering})); sys.exit(0)
tools_found = []
for t in range(1, tl.GetTrackCount("video") + 1):
    for it in tl.GetItemListInTrack("video", t):
        graph = it.GetNodeGraph()
        for i in range(1, graph.GetNumNodes() + 1):
            tools_found.extend(graph.GetToolsInNode(i) or [])
print(json.dumps({"tools": tools_found, "rendering": rendering}))
'''


def scan_timeline_for_heavy_effects(timeout: float = 6.0) -> bool | None:
    """True/False if the current timeline's clip node graphs were actually checked, None
    if it couldn't be determined (Resolve unreachable, nothing open, or the call timed out).

    Runs the actual scripting-API call in a subprocess with a hard timeout, on purpose:
    `dvr.scriptapp('Resolve')` blocks indefinitely while Resolve's GUI is mid-playback (see
    the parent README's "Known limitations on Linux"), and that must never hang this
    script's own --watch loop.

    **A render job in progress always reads as heavy, regardless of the node scan.**
    While a real render job runs, the node-graph scan below can come back empty for
    several polls in a row, which would drop the GPU to its floor *during the render
    itself* -- exactly backwards. `proj.IsRenderingInProgress()`
    is cheap and, unlike a full node-graph walk, has no reason to race with the render
    engine, so it's checked first and short-circuits straight to heavy=True.

    Known blind spots, all observed live:
    - This only sees per-clip node-graph tools (`TimelineItem.GetNodeGraph().GetToolsInNode()`).
      It does NOT see a Timeline-level grade (Color page's Clip/Timeline toggle) or a
      track-level effect -- both real, both confirmed to evade this exact check. A
      timeline that's only heavy at one of those levels reads as light here.
    - `GetToolsInNode()` still reports a tool on a node that's been bypassed via
      `SetNodeEnabled(i, False)` -- there is no `GetNodeEnabled()` to check the other
      direction. This function has no way to tell "heavy tool present but bypassed" from
      "heavy tool present and actually running", so it treats both as heavy. Deliberately
      the conservative direction (a false "go full power" costs watts; a false "stay
      capped" costs a stutter), but it means disabling a heavy node without removing it
      won't bring this back down to the previous tier.
    - The node-graph scan itself was observed to intermittently read as empty while a
      render was actively in progress (the bug the `IsRenderingInProgress()` short-circuit
      above exists to route around) -- treat a "no tools found" result taken *outside* of
      an active render with some caution too; it has not been proven reliable under all
      load, only under the idle/scrubbing case it was designed for.
    """
    env = {
        **os.environ,
        "RESOLVE_SCRIPT_API": RESOLVE_SCRIPT_API,
        "RESOLVE_SCRIPT_LIB": RESOLVE_SCRIPT_LIB,
        "DISPLAY": os.environ.get("DISPLAY", ":0"),
    }
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _TIMELINE_SCAN_SCRIPT],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return None
    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return None
    if data.get("rendering"):
        return True
    if "error" in data:
        return None
    tools = data.get("tools", [])
    return any(any(kw in tool for kw in HEAVY_EFFECT_KEYWORDS) for tool in tools)

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


def unit_active(unit: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True)
    return r.returncode == 0


def unit_present(unit: str) -> bool:
    r = subprocess.run(["systemctl", "cat", unit], capture_output=True)
    return r.returncode == 0


def unit_stop(unit: str) -> None:
    subprocess.run(["systemctl", "stop", unit], capture_output=True)


def unit_start(unit: str) -> None:
    subprocess.run(["systemctl", "start", unit], capture_output=True)


def report(pause_units: list[str] | None = None) -> None:
    print("=== power state (for DaVinci Resolve) ===")
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
    for unit in pause_units or []:
        if unit_present(unit):
            print(f"  {'pause unit':<14} {unit}: {'active' if unit_active(unit) else 'inactive'}")
        else:
            print(f"  {'pause unit':<14} {C_DIM}{unit}: not installed{C_OFF}")

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


def watch(poll_interval: int = 10, gpu_limit_pct: int | None = None, adaptive: bool = False,
          pause_units: list[str] | None = None) -> None:
    """Hold full performance for as long as a `resolve` process is alive, with each of
    `pause_units` stopped for the duration. Manual, foreground tool -- Ctrl+C cleans up too.

    Two modes:
    - default: unconditional full performance for the whole session (the documented
      recommendation for real editing/grading -- see the module docstring for why).
    - `adaptive=True`: only go full while `scan_timeline_for_heavy_effects()` finds a
      known-heavy OFX/ResolveFX tool actually on the current timeline; drop back to
      whatever was there before the moment it isn't. Re-checked every `poll_interval` while Resolve runs. An uncertain scan
      (Resolve mid-playback, no project/timeline open, timeout) leaves the current tier
      alone rather than flip-flopping on a guess.
    """
    need_root()
    paused = [u for u in (pause_units or []) if unit_present(u) and unit_active(u)]
    for unit in paused:
        print(f"{unit} is active -- stopping it for the duration.")
        unit_stop(unit)

    cleaned_up = False
    is_full = False  # only meaningful in adaptive mode; unused otherwise

    def cleanup(*_a) -> None:
        nonlocal cleaned_up
        if cleaned_up:
            return
        cleaned_up = True
        if STATE_FILE.exists():
            print("\nrestoring previous power state...")
            restore()
        elif not adaptive or is_full:
            # non-adaptive: we always apply() before this point, so a missing STATE_FILE
            # here would be a bug, not a real "nothing changed" case -- saver() is the
            # correct floor to fall back to.
            # adaptive-but-somehow-is_full: same bug case, same fallback.
            print("\nrestoring previous power state...")
            saver()
        # else: adaptive mode and we never applied full power -- nothing was ever
        # changed, so there is nothing to restore. Leave CPU/GPU state exactly as-is.
        for unit in paused:
            print(f"restarting {unit}...")
            unit_start(unit)
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print("waiting for a `resolve` process to start (Ctrl+C to stop watching)...")
    while not resolve_running():
        time.sleep(poll_interval)

    if not adaptive:
        print("Resolve detected -> applying performance mode.")
        apply(gpu_limit_pct)
        while resolve_running():
            time.sleep(poll_interval)
        print("Resolve exited.")
        cleanup()
        return

    print("Resolve detected -> adaptive mode: scanning the timeline before deciding.")
    while resolve_running():
        heavy = scan_timeline_for_heavy_effects()
        if heavy is True and not is_full:
            print("heavy effect found on the timeline -> applying full performance.")
            apply(gpu_limit_pct)
            is_full = True
        elif heavy is False and is_full:
            print("no heavy effect on the timeline anymore -> restoring the previous state.")
            restore()
            is_full = False
        elif heavy is None:
            print("could not scan the timeline this round (Resolve busy / no timeline open) -- leaving power tier as-is.")
        time.sleep(poll_interval)

    print("Resolve exited.")
    cleanup()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="pin CPU+GPU to full performance now")
    g.add_argument("--saver", action="store_true", help="drop to minimum watts now")
    g.add_argument("--restore", action="store_true", help="restore the state saved before --apply")
    g.add_argument("--watch", action="store_true", help="hold full performance while a Resolve process runs, restore it afterwards")
    p.add_argument("--adaptive", action="store_true", help="with --watch: only go full power while a known-heavy OFX/ResolveFX effect is actually on the current timeline, instead of unconditionally for the whole session (a running render always counts as heavy).")
    p.add_argument("--pause-unit", action="append", default=[], metavar="NAME", help="with --watch: systemd unit that manages power (e.g. a power-profile daemon) to stop while holding and restart afterwards if it was active; repeatable")
    p.add_argument("--gpu-limit", type=int, default=None, metavar="PCT", help="cap GPU watts to this percent of max instead of 100%% (used by --apply/--watch)")
    p.add_argument("--poll-interval", type=int, default=10, help="seconds between checks in --watch (default: 10)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.pause_unit and not args.watch and (args.apply or args.saver or args.restore):
        sys.exit("--pause-unit only makes sense together with --watch")
    if args.adaptive and not args.watch:
        sys.exit("--adaptive only makes sense together with --watch")
    if args.apply:
        apply(args.gpu_limit)
    elif args.saver:
        saver()
    elif args.restore:
        restore()
    elif args.watch:
        watch(args.poll_interval, args.gpu_limit, args.adaptive, args.pause_unit)
    else:
        report(args.pause_unit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
