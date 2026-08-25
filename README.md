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
  AI-driven control of Resolve via its scripting API, live-probed against a real instance.
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

### Guide backlog — not built yet, tracked so it isn't lost

- **Multicam AI auto-edit**: Resolve's built-in feature for detecting who's speaking
  across synced camera angles and auto-cutting between them. Needs: a guide for doing it
  well by hand, an evaluation of how much of it the scripting API actually exposes for
  automation, and a call on whether a non-Resolve equivalent is even worth attempting
  (this one leans "hard to fully replace outside Resolve" — multicam sync + AI speaker
  detection isn't a quick ffmpeg job — but worth confirming rather than assuming).

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
  full account and next steps in [MCP-CAPABILITIES.md](MCP-CAPABILITIES.md#final-data-point-before-stopping-thumbnails-stopped-rendering-too).
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
  [MCP-CAPABILITIES.md](MCP-CAPABILITIES.md) used for the original AAC-decode-failure
  finding).
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
- **Build the effect-by-effect performance benchmark harness** described above — this
  session only proved the methodology on one effect (`Stabilize()`).
- **Full hands-on edit/grade/export verification** by the user, end to end on
  `resolve_linux_validation` — everything above is scripted/API-verified, which is real
  signal but is explicitly not the same bar as a human actually editing (see "Why this
  matters" at the bottom of this doc).

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
[MCP-CAPABILITIES.md](MCP-CAPABILITIES.md#1-aac-decode-failure--confirmed-at-the-sample-level-not-just-a-spec-sheet-claim)
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

## AI-driven control via MCP — installed, connection pending

[samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp)
(v2.103.1) exposes Resolve's official scripting API to any MCP client, plus an optional
offline file-level server. This turned out to be a **far more mature project than the
original "expect a thin wrapper, plan to fork" assessment assumed** — 100% scripting-API
coverage, 338/338 live-tested methods passing, a browser control panel, and a second
independent server for offline `.drp`/`.drt`/`.drx` work. Forking is no longer the
obvious next step; using it as-is and evaluating gaps first is.

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
- **No MCP client configured yet** (Claude Code / Claude Desktop / etc.) — deliberately
  skipped for now, decide later how/where to wire it in.

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
catalog (36 tools, **667 actions**, extracted directly from the running server's own
docstrings, not the vendor README's summary numbers) plus a 41-action live read-only
probe against the connected Resolve instance — checkpointed to disk every 10 calls so
a crash mid-run couldn't lose progress. All 41 succeeded, every error came back
structured (`{code, category, retryable, remediation}`) rather than a raw exception,
and `media_analysis.capabilities` confirmed the optional extras venv wiring is correct
end-to-end (found `whisper` at the actual venv path, not just `pip install`-successful).

### The fork question — answered with a real workflow test, not just reading the code

The earlier plan in this repo was "expect to fork/heavily modify — the upstream project
looked thin." Having read the code and docs, that assessment didn't hold on paper:
100% API coverage, an independent offline server, a control panel, source-media-safety
guarantees, and an explicit "what this does not do" section are not signs of a thin
wrapper. But reading the code isn't the same as driving it — a real test (import AAC
footage, build a timeline, render draft quality; full account in
[MCP-CAPABILITIES.md](MCP-CAPABILITIES.md#real-workflow-test-aac-audio-timeline-edit-draft-render--and-what-it-actually-found))
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
