# resolve-power

`resolve_power.py` holds the CPU and GPU at full performance while DaVinci Resolve runs,
and puts back the previous state when it exits. Desktop power policies (a `powersave`
governor, boost off, a capped NVIDIA power limit, or a service that manages those) don't
treat Resolve as "active", so without this a machine can spend a whole editing session at
its power floor.

What it sets while holding: CPU governor `performance`, EPP `performance`, CPU boost on,
PCIe ASPM `performance`, NVIDIA power limit at its maximum (or `--gpu-limit PCT` of it).
The previous values are saved to `/var/lib/resolve-power/saved-state` and restored.

## Usage

Needs root for everything except the report. Python standard library only; `nvidia-smi`
for the GPU part.

```bash
./resolve_power.py                         # report current state, changes nothing
sudo ./resolve_power.py --watch            # wait for Resolve, hold full power, restore on exit / Ctrl+C
sudo ./resolve_power.py --watch --pause-unit power-profiles-daemon.service
                                           # also stop a service that manages power, restart it after
sudo ./resolve_power.py --watch --adaptive # full power only while a heavy effect or a render is on the timeline
sudo ./resolve_power.py --apply | --saver | --restore   # one-shot changes
```

`--pause-unit` is repeatable; a unit is restarted afterwards only if it was active when
`--watch` started.

`--adaptive` connects to Resolve's scripting API every `--poll-interval` seconds (in a
subprocess with a timeout, because the connection blocks while Resolve is mid-playback)
and looks for known-heavy tools (Relight, Super Scale, Speed Warp, Noise Reduction, Magic
Mask) in the clips' node graphs. A running render always counts as heavy. It cannot see a
Timeline-level grade or a track-level effect, and a bypassed node still counts as present;
the docstring of `scan_timeline_for_heavy_effects()` lists the blind spots.

## Is it worth it?

Measured on an RTX 3060 Ti (250 W max) with a Ryzen 5 5600X, comparing a 100 W capped
floor with `powersave` against full power:

| Workload | Result |
|---|---|
| Light cut, batch export, no grading | ~0% export-speed difference |
| `SmartReframe()` (GPU-bound AI op) | ~20% faster at ~2.4x the power |
| Full-timeline H.265 NVENC render (16,172 frames) | ~25% faster (225.8 s at 100 W vs 180.1 s at 210 W) |

The export numbers are the smaller argument. During interactive editing, a governor
ramping up or a GPU boosting late adds latency non-deterministically while scrubbing or
playing back, which no average shows, and idle draw is the same capped or uncapped. Run
`--watch` for the whole time Resolve is open for real work; stay capped only for
unattended export-only sessions.
