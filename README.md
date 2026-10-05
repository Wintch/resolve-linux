# resolve-linux

Automating video editing on Linux, with [DaVinci Resolve](https://www.blackmagicdesign.com/products/davinciresolve)
and its [scripting API](https://github.com/samuelgursky/davinci-resolve-mcp) (via MCP) as
the primary path, and ffmpeg/Python pipelines for the parts that don't need Resolve at all
or exist to prepare footage for it. Install notes, gotchas, official codec/requirements
findings, MCP capability mapping, and standalone pipelines/guides live here together —
one repo, not one per topic.

## Goal

Get Resolve on Linux fully solved and ready to actually work with — install, gotchas,
every pipeline that's worth having, automated wherever automation makes sense. For each
editing task this covers, the aim is to have as many of these as apply:

1. **A guide for doing it well through Resolve itself** — including Resolve's own
   AI-assisted features (e.g. multicam auto-edit: detecting who's talking and cutting
   between camera angles automatically), not just the manual mechanics.
2. **Automation of that same task**, via the scripting API/MCP where Resolve's own tools
   can be driven programmatically instead of by hand, so it doesn't have to be redone
   through the GUI every time.
3. **A Resolve-free alternative** (ffmpeg/Python, or whatever fits) where one exists and
   is worth having — not every task can be replaced this way, but where it can, it means
   one less dependency on Resolve being installed/running/licensed at all.
4. **Free vs. Studio noted explicitly** wherever a feature is Studio-gated, so Free-edition
   users know what does and doesn't apply to them, and so a Resolve-free alternative is
   flagged as the practical path when the Studio-only route isn't available to them.

Resolve stays the primary tool here — not everything can or should be reimplemented as a
script — but every pipeline that *can* stand on its own outside Resolve gets to.

## What's in here

- **This README**: installing DaVinci Resolve Studio on Debian (unofficial, via
  `makeresolvedeb`), the real codec/RAM/GPU picture vs. Blackmagic's official spec, and
  everything found running it for real (see "Status" and the session logs below).
- **[MCP-CAPABILITIES.md](MCP-CAPABILITIES.md)**: the full tool/action catalog for
  AI-driven control of Resolve via its scripting API (37 tools, 718 actions as of the
  latest refresh); live probes and workflow tests against it live in
  [pipelines/mcp-benchmark/](pipelines/mcp-benchmark/).
- **[AI-VFX-RESEARCH.md](AI-VFX-RESEARCH.md)**: research log of AI/ML tools and
  techniques relevant to the post-production workflow on this rig — vetted community
  findings (Reddit/HuggingFace/docs) on open-weight models, local VFX pipelines, and
  techniques worth evaluating before committing to a production workflow. Current
  entries: Lightricks LTX-2.5 VFX toolset (7 IC-LoRAs: native-res editing, restore,
  SDR→HDR, native HDR, layout→render, alpha matte generation); MiniMax H3 360° orbit
  LoRA technique; Tencent Hunyuan AuK (1.5B speech generation/editing model — TTS,
  voice cloning, content/acoustic/paralinguistic editing, enhancement, separation,
  with GGUF port via `audio.cpp`).
- **[PERFORMANCE.md](PERFORMANCE.md)**: actionable hardware-performance guide for this
  rig specifically — storage (the big one: the current Media Storage path sustains only
  ~46MB/s write, ~35x slower than the NVMe mount sitting unused), RAM, cache placement,
  proxy workflow, VRAM, zram/swap. Not a session log — the summary to act on.
- **[pipelines/raw-highlight/](pipelines/raw-highlight/)**: a standalone, tool-agnostic
  guide + working ffmpeg/Python implementation for turning a RAW-heavy event shoot (photos,
  plus any RAW video clips) into a finished highlight video. Doesn't require Resolve at
  all — this is the "some things don't need it" half of the repo's scope. A second guide
  covering the same pipeline done through Resolve/MCP instead is planned to join it here.
- **[pipelines/prepare-for-resolve/](pipelines/prepare-for-resolve/)**: normalizes a
  folder of footage (VFR, interlacing, codecs Resolve-on-Linux can't decode) so it imports
  cleanly — a Python rewrite of a tool originally shared by Boris Kovalev on the Blackmagic
  forum, informed by this repo's own confirmed AAC-decode findings. **Validated against a
  real 10-file footage directory** (2026-08-25) — see "Session update: real project build"
  below and that folder's README.
- **[pipelines/resolve-power/](pipelines/resolve-power/)**: a self-contained Python tool
  that pins this rig's CPU/GPU to full performance while Resolve is actually running, and
  reports back to minimum watts otherwise — because the sibling `reverb-g2` project's own
  power watchdog only reacts to VR/game activity, not Resolve. See "Session update: real
  project build" below for why this exists and what the measured payoff actually is.
- **[pipelines/effect-benchmark/](pipelines/effect-benchmark/)**: two Python tools —
  `effect_benchmark.py` (per-effect GPU/timing, e.g. `Stabilize()`/`SmartReframe()`) and
  `render_benchmark.py` (per-delivery-profile render timing/GPU load) — built to answer
  "what does this cost on this GPU" for real, one JSON-lines record per run, not a
  one-off measurement. See "Effect-by-effect benchmark harness" and "Render-profile
  benchmark" below. (2026-09-17: both now share one `connect()` that probes the scripting
  connection in a throwaway subprocess first, so the mid-playback hang is a clear error
  instead of a frozen script; `render_benchmark.py` records a failed preset as an error row
  — it used to crash on the unpack — and gives up on a job after `--max-render-s`.)
- **[pipelines/resolve-backup/](pipelines/resolve-backup/)**: a self-contained Python tool
  that snapshots Resolve's own project files (Disk Database) and user config/Fusion/
  Fairlight state — ported from the same idea as `reverb-g2`'s `backup-steam-config.sh`
  (timestamped `cp -a`, kept outside git, warns if the live app is running). On-demand,
  not wired into cron/systemd yet.
- **[pipelines/mcp-benchmark/](pipelines/mcp-benchmark/)**: the Resolve automation layer —
  `resolve_client.py` (`ResolveClient`: native-first, raising, timeout-guarded client over
  both MCP servers, including import, stills and a render-with-loudness-gate wrapper),
  `validate_client.py` (its live end-to-end test), the native-vs-community benchmarks, and
  `resolve_mcp_wrapper.sh` (what the repo-root `.mcp.json` points MCP clients at). See
  "AI-driven control via MCP" below.
- **[pipelines/resolve-update/](pipelines/resolve-update/)**: procedure + scripts for
  updating the OS and Resolve itself (21.0.4 → 21.1) with a rollback path at every stage —
  repacked `.deb`s of the validated build, a project/config snapshot via `resolve-backup`,
  and a post-update NVIDIA/DKMS check that defers to `reverb-g2`'s own
  `pre-update-check.sh`, since the patched 595 driver is shared state with that project.
  Its README is upfront about the limit: no whole-disk snapshot is possible on this rig.

### Guide backlog — not built yet, tracked so it isn't lost

- **Multicam AI auto-edit**: Resolve's built-in feature for detecting who's speaking
  across synced camera angles and auto-cutting between them. Needs: a guide for doing it
  well by hand, an evaluation of how much of it the scripting API actually exposes for
  automation, and a call on whether a non-Resolve equivalent is even worth attempting
  (this one leans "hard to fully replace outside Resolve" — multicam sync + AI speaker
  detection isn't a quick ffmpeg job — but worth confirming rather than assuming).
- **Genuinely-trivial-timeline power floor, below what the VR watchdog sets**: not
  attempted — not measured yet whether this GPU has a useful floor below the ~100W the VR
  watchdog already parks it at, and `--adaptive` (built 2026-09-07, see "Content-aware
  dynamic power" below) only ever drops back to that existing floor, never lower.

Split out (2026-08-24) from an unrelated VR headset project (`reverb-g2`) on the same rig,
where this had been accumulating as a side note.

Rig: `iashur`, dedicated Debian 13 box also used for that unrelated VR project (see "Why
this matters" at the bottom). Install path used:
[makeresolvedeb](https://www.danieltufvesson.com/makeresolvedeb), which converts
Blackmagic's official `.run` installer into clean `.deb` packages for Debian — Blackmagic
doesn't ship a native `.deb`, and doesn't officially support Debian at all (see
"Official requirements vs. this rig" below).

## Status (2026-08-24)

- **Installed and launches.** Both packages (`davinci-resolve-studio` and
  `davinci-resolve-studio-data`, 21.0.4-mrd1.10.0) installed clean via `dpkg -i`, process
  starts and stays up, confirmed visually by the user on-screen. A targeted window
  screenshot of the Project Manager (kept local, not committed — described here instead)
  shows a clean render, title bar correctly reading "DaVinci Resolve Studio 21" (Studio
  edition active, not Free), empty local project database (expected, first launch), no
  crash dialogs or missing-library errors. No dongle/activation prompt at this screen —
  expected to surface on first render or explicit activation, not yet triggered.
  **Editing/color-grading quality has NOT been judged** — a launched process is not the
  same as "works well"; see "Why this matters" for why that distinction is the actual
  point of this exercise.
- **Real dependency gap found and fixed**: `libGLU.so.1` was missing (`libglu1-mesa`) —
  the binary wouldn't even load without it, failing immediately with "error while loading
  shared libraries." Not mentioned in any Blackmagic doc found; discovered live via `ldd`.
- **Activation dongle confirmed present and genuine**: `lsusb` shows `ID 096e:0201
  Feitian Technologies, Inc. USB DONGLE` — Feitian is Blackmagic's actual OEM hardware
  vendor for the Resolve Studio dongle, not a generic/clone brand.
- **RAM is under Blackmagic's official minimum for this rig — flagged as a main open
  risk.** See "Official requirements vs. this rig" below; this is not a minor gap.
- **The render pipeline reliably livelocks — the single biggest finding of this session,
  found through real editing/export testing, not just reading docs.** H.264/H.265 are
  entirely absent from the available render-codec list on this install (confirmed via
  the scripting API, every container checked); and independent of that, rendering with
  AAC audio, ProRes, or H.265 (selectable in the GUI despite not being API-visible) each
  triggered the same signature — no progress, no error, no timeout, a process pinned at
  ~90-99% CPU with zero I/O-wait threads (a genuine spin, not a blocked dialog), only
  recoverable by killing and relaunching. Reproduced 3 times via 3 different triggers,
  in both GUI and headless mode. **Investigation paused deliberately, not resolved** —
  full account and next steps in
  [pipelines/mcp-benchmark/README.md](pipelines/mcp-benchmark/README.md#final-data-point-before-stopping-thumbnails-stopped-rendering-too).
  Treat any render/export workflow on this rig as unverified until this is root-caused.
- **MCP (davinci-resolve-mcp v2.103.1) installed**, both the Python compound/granular
  server and the optional Node "advanced" (offline, no-Resolve-required) server, plus all
  optional AI extras — all six (`numpy`, `librosa`, `whisper`, `open_clip_torch`,
  `transformers`, `opencv-python`) confirmed `[OK]` by `scripts/doctor.py`, including
  CUDA-13-enabled PyTorch matching this rig's driver. See "AI-driven control via MCP"
  below for the full tool/capability mapping. **Live connection confirmed** — External
  scripting set to Local, `doctor.py` and a real scripting-API call both succeed against
  DaVinci Resolve Studio 21.0.4.5 (see that section for the live-test output). *(Superseded
  2026-08-25 — this was connection-only; see "Session update: real project build" below for
  the MCP actually driving a full project/import/edit/render workflow.)*
- **AI Extras pack finished downloading** (all 7 packages, ~24.4GB — IntelliSearch,
  Slate ID, Speech Generator, Voice Training, Extended Transcription language support,
  Tensor RT Engines). Not yet tested whether these specific features run well given the
  VRAM shortfall flagged above — downloaded and available, performance unverified.
- **Install-artifact cleanup done**: the ~18GB `.run`/`.deb`/build-script staging dir
  (`~/resolve-install/extract/`) was deleted once both packages were confirmed installed
  and working — nothing downstream needs it. The MCP checkout (`~/resolve-install/davinci-resolve-mcp/`,
  ~5.7GB incl. venv) was kept, it's a live install, not build cruft.

## Current known limitations on Linux (quick reference)

One table, so nobody has to re-read the whole session log to know what's actually broken
right now vs. what has a known workaround vs. what's a hard hardware/vendor ceiling. Full
investigation and evidence for each row lives in the section named in "Doc" — this is a
summary, not the source of truth; if a row here ever disagrees with its own section below,
the section wins. Two big items that used to be here are gone because they got physically/
structurally fixed, not just worked around: **RAM** (was 15GB vs. the 32GB official
minimum — resolved by a hardware upgrade to 31GB, see "Session update: RAM added...") and
the **render livelock** (was reliably reproducible — root-caused and fixed, see below).

| Limitation | Mitigation | Doc |
|---|---|---|
| **AAC audio decode/encode unsupported on Linux, any Resolve edition** | Transcode audio to PCM before import (`ffmpeg -c:v copy -c:a pcm_s16le`, or the full `prepare_for_resolve.py` pipeline for a whole directory) | "Codec support on Linux" |
| **H.264/HEVC decode/encode requires Studio + NVIDIA GPU — no CPU/software fallback on Linux** | Use Studio (not Free) with a working NVIDIA GPU path (see the CUDA/GPUDetect row below); if that's not available, pre-transcode to DNxHR with `ffmpeg` or the community `resolve_convert.sh` tool | "Codec support on Linux", "The bigger finding: H.264/H.265 aren't even in this install's render-codec list" |
| **GPUDetect fails to correlate CUDA under *-on-Wayland / XWayland-rootless → silently falls back to OpenCL → NVENC codecs missing from render list → render pipeline livelocks. This is a workaround, not a fix — Wayland itself is not, and cannot be, fixed from this repo.** | Log into any **native X11 session** and do a **full machine reboot** (switching session type alone, without a reboot, is not enough) — confirmed reproducible fix across two independent GNOME-on-Xorg reboots, and now confirmed **desktop-environment-agnostic**: a KDE Plasma-on-X11 session, first Resolve launch after a fresh boot, reproduced the identical good state (`supports CUDA 13.2`, `Matches: CUDA, NVML, OpenCL, XOrg`) with no extra dance needed — confirming the root cause is the X11-vs-Wayland protocol, not anything GNOME-specific. **Avoiding Wayland entirely, permanently, is the actual mitigation** — there is no known way to make CUDA detection work correctly under Wayland/XWayland-rootless on this rig; that would require Blackmagic fixing `GPUDetect` itself, not a client-side config change. If a future Resolve release fixes this, re-test before assuming it still applies. | "Session update: Xorg + reboot — render livelock RESOLVED", "Session update: fresh reboot re-confirms the CUDA/X11 fix", "Session update: KDE Plasma on X11 confirms the fix is DE-agnostic" |
| **Scripting API connection (`dvr.scriptapp('Resolve')`) blocks indefinitely while Resolve's GUI is mid-playback loop** | Make sure playback is stopped before any script/MCP call connects; no way to detect this from the API side ahead of time, the call just never returns | "Session update: fresh graphical session, playback retest" |
| **Blind GUI automation (`xdotool`/`import -window`) needs the window's on-screen offset added to click coordinates by hand, or clicks silently land on the wrong widget** | Get the real offset via `xdotool getwindowgeometry` or the window's `import -window` capture position before sending any `mousemove`; prefer the scripting API over blind clicks wherever an API method exists | "Blind GUI automation notes" (in "Session update: fresh graphical session, playback retest") |
| **No scripting-API method to add an OFX/ResolveFX filter (e.g. Film Grain) to a node graph** | Manual GUI step: Color page → select the clip/compound clip → Effects Library → OpenFX → drag the effect onto a node | "Real workflow, driven entirely through the scripting API" |
| **`CreateMagicMask()` does nothing without prior human clicks on the subject** | A human clicks the subject in the Color page's Magic Mask palette + presses Track Forward once; `RegenerateMagicMask()` can then be called via the API afterward | "Effect-by-effect benchmark harness, built and run" |
| **Super Scale, Speed Warp, Temporal/Spatial Noise Reduction have no dedicated scripting method** (same class of gap as Film Grain) | GUI-only for now; untried fallback is a pre-built `.drx`/PowerGrade template via `graph.apply_grade_from_drx` | "Effect-by-effect API + performance map — started, not finished" |
| **A failed/interrupted render doesn't recover cleanly via the API** — silent `None` returns, GUI dialogs block further calls with no signal, `Quit()` can hang on its own save-changes dialog | Only reliable recovery found is a full process kill + relaunch; treat an unexpected `None` as a probable blocked-dialog signal, not a clean failure | "The fork question — answered with a real workflow test, not just reading the code" |
| **Render output path must be inside a registered Media Storage volume** — any other path fails with a blocking "Render Path Inaccessible" dialog | Register the target directory as Media Storage (Preferences/Project Settings) before pointing a render job at it | "Bundled plugins, and what third-party plugins are actually worth trying on Linux" |
| **GNOME Mutter shows false "app not responding" dialogs during heavy Resolve activity on a RAM-tight box** | `gsettings set org.gnome.mutter check-alive-timeout 20000` (5s → 20s, applies live, no restart needed) | "Bundled plugins, and what third-party plugins are actually worth trying on Linux" |
| **VRAM below official minimums for AI tools (16GB) and background render (12GB)** — this rig has 8GB | Hard hardware ceiling, no software fix; budget for it when planning AI-heavy or background-render workloads | "Official requirements vs. this rig" |
| **Single-seat Resolve Studio license blocks any remote-viewing setup that needs a second Resolve instance** (e.g. desktop-side Remote Monitor viewer, "Resolve Live"-style collaboration) | Not actually needed here — the user decided to keep using the existing Moonlight/Sunshine full-desktop stream (already works well enough); a lighter `x11vnc`-scoped-to-the-window option was considered and explicitly not adopted. Free mobile app (iOS/Android, local-IP) remains the option if a phone/tablet viewer is ever wanted instead | "Remote monitoring while away from the rig — investigated, no Wine needed" |
| **NDI is not built into Resolve** | Needs the paid third-party Nobe Display plugin (with a separate NDI-output upgrade); skip unless broadcast-quality streaming is specifically needed | "Remote monitoring while away from the rig — investigated, no Wine needed" |
| **Third-party OFX plugin Linux support is inconsistent and vendor-specific** — e.g. Red Giant/Maxon ship no Linux build at all | Check per-vendor before relying on anything; Boris FX (Sapphire, Mocha Pro) is the one major suite confirmed with current official Linux builds | "Bundled plugins, and what third-party plugins are actually worth trying on Linux" |
| **Blackmagic gates the Linux download behind a free-account login** — can't be scripted/automated | One-time manual login + download, tied to the Studio dongle's account | "The one manual step that can't be scripted around" |
| **This rig's VR power-management watchdog doesn't recognize Resolve as "active"** — sits at its power floor (100W/powersave) for an entire Resolve session by default | Run `pipelines/resolve-power/resolve_power.py --watch` (needs root) to bracket the watchdog and hold full power only while Resolve runs. Whether it's worth it is workload-dependent, and the two real measurements taken so far disagree in magnitude: ~0% export-speed difference for a light cut, a real ~20%-faster/~2.4x-power trade for a GPU-bound AI op (`SmartReframe()`), and a **~25% faster full-timeline H.265 NVENC render** (225.8s -> 180.1s, same 16172-frame job, 100W capped vs 210W full) on 2026-09-07 — see the sections below for all three before assuming any single number generalizes | "Power: this rig's VR power-management setup doesn't know Resolve exists", "Effect-by-effect benchmark harness, built and run", "Session update: power-capped vs full-power render pacing, real timeline" |
| **Disk usage climbing** (87%/28GB free as of the last check) from validation sources and renders, and DNxHR exports run ~700GB/hour | Delete throwaway render/verification output right after `ffprobe` confirms it (already the practice here); prune validated sources once no longer needed; plan storage before any real DNxHR batch export | "Disk, again" |
| **Blackmagic only officially supports Rocky Linux 8.6** — this rig runs Debian 13, unofficially | `makeresolvedeb` builds working `.deb`s from the official `.run` installer for Debian/Ubuntu-family systems; works in practice, just not vendor-supported | "Official requirements vs. this rig", "Install" |

## Session update (2026-08-25): fresh graphical session, playback retest

Picked up the paused investigation with a brand-new graphical session (fresh login,
2 minutes of uptime, GPU clean at launch — 431MiB/8192MiB, 1% util) specifically to test
whether a full session restart (not just relaunching Resolve) would clear the broken
thumbnail/preview state the last session ended on. **It did.**

- **Thumbnail/preview rendering confirmed fixed by the session restart.** Reopened
  directly into the last project (`test1`). Media Pool clip thumbnails render as real
  frames again (not the generic music-note icon from last session's end state), and the
  Color-page viewer shows live video content, not black. This points at GPU/driver/GL
  context state (texture cache etc.) rather than something broken inside Resolve's own
  process — consistent with the theory noted at the end of the last session.
- **Playback under a real color grade is smooth and real-time, no livelock signature.**
  The user built a 3-node grade using **Film Look Creator** (Color Blend 0.659, Effects
  Blend 0.558, Film Look Blend 0.705, Core Look "Cinematic", plus Vignette/Halation/
  Bloom/Grain) specifically to soften the harsh look of 24fps footage. Two screenshots
  taken 3 seconds apart (real wall-clock) showed the playhead advancing from
  01:01:41:01 to 01:01:43:21 — i.e. tracking real time almost exactly, with genuinely
  different frame content each time (not a frozen/stale frame). Load during this was
  **GPU 22-48% util at ~4GB VRAM, CPU ~176-265% (of one process, ~2-2.6 cores)** — normal
  for live grading, nothing like the render livelock's ~90-99% CPU + zero I/O-wait spin
  signature. **This is playback only, not a render/export** — it doesn't yet confirm the
  render pipeline itself is fixed; see below.
- **Scripting API connection blocks while Resolve's UI thread is busy in an active
  playback loop.** `dvr.scriptapp("Resolve")` hung indefinitely (tested with an explicit
  timeout, twice) while the timeline was looping playback, then connected immediately
  (`exit 0`) once playback was stopped. Worth documenting as a real characteristic — not
  a preference regression, not a crash — but it means anything driving Resolve via the
  scripting API (including the MCP) should expect a stall if it tries to connect while
  the GUI is mid-playback, and has no way to know that from the API side (the call just
  never returns rather than erroring).
- **The two render jobs from the earlier livelock session are still sitting in the
  Render Queue panel, both marked "Job Cancelled"**, unchanged across the full session
  restart — confirms those really are the interrupted ProRes/H.264 jobs from before, not
  something that silently cleared itself.
- **Blind GUI automation notes** (for anyone scripting clicks against this app instead of
  using the scripting API): `import -window <id>` screenshots are captured 1:1 in the
  window's own coordinate space, but the window itself is offset on screen (`+0+29` here,
  a top panel) — clicks sent via plain `xdotool mousemove <x> <y>` need that offset added
  back in, or they silently land on the wrong widget with no error. Cost real time here:
  several clicks on the Deliver page's `File Name`/`Browse` controls did nothing because
  of exactly this. The **File Destination** picker (opened via `Browse`) is a custom
  tree browser, not a native file-chooser — the render `Location` field is **not**
  persisted from any project default and has to be picked via that dialog every time.
- **`/home/iam/Videos` (the registered Media Storage / render-output path) is at 85%
  disk usage** (`df`: 165G/207G used, 32G free) — up from the "54GB free" figure noted
  2026-08-24. Worth tracking given each ProRes export from this project alone estimates
  at 4.37GB.
- **Render/livelock retest not completed this session** — paused before actually
  starting a render (mid-way through configuring the Deliver-page job) because the user
  is about to add more RAM to the rig, a hardware change that needs the session stopped
  first. **Still the top open item**, unchanged from the prior pause: whether the session
  restart that fixed thumbnails/preview also fixed the render pipeline is genuinely
  unknown — GUI playback health is not evidence either way for the render path.
- **Plan for next session, per the user**: instead of hand-configuring render
  Location/frame-rate/etc. through the GUI every time (the exact tedium that ate most of
  this session's clock via the coordinate-offset issue above), bake those into project
  settings, a render preset, or a script — driven either by the scripting API (now
  confirmed reachable once Resolve isn't mid-playback) or the MCP. Not yet decided which;
  revisit once RAM is added and the session resumes.

## Session update (2026-08-25 continued): RAM added, render livelock reproduced and root-caused

RAM was physically added — `free -h` now reports **31Gi total** (up from 15GB), meeting
Blackmagic's 32GB minimum in practice. Resumed the paused render/livelock retest, this
time driving Resolve entirely through the scripting API (per the plan above) instead of
hand-clicking the Deliver page: deleted the two stale "Job Cancelled" jobs, loaded the
"ProRes 422 HQ" preset, set target dir/filename via `SetRenderSettings`, `AddRenderJob`,
`StartRendering`.

**The livelock reproduced identically, even with RAM no longer under spec** — this
decouples the render livelock from the RAM-shortfall risk documented above; they are two
separate issues, not one. Signature: `CompletionPercentage` stuck at 0 while
`EstimatedTimeRemainingInMs` climbed without bound (54s → over 1,000,000,000ms across
~2 minutes), GPU utilization ~0%, no output file ever created in `/home/iam/Videos`, and
`StopRendering()` via the scripting API did not unstick it — the process kept spinning
after the call returned.

**Root cause, isolated via `gdb -p <pid> -batch -ex "thread apply all bt"` on the
100%-CPU thread**: every resolved frame is inside `/lib/x86_64-linux-gnu/libnvidia-opencl.so.1`,
never returning. Resolve's own `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`
explains why: at startup, `GPUDetect` logs `Detected 1 GPUs: ... "NVIDIA GeForce RTX 3060
Ti" ... Matches: NVML, OpenCL` — **no CUDA** — so `Main.GPUConfig` has only OpenCL to pick
for "Compute API set to automatic". Preferences confirms this from the GUI side too: CUDA
isn't offered as a selectable option at all, only OpenCL — consistent with GPUDetect
never having matched it, not with Resolve merely preferring OpenCL.

Verified this is **not** a real CUDA absence: calling the CUDA Driver API directly via
Python `ctypes` against `libcuda.so.1` — `cuInit`, `cuDriverGetVersion` (13.2),
`cuDeviceGetCount`, `cuDeviceGetName`, `cuDeviceGetPCIBusId` (`0000:05:00.0`),
`cuDeviceComputeCapability` (8.6, correct for Ampere) — all succeeded cleanly, standalone,
outside Resolve. `nvidia-driver-cuda` and matching `libcudart.so.12`/`libcuda.so.595.71.05`
are installed and consistent with the 595.71.05 driver. So the CUDA stack itself is
healthy; the failure is specific to Resolve's bundled `libgpudetect.so`, which contains
error strings `"Failed to find CUDA Driver API device ID for {}"` and `"Failed to query
PCI Bus ID to the CUDA Driver API."` — i.e. GPUDetect can't correlate the CUDA-reported
device with the NVML/OpenCL-reported device, even though each individually works.

**Leading hypothesis, not yet confirmed**: this correlation failure traces to an earlier
log line, `GPUDetect ERROR | No Main Display GPU found and no monitors found to match,
defaulting to gpu:...`, logged before any GPU work starts. Confirmed this rig's graphical
session is **GNOME on Wayland**, with Resolve running as an **XWayland-rootless** app
(`Xwayland :0 -rootless ...`) — `xrandr` does report a monitor via XWayland's RandR
emulation, but that emulation likely doesn't expose the GPU↔output/PCI correlation the
way a native Xorg session with the NVIDIA X driver does, which would explain why NVML/
OpenCL (independent of that correlation) match fine while CUDA (which needs it) doesn't.
This points at the **display session type**, not the patched `nvidia-open` 595.71.05
driver itself, as the likely culprit — notably different from this rig's original purpose
of stress-testing that driver.

**Two-tier fix plan, agreed with the user — cheaper/lower-risk option first**:
1. **(Prepared, not yet run)** Log into the **"GNOME on Xorg"** session (`gnome-xorg.desktop`,
   confirmed available at the login screen) instead of the default Wayland session, relaunch
   Resolve, and check whether `GPUDetect`'s `Matches:` line now includes CUDA. No driver
   change, fully reversible by logging back into the Wayland session afterward. Verification
   script staged at `/tmp/claude-1000/.../scratchpad/verify_cuda_under_xorg.sh` (session-local
   scratchpad, not committed) — relaunches Resolve, greps the fresh debug log for the
   `Matches:`/compute-API lines, and if CUDA is now offered, re-runs the same render-job
   scripting steps used above to confirm the render actually completes.
2. **(Reserved, only if #1 fails)** Test the **proprietary `nvidia-driver` package instead
   of `nvidia-open`** to see if GPUDetect's CUDA correlation works against the standard
   kernel module. Higher-risk: this rig's `nvidia-open` driver was specifically installed
   to validate the patched/open kernel module for the unrelated VR project (`reverb-g2`),
   which depends on staying on that variant — so this option needs explicit confirmation
   before touching it, and should be scoped/timed to not disrupt that project.

**Two external research passes done (web search) before deciding what to report to
Blackmagic** — deliberately not filing anything upstream yet, per the user, until the
root cause is nailed down precisely:
- Multiple independent Linux/Resolve community sources (ArchWiki, Linux Mint/Manjaro
  forums, a 2026 Medium writeup) converge on "NVIDIA + OpenCL on Resolve/Linux is
  unstable, switch Compute API to CUDA" as a general pattern — corroborates the
  OpenCL-is-the-symptom direction, but none reproduce this exact livelock signature.
  A relevant thread was flagged: Blackmagic forum "Resolve Routinely Hangs During
  Export" (`t=210537`) — worth reading in full before any upstream report.
- No evidence found of a codec being removed in Resolve 21.x on Linux (reviewed 21.0.2–
  21.0.4 changelogs) — the user's initial "codec removal" hypothesis is not supported.
  One tangential but relevant find: a separate documented Blackmagic-forum bug where
  Resolve's `gpudetect` hangs because it loads Intel oneAPI's `libOpenCL.so` incorrectly
  (fixed by renaming that file) — different mechanism, but confirms GPUDetect/OpenCL-path
  hangs are a recurring, real category of bug on Linux, not a one-off.
- A generic (non-bug-specific) Blackmagic forum thread the user pasted in ("Resolve on
  Linux", started Jan 2024) added no new evidence on this specific issue, beyond
  reinforcing the general community sentiment that CUDA is preferred over OpenCL on
  Resolve/Linux.

Left the hung Resolve process running (not killed) as of this write-up, in case further
inspection is wanted before the Xorg-session test.

## Session update (2026-08-25, later): Xorg + reboot — render livelock RESOLVED

Ran tier 1 of the fix plan above: logged into **GNOME on Xorg** (confirmed via
`$XDG_SESSION_TYPE=x11` and no `Xwayland` process in `ps aux` — a native X11 session,
not XWayland-rootless). First pass, done in-place without a reboot, was **not** enough on
its own: `ResolveDebug.txt` still showed `NVIDIA GPU Driver: 595.71, supports CUDA -1.-1`
and `Matches: NVML, OpenCL, XOrg` — CUDA absent, still defaulting to OpenCL, even though
GPUDetect's monitor-correlation error was gone (`XOrg` now matched, unlike the Wayland
session's "no monitors found"). The `-1.-1` CUDA version is the tell: the CUDA driver
library itself hadn't come up cleanly, independent of session type.

A **full machine reboot**, still into the Xorg session, fixed it: fresh
`ResolveDebug.txt` shows `NVIDIA GPU Driver: 595.71, supports CUDA 13.2`, `Matches: CUDA,
NVML, OpenCL, XOrg`, and `Compute API set to automatic, defaulting to CUDA.` So the real
fix is **Xorg session + reboot**, not Xorg alone — something about the driver/kernel
module state from the prior Wayland session (or from switching sessions live) left CUDA
half-initialized until a clean boot.

**Render livelock confirmed fixed**, tested via the scripting API against the same
`test1` project/timeline used in the original livelock reproduction:
- **ProRes 422 HQ** (the exact preset that livelocked before): job completed in **1.2s**
  (`TimeTakenToRenderInMs: 1213`), no stuck `CompletionPercentage`, no runaway
  `EstimatedTimeRemainingInMs`. Output verified with `ffprobe`: valid 1920x1080 ProRes
  QuickTime, 6.67s, ~100MB, PCM audio.
- **H.264 NVIDIA** (a codec that was **entirely absent** from `GetRenderCodecs()` before —
  confirmed via the API at the time): now present (`GetRenderCodecs("MP4")` lists
  `H.264 NVIDIA` and `H.265 NVIDIA`), and a real render job with it completed in **1.18s**.
  Output verified: valid 1920x1080 H.264 MP4, ~14MB, plays back correctly. **H.265 NVIDIA**
  is listed too (not yet render-tested, but same NVENC code path as H.264 — high
  confidence it works).

This means the earlier "H.264/HEVC decode/encode on Linux requires Studio + NVIDIA GPU
with no documented software fallback" finding was accurate about the *hard requirement*
but incomplete about *this rig's failure mode*: the codec wasn't missing from Resolve, it
was gated behind GPUDetect's broken CUDA correlation — fix that, and NVENC H.264/H.265
export is available.

**Practical takeaway for anyone hitting this**: if `GPUDetect`'s `Matches:` line is
missing `CUDA` and `Main.GPUConfig` defaults to OpenCL on an NVIDIA-driver Linux box,
check the driver-reported CUDA version in the same log line first
(`supports CUDA X.Y` vs `supports CUDA -1.-1`) — a `-1.-1` means the CUDA stack itself
isn't up yet, and switching desktop session type without a reboot may not clear it.
**Tier 2 of the fix plan (swapping `nvidia-open` for the proprietary `nvidia-driver`
package) turned out to be unnecessary** — closing that option out.

Verification render artifacts (`/home/iam/Videos/x11_cuda_retest/`) deleted after
`ffprobe` confirmation — throwaway test output, not project media, and disk was already
at 85% (see above). The `test1` project still has one stale "Job 1" (`Job Cancelled`,
`livelock_retest_20260825.mov`) left over from a same-day pre-reboot attempt — harmless,
can be cleared from the Render Queue panel whenever.

**Still open**: editing/color-grading *quality* under this fixed config hasn't been
separately re-judged (the prior session's smooth-playback finding predates this reboot);
worth a real hands-on editing pass now that both playback and render are confirmed
healthy, not just launching.

## Session update (2026-08-25, later still): real project build via MCP, audio/interlace prep validated, power findings

Everything above through the CUDA fix was launch/render-plumbing validation. This pass is
the thing that was actually still missing: **the MCP driving a full real workflow** (new
project → real footage → import → cut → timeline-level grade prep → render), not just a
connection check — and a first look at whether this rig's VR power-management setup helps
or hurts Resolve.

### Real workflow, driven entirely through the scripting API

- **New project** (`resolve_linux_validation`) created via `ProjectManager.CreateProject`,
  not reused from `test1` — a clean validation target.
- **Real source footage**: 10 files from `~/Videos/oldback_nvme` (H.264/HEVC video, AAC
  audio, matching this repo's own codec findings — the same directory
  [pipelines/mcp-benchmark/README.md](pipelines/mcp-benchmark/README.md) used for the
  original AAC-decode-failure finding).
- **`prepare_for_resolve.py` run for real, not just `--dry-run`**, against the whole
  directory (`pipelines/prepare-for-resolve/prepare_for_resolve.py ~/Videos/oldback_nvme
  --output-dir ~/Videos/resolve_validation_sources`): all 10 files correctly identified as
  AAC (needs transcode to `pcm_s24le`); **zero interlaced sources in this batch**, but the
  `yadif` deinterlace branch (the "old `.sh` script" logic — see the file's own
  docstring/credit section) is unexercised here only because none of these particular files
  needed it, not because it's unimplemented. All 10 processed in 5.5s (video stream-copied,
  only audio transcoded — cheap), verified with `ffprobe`: every output's audio stream is
  `pcm_s24le`, matching the fix already confirmed at the log level in the original AAC
  finding.
- **Real correctness bug found and fixed in the VFR path, flagged by the user immediately
  after the first run**: one file (`video_2025-12-31_14-53-07.mp4`) was flagged VFR and the
  script forced it to a hardcoded global default of **24fps** — but that source's actual
  average is **~30.019fps** (`r_frame_rate` exactly `30/1`, `avg_frame_rate` a hair off from
  timestamp jitter, enough to trip `is_vfr()`'s exact-fraction check). Forcing a ~30fps
  source down to 24fps for no reason tied to the source itself is a real motion-quality
  regression (dropped/duplicated frames), not a neutral normalization — the user's point
  exactly: "si se filma en 25 constantes, es por algo, no lo forzamos a 24 porque sí." Fixed
  two ways: (1) `--framerate`'s default is now `None` — when unset, a VFR source's own
  average is snapped to the *nearest standard rate* (23.976/24/25/29.97/30/48/50/59.94/60)
  instead of a single hardcoded value, so a ~30fps source now correctly targets 30fps, not
  24; (2) the forced `-r` was previously applied to **every** video transcode, including
  ones triggered only by a bad codec on an already-constant-rate source — now `-r` is only
  emitted when a VFR fix is actually the reason for the transcode. A **second bug** this
  surfaced along the way: `default_video_codec()` (the NVENC-preference logic above) was
  defined but never actually called from `main()`, so `args.video_codec` stayed `None` and
  crashed every real (non-dry-run) invocation — fixed by resolving it once in `main()`
  before dispatching to the worker pool. Re-ran on the affected file after both fixes:
  confirmed via `ffprobe` — `r_frame_rate=30/1`, `avg_frame_rate=30/1`, audio `pcm_s24le`.
- **Timeline built from the prepped clips** (`MediaPool.CreateTimelineFromClips`, 4 of the
  10 imported) — a real edit with hard cuts between 4 different source clips, 8030 frames
  at 24fps (~5.6 minutes of source cut down to ~5.6 min... actually 8030/24 ≈ 334.6s ≈
  5m35s of assembled timeline).
- **Grain at the timeline level, not per-clip — partially automated, one step genuinely
  needs the GUI**: wrapped the whole 4-clip cut into a single `CreateCompoundClip` (API
  call, confirmed working — `Items after: [('validation_cut_ALL', 86400, 94430)]`). Grading
  *that one compound clip* on the Color page grades the whole cut uniformly, which is the
  actual mechanism for "timeline-level, not per-clip" in Resolve. **Adding the Film Grain
  OFX filter itself to that clip's node graph could not be scripted** — confirmed via this
  MCP's own `docs/notes/openfx-notes.md`: the public Resolve scripting API has no method to
  add a ResolveFX/OFX filter to a node's tool list (`graph`'s action list has
  `get_tools_in_node` but nothing like `add_tool`); this is a documented gap in Blackmagic's
  own API, not something the MCP declined to wrap. Confirmed the plugin itself **is**
  present in this Studio install (`/opt/resolve/UI_Resource/Steinway/filmGrain.bmp` and
  friends — Resolve's own Effects Library icon assets for it), so the manual step is
  expected to work cleanly: open the Color page, select `validation_cut_ALL`, Effects
  Library → OpenFX → ResolveFX Texture → **Film Grain**, drag onto a new serial node. **Not
  yet done by human hands this session** — this is the one remaining "must be done through
  the GUI, exactly like Windows" step before the edit can be called fully validated.

### Power: this rig's VR power-management setup doesn't know Resolve exists

`reverb-g2`'s `vr-power-watchdog.service` (see that repo's `docs/68`) only treats a
`monado-service` process or a live Proton game tree as "active" — confirmed live that a
running Resolve process does **not** trigger it: while Resolve was open and in active use
this session, the box sat at `gpu power: 100 W of 250 W max (40%)`, `cpu governor:
powersave`, `cpu epp: power`, `cpu boost: 0` — the watchdog's idle floor, the whole time.

- **New tool**: [pipelines/resolve-power/resolve_power.py](pipelines/resolve-power/resolve_power.py)
  — a self-contained Python port of the same idea as `reverb-g2`'s
  `vr-power-setup.sh`/`vr-power-watchdog.py` (not a fork of that code, no code dependency on
  that repo — see the script's own docstring). `report` needs no root; `--apply`/`--saver`/
  `--restore` mirror the bash script's mechanics; `--watch` is the practical one: it stops
  `vr-power-watchdog.service` for the duration (if present/active — degrades gracefully if
  not), applies full performance the moment a `resolve` process is detected, and restores +
  restarts the VR watchdog when Resolve exits or on Ctrl+C. **This is a manual, run-when-
  validating tool, not a new systemd service** — `reverb-g2`'s own watchdog keeps running
  independently the rest of the time, per the user's explicit call to keep the two projects
  decoupled.
- **Measured, not assumed, whether forcing full power is worth it**: rendered the same
  4-clip timeline (H.264 NVIDIA/NVENC, 8030 frames) as a straight cut with no grading —
  1 rep at the watchdog's `saver` floor (100W/powersave), 3 reps at full `--apply`
  performance (250W/performance):

  | power state | rep1 | rep2 | rep3 |
  |---|---|---|---|
  | saver (100W, powersave) | 16.75s | — | — |
  | performance (250W, performance) | 15.76s | 17.70s | 26.29s |

  The spread *between* the three performance-mode reps (15.8s–26.3s) is far larger than the
  saver-vs-performance gap. **For a light cut-only render like this one, forcing full power
  showed no measurable export-speed benefit** — matches the same "heat without frames"
  pattern `reverb-g2`'s `docs/48-gpu-power-waste-landscape.md` already documented for
  CPU/pacing-bound VR workloads: more watts doesn't help *export throughput* when something
  else is the bottleneck.

  **Correction, flagged by the user immediately after this result**: that finding only
  covers batch export speed, and export speed is the wrong thing to optimize for here.
  **Quality and frame consistency during actual interactive work (scrubbing, live playback
  while grading) is what matters, and losing even a single frame there is a real cost, not
  a benchmark footnote** — this is the exact same principle `reverb-g2`'s own
  `vr-power-setup.sh` states as its reason for existing: "anything that can add latency
  non-deterministically -- a governor ramping, a link entering a low-power state -- can
  produce [a dropped frame], and none of it is visible in an average." A batch render's
  total wall time hides exactly this: it doesn't care if frame 400 took 3x longer than
  frame 401 while a governor spun up, only that the whole job finished. Live playback and
  scrubbing do care, every frame, in a way this sweep never measured. **Revised
  recommendation: default to `--watch` for the whole time Resolve is open for real
  editing/grading, not just exports** — the interactive-smoothness risk of running
  under-clocked outweighs the (already-shown-to-be-small-to-nil) export-speed cost of
  running at full power, especially given `reverb-g2`'s own separate finding that idle GPU
  draw is *identical* at 100W-capped vs. uncapped (both 19.9-19.8W) — so there is no real
  power being "saved" by staying capped during a session anyway, only spike protection at
  true rest is given up. Batch-export-only sessions (e.g. an unattended overnight queue)
  are the one case where the original light-render finding above still applies as-is. Worth
  re-running the export-speed sweep once the Film Grain node is in the graph, but as a
  secondary data point, not the deciding one.
- **NVIDIA settings recap for this rig** (cross-referenced from `reverb-g2`, which has done
  the deep measurement work): persistence mode should stay **exactly as-is, never toggled**
  by anything Resolve-related — `reverb-g2` found toggling it risks a modeset on the
  desktop monitor, which lives on the same GPU as everything else here (`resolve_power.py`
  inherits this rule, same as the bash script it's modeled on). The GPU's efficiency knee
  is around 60% power on this specific card per `reverb-g2`'s own sweep methodology
  (genuinely GPU-bound workloads only — Quake II RTX, not the pacing-bound VR case) — worth
  keeping in mind if a future heavier Resolve render sweep shows a real GPU-bound curve
  instead of today's noise-dominated one.
- **`prepare_for_resolve.py` now prefers GPU (NVENC) over CPU by default**, per explicit
  direction this session ("tratá de siempre usar GPU, CPU solo si hace mucha falta,
  alertando al usuario"): `--video-codec` defaults to `h264_nvenc` when this machine's
  `ffmpeg` build has it (confirmed present: `h264_nvenc`, `hevc_nvenc`, `av1_nvenc`), falling
  back to CPU `libx264` only if NVENC genuinely isn't available, with an explicit `WARNING`
  printed to stderr when that fallback happens — not a silent downgrade. (Didn't actually
  trigger a video transcode in this session's real run — all 10 source files were already
  H.264/HEVC, safe to stream-copy — so this only affects footage that genuinely needs
  re-encoding.)

### Disk, again

`/home/iam/Videos` is now at **87% (28G free)**, down from 85%/32G free two sessions ago —
the ~940MB of prepped validation sources (`~/Videos/resolve_validation_sources/`) is most of
that delta. Kept, not cleaned up, since it's real validated output feeding the still-open
`resolve_linux_validation` project, not throwaway test data (the actual throwaway renders —
`x11_cuda_retest/`, `power_sweep_test/` — were deleted after verification, as before). Worth
a real prune pass if this keeps climbing.

### Effect-by-effect API + performance map — started, not finished

The Film Grain gap above raised the real question (asked directly this session): which
Resolve effects are actually scriptable at all, and of those, which are GPU-heavy
(especially the AI-driven ones) vs. lightweight/CPU-bound? First real pass at mapping it,
via the MCP's own source (`src/granular/timeline_item.py`) rather than guessing from the
GUI:

- **Scriptable, dedicated API methods (not the generic OFX-add gap)** —
  `TimelineItem.Stabilize()`, `.SmartReframe()`, `.CreateMagicMask()` /
  `.RegenerateMagicMask()`, plus the audio side (`transcribe_audio`, voice isolation
  get/set). These are real Resolve scripting API methods, not MCP-invented wrappers.
- **Measured, not assumed**: called `Stabilize()` on the `validation_cut_ALL` compound
  clip (8030 frames) and polled `nvidia-smi` every 0.5s for the duration. **Confirmed
  genuinely GPU-bound**: GPU utilization jumped from idle 0% to 47-54%, SM clock from
  210MHz (P8 idle) to 1980MHz, power draw to ~104-110W, over a **28.59s** call —
  real motion-analysis compute, not a metadata-only flag flip. (Minor API quirk noted in
  passing: `GetProperty("StabilizationEnable")` read back `None` immediately after a
  successful `Stabilize()` call returning `True` — worth checking whether that property
  needs the GUI's Inspector panel open/refreshed to reflect, or is a genuine read gap, next
  time this is touched.)
- **Confirmed NOT scriptable via a dedicated method** (same class of gap as Film Grain,
  would need the generic OFX-add path that doesn't exist): Super Scale (AI upscale), Speed
  Warp (AI-based optical-flow retiming), Temporal/Spatial Noise Reduction. These are the
  "denoise via GPU" and "scaling" cases flagged this session — real, GPU/AI-heavy, but GUI-
  only for now unless a pre-built `.drx`/PowerGrade template turns out to carry them through
  `graph.apply_grade_from_drx` (not yet tried).
- **Not yet mapped at all**: the rest of `timeline_item_color`'s 35 actions, `fusion_comp`'s
  41, and every plain linear/CPU-side operation (crop, pan/zoom/transform, basic primaries,
  transitions) as a deliberate lower-GPU-load comparison point against the AI-driven ones
  above.
- **The real ask, for a future session**: a repeatable per-effect benchmark harness —
  trigger each scriptable effect (or document as GUI-only where it isn't), poll
  `nvidia-smi` (util/power/clock) and CPU governor state throughout, log wall time +
  samples to a structured file (CSV or JSON lines, one row per effect per run), and treat
  it as a real regression/coverage suite for "does the API still do what it says" — not
  just a one-off number like today's single `Stabilize()` sample. Today's pass is the seed
  of that (methodology proven, one real data point), not the harness itself.

### Still open

- **Manual Film Grain application** to `validation_cut_ALL` in the GUI (see above) — the
  one step that needs a human at the Color page.
- **Re-run the power sweep with real grading in the node graph**, once grain is applied —
  today's result only rules out "worth it for light cuts," not the heavier case.
- **Full hands-on edit/grade/export verification** by the user, end to end on
  `resolve_linux_validation` — everything above is scripted/API-verified, which is real
  signal but is explicitly not the same bar as a human actually editing (see "Why this
  matters" at the bottom of this doc).

## Session update (2026-08-25, later still): fresh reboot re-confirms the CUDA/X11 fix, effect benchmark harness built

Machine was rebooted again (independent of the reboot that first fixed the livelock) and
logged back into **GNOME on Xorg**. Before touching anything else, re-checked
`GPUDetect`'s log line the moment Resolve launched: `NVIDIA GPU Driver: 595.71, supports
CUDA 13.2`, `Matches: CUDA, NVML, OpenCL, XOrg`, `Compute API set to automatic, defaulting
to CUDA.` — identical to the first fix. **This upgrades the finding from "fixed by one
specific reboot" to "fixed by Xorg-session + reboot, reproducibly"** — worth knowing since
this rig's GPU/driver state is otherwise easy to second-guess after every reboot.

### Effect-by-effect benchmark harness, built and run

Turned the prior session's one-off manual `Stabilize()` measurement into a real tool:
[pipelines/effect-benchmark/effect_benchmark.py](pipelines/effect-benchmark/effect_benchmark.py).
Connects via the scripting API, calls a named timeline-item effect, samples `nvidia-smi`
throughout, and appends one JSON-lines record per run to
`effect_benchmark_results.jsonl` — so results accumulate across sessions instead of
living only in prose.

**Real tooling bug caught and fixed before the numbers could be trusted**: the first cut
sampled GPU state from a Python `threading.Thread` running alongside the effect call, and
it silently produced exactly **1 sample regardless of the effect's actual duration** (1
sample for a 1.2s call, 1 sample for a 19s call) — worthless data that looked plausible at
a glance. Root cause: the scripting API's blocking calls go through `fusionscript.so`'s
socket RPC, which does not release the GIL while blocked, so a same-process Python thread
never gets CPU time until the call already returned. Fixed by sampling via `nvidia-smi`'s
own `-lms` loop in a **separate OS subprocess** instead of a Python thread — a real
process's internal timing isn't subject to our interpreter's GIL at all. **Lesson for
anyone benchmarking through this scripting API from Python: don't trust a same-process
sampling thread's cadence during a blocking call — verify sample count scales with wall
time, or use a subprocess-based sampler from the start.**

This pass was run deliberately **without** forcing performance mode — the watchdog's
saver floor (100W/powersave, per `resolve_power.py`) was left exactly as-is, per explicit
direction this session, since forcing full power for benchmarking would have begged the
"is it worth it" question rather than answering it:

| effect | wall time | GPU samples | GPU util max | power max | SM clock max | note |
|---|---|---|---|---|---|---|
| `Stabilize()` (2nd call, same clip) | 1.5s | 3 | 8% | 57.9W | 1770MHz | already stabilized from the prior session — see below |
| `SmartReframe()` | 19.5s | 39 | **96%** | **100.1W** | **2010MHz** | genuinely GPU-bound |
| `CreateMagicMask()` | 0.03s | 1 | 6% | 57.7W | 1770MHz | `needs_hitl` — no clicks placed, as expected |
| `RegenerateMagicMask()` | 0.02s | 1 | 4% | 57.8W | 1770MHz | nothing to regenerate, consistent with the above |
| `GetVoiceIsolationState()` / `SetVoiceIsolationState()` | ~0.001-0.5s | 0-1 | — | — | — | instant metadata get/set, round-trips correctly (verified `isEnabled`/`amount` set then read back, then reset to off) — **not itself GPU work**, presumably deferred to playback/render |

Two real findings beyond "the harness works":
- **`Stabilize()` on an already-stabilized clip is a fast no-op, not a re-analysis** — the
  1.5s/3-sample result here is nothing like the original session's genuine 28.6s/GPU-bound
  pass on the same clip before it had ever been stabilized. Don't read a fast repeat call
  as evidence stabilization got cheaper; it means the clip already had it applied.
- **`SmartReframe()` boosted the GPU to its full 2010MHz SM clock and 100W (the watchdog's
  own cap) even while the CPU governor stayed `powersave` and the GPU was never taken out
  of the watchdog's capped power state.** For a single ~20s GPU-bound call, the power cap
  didn't stop it from reaching real GPU load.

**Follow-up, done at the user's explicit request right after the table above**: applied
`sudo resolve_power.py --apply` (250W/100%, `performance` governor, boost on) and
re-ran the same two effects for a real saver-vs-performance comparison, not just the
single-state pass above. Unlike `Stabilize()`, **`SmartReframe()` turned out NOT to
cache/no-op on a repeat call** — the second call still did a real ~89-96%-util GPU pass
both times, which is what makes this a fair apples-to-apples comparison (the `Stabilize()`
repeat, by contrast, stayed a fast no-op at full power too — 1.0s vs. 1.5s, both
non-signals, not included as a real comparison):

| effect | power state | wall time | GPU util max | power draw max | SM clock max |
|---|---|---|---|---|---|
| `SmartReframe()` | saver (100W cap, powersave) | 19.5s | 96% | 100.1W | 2010MHz |
| `SmartReframe()` | performance (250W cap) | **15.5s** | 89% | **239.8W** | 1995MHz |

**~20% faster wall time for ~2.4x the power draw, at essentially the same peak SM
clock either way** (2010 vs 1995MHz — the 100W cap already let this card reach nearly
its full boost clock for this workload; the extra power budget bought sustained
throughput more than peak clock). Reads as a genuine, if modest, real speedup — not the
"heat without frames" null result the earlier batch-export sweep found — but the
watts-per-second-saved trade is steep. Consistent with `resolve_power.py`'s own
recommendation to reserve full power for when it's actually needed (interactive
scrub/playback, or a GPU-bound op like this one mid-session) rather than leaving it on
by default: **worth the ~20% for an occasional ~15-20s AI-analysis call, not obviously
worth it to hold 250W for an entire editing session on this evidence alone.** Power
state left at `performance` after this test (the user's own call, not reverted
automatically) — check `resolve_power.py` (no args, no root needed) before assuming
which state the rig is in for any later measurement.
- Confirms the "not yet mapped" list from the prior session's pass on `Stabilize()` alone —
  `SmartReframe` joins it as confirmed genuinely GPU-bound; Magic Mask remains
  confirmed-blocked-on-human-clicks (not a bug); voice isolation confirmed as a cheap
  state toggle, decoupling "is this AI feature scriptable" from "is calling it GPU work."

### Render-profile benchmark + bundled Blackmagic tools map

Follow-up to the effect harness above, done at the user's request right after: the user
hand-built a **new timeline ("Timeline 2")** with a different real grading effect on each
of the same 4 clips, applied entirely through the GUI (this is exactly the class of
operation the effect-benchmark harness confirmed has no scripting-API entry point):

| Clip | Effect (node-graph tool name, via `GetToolsInNode`) | Actual variant (per the user, not visible via the API) |
|---|---|---|
| `11111_a.mkv` | Noise Reduction | **AI Ultra NR** — Neural Engine-based, the heavy variant |
| `Actitud Feria 17_a.mkv` | Noise Reduction | **Temporal NR** — the classic algorithmic variant, lighter |
| `DarkSpirit1025master_a.mkv` | **Film Grain** — the exact manual step flagged as still-open two sessions ago | — |
| `Ice Queen Video_a.mkv` | Analog Damage | — |

**Real gap confirmed, and sharper than first assumed**: the user flagged the two "Noise
Reduction" instances are actually very different tools — one is **AI Ultra NR** (the
Neural Engine/AI-model-based variant, part of the same bundled AI feature set as Magic
Mask/Speed Warp), the other is plain **Temporal NR** (the classic algorithmic denoiser,
no neural model involved). **The scripting API cannot distinguish this at all, not even
at the tool-*name* level** — `GetToolsInNode` reported the generic string `"Noise
Reduction"` for both, with no variant/parameter information; confirmed against this MCP's
own `docs/notes/openfx-notes.md`, which states there is no general OFX
parameter-inspection method exposed by the scripting API (same shape as the "can't add a
tool" gap, just the read side of it — and worse than assumed, since even *which specific
tool* is genuinely ambiguous from the API, not just its settings). **The only way to get
this mapping at all was asking the user directly** — there is no API-only path to it.
Given how different these two really are (AI/Neural-Engine-driven vs. a classic filter),
treat the two rows above as **not comparable GPU-load-wise** even though they share one
generic API-visible name — a future benchmark pass should test `AI Ultra NR` and
`Temporal NR` as separate named entries once each can be triggered in isolation (neither
has a dedicated scripting method the way `Stabilize()`/`SmartReframe()` do, so this still
needs a human to apply each on its own test clip while the harness samples).

**Render-profile benchmark**, via the new `render_benchmark.py` (reuses
`effect_benchmark.py`'s subprocess-based `GpuPoller`, same reason: a Python thread can't
sample during a blocking scripting-API call) — rendered this same 4-effect timeline
(334.68s of assembled output) through three delivery profiles, **all done at forced full
performance power (250W/`performance`, applied by the user for this test)**, not the
saver floor used for the earlier `SmartReframe()` comparison:

| preset | wall time | GPU util (max/avg) | power max | SM clock max | output size |
|---|---|---|---|---|---|
| H.265 Master | 173.0s | — (samples lost, see below) | — | — | 496.8MB |
| H.264 Master | 178.6s | 100% / 92.4% | 250.1W | 1995MHz | 760.0MB |
| ProRes 422 HQ | 200.6s | 100% / 79.8% | 249.6W | 2010MHz | 4442.5MB |

**Real tooling failure caught mid-run, worth documenting so it isn't repeated**: the
first attempt ran all three presets in one `render_benchmark.py` invocation under a
300s shell `timeout` — too short. The H.265 Master render (173s) actually completed and
had its record built, but the process was killed by the timeout partway through the
*second* preset before Python's buffered file write for the first record ever hit disk
(`f.write()` without `f.flush()` — a `timeout`-driven `SIGKILL` doesn't give buffered I/O
a chance to flush). **Fixed**: added an explicit `f.flush()` after every JSONL write in
`render_benchmark.py`. The H.265 Master row above was reconstructed by hand from
`GetRenderJobStatus` (job survived and kept rendering server-side even after the killed
script's own polling loop died — Resolve's render doesn't depend on the calling script
staying alive) and `ffprobe` on the still-present output file, which is why it's missing
GPU samples — a live spot-check mid-render did catch 95% util / 170.75W / 1980MHz,
consistent with the other two rows, just not a full time series. H.264 Master and ProRes
422 HQ were re-run individually afterward with the fix in place and have full data.

**Takeaway**: this timeline — 4 real GPU-heavy grading effects on ~5.6 minutes of
footage — pushes the GPU to 100% peak on every profile tested, a genuinely different
load shape than the earlier trivial cut-only renders (1.2s ProRes, no grading) or the
single-effect `SmartReframe()` call. ProRes's lower average util (79.8% vs. H.264's
92.4%) despite being the slowest wall-clock likely reflects CPU-side ProRes encode work
competing for the pipeline rather than the GPU being less busy overall — not yet
root-caused further.

**Power sweep on this exact heavy timeline, closing out the "still open" item from two
sessions ago** — H.264 Master re-rendered at the watchdog's `saver` floor (100W cap) for
a clean comparison against the 250W row above. Had to stop `resolve_power.py --watch`
first (it was running, and immediately re-applies `performance` the moment it sees a
`resolve` process — fighting any attempt to measure `saver` state while Resolve is
open; the user paused it by hand for this test, restart it separately if wanted going
forward):

| power state | wall time | GPU util (max/avg) | power max | SM clock max |
|---|---|---|---|---|
| saver (100W cap) | 272.1s | 100% / 89.9% | 100.3W | 1935MHz |
| performance (250W cap) | 178.6s | 100% / 92.4% | 250.1W | 1995MHz |

**~34% faster at full power for this real 4-effect grading timeline** — a bigger real
speedup than the single-effect `SmartReframe()` comparison found (~20%), consistent with
more/longer sustained GPU-bound work benefiting more from full power than one short
call. Same conclusion as before, more strongly confirmed now: **worth full power for a
real export of a heavily-graded timeline like this one, matches `resolve_power.py`'s own
recommendation to stay in performance for the actual working session rather than only
exports** — the watts-per-second-saved trade from the earlier single-effect test holds
up as a real, not marginal, effect at this heavier load.

### Bundled Blackmagic tools beyond the main app, and "can we run Resolve's own benchmark on Linux"

Asked directly this session: what other tools ship with this Studio install, and is there
an official way to benchmark Resolve itself on Linux instead of only this repo's own
harness. Answer: **no official full-pipeline benchmark exists for Linux**, but one real,
usable piece does:

| Tool | Location | What it is |
|---|---|---|
| `TestIO` | `/opt/resolve/bin/` | **Real Blackmagic disk-I/O benchmark, native Linux CLI** — `TestIO <path> <frameSizeMB> <numFrames> <threads> <keepCpuBusy> <useDirectIO>`. Directly relevant to this repo's own "DNxHR ≈ 700GB/hour, plan storage accordingly" note. |
| `BlackmagicRAWSpeedTest` | `/opt/resolve/BlackmagicRAWSpeedTest/` | Real BRAW decode-speed benchmark, bundled — but **GUI-only** (Qt app, no CLI/headless flags found), not scriptable. |
| `BlackmagicRAWPlayer` | `/opt/resolve/BlackmagicRAWPlayer/` | Standalone BRAW viewer, same GUI-only shape. |
| `ShowDpxHeader` | `/opt/resolve/bin/` | CLI DPX file header dumper. |
| `sqlite3` | `/opt/resolve/bin/` | Bundled SQLite CLI — useful for inspecting Disk Database project files directly. |
| `VstScanner` | `/opt/resolve/bin/` | Fairlight VST plugin scanner. |
| `OFXLoader` | `/opt/resolve/bin/` | OFX plugin load/validate utility. |
| `DaVinci Control Panels Setup`, `Fairlight Studio Utility` | own dirs | GUI config tools for Blackmagic control-surface/audio hardware. *(2026-09-24: no longer "not relevant" — a Speed Editor is now connected, see "Session update" below.)* |
| `BMDPanelDaemon`/`BMDPanelFirmware`/`run_bmdpaneld`, `DaVinciPanelDaemon` | `bin/` | Control-panel hardware daemons (already running as part of the base install, not user-facing tools) — these are what picked up the Speed Editor below. |
| `gst-plugin-scanner` | `bin/` | GStreamer plugin discovery, bundled media-format support. |

**Third-party**: Puget Systems' PugetBench for DaVinci Resolve — the best-known
community benchmark — is confirmed **Windows/Mac only**, current as of its "2.0" release
(Feb 2026); Puget has stated Linux support is "planned, no ETA." Not a partial option
either — the automation/scoring layer itself is platform-native, not just the launcher.

**`TestIO` retried and got real numbers.** The first attempt (a `timeout`-wrapped run)
printed nothing before being killed — root cause: `timeout` sends `SIGTERM` by default,
and `TestIO` only prints its results at the very end of a natural run (five sequential
phases: WRITE, READ, READ-reverse, RANDOM READ, READ-WRITE), so anything that kills it
mid-run — `SIGTERM`, or just not enough wall-clock before its own `timeout` cap — loses
the output entirely, not a partial result. Fixed by giving it a size small enough to
finish well inside the wrapper's time budget:

```
TestIO /home/iam/Videos/testio_scratch/ 50 10 1 0 0   # 50MB x 10 frames, cached I/O
  WRITE            2297.94 MB/s (45.96 FPS)
  READ             3685.49 MB/s (73.70 FPS)
  READ-reverse     9085.83 MB/s (181.70 FPS)
  RANDOM READ      9144.50 MB/s (182.88 FPS)
  READ-WRITE       3582.64 MB/s (35.82 FPS)
```

**Read the numbers above with a real caveat, don't take them as this disk's sustained
hardware speed**: run with `useDirectIO=0` (cached I/O) against only 500MB total on a
31GB-RAM box — the ~9GB/s READ-reverse/RANDOM READ figures are almost certainly page
cache, not the physical disk.

**Direct IO followed up — it wasn't actually hung, just slower than the first
`timeout` window allowed**, same "only prints at the very end" characteristic as above
biting twice. Confirmed by watching the process directly (`/proc/<pid>/status`, no
`strace` installed on this rig) instead of assuming: it stayed in state `R` (running,
not blocked) the whole time, and a longer-lived background run completed cleanly and
printed real numbers:

```
TestIO /home/iam/Videos/testio_scratch/ 50 4 1 0 1   # 50MB x 4 frames, DIRECT IO (real hardware)
  WRITE            46.43 MB/s (0.93 FPS)
  READ            220.95 MB/s (4.42 FPS)
  READ-reverse    226.09 MB/s (4.52 FPS)
  RANDOM READ     184.08 MB/s (3.68 FPS)
  READ-WRITE       74.63 MB/s (0.75 FPS)
```

**This is a real, important finding, not a benchmark curiosity**: `df -T` / `lsblk`
confirm `/home/iam/Videos` (the only registered Media Storage volume, where every
render/export in this repo has gone) sits on **`/dev/sda`, a Kingston SA400S37240G —
a budget, DRAM-less SATA SSD with a small SLC write cache**. ~46 MB/s sustained
Direct-IO write is consistent with this exact drive model's well-documented real-world
behavior once that small cache is exhausted (a known characteristic, not a fluke or a
misconfiguration). **This directly threatens the "DNxHR HQ 4K ≈ 700GB/hour" estimate
earlier in this doc** — 700GB/hour needs ~194MB/s sustained write, and this drive's
measured real ceiling is under a quarter of that. A long DNxHR 4K export on this rig, as
currently configured, is a real risk of write-starving faster than the render pipeline
can produce frames, not just a "budget for the space" storage-capacity concern.

**Second drive exists but isn't a simple fix**: `/dev/nvme0n1` is a 1.8TB Kingston
NVMe — but it's partitioned for a **Windows dual-boot** (EFI + Windows boot + three NTFS
partitions), not a native Linux data drive. One NTFS partition is mounted at
`/mnt/videos` via `ntfs-3g` (FUSE, real overhead vs. a native filesystem) — not
currently registered as a Resolve Media Storage volume, and its real throughput hasn't
been measured with `TestIO` yet. Moving render output there is a plausible win (even
NTFS-3G overhead likely beats 46MB/s off NVMe hardware) but not yet measured, and
reformatting any part of that drive for native Linux use would touch the Windows
dual-boot — not something to do without deciding that trade-off deliberately. **A
dedicated hardware-performance investigation (RAM, cache placement, proxy workflow, and
this storage question specifically) was kicked off right after this finding** — see the
next session update for what came back.

**Bottom line**: for repeatable GPU/effect/render numbers, this repo's own
`pipelines/effect-benchmark/` harness (built this session) is genuinely the closest thing
to an equivalent Blackmagic/Puget don't provide natively on Linux — worth continuing to
build out rather than waiting on either vendor.

### Remote monitoring while away from the rig — investigated, no Wine needed

Ask from the user: watch Resolve's progress from another computer while this session
drives it, **without** spinning up the full Moonlight/Sunshine desktop-streaming setup
this rig already uses for the unrelated `reverb-g2` VR project (heavyweight — full remote
desktop, not needed just to *watch*). `/opt/resolve/bin/Blackmagic Remote Monitor` was the
lead worth checking before assuming Wine is required — it's a native Linux binary bundled
with this Resolve install (links `QWebSocket` + Qt), and it turns out to be exactly what
it looks like: **no Wine involved anywhere in this**.

- **`DaVinci Remote Monitor` is a real Resolve Studio feature, already installed, driven
  from Resolve's own Workspace → Remote Monitoring menu** — it live-streams the Viewer as
  low-latency H.264/H.265 (8/10-bit) to another device. Linux specifically requires an
  RTX-series NVIDIA GPU (AMD/Intel unsupported on this feature) — this rig's 3060 Ti
  qualifies. It supports a **local-IP mode with no Blackmagic Cloud account**
  (Preferences → System → General → "Use Remote Monitoring without Blackmagic Cloud").
  Launching the bundled binary standalone (no Resolve running) produced no output and no
  listening port in a quick test — it's not meant to run outside Resolve, only from that
  menu.
- **Hard constraint that actually matters here: this rig has a single-seat Resolve Studio
  license** — no second simultaneous Resolve instance can run anywhere else under it. This
  rules out the *desktop* side of Remote Monitor: on Mac/Windows, viewing the stream on
  another computer requires Resolve Studio running there too, which would need its own
  license. **The only free, license-free viewer is Blackmagic's iOS/Android app**
  ("DaVinci Remote Monitor" / "Blackmagic Remote Monitor" on the App Store/Play Store) —
  fine if the "otra compu" can be a phone/tablet next to it, not usable as a literal
  second-desktop viewer without buying another license.
- **NDI is not built into Resolve** — it needs the paid third-party **Nobe Display**
  plugin (with a separate NDI-output upgrade) to get an NDI stream out of Resolve at all.
  Ruled out as a free/native option; only worth it later if a proper broadcast-quality
  stream is worth paying for.
- **Lighter alternative considered, not adopted: a VNC session scoped to just the Resolve
  window** (e.g. `x11vnc` with a window-clip/geometry flag) — no Blackmagic feature
  dependency, no second Resolve install/license needed, and lighter than a full desktop
  capture. Not installed on this rig (`x11vnc` isn't present, would need `sudo apt
  install`). **Decision, made by the user after weighing it: stick with the existing
  Moonlight/Sunshine setup** — it already works well enough for this ("me cierra
  bastante") and isn't worth swapping out just to save the weight of a full desktop
  stream. Not a to-do anymore; don't re-propose x11vnc unless something about Moonlight
  itself stops working for this use case.

## Official requirements vs. this rig

Blackmagic's official Linux target is **Rocky Linux 8.6** — this rig runs **Debian 13**,
which is why `makeresolvedeb` exists at all (Blackmagic ships no Debian package and does
not test against it). Everything below is Blackmagic's stated Rocky Linux 8.6 spec for
**DaVinci Resolve Studio 21.0.4** (from the official forum release-announcement thread,
2026-08-05), compared against what this rig actually has:

| Requirement | Official minimum (21.0.4, Linux) | This rig | OK? |
|---|---|---|---|
| OS | Rocky Linux 8.6 | Debian 13 (via makeresolvedeb) | Unofficial, works in practice |
| System RAM | **32 GB** | **15 GB** (`free -h`) | **No — less than half the minimum** |
| GPU VRAM (baseline) | Discrete GPU, ≥4 GB VRAM | RTX 3060 Ti, 8 GB VRAM | Yes |
| GPU VRAM (AI tools) | ≥16 GB VRAM | 8 GB VRAM | **No** |
| GPU VRAM (background render) | ≥32 GB RAM + ≥12 GB VRAM | 15 GB RAM + 8 GB VRAM | **No** |
| GPU compute | OpenCL 1.2 or CUDA 12.8 | CUDA 13.2 | Yes |
| NVIDIA driver | Studio driver ≥580.119.02 | 595.71.05 (open, patched for VR) | Yes |
| Monitoring (if used) | Desktop Video ≥12.9 | n/a, not using BMD hardware out | n/a |

**The RAM shortfall is the real finding here.** A process that launches and idles fine
says nothing about whether it holds up on a real project — large timelines, cache,
multiple nodes, background analysis are exactly where 15 GB vs. a 32 GB minimum would
show up first. This is squarely the kind of instability this rig is supposed to be
catching *before* the user's main system moves to the same NVIDIA 595-open driver — see
"Why this matters." Don't treat "it opened" as "it's validated."

### Measured resource footprint — simplest possible playback loop

First real number, not a spec-sheet comparison: 45 seconds of `nvidia-smi`/`pidstat`
sampling (2s interval) while looping playback of a single 1080p test clip, no effects,
no grading — the lightest possible timeline, deliberately, as a baseline before testing
anything heavier.

| Metric | Value |
|---|---|
| CPU | 127–156% (~1.3–1.5 of 12 cores) |
| RAM (RSS), Resolve's main process only | ~4.65GB steady — **28.5% of this rig's 15GB total** |
| GPU utilization | 5%, flat across all 20 samples |
| VRAM used | 1936MiB / 8192MiB (23.6%) |
| GPU power draw | ~59.6W |
| GPU clock | 1770MHz, pinned high despite the low utilization |
| GPU temp | 49–52°C |
| System swap, over the same window | **1.6GB → 2.5GB**, actively climbing |

**5% GPU utilization is expected here, not a driver problem**: a single-clip, no-effects,
1080p timeline gives the GPU almost nothing to do — no grading, no multi-layer
compositing, no scaling. CPU carrying the decode/playback load at this timeline
complexity is normal. Treat these CPU/RAM numbers as the **floor**, not representative of
real editing load — the useful next step is re-measuring with a graded, multi-layer, or
4K timeline to see how these numbers actually move.

**The swap growth is the one data point worth taking seriously from this run**: growing
swap under literally the lightest possible timeline, on a rig already under the official
RAM minimum, is a real signal — not the buff/cache "low free RAM" red herring (Linux
using spare RAM for page cache is normal and does NOT indicate pressure; growing swap
*usage* under active load is a different, more meaningful signal, and this rig showed
it here). Consistent with, not yet definitive proof of, the RAM-shortfall risk already
flagged above. Worth re-checking whether swap keeps climbing (or plateaus) under a
heavier timeline next.

## The one manual step that can't be scripted around

Blackmagic gates the Linux download behind a free-account login on
[blackmagicdesign.com/support/family/davinci-resolve-and-fusion](https://www.blackmagicdesign.com/support/family/davinci-resolve-and-fusion).
Log in with the account tied to your Studio dongle and download **DaVinci Resolve
Studio for Linux** — not Free. Studio matters here specifically because it's the edition
actually being used, and because of the codec findings below.

## Install

**Gotcha, found live**: `makeresolvedeb` requires the `.run` file to be in the same
working directory the script is invoked from — it refuses ("does not exist or is
located outside the working directory") if you pass a path elsewhere, even an absolute
one. Copy the script alongside the `.run` first.

```bash
unzip DaVinci_Resolve_Studio_*_Linux.zip        # produces the .run + an install-instructions HTML
cp /path/to/makeresolvedeb_*.sh .               # must be in the SAME dir as the .run
./makeresolvedeb_*.sh DaVinci_Resolve_Studio_*_Linux.run
sudo dpkg -i davinci-resolve-studio_*.deb davinci-resolve-studio-data_*.deb   # both packages
sudo apt install -y libglu1-mesa python3-pip    # libGLU.so.1 is NOT optional — Resolve
                                                 # fails to even start without it. pip is
                                                 # only needed for the MCP's optional extras.
```

**Disk space, found live**: the script does a real `cp -rp` (not a hardlink) to build
each package's staging tree from the unpacked `.run` payload — budget roughly 2-3x the
`.run`'s size in free scratch space during the build (an 11GB `.run` unpacks to ~14GB,
then gets copied again into the package trees). The original downloaded `.zip` is safe
to delete immediately after `unzip` succeeds — nothing downstream reads it again, only
the `.run` and the unpacked tree matter from that point on. Delete it early if space is
tight rather than waiting for the whole build to finish.

**What the two `.deb`s actually are**, since it's not obvious from the filenames alone:
`davinci-resolve-studio_*.deb` (~2.9GB) is the program itself — binaries, libraries,
desktop integration. `davinci-resolve-studio-data_*.deb` (~5GB) is the accompanying
static content — LUTs, Fusion/Fairlight templates, bundled presets. Both are required;
installing only one leaves the other half missing resources or missing the app entirely.

## Codec support on Linux (official, corrected from the original assumption)

Sourced from Blackmagic's official **"Supported Formats and Codecs" PDF, July 2025
(DaVinci Resolve 20, Rocky Linux 8.6 table)** — the only Linux-specific codec matrix
Blackmagic publishes. **Independently confirmed against 21.0.4 on this exact rig with
real footage** — not just trusted from the PDF. See
[pipelines/mcp-benchmark/README.md](pipelines/mcp-benchmark/README.md#1-aac-decode-failure--confirmed-at-the-sample-level-not-just-a-spec-sheet-claim)
for the log-level proof: pushing real AAC-audio camera footage through the render
pipeline produced hundreds of repeated `IO.Audio | ERROR | Failed to decode the audio
samples` entries in Resolve's own debug log — not a guess, not container-metadata
reflection (the Media Pool's clip-property inspector reports `Audio Codec: AAC` for
these files regardless, which is *not* proof of working decode, a distinction that
tripped this investigation up before the render-level test settled it).

**This corrects an earlier assumption in this repo.** The original note was "Free
doesn't decode H.264/HEVC/AAC, Studio does." The real picture, per the official Linux
table, is more specific and more restrictive:

| Codec | Linux behavior (any edition) | Notes |
|---|---|---|
| H.264 decode/encode | **Studio only**, and GPU-accelerated on NVIDIA graphics | No documented CPU/software path on Linux at all — unlike macOS/Windows, which both list an unconditional "Yes." If the GPU path doesn't apply, Linux has no fallback per this table. |
| H.265/HEVC decode/encode | **Studio only**, GPU-accelerated on NVIDIA graphics | Same shape as H.264. |
| **AAC (aac/m4a)** | **Unsupported — decode and encode both "–"**, regardless of edition | This is the actual gotcha. macOS and Windows both get full AAC (CBR/VBR/average); Linux gets none. Embedded-audio-in-container decode also excludes AAC on Linux (PCM/mp3/flac only) — it's listed explicitly for mac/Windows. |
| AV1 | Decode: Yes, GPU-accelerated (NVIDIA). Encode: NVIDIA GPU-accelerated | |

**Practical consequence, confirmed not theoretical**: typical H.264/AAC phone or camera
footage on this rig needs a transcode regardless of edition, because of the audio
track, not just the video codec. The DNxHR workaround below handles both halves in one
pass. A lighter fix also confirmed working when the video codec itself is fine (Studio +
NVIDIA decodes H.264/HEVC natively) is audio-only remux — `ffmpeg -c:v copy -c:a
pcm_s16le` — which eliminated every `IO.Audio` decode error in the real test.

```bash
ffmpeg -hwaccel cuda -i input.mp4 \
       -c:v dnxhd -profile:v dnxhr_hq -pix_fmt yuv422p \
       -c:a pcm_s16le output.mov
```

### The bigger finding: H.264/H.265 aren't even in this install's render-codec list

Decode is one half of the story — **encode (render/export) is worse than the official
table implies**. Queried the live scripting API directly (`Project.GetRenderCodecs(format)`
for every container Resolve offers: `mov`, `mp4`, `mkv`, `mxf`, `mxf_op1a`) and **H.264
and H.265/HEVC do not appear as an available encode codec in any of them** on this
install. What *is* available: ProRes (all variants), DNxHD/DNxHR (all variants), FFV1,
Kakadu JPEG2000, uncompressed, Panasonic/Sony intra codecs, GoPro CineForm — a real,
substantial list, just missing the two most common delivery codecs entirely. The
official codec table's "Studio + NVIDIA GPU-accelerated" claim for H.264/H.265 encode
may describe the driver/hardware path in principle without it actually being wired up
as a selectable render codec on this specific install — not yet root-caused (missing
license component? a packaging gap in `makeresolvedeb`'s conversion? something
`gpudetect`-related?), but confirmed absent, not a guess. Practical consequence: **any
delivery preset that targets H.264/H.265 (which is most of them — YouTube, Vimeo,
TikTok, Presentations) cannot actually render on this install**; ProRes/DNxHR are the
real fallback for now, not a stylistic choice. Investigate the missing-encoder root
cause before trusting any H.264/H.265 delivery workflow on this rig.

DNxHR HQ 4K ≈ 700 GB/hour — plan storage accordingly (a native partition beats a
cross-filesystem NTFS mount for sustained write throughput at that rate).

**This is a known, long-standing community issue, not something specific to this rig or
build.** Forum research (t=59346, "How to transcode video to Resolve for Linux", and
others) confirms this has been a recurring Linux-Resolve pain point, with an established
community workaround pattern matching the DNxHR approach above. A maintained batch tool
already exists rather than needing another one-off script:
[ChrisTitusTech/resolve-linux `resolve_convert.sh`](https://github.com/ChrisTitusTech/resolve-linux) —
recursively scans a directory tree, converts everything to DNxHR (`.mov`) + PCM
24-bit/48kHz (`.wav`), mirrors the folder structure into an output dir. Five DNxHR
quality tiers: LB (~100Mbps, proxy), SQ (~220Mbps), HQ (~440Mbps, default), HQX
(~660Mbps, 12-bit), 444 (~880Mbps, max). Handles mp4/mkv/mov/mxf containers and
hevc/h264/aac/flac/opus inputs — needs an ffmpeg build with DNxHD/DNxHR encoder support
(already confirmed present on this rig — the standard Debian `ffmpeg` package used
throughout this repo). Worth adopting instead of ad-hoc per-file `ffmpeg` commands once
batch-converting a real footage library, not evaluated hands-on yet.

## Linux-relevant changes tracked across recent point releases

Blackmagic doesn't publish a Linux-specific changelog — Linux items are folded into the
same release notes as macOS/Windows. Tracked here so drift is visible over time instead
of re-discovered per version. **21.0.4's "What's New" is sourced directly from the
official forum release thread** (pasted in full by the user, since the forum blocks
automated fetches with a 403/bot challenge); 21.0.2 and 21.0.3 items below are from
secondary reporting (CineD, 4kShooters, Newsshooter) since those threads couldn't be
fetched directly — flagged as such, not first-party-verified.

- **21.0.4** (2026-08-05): No Linux-specific "What's New" items. One codec-adjacent fix,
  Windows-only: "H.264 software decoding on Windows." Nothing changes the codec table
  above. Official Linux minimums reaffirmed unchanged from 21.0.2/21.0.3 (Rocky Linux
  8.6, 32GB RAM, NVIDIA Studio driver ≥580.119.02, CUDA 12.8/OpenCL 1.2).
- **21.0.3** (2026-07-22, secondhand): Retime/interlaced/Fusion/Sony BURANO fixes;
  reaffirmed Rocky Linux 8.6 + Desktop Video ≥12.9 requirement. No Linux-specific codec
  changes reported.
- **21.0.2** (secondhand): **Studio-only fix**: "addressed H.264 and H.265 NVIDIA decode
  performance" — the one item in this window that's specifically Linux/NVIDIA-relevant,
  consistent with the "GPU-accelerated on NVIDIA graphics" shape of the codec table
  above.

Net: nothing across 21.0.2 → 21.0.4 changes the AAC-unsupported or
H.264/HEVC-Studio-plus-NVIDIA-only findings. Re-check this section on the next point
release rather than assuming it's stable indefinitely.

## AI-driven control via MCP — two servers installed, connected, wired into Claude Code

[samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)
(v2.103.1) exposes Resolve's official scripting API to any MCP client, plus an optional
offline file-level server. This turned out to be a **far more mature project than the
original "expect a thin wrapper, plan to fork" assessment assumed** — 100% scripting-API
coverage, 338/338 live-tested methods passing, a browser control panel, and a second
independent server for offline `.drp`/`.drt`/`.drx` work. Forking is no longer the
obvious next step; using it as-is and evaluating gaps first is.

**Version check (2026-09-07): the installed build was over 100 releases behind — upgraded
same day.** Rig had v2.103.1 (installed 2026-08-25); upstream was at **v2.212.1** at check
time, released within hours of it — this project ships multiple releases per day some
days. Recent release titles showed a clear theme that wasn't present in 2.103.1: **an
agent-safety layer** — e.g. "every destructive action carries a real risk rating"
(v2.210.0), "`dry_run` refuses on actions that cannot honour it" (v2.211.0), risk rating
tied to the actual graph a call targets (v2.212.0). See
[releases](https://github.com/samuelgursky/davinci-resolve-mcp/releases).

**Upgrade done (2026-09-07), 178 commits, v2.103.1 → v2.212.1**: `git fetch` + `git merge
--ff-only` (clean fast-forward, no conflicts) in `~/resolve-install/davinci-resolve-mcp`,
stashing/reapplying one local `package.json` tweak (`allowScripts` entries for
`better-sqlite3`/`sharp` native builds) across the merge. Then `pip install -r
requirements.txt --upgrade` (venv), `npm install` at the repo root, and `npm install`
inside `resolve-advanced/` — whose `node_modules` turned out to not actually be installed
at all despite the original install notes above claiming both servers were set up; this
upgrade is what surfaced that gap and fixed it. Verified, not just assumed: `scripts/doctor.py`
reports `MCP server version: 2.212.1` and a live `Resolve scripting connection: DaVinci
Resolve Studio 21.0.4.5` against the same running Resolve instance from the KDE-on-X11
session above, all six AI extras still `[OK]`, `npm run smoke` passes, and
`resolve-advanced`'s offline unit suite (`npm run test:libs`) passes 251/272 (21 skipped,
env-gated) with zero failures. The two pre-existing `doctor.py` warnings about missing
Codex/Claude-Desktop MCP config entries predate this upgrade and are about client wiring,
not the server itself.

Separately, the same author also ships **Bradford Post Assistant**
(bradfordoperations.com/software/post-assistant) — a standalone desktop chat-window
agent (not just an MCP server) that wires this same MCP together with an LLM and an
extended "Bradford API" for higher-level post-production work: timeline organization,
shot matching, delivery-spec validation, natural-language color direction, editorial
pacing analysis, Fusion comp authoring. **Currently closed beta, access requested, not
installed** — a different tier of tool than the MCP-server-plus-your-own-client setup
this repo evaluated (a packaged agent vs. a protocol server), worth watching but not
yet something to install and test here.

### What's installed

- **Cloned to** `~/resolve-install/davinci-resolve-mcp` (scratch/build location, not
  inside this repo — keeps ~8GB of `.deb`s and this MCP checkout out of git).
- **Python venv** (`venv/`) with the MCP SDK and all optional extras (see below).
- **Node dependencies** (`npm install`, 177 packages) for the advanced offline server,
  including the two native modules that need compiling: `better-sqlite3` and `sharp`
  (npm 11's script-approval gate blocked these by default — approved explicitly via
  `npm approve-scripts better-sqlite3 sharp`; both verified loading with `node -e
  "require(...)"`).
- **Environment variables**, added to `~/.bashrc`:
  ```bash
  export RESOLVE_SCRIPT_API="/opt/resolve/Developer/Scripting"
  export RESOLVE_SCRIPT_LIB="/opt/resolve/libs/Fusion/fusionscript.so"
  export PYTHONPATH="$PYTHONPATH:$RESOLVE_SCRIPT_API/Modules/"
  ```
- **MCP client wiring (2026-09-17)**: the repo-root [`.mcp.json`](.mcp.json) registers both
  servers with Claude Code as `resolve-native` and `resolve-community`, each launched through
  [`resolve_mcp_wrapper.sh`](pipelines/mcp-benchmark/resolve_mcp_wrapper.sh). The wrapper
  exists because a static `env` block can't express this rig's X11 cookie (a new
  `.mutter-Xwaylandauth.<random>` name every login — see the `launch_resolve` crash below).
  Project-scoped servers need a one-time approval the first time `claude` is opened in this
  directory. (Until this date no client was configured at all — everything went through the
  Python harness.)

### Server modes (two independent servers, can run together)

| Server | Entry point | Tools | What it does |
|---|---|---|---|
| Compound (Python) | `src/server.py` | 36 | Default — related operations grouped behind action parameters, keeps LLM context small. Requires a **live** Resolve via the scripting API. |
| Granular (Python) | `src/server.py --full` | 353 | One MCP tool per Resolve API method. For power users. |
| Advanced (Node, optional) | `bin/davinci-resolve-advanced-mcp.mjs` | 18 | Reads/writes Resolve **files** (`.drp`/`.drt`/`.drx`) and does DB/XML-level edits with **no Resolve running** — works cloud or local. Covers grade transfer, ASC CDL import, broadcast-legal QC, node-graph cleanup, a DB-as-truth pipeline (YAML → SQLite → staged execution with gates/provenance), conform/relink QC, deliverable QC, and more. |

Key stats from the project's own coverage tracking: 361/361 scripting API methods
covered (100%), 338/361 live-tested (93.6%), 338/338 of those passing, across Resolve
19.1.3 Studio, 20.3.2 Studio, 21.0.2 Studio, and 21.0.3 free (via the in-app bridge).

### Optional AI extras — all installed

The core install is deliberately minimal (Python + ffmpeg + the scripting API); each
extra below unlocks specific features and refuses honestly with its own install line if
missing, rather than guessing. Per the user's request to get full capability, all of
these are installed in the venv:

| Extra | Unlocks |
|---|---|
| ffmpeg (already on PATH, confirmed 7.1.5) | Silence detection, dead-space markers, level measurement, audio analysis |
| numpy | Colour pre-balance, reference-still matching, sound-density audit |
| librosa | Beat/bar/phrase detection for music-driven cutting |
| openai-whisper | Transcription and word-level tools built on it |
| open_clip_torch | Visual similarity, `find_similar` |
| transformers | CLAP audio embeddings |
| opencv-python | Additional frame analysis |

Run `venv/bin/python scripts/doctor.py` from the checkout to re-check status at any
time — it also reports Resolve connection state and per-client MCP config status.

### Connecting it: done, live-tested

Fixed, in Resolve itself (GUI-only step, can't be scripted): **DaVinci Resolve →
Preferences → General → External scripting using → Local**, Save, then a full restart
(close and reopen, not just Save). `doctor.py` now reports:

```
[OK] Resolve scripting connection: DaVinci Resolve Studio 21.0.4.5
[OK] Resolve edition: Studio
```

Verified beyond just the connection check — a live call through the actual scripting
API, not just `doctor.py`'s own probe:

```python
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp('Resolve')
resolve.GetProductName()      # 'DaVinci Resolve Studio'
resolve.GetVersionString()    # '21.0.4.5'
resolve.GetProjectManager().GetCurrentProject().GetName()   # 'Untitled Project'
```

Real end-to-end proof the MCP's foundation works, not just that the module imports.
One incidental note, not a bug: relaunching Resolve after the preference change landed
on a fresh "Untitled Project" rather than reopening the `test1` project used for the
earlier playback-loop measurements — expected if Resolve doesn't have "reopen last
project on launch" set, not investigated further since it doesn't affect the MCP
connection itself.

### Full capability map — live-extracted, not just quoted from the vendor docs

See **[MCP-CAPABILITIES.md](MCP-CAPABILITIES.md)** for the complete tool → action
catalog (37 tools, **718 actions** as of the latest refresh, extracted directly from the
running server's own docstrings, not the vendor README's summary numbers). The original
pass also ran a 41-action live read-only probe against the connected Resolve instance —
checkpointed to disk every 10 calls so a crash mid-run couldn't lose progress. All 41
succeeded, every error came back structured (`{code, category, retryable, remediation}`)
rather than a raw exception, and `media_analysis.capabilities` confirmed the optional
extras venv wiring is correct end-to-end (found `whisper` at the actual venv path, not
just `pip install`-successful) — see
[pipelines/mcp-benchmark/README.md](pipelines/mcp-benchmark/README.md) for that probe and
every live workflow test run since.

### The fork question — answered with a real workflow test, not just reading the code

The earlier plan in this repo was "expect to fork/heavily modify — the upstream project
looked thin." Having read the code and docs, that assessment didn't hold on paper:
100% API coverage, an independent offline server, a control panel, source-media-safety
guarantees, and an explicit "what this does not do" section are not signs of a thin
wrapper. But reading the code isn't the same as driving it — a real test (import AAC
footage, build a timeline, render draft quality; full account in
[pipelines/mcp-benchmark/README.md](pipelines/mcp-benchmark/README.md#real-workflow-test-aac-audio-timeline-edit-draft-render--and-what-it-actually-found))
found a genuine, narrower gap. Short version, once you strip away the individual repro
steps: **the read path is solid; the moment a render fails to complete, Resolve hangs
badly** — not a graceful failure, not a clean error, an actual stuck/unrecoverable state
more often than not. Longer version:

- The **read-only/informational surface is solid** (41/41 live probe, clean typed
  errors) — no fork needed there.
- The **render pipeline does not recover cleanly once a render is interrupted or hits a
  failing clip**: silent `None` returns with zero diagnostic signal, GUI-mode dialog
  blocking (confirmed by literally enumerating X11 windows since the API gives no
  signal), two reproduced crashes from `Quit()`'s save-changes dialog blocking a
  process kill, and — even in headless mode, which the project's own docs recommend
  specifically to dodge dialog-blocking — a real stall/deadlock (0% completion, static
  output file, zero log output, ~90% CPU, 0% GPU, for 4+ minutes) that produced a
  corrupt output file with no `moov atom`. The only reliable recovery found was a full
  process kill + restart.

**Revised answer**: not a full fork. The informational/read side doesn't need one. What's
missing is a **resilience wrapper around the render lifecycle specifically** — detect a
stuck render (0% + static file size over N seconds), treat an unexpected `None` as a
probable blocked-dialog signal instead of silent success, and make "kill and restart" an
explicit logged recovery path instead of something a human has to diagnose by hand, the
way it had to be done here. Check `docs/reference/api-limitations.md` in the checkout
first — this may already be a known, curated gap worth reporting upstream rather than
patching locally.

### Native MCP server arrives in Resolve 21.1 — benchmarked head-to-head against the community server

**Blackmagic shipped its own MCP server in Resolve Studio 21.1** (released 2026-09-08).
Found live on this rig, not from release notes: the install here had already been silently
upgraded to `21.1-mrd1.10.1` (this doc still said 21.0.4.5 above) and
`/opt/resolve/bin/ResolveMCP` was sitting right there, bundled with the app itself — no
separate install, no client wiring beyond what the community MCP already needed (External
scripting = Local).

**Public coverage of this release is wrong about what it actually ships.** CineD,
digitalproduction.com, explainx.ai and others all report "~88 tools" covering editorial
operations, Fairlight mixing, multicam switching, etc. Running `ResolveMCP --dump-tools`
against the real installed binary returns **14 tools**, and the design is nothing like what
those articles describe — not a bigger or smaller version of the community server's
one-tool-per-action model, a different model entirely:

| Tool | What it does |
|---|---|
| `launch_resolve`, `get_resolve_status`, `get_whats_new` | Lifecycle + a changelog tool explicitly meant to tell the calling agent about features newer than its training data |
| `get_scripting_api`, `search_scripting_api`, `get_scripting_docs` | Fetch the live `.pyi` stubs (main scripting API, Fusion API, **and the Fusion UI Manager API** — nothing in the community server touches UI Manager) and dev docs straight from the installed build, always version-matched |
| `run_script` | Sandboxed Python 3.14 against the live `resolve`/`project` objects — no filesystem/network/process access |
| `run_script_unsafe` | Same, plus full OS access, for tasks like shelling out to `ffmpeg` |
| `list_luts`, `list_dctls`, `update_dctl`, `delete_dctl`, `delete_lut` | File management for `~/.local/share/DaVinciResolve/LUT/MCP` |
| `generate_lut` | Builds a `.cube` 3D LUT by evaluating an arbitrary Python transform function per lattice point — no community-server equivalent (theirs only imports/exports/validates *existing* LUT/DCTL files) |

In short: instead of a fixed catalog of pre-built actions, native hands the agent the whole
scripting surface and a code-execution sandbox to drive it with — the opposite trade-off
from the community server's 36 (compound) / 353 (granular) purpose-built, safety-wrapped
tools. Native has zero `safe_*`/dry-run/risk-rating layer (`run_script*` is just flagged
`destructiveHint: true`), zero built-in ML (no transcription, no visual similarity, no beat
detection — you'd have to call out to those yourself via `run_script_unsafe`), and only
works with Resolve already running. Community's Advanced (Node) server is the only one of
the three that works fully offline against `.drp`/`.drt`/`.drx` files.

**Two real bugs found and fixed while building the benchmark harness below, before any
number could be trusted:**

1. **`launch_resolve` crashed Resolve outright** the first time — Qt aborted with
   `Authorization required, but no authorization protocol specified`. This rig's graphical
   session is Wayland (`mutter`) with a rootless Xwayland, so `DISPLAY=:0` alone isn't
   enough — the process also needs `XAUTHORITY` pointed at the live
   `/run/user/1000/.mutter-Xwaylandauth.*` cookie, which isn't exported anywhere by
   default for a non-interactive shell. Fixed in `bench_lib.resolve_env()`.
2. **Right after `launch_resolve`, Resolve is sitting on the Project Manager screen, not
   inside a loaded project** — `GetCurrentProject()` still returns a usable "Untitled
   Project" object (its name reads fine), but `GetCurrentPage()` returns `None` and any
   Media Pool write (`CreateEmptyTimeline`, etc.) **silently returns `None` and does
   nothing — no exception, no error field, nothing.** The fix is to explicitly
   `CreateProject`/`LoadProject` a *named* project first; that's what actually dismisses
   the Project Manager and drops the session into the Cut page. Cost real time to catch
   because the failure mode looks identical to "nothing happened yet."

**Benchmark harness**: [pipelines/mcp-benchmark/](pipelines/mcp-benchmark/) — `bench_lib.py`
(shared stdio-client helpers for both servers, using the official `mcp` Python SDK already
vendored in the community server's venv), `setup_bench_project.py` (idempotent fixture: a
project + `bench_tl` timeline with 8 "Solid Color" generator clips — generators on purpose,
so the benchmark needs no source footage), and two benchmarks kept as runnable examples:

- **`bench_01_simple.py`** — 4 trivial read-only asks (Resolve version, project name,
  timeline count, current timeline name), 10 reps each, both servers. Native has no
  dedicated tool for any of these, so every ask goes through one `run_script` call; community
  answers each with one purpose-built compound-tool call.

  | backend | median per call |
  |---|---|
  | community (compound) | **~5.5 ms** |
  | native (`run_script`) | **~66 ms** |

  Native pays a flat ~12x tax per call here — consistent with spinning up a fresh sandboxed
  interpreter context per `run_script` invocation rather than reusing a persistent one, not
  with any network/protocol overhead (both talk stdio JSON-RPC the same way).

- **`bench_02_batch_rename.py`** — a real compound task: rename all 8 clips on `bench_tl` to
  `Clip 01`..`Clip 08`. `timeline.get_items` on the community server already batches
  name/start/end/duration into one call (good design on their part), but there's no batch
  *write* action — renaming still costs 1 (list) + 8 (one `timeline_item.set_name` each) = 9
  round trips. Native does the whole loop inside one `run_script` call.

  | backend | median wall time | round trips |
  |---|---|---|
  | community (compound) | ~101 ms | 9 |
  | native (`run_script`) | ~76 ms | 1 |

  This is the crossover point bench_01 doesn't show: native's ~12x-higher fixed cost per
  call stops mattering once a task needs enough per-item round trips — at 9 calls, native
  already wins on wall-clock. Both scripts reset the fixture's clip names back to
  `"Solid Color"` when done, so they're safe to re-run as-is or use as templates for new
  comparisons.

**Practical takeaway**: pick native for scripted/compound edits (especially anything
touching Fusion, DCTLs, or generated LUTs) and for driving whatever Resolve feature shipped
too recently for the community server to have caught up to yet; pick community for single
quick lookups, anything needing the safety rails (dry-run, risk rating, typed errors), the
ML extras, or offline `.drp`/`.drt` work with Resolve not even running. Realistically, both
stay installed and get used for what they're each actually good at.

**Superseded for routing purposes (2026-09-17)**: the numbers above stand, but "pick
community for single quick lookups" is no longer how `ResolveClient` routes — the standing
rule for this project is Resolve's own tooling first, and ~60 ms on a lookup doesn't justify
a second server in the path. See the unified-client section below. Also worth knowing when
reading bench_02's community numbers: that server's safety layer archives the timeline the
first time a session mutates it (`bench_tl_archived_v0N` copies pile up in the fixture
project, one per run), which is what separates its mean (~160 ms) from its median (~90 ms).

**Real limitation found while validating `generate_lut` end-to-end**: generated a LUT with
native (`generate_lut` -> `list_luts` confirmed the file), then tried to apply it to a
`bench_tl` clip to prove a full native-generates/community-applies round trip. Both sides
failed identically -- community's `graph.set_lut`/`timeline_item_color.set_cdl` and native's
own raw `TimelineItem.GetNodeGraph()` all agree: **a "Solid Color" generator clip inserted
via `InsertGeneratorIntoTimeline` has no node graph at all** (`GetNodeGraph()` returns `None`
outright) -- not an MCP-layer bug on either side, a real gap in what the scripting API
exposes for generator clips specifically. `SetLUT`/`SetCDL` both live on the `Graph`/node
object, so neither backend has any path around it without a node graph to begin with. This
is exactly why the fixture uses generators (no source-media dependency) but it means
color/LUT operations need a **real imported clip** to test properly -- noted here rather
than silently declared "working" on the strength of the generate+list half alone.

### Unified client: native first, errors that raise, a timeout on everything

[pipelines/mcp-benchmark/resolve_client.py](pipelines/mcp-benchmark/resolve_client.py)
(`ResolveClient`) is the one entry point for scripted Resolve work in this repo. Reworked
2026-09-17 after a review of the first cut found it could report success on failure:

- **Routing: native always, community only as a lazy fallback.** The first cut sent lookups
  to community on the strength of bench_01's latency gap; that contradicted this project's
  own native-first rule. Lookups now come from one native round trip (`get_context()`), and
  the community server isn't even started unless a fallback is needed — the client works
  with it absent.
- **Every method raises `ResolveError` and returns parsed data**, never a timing dict to be
  substring-matched. This surfaced the most important finding of the review: **native's
  `run_script` reports a script exception as a *successful* MCP result** — `isError` stays
  false and the traceback arrives as `{"error": "Traceback…"}` (a normal run is
  `{"output": …, "result": …}`). Every `is_error` check in this directory had therefore been
  blind to script failures; `bench_lib.timed_call` now treats that envelope as the error it is.
- **Every call carries a timeout** (native's own server-side one, max 60 s, plus a
  client-side read timeout above it), so the documented "scripting call never returns while
  the GUI is mid-playback" hang surfaces as an error. Verified both ways: a `while True`
  script comes back as `ResolveError` in 3 s, a client-side timeout in 1 s, and the session
  stays usable afterwards.
- **Values are interpolated with `repr()`**, not pasted between quotes. (Probed live while
  testing this: Resolve itself refuses a `"` in a project name — `CreateProject` returns
  `None` — but accepts an apostrophe.)
- **`import_media()`** encodes the three import gotchas below as code — registered-volume
  check, `AddItemListToMediaPool` rather than `ImportMedia`, and a path lookup instead of
  the silent empty list a re-import returns. One more found here: imports land in whichever
  Media Pool folder is *current*, not the root.
- **`grab_still()` runs native-only.** See the correction in the grain section below.
- **`render()`** is the render-lifecycle wrapper the fork question above concluded was the
  missing piece: refuses a target outside a Media Storage volume *before* queueing, raises on
  a silent `AddRenderJob` `None`, polls with a hard deadline (then `StopRendering`), only
  ever touches its own job, verifies the file with `ffprobe`, and finally runs
  `audio_loudness_check` on the real deliverable — so the loudness gate is now actually part
  of an export rather than a standalone script.
- `bench_lib.resolve_env()` no longer hardcodes uid 1000, respects an existing
  `DISPLAY`/`XAUTHORITY` (the Xorg/KDE case), picks the newest cookie, and never exports an
  empty `XAUTHORITY`. Server stderr is dropped unless `RESOLVE_MCP_DEBUG=1`.

[validate_client.py](pipelines/mcp-benchmark/validate_client.py) is the live test, and the
part of the review that mattered most: **its original 8 green checks proved much less than
they appeared to** — a check only failed on an exception, and the calls underneath never
raised. It now asserts on returned values, runs only against the disposable `MCP-Benchmark`
fixture (plus a synthetic DNxHR/PCM clip generated under `~/Videos/mcp-bench-scratch`, since
generator clips have no node graph to grab a still from), and ends with negative checks
proving failures are loud: 17 checks, all passing live on 21.1.0.17, including a real render
measured at −14.04 LUFS by the gate. The benchmarks also gained a guard after one was caught
measuring the wrong timeline: they all address "the current timeline", so they now select
`bench_tl` and verify its 8 clips first, and exit non-zero on any failed call. That guard
does three more things, each added for a reason found live:

- **It refuses to run against any project but `MCP-Benchmark`.** These scripts rename clips,
  import media and create timelines in whatever project is *current* — with the real `test1`
  left open, `bench_03` would have imported its WAV and built a timeline there. Verified:
  with `test1` open all three now stop with one line and `test1` is untouched.
- **It pins the GUI to the Edit page, because the active page is part of the measurement.**
  The identical 8-clip native rename takes ~73 ms on Edit/Color and **~166 ms on Deliver**
  (reproducible, 2.2×) — enough to flip bench_02's native-vs-community verdict depending on
  nothing but where the GUI was last left. Any timing in this repo taken on Deliver is
  pessimistic for timeline edits.
- **It reports as one line.** The MCP SDK runs every session inside two nested anyio task
  groups, which re-raise anything crossing them as `ExceptionGroup(ExceptionGroup(exc))` — so
  an `except SomeError` around a session never matches and the real message lands under a
  60-line traceback. `bench_lib._session` unwraps a group holding exactly one exception.

`ResolveClient` also records every call community had to serve in `client.fallbacks`
(capability + why native failed), so a fallback is never silent; `validate_client.py` asserts
it stays empty. And `render()` only accepts files written by *this* render (a stale
`<name>_v1.mov` shares the glob prefix), keeps a wedged Resolve's cleanup failure from
masking the error that explains it, and runs `ffprobe`/loudness off the event loop.

Run anything here with the community checkout's interpreter
(`~/resolve-install/davinci-resolve-mcp/venv/bin/python <script>`), or build a local venv
from [requirements.txt](pipelines/mcp-benchmark/requirements.txt) — the `venv/bin/python`
the scripts' docstrings used to name never existed in this directory.

### Three more real features validated: timeline-level grain, auto-subtitles, audio-export gate

Per the standing instruction for this project — **always reach for Resolve's own tooling
first** before either MCP wrapper — all three of these go through native `run_script`
calling the raw scripting API directly wherever that API has the feature at all; community
was used only for the one piece native has no tool for (Gallery stills).

**Correction (2026-09-17): "native can't do stills" was wrong.** Native ships no stills
*tool*, but `Timeline.GrabStill()` + `GalleryStillAlbum.ExportStills()` are in the scripting
API and `run_script_unsafe` can read the exported PNG back (and delete the still again, so
the Gallery is left as found). `grain_timeline_toggle.py` now goes through
`ResolveClient.grab_still()`, runs with the community server not even started, and takes
`--project/--timeline/--node`.

**…and the port immediately found a real trap: `GrabStill()` returns before the still's
image exists.** Re-run against `test1` (with a fresh go-ahead), the native port reported **0
changed pixels** on vs off — reading exactly like "the toggle does nothing". Grabbing native
and community side by side showed otherwise: native's still differed from community's in
*every* pixel (max delta 73 — a different frame altogether), and repeated native grabs agreed
with each other, so "grab until two match" doesn't catch it either. `ExportStills` called
straight after `GrabStill` exports stale content — no error, a valid PNG, perfectly
repeatable. Community never showed this only because its `grab_and_export` happens to
`time.sleep(0.5)` between the two calls. With the same 0.5 s wait
(`resolve_client.STILL_SETTLE_S`), native and community stills are **pixel-identical in all
three states** (on / off / restored), and the native-only run reproduces the original
measurement exactly: **3,223 of 2,073,600 pixels shift by 1 luma level; 0 pixels differ
after restore.** `test1` left as found (node 1 still `["OFX: Film Grain"]`). The smoke test
had been fooled too — it only checked for a PNG header — so `validate_client.py` now makes
the grab prove it follows the playhead (frame A ≠ frame B, frame A = frame A again), and
that check was confirmed to fail with the wait set to 0. 0.5 s is what held on this rig at
1080p; nothing in the API signals readiness, so a heavier grade or 4K may want more.

**[grain_timeline_toggle.py](pipelines/mcp-benchmark/grain_timeline_toggle.py) — grade
applied to the whole timeline, not per clip.** `Timeline.GetNodeGraph()` is a real, distinct
node graph from any clip's own (`TimelineItem.GetNodeGraph()`) — confirmed by finding a
real, already-existing case: `test1` / "Timeline 1" (the same 16172-frame timeline this
repo's render-pacing benchmarks used) already carries `"OFX: Film Grain"` on node 1 of its
*timeline*-level graph. Toggled it off and back on via `Graph.SetNodeEnabled` to validate
control over it, with the user's explicit go-ahead to touch this real project (not the
disposable `bench_tl` fixture) given two real constraints:

- **Neither backend can add a *new* OFX effect to a graph.** Searched the full scripting API
  surface: `Graph` only has `GetNumNodes`, `SetLUT`/`GetLUT`, node cache/label/enable
  getters+setters, `GetToolsInNode`, `ApplyArriCdlLut`, `ApplyGradeFromDRX`, and
  `ResetAllGrades` — no `AddTool`/`AddNode` at all. The only way to introduce a *new* node
  onto a graph from scripting is `ApplyGradeFromDRX`, which needs a pre-authored `.drx`
  (built once via the GUI or the Gallery). This is a shared Resolve API limitation, not an
  MCP-layer gap on either side.
- **Verifying the toggle actually changed the render needed real pixel diffing, not a
  returned bool.** `GetToolsInNode()` keeps listing a tool even when its node is disabled
  (already known from this repo's earlier `--adaptive` work), and there's no
  `GetNodeEnabled()` getter either — so a still grab (community's `gallery_stills`, the only
  Gallery tool either server exposes; its own docstring calls this the "WYSIWYG PROOF RULE")
  is the only trustworthy evidence. **First metric tried, whole-frame luma stddev, was a
  dead end** — on (8.120) vs off (8.128) looked like noise, indistinguishable from "no
  effect." An exact per-pixel diff told the real story: this node's grain amount is genuinely
  low — ~3,200 of 2,073,600 pixels (0.16%) shift, by exactly 1 luma level, every time,
  reproducibly. Real, just too subtle for a single global average to surface. Switched to
  "any nonzero, reproducible pixel delta counts" instead of a magnitude threshold. Restore
  was confirmed the same way: 0 pixels differ between the original grab and the post-restore
  one. `test1` was left exactly as found (verified live after the fact: node 1 still reports
  `["OFX: Film Grain"]`, current project back on `MCP-Benchmark`).

**[bench_03_subtitles.py](pipelines/mcp-benchmark/bench_03_subtitles.py) — auto-captions via
`Timeline.CreateSubtitlesFromAudio()`.** First call, against `bench_tl`'s 8 silent generator
clips, correctly returned `False` and created nothing — the right answer, not a silent
no-op; the feature genuinely needs audio. Fixed by synthesizing real speech with `espeak-ng`
(already on this system) into a *separate* `bench_tl_subs` timeline, so `bench_01`/`bench_02`
keep their assumption of 8 clips on `bench_tl` intact. **Two more real import gotchas found
en route**: `MediaPool.ImportMedia()` silently returns an empty list for a plain valid WAV
outside a registered Media Storage volume (`/tmp` isn't one — `/home/iam/Videos` and
`/mnt/videos` are, per `MediaStorage.GetMountedVolumeList()`) — a source-import version of
this repo's already-known render-output-path constraint. And *even inside* a registered
volume, `ImportMedia` still silently failed on the exact file that
`MediaStorage.AddItemListToMediaPool()` (the Media Storage browser's own import call)
imported correctly on the first try — worked around by using that instead. Then a *third*
gotcha: re-running against the same synthesized filename made `AddItemListToMediaPool` fail
too — it dedups by path against what's already in the project's Media Pool and returns
nothing (not the existing item, not an error) for a path it already imported, even if the
file on disk changed since. Fixed with a uuid-suffixed filename per run. End state, once all
three were fixed: real, correct transcription — 3 subtitle segments matching the spoken
sentence, in ~12.5s.

**[audio_loudness_check.py](pipelines/mcp-benchmark/audio_loudness_check.py) — an
export-time gate against YouTube's commonly-cited loudness guidance** (~-14 LUFS integrated,
true peak at or below -1 dBTP, a sane loudness range so the mix isn't over-compressed).
Checked Resolve's own tooling first, per the same instruction: the scripting API can *set* a
normalization target (`Timeline.NormalizeAudioLevel` + `NormalizeAudioOptions.targetLoudness`,
which takes LKFS) but has no method anywhere to *read back* measured loudness or true peak —
confirmed by searching the whole API surface, not just assuming. So this genuinely has no
Resolve-native path and goes through ffmpeg's `loudnorm` filter (already on this rig as one
of the AI-extras dependencies) analyzing the actual exported file — arguably the more correct
place for an export gate anyway, since it validates the real deliverable rather than
Resolve's internal state. Self-tested with two synthetic tones (no Resolve, no real project
needed): one normalized to -14 LUFS passes, one pushed +12dB and hard-limited fails on the
loudness check — `--selftest` flag reproduces this on demand.

## Bundled plugins, and what third-party plugins are actually worth trying on Linux

**Blackmagic's own "AI Extras" pack (downloaded separately, paused mid-download)**: on
first launch Resolve opened its own **Extras Download Manager**, offering 7 packages,
~24.4GB total: AI IntelliSearch – Better (4.41GB), AI IntelliSearch – Faster (1.08GB), AI
Slate ID (5.64GB), AI Speech Generator (1.75GB), AI Voice Training (7.22GB), Extended AI
Transcription Language Support 2 (1.89GB), plus Tensor RT Engines (2.40GB, pre-loaded
already). Set to download all, per the user's "todo, todos los plugins" instruction. This
is the exact pack the MCP's own docs reference: `AnalyzeForIntellisearch`,
`AnalyzeForSlate`, and `GenerateSpeech` all require it and report `success: false` with
Resolve's own reason string if it's missing — confirms that note rather than being a
separate thing. Disk: 54GB free when this started, comfortably covers the ~24GB (minus
the 2.4GB already pre-loaded). **Worth flagging**: these are exactly the "advanced AI
tools" workloads the official spec ties to a 16GB-VRAM minimum (see "Official
requirements vs. this rig" above) — this rig has 8GB. Downloading them doesn't guarantee
they'll run well; that's separate to actually test once downloads finish. Download
paused when Resolve was closed (deliberate close by the user after saving the project
and configuring Media Storage — not a crash; `ps aux` showed no Resolve process and no
coredump or kernel OOM entry for it) — resumes next time Resolve reopens.

**Another install-time requirement found**: Resolve wouldn't let the user proceed past
initial project setup without at least one **Media Storage** path configured
(Preferences/Project Settings → Media Storage — a filesystem location Resolve scans for
source media). Not optional on first run; configured and saved.

**Corollary, found via the MCP render test below**: the render output directory has the
*same* constraint — it must be inside a registered Media Storage volume, not just any
writable path. Pointing a render at a scratch directory outside it fails immediately
with a **"Render Path Inaccessible — Please select a render path from within the media
storage"** dialog (GUI mode) that silently blocks any further scripting calls until
dismissed — not an error returned to the API caller. Not obviously documented anywhere;
found live.

**Speeding up the AI Extras download**: the download was slow enough that switching
this rig's default route to a faster available uplink was worth it — a synthetic
speed check (`curl` against `speed.cloudflare.com`, 25MB) measured **~48MB/s
(~383Mbps)** over the new route, and resuming the AI Extras download **confirmed
faster**. Network-topology specifics omitted here as out of scope for a Resolve
writeup; this is machine-wide routing, not anything Resolve-specific.

**GNOME "app not responding" false positives during heavy Resolve activity**: with the
download running and RAM already tight (see the official-minimum shortfall above),
GNOME Shell/Mutter repeatedly popped up its "not responding" dialog for Resolve even
though it was just busy, not hung. Root cause: Mutter's `check-alive-timeout` — the
number of milliseconds it waits for a window to answer a ping before flagging it —
defaults to 5000 (5s), too tight for a heavy app on a RAM-constrained box. Fixed:
```bash
gsettings set org.gnome.mutter check-alive-timeout 20000   # 5s -> 20s, applies live
```
No Resolve/GNOME restart needed — Mutter picks up the new value immediately. Purely a
GNOME-shell false-positive fix; doesn't change Resolve's actual responsiveness, just
stops the misleading dialog from firing on normal slow-but-alive frames.

**Bundled (comes with the install, already working since Resolve launches)**:
`/opt/resolve/plugins` is ~4.8GB — almost entirely opaque, hash-named `.bin` blobs (this
is Blackmagic's own Neural Engine AI-feature bundle: Magic Mask, Speed Warp, Super Scale,
face/voice tools, etc. — not human-readable OFX plugin names, just packaged model/feature
binaries) plus two visible codec libs, `libCFHDEncoder.so` / `libCFHDDecoder.so` (GoPro
CineForm read/write, matches the CineForm row in the codec table above). Nothing to
install separately here — it's part of the two `.deb`s already in place.

**Third-party OFX plugin ecosystem — Linux support is inconsistent and vendor-specific,**
researched directly against vendor docs since no single up-to-date community list exists
for this. Do not assume a plugin "for Resolve" ships a Linux build — "supports Resolve"
and "supports Resolve on Linux" are frequently different claims from the same vendor.

| Vendor / product | Linux support | Notes |
|---|---|---|
| **Boris FX — Sapphire** | **Yes** | 2026 docs explicitly list RHEL 7-9 / Rocky Linux. The one major suite with confirmed, current, official Linux builds. |
| **Boris FX — Mocha Pro** | **Yes** | Ships CentOS/RHEL/Ubuntu builds, lists Resolve as a supported OFX host. One old (2019) forum report of a ProRes 4444 quirk on standalone Linux Mocha — likely stale, worth a quick re-check if it comes up rather than assuming still true. |
| **Boris FX — Continuum** | Unclear | Sapphire/Mocha both confirmed; Continuum's own supported-hosts page didn't clearly break out Linux — check directly before assuming parity with the other two. |
| **Red Giant / Maxon** (Magic Bullet, Universe, Trapcode, VFX Suite) | **No** | Windows/macOS only, across every doc found, including Maxon's own current release notes. Resolve support exists, but not on Linux. Don't bother trying. |
| **Neat Video** | **Partial** | Linux builds exist but are license-gated (v4 Pro OFX / v5 Pro-for-Resolve), not a plain public download — easy to miss if you only check the main download page. |
| **FilmConvert** | Unknown | Resolve OFX plugin confirmed; no Linux system requirement surfaced in vendor docs either way. Needs a direct check before relying on it. |
| **Digital Anarchy** (Beauty Box, Flicker Free) | Unknown | Same gap as FilmConvert — Resolve build exists, Linux status unverified. |
| **GenArts Sapphire** | N/A | Folded into Boris FX Sapphire years ago; not a separate current product. |
| **Video Copilot** (Optical Flares, etc.) | **No** | After Effects plugins, not OFX — not relevant to Resolve at all, Linux or otherwise. |

**What this means for "what's worth trying"**: Boris FX (Sapphire, Mocha Pro) is the one
paid suite worth trying first if third-party grading/tracking tools are ever needed —
everything else either doesn't ship Linux builds (Red Giant/Maxon, skip entirely) or is
unverified enough that it needs a direct vendor-site check before spending time on it.
None of this has been installed or tested on this rig — this is a compatibility survey to
scope future effort, not a confirmation any of it works here specifically. No install-size
figures were reliably found for any of these; expect to check per-vendor at install time.

## Session update (2026-09-07): KDE Plasma on X11 confirms the fix is DE-agnostic

The rig's desktop environment was switched to **KDE Plasma**, still on X11 (`sddm` →
`startplasma-x11`, no `Xwayland` process). This was the first real test of whether the
CUDA/GPUDetect fix documented above is actually about the **X11 vs. Wayland protocol**,
as claimed, or was silently GNOME-specific and just never checked.

Checked immediately after a fresh machine boot straight into the KDE-on-X11 session
(`loginctl show-session` confirms `Desktop=KDE`, `Type=x11`), then launched Resolve for
the first time on this boot — no prior Wayland-session dance needed this time, since the
boot went straight into X11:

- **`ResolveDebug.txt` shows the identical good signature**: `NVIDIA GPU Driver: 595.71,
  supports CUDA 13.2`, `Matches: CUDA, NVML, OpenCL, XOrg`, `Compute API set to automatic,
  defaulting to CUDA.` — same as every GNOME-on-Xorg run, no `-1.-1` CUDA version.
- **NVENC codecs present**: live scripting-API connection (`dvr.scriptapp("Resolve")`,
  version `21.0.4.5`) confirms `GetRenderCodecs("MP4")` lists `H.264 NVIDIA` and
  `H.265 NVIDIA`, same as under GNOME.
- **Real render test, same as the original livelock reproduction**: loaded the `test1`
  project, rendered its timeline as H.264 NVIDIA MP4 via the API. Completed in **1.18s**
  (`TimeTakenToRenderInMs: 1179`), no stuck completion percentage. Output verified with
  `ffprobe`: valid 1920x1080 H.264 MP4, 6.67s, ~14MB — matches the GNOME-on-Xorg result
  almost exactly.
- License/dongle: splash screen logged `Checking Licenses` and proceeded straight into
  the UI with no activation prompt and no license-related error in the log — same
  behavior as every prior session, dongle (`lsusb`: Feitian `096e:0201`) still detected.

**Conclusion: the fix is desktop-environment-agnostic, as the root-cause theory predicted.**
It's the X11 protocol that matters for `GPUDetect`'s CUDA correlation, not anything
GNOME-specific — KDE Plasma on X11 works identically, on the very first launch after a
boot that went directly into an X11 session (no need to log into Wayland first). This
closes the open question of whether "GNOME on Xorg" was actually the necessary condition
or just the DE that happened to be tested first.

**New footgun found in the process of cleaning up this test**: this render's target
directory (`/home/iam/Videos/kde_x11_retest`) was deleted with `rm -rf` right after
`ffprobe` verification, per this repo's usual throwaway-artifact convention — but the
render job stayed in `test1`'s Render Queue (via the scripting API, jobs aren't
auto-removed after completion) still pointing at that now-deleted path. Touching that
queue afterward hit **"Render Path Inaccessible"** — not because the path was ever wrong,
but because a *directory a persisted render job still references* got deleted out from
under it. User re-created the directory by hand and re-rendered successfully (verified:
same 1920x1080 H.264, 6.67s, ~14MB output). **Revised convention going forward**: when a
render job created via the API is going to be left in the project's queue (the normal
case — nothing in this repo's scripts calls `DeleteRenderJob`), delete only the output
*file* after verification, not its containing directory — or call `DeleteRenderJob` on
the job itself if the directory really needs to go.

## Session update (2026-09-07, later): "no audio in Resolve" was the already-documented AAC bug, not routing

User reported no audio during playback in the KDE-on-X11 session above. First hypothesis
tried — **wrong, corrected by the user** — was system audio routing: this rig runs
[Sunshine](https://github.com/LizardByte/Sunshine) (`app-dev.lizardbyte.app.Sunshine.service`,
a Moonlight game-stream host, unrelated to Resolve but running on the same box for a
separate use case) which creates its own null sinks (`sink-sunshine-stereo` etc.) and sets
one as the **system default audio sink** — meaning normal desktop audio, Resolve included,
was flowing into a virtual capture point with no physical output rather than real
speakers. Loaded a `module-loopback` from `sink-sunshine-stereo.monitor` to the physical
`alsa_output.pci-0000_07_00.4.analog-stereo` sink to fix that — real, and left in place
since it's generally useful on a rig that's both a local workstation and a stream host —
but **it did not fix the reported problem**, because that wasn't the actual cause.

**Actual cause, per the user's own correction**: the loaded clips are AAC-audio MP4s —
exactly [the already-documented, evidence-backed AAC-decode-unsupported-on-Linux
finding](#codec-support-on-linux-official-corrected-from-the-original-assumption) from
earlier in this same project. Confirmed via the Media Pool: all 10 real video clips in
`test1`'s Bin 1 (from `/home/iam/Videos/oldback_nvme/`) showed `Audio Codec: AAC` — the
two exceptions already in the bin (`Letov_pcm.mov`, `NoxArtFirstEdition2_pcm.mov`) were
manually fixed in an earlier session, which is exactly why this pattern wasn't caught
sooner: it had already been solved once, just not for every clip.

**Fix applied, using this repo's own tool** (`pipelines/prepare-for-resolve/prepare_for_resolve.py`),
per the user's explicit ask to keep it fast and lossless — copy the video stream, only
re-encode audio:

```
python3 prepare_for_resolve.py /home/iam/Videos/oldback_nvme --output-dir /home/iam/Videos/oldback_nvme_pcm
```

- **9 of 10 clips**: video stream copied untouched (`-c:v copy`, confirmed via `ffprobe` —
  same H.264/H.265 codec and level before/after), audio re-encoded AAC → `pcm_s24le`. Whole
  batch finished in **2.9s** (stream copy is nearly free) — confirms the tool's default
  behavior already matches "copy video, fix only audio" whenever the source video codec is
  already safe, no flag needed.
- **1 of 10** (`video_2025-12-31_14-53-07.mp4`) **could not be a pure stream copy**: it's
  variable-frame-rate (~30.019fps average), which this tool always fixes by re-encoding
  video to a snapped constant rate (30fps here) — unrelated to the audio fix, and not
  avoidable without leaving genuinely broken timing in an editing timeline. Correctly
  flagged in the tool's own dry-run reasoning before running.
- All 10 outputs imported into `test1`'s Bin 1 via `MediaPool.ImportMedia()` — left
  alongside the original AAC clips (same convention as the two pre-existing `_pcm.mov`
  fixes), not replacing them, so nothing in the existing timeline breaks; the new
  `Linear PCM`-audio versions are what should actually get cut in from here on.

## Session update (2026-09-07, later still): power-capped vs full-power render pacing, real timeline

User was rendering `Timeline 1` (16172 frames, 1920x1080/24fps, H.265 NVIDIA, the same
range used throughout this doc's render tests) for real, still under the power-floor state
from "Power" above (GPU capped 100W of 210W). Asked for the pacing to be documented, then
to re-measure after applying full power via `resolve_power.py`, to close out the power
question with a real editing-load number instead of only the earlier light-cut/SmartReframe
data points.

**Capped (100W/210W, 48%)**: `Job 6` (same 16172-frame range, same codec/profile) completed
in **225.847s** total (`TimeTakenToRenderInMs`) — 71.6 fps average, ~2.98x realtime. No
phase-level breakdown for this run — the monitoring script was started after the job was
already 28% in, so only the final total is solid; a repeat of this test should start
watching *before* triggering the render, not after, to get a full curve on the capped side
too.

**Full power (`sudo resolve_power.py --apply`, GPU 210W/210W, CPU governor `performance`,
boost on)**: `Job 7`, identical range/codec, completed in **180.095s** — 89.8 fps average,
~3.74x realtime. **25.4% faster wall-clock than the capped run for the same export.** This
time the watcher caught the job from frame zero, polling `GetRenderJobStatus` +
`nvidia-smi` every 2s, giving a real phase-by-phase curve (16172 frames total):

| Phase | Frames | Time | Effective fps | What's actually there |
|---|---|---|---|---|
| Opening section | ~1–2426 (0–15%) | ~10s (plus an ~8s pre-roll before progress starts moving — likely codec/GPU-context init, not render work) | fast | normal 1920x1080 h264/hevc source, matches timeline format |
| **Complex scene** | ~2426–4528 (15–28%) | **143.7s** | **~14.6 fps** | see root cause below |
| Rest of timeline | ~4528–16172 (28–100%) | 26.3s | ~443 fps | normal-format sources again |

User's own live GUI read during the render ("la escena compleja va a 10-15fps, el resto a
~500fps aprox, antes era menos, ~350 aprox") **matches this measured curve closely** —
14.6fps and ~443-477fps bracket the 10-15 / ~500 estimate almost exactly, a good sign the
scripting-API `CompletionPercentage` polling here tracks what's actually rendering, not
just a coarse/lagged number.

**Root cause of the "complex scene", found by cross-referencing the timeline against the
Media Pool — it is not a power/GPU problem at all**: the two clips sitting in that frame
range, `Ice Queen Video _a.mkv` (1152x1728) and `11111_a.mkv` (1080x1920), are **portrait
sources at 25fps** being placed into this **1920x1080/24fps landscape timeline** — Resolve
has to reformat/scale *and* frame-rate-conform them in realtime during render. That's a
CPU/scaling-bound cost, which is exactly why more GPU wattage didn't fix it: the segment
stayed the visible bottleneck under full power too, just inside an overall-faster render.
**If this bottleneck specifically needs to go away, the fix is pre-conforming those two
source clips (scale/pad to 1920x1080, conform 25→24fps) before cutting them in** —
`prepare_for_resolve.py` doesn't currently do this (it only fixes bad audio/video codecs,
VFR, and interlacing, not resolution/aspect/frame-rate mismatches against a target
timeline) — a real gap to close if this pattern shows up again, not a bug in what exists.

**Bottom line for "production ready"**: full power is a real, measured **~25% wall-clock
win on a real mixed-content export**, on top of the light-cut/SmartReframe data points
already in the "Power" section above — worth defaulting to `--watch` for any real export,
not just AI-heavy operations. It does **not** fix a resolution/frame-rate mismatch bottleneck
like the one found here; that needs fixing at the source-media level, independent of power.

## Session update (2026-09-07, later still): content-aware dynamic power, built

User's ask after seeing the pacing numbers above: don't hold full power for a whole
session just because Resolve is open — go full only when the *current timeline* actually
has something heavy on it, drop back down the moment it doesn't. Built as
`pipelines/resolve-power/resolve_power.py --watch --adaptive` (opt-in flag; plain
`--watch` keeps its original unconditional-full behavior, since that's still the
documented recommendation for a real editing/grading session).

**How it decides**: `scan_timeline_for_heavy_effects()` runs the actual
`TimelineItem.GetNodeGraph().GetToolsInNode(i)` scan (the exact call used minutes earlier
this same session to find and disable the `OFX: Relight` node on `11111_a.mkv`) across
every clip on every video track, in a **subprocess with a hard timeout** — not inline —
specifically because `dvr.scriptapp('Resolve')` is documented above to block forever
while Resolve's GUI is mid-playback; that must never be allowed to hang the watch loop.
An inconclusive scan (timeout, no project/timeline open, Resolve unreachable) leaves the
current power tier alone rather than guessing.

**Verified live against this exact session**: called `scan_timeline_for_heavy_effects()`
directly against `Timeline 1` right after disabling Relight's node — it returned `True`,
which surfaced a real, previously-unknown limitation rather than a bug: **`GetToolsInNode()`
still reports a tool sitting on a node that's been bypassed via `SetNodeEnabled(i, False)`,
and there is no `GetNodeEnabled()` to check the reverse.** This function cannot currently
tell "heavy tool present but bypassed" from "heavy tool present and running" — it treats
both as heavy, which is the conservative direction (wasted watts vs. a mid-scrub stutter)
but means bypassing a heavy node without removing it won't bring `--adaptive` back down to
the capped tier. Combined with the already-known blind spot (a Timeline-level grade or a
track-level effect — like the Film Grain the user applied at the Timeline level right
before this — isn't visible to `GetToolsInNode` at all), **`--adaptive` should be read as
"probably won't under-power a genuinely heavy timeline" rather than "always picks the
minimally-sufficient tier"** — false-heavy is possible and expected, false-light (missing
real heavy work) is the failure mode that would actually matter and hasn't been observed
yet but also hasn't been exhaustively tested against a Timeline-grade-only heavy case.

## Session update (2026-09-07/08): --adaptive caught dropping power mid-render, fixed, re-verified live

The false-light failure mode flagged as "hasn't been observed yet" above got observed
within the hour, on a real export. User ran `sudo resolve_power.py --watch --adaptive`,
then rendered the final grained timeline for real. Sampling `nvidia-smi` through the whole
render showed `power.limit` pinned at **exactly 100.00W (the VR-watchdog floor) for the
last ~45% of the job**, jumping back to 210W within seconds of completion — full power was
active at the start, silently dropped mid-render, and the render finished anyway (valid
1920x1080/673.9s output) but almost certainly slower than it should have been.

**Root cause**: the node-graph scan (`GetToolsInNode` across every clip) came back empty
on at least one poll while the render was actively in progress, which the watch loop
correctly-per-its-own-logic read as "no heavy effect anymore" and called `restore()` —
exactly backwards, since a render in progress is the one moment this tool most needs to
stay at full power regardless of what a content scan says.

**Fix**: the scan subprocess now also reports `proj.IsRenderingInProgress()`, checked
*before* trusting the node-graph result — a render actively running always reads as heavy,
full stop, independent of the scan. Landed in `resolve_power.py`; **note that a process
already running `--watch --adaptive` has the old code loaded in memory and needs a
restart to pick up the fix** (not a hot-reload — plain Python).

**Verification, done carefully after a flawed first attempt**: the first re-test used a
background script that both *started* a render and *held its own scripting-API
connection open concurrently* with the scan's own subprocess connections — every scan
timed out, because simultaneous new `dvr.scriptapp()` connections appear to contend with
each other (a real, separate finding, not this bug), giving a false read on the fix. The
clean version — a script that submits the render job and exits immediately, letting
Resolve's own process own the render server-side, exactly like a GUI-triggered render —
showed 5 consecutive scans during the render all correctly returning `True` in ~0.3-0.4s
each. **Final full-timeline re-render with the fixed, restarted `--adaptive` process
confirmed it end-to-end**: `gpu_limit_w` sampled every 3 seconds stayed at 210W for the
entire render, 17% through 100%, no drop at any point — GPU utilization 93-98% throughout,
job completed in ~43s.

**Unrelated mid-session mistake, self-inflicted, no impact on real project data**: during
the first (flawed) verification attempt, the render's output file was deleted while that
render was still in progress (not after, unlike every other throwaway-artifact cleanup in
this doc) — the job correctly came back `Failed`. Cleaned up via `DeleteRenderJob` on the
now-stale entry. This was a disposable verification file in a scratch directory, not
anything from the user's actual edit.

## Session update (2026-09-24): Speed Editor connected, upstream version check

**Blackmagic Speed Editor connected to this rig for the first time.** Picked up by the
existing `BMDPanelDaemon`/`run_bmdpaneld` control-panel daemon (already running as part
of the base install, see "Bundled Blackmagic tools" above) with no extra setup on this
end. User reports it working well in hands-on use. **Not yet independently verified**
against this repo's own tooling (e.g. whether `resolve_control`'s
`open_control_panel`/`control_panel_status` MCP actions see it, or whether it holds up
across a reboot/session change) — flagging that as open, not claiming more than the
user's own hands-on check covers, per the "user's own eyes" note below.

**Upstream version check, both fronts:**

- **DaVinci Resolve itself**: no release beyond **21.1** (2026-09-08, the native-MCP
  release already covered above) found as of this check. This rig's installed
  `21.1.0.17` appears current.
- **Community `davinci-resolve-mcp` (samuelgursky)**: moved a lot since the last check
  documented here (v2.103.1 → v2.212.1, 2026-09-07, "AI-driven control via MCP" above).
  Upstream is now at **v4.8.20** (2026-09-24) — a version-scheme jump (2.x → 4.x) on top
  of near-daily releases through September, e.g. v4.8.17 "`dry_run="false"` runs the real
  operation" and v4.8.16 "`organize_clips` honours `create_missing="false"`" — the kind of
  correctness fix worth having. **Not pulled onto this rig yet**; same upgrade procedure
  as the v2.212.1 bump applies (`git fetch` + `git merge`, then re-run `validate_client.py`
  before trusting it) — sizing that as its own session rather than doing it inline here,
  given how large the version gap is.

## Session update (2026-09-27): forum outreach — updated Boris Kovalev's original thread

Posted an update to the original `davincirecode10.sh` thread
([viewtopic.php?f=21&t=136365](https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=136365))
pointing at this repo's `prepare_for_resolve.py` as a from-scratch Python rewrite of that
script (credit to Boris Kovalev and the thread's contributors kept explicit — reimplemented
idea, not a fork of his code), plus a current-as-of-21.1 answer to "is a script like this
still needed": **yes, but narrower than in 2021.**

- **AAC decode is still fully unsupported on Linux, any edition** — unchanged across every
  point release checked (21.0.2 → 21.1, see "Linux-relevant changes tracked across recent
  point releases" above). This is now the main reason the script still earns its keep.
- **VFR** handling is still relevant too — a general Resolve quirk, not Linux-specific.
- **H.264/HEVC is more nuanced now, not a hard Linux limitation**: Studio + an NVIDIA GPU
  that `GPUDetect` correctly resolves to CUDA decodes/encodes it natively (NVENC/NVDEC) —
  the real trap is the Wayland/XWayland `GPUDetect`-CUDA-correlation bug documented above
  under "Codec support on Linux", which silently drops those codecs with no error. Native
  X11 fixes it. Free edition, no NVIDIA GPU, or stuck on Wayland: still need to pre-transcode.
- **AC3** is already fixed (decodes since 18.5b1) — no longer needs special-casing, matching
  what the rewrite's `SAFE_AUDIO_CODECS` already assumed.

Deliberately left this repo's MCP/AI-driven-control work out of that post — scoped to a
separate follow-up mention there, not this one.

## Session update (2026-10-01): headless mode for a remote MCP caller

New consumer for this rig's MCP setup: a remote AI agent (Hermes, running on a separate
machine, no GUI, reached over SSH) wired up via `hermes mcp add davinci-resolve --command ssh
--args ...`, on top of the existing `community` backend. Surfaced a gap neither `native` nor
`community` needed to handle before, because both assume a human is already at this rig with
Resolve open (or about to open it) — a remote, unattended caller can't assume that.

**Symptom**: invoking `scripts/resolve_headless.py run -- <community server>` bare over SSH
crashed Resolve on launch, even with `-nogui`: `SIGABRT` in `QApplicationPrivate::init`
(see `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`). Same underlying cause this
script's own header already documents for `native`/`community` (no `DISPLAY`/`XAUTHORITY` in
a static env block) — just reached through a third path: an SSH session has neither, same as
a `.mcp.json` static block doesn't, and `resolve_headless.py` (upstream, not this repo's code)
doesn't set them itself.

**Fix**: added a `headless` backend to `resolve_mcp_wrapper.sh`, reusing this script's
existing `DISPLAY`/`XAUTHORITY` discovery (the `.mutter-Xwaylandauth.<random>` glob above,
already correct — new login means a new random suffix, so it's re-discovered every call, not
cached) ahead of `resolve_headless.py run --`, which adds the guard/start/wait-until-
scriptable/stop lifecycle `native`/`community` don't have and this caller needs (nobody at
the keyboard to notice a stolen session or restart a crashed one). Community backend only —
hasn't been needed for `native` yet.

**Validated, cold start, this rig**:
```
OK: nothing running; safe to start a headless instance.
starting: /opt/resolve/bin/resolve -nogui
ready in 6.0s
stopped in 0.5s
```
`guard` correctly detected a clean state, `-nogui` booted in 6.0s (not the many-minutes worry
an earlier debugging pass had, once the env was actually right), and `stop` tore down cleanly
afterward since this run was the one that started it.

Hermes-side config (the operator's `bridgeai`/`HERMES_ARCHITECTURE.md` project, not this
repo): `hermes mcp add davinci-resolve --command ssh --args iashur
/home/iam/Documents/resolve-linux/pipelines/mcp-benchmark/resolve_mcp_wrapper.sh headless`.

## Session update (2026-10-05): making the headless MCP reliable from Hermes

A Hermes run reported `name is required` (`create_timeline_from_clips`), `missing track_type`
(`add_track`) and `Provide clip_ids ... or clip_infos` (`append_to_timeline`) and fell back to
ffmpeg. Reproduced live against the `headless` wrapper; two separate causes:

1. **Calling convention, not a headless limitation.** Every community tool has the signature
   `tool(action: str, params: object|null)`. Arguments must be nested:
   `media_pool(action="create_timeline_from_clips", params={"name": "...", "clip_ids": [...]})`.
   Passing `name`/`clip_ids`/`track_type` at the top level is silently dropped by schema
   validation and the action sees empty params -- exactly the three errors above.
   A `params` given as a JSON *string* is accepted. Tell the agent's skill to always use
   `params={...}`.
2. **Stdout pollution (fixed).** `resolve_headless.py run` prints `starting:` / `ready in` /
   `reusing the running headless instance` to stdout, which is the MCP JSON-RPC channel;
   the Python `mcp` client dies with `Invalid JSON: expected value at line 1 column 1`.
   `resolve_mcp_wrapper.sh headless` now goes through `resolve_headless_stdio.py`, which
   sends those lines to stderr (fd 1 untouched, so the server's frames still flow).

The only headless restriction that stands is the one already documented: no MCP render queue
(render with ffmpeg); timeline edits and transforms work.

## Session update (2026-10-05): Resolve Studio 21.1.1 upgrade & community MCP v4.8.28 bump

Upgraded Resolve Studio from 21.1.0 (`21.1-mrd1.10.1`) to **21.1.1** (`21.1.1-mrd1.10.1`,
binary `21.1.1.0010`) using `pipelines/resolve-update/install_resolve.sh` with MakeResolveDeb
1.10.1. Stale `.deb` files from previous builds were quarantined to prevent multi-version
install conflicts, and a check was added to `install_resolve.sh` to guard against this.

**Vendored community MCP server (`samuelgursky/davinci-resolve-mcp`)**:
- Bumped upstream to **v4.8.28** (covering v4.8.27 Linux temp-path check and v4.8.28 ffmpeg stdin hang fixes).
- Re-adapted `app_control-headless-restart.patch` to preserve both the headless `-nogui` flag and upstream's new `stdin=subprocess.DEVNULL` parameter.
- Offline unit tests passed (3997 tests, 0 failures, 18 skipped).

**Verification**:
- `post_update_driver_check.sh`: all checks passed (Kernel 6.12.111, NVIDIA 595.71.05-1 DKMS installed, all 4 patches applied).
- `validate_client.py`: live validation against headless Resolve 21.1.1 passed 100% of checks (project loading, clip renaming, LUT roundtrips, real media imports, still captures, render & loudness gate, and negative assertions). `client.fallbacks` stayed empty throughout.
- Benchmark suite (`bench_01_simple.py`, `bench_02_batch_rename.py`): verified across native and community backends.
- Visual grading & pixel-diff verification (`grain_timeline_toggle.py`): run against real project `test1`, confirming node graph toggling and pixel-exact restore (3,223 px changed on, 0 px on restore).
- Neural speech-to-subtitles (`bench_03_subtitles.py`): verified live (`CreateSubtitlesFromAudio`, 9 items, 564.6ms).
- Export presets (`render_benchmark.py --list-presets`): all 31 delivery presets listed and operational.

## Why this matters (context, not a how-to)

Resolve was already validated working on the user's main system. This separate rig
re-validates it specifically on the patched NVIDIA 595-open driver used for an unrelated
VR headset project running on the same machine — the goal is to catch any
artifacts/instability under that driver BEFORE the user migrates their main system to
it. **Visual/editing-quality verification needs the user's own eyes** — a process that
launches and stays up is not the same as "it works well"; don't claim the latter from
logs or a process check alone. The RAM shortfall documented above is exactly the kind of
thing that could produce instability under real load without showing up in a basic
launch check — a specific reason to weight the user's own hands-on testing here more
heavily than usual, not less.

There's also a longer-term, not-yet-scoped idea: eventually let this machine be rented
out (via a separate tool, "pmadminka") for either a gaming session or a Resolve editing
session. Irrelevant to getting the install itself working, just context for why Resolve
running well on *this specific* machine matters beyond one person's own use.

## License

MIT — see [LICENSE](LICENSE).
