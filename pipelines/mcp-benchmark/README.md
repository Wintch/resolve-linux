# mcp-benchmark

Native-vs-community MCP server benchmarking, a unified client, and the live
probes/workflow tests that validated
[../../MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md)'s catalog against a
real, connected DaVinci Resolve instance — not just read from the code.

## What's here

- **`resolve_client.py`** (`ResolveClient`): native-first, raising,
  timeout-guarded client over both MCP servers — `import_media`,
  `grab_still`, and a `render()` wrapper with a loudness gate. Falls back to
  community only when native genuinely can't serve a call, and tracks every
  fallback so a caller can assert native-first held.
- **`validate_client.py`**: the live end-to-end test for `resolve_client.py`,
  against the disposable `MCP-Benchmark` fixture project only (never a real
  project). Asserts on the *value* every call returns, not just "didn't
  raise" — including negative checks that a bad call actually raises.
- **`setup_bench_project.py`**: idempotent fixture setup (`MCP-Benchmark`
  project, `bench_tl` timeline, 8 clips) that `validate_client.py` and the
  `bench_*` scripts assume already exists.
- **`bench_lib.py`**: shared connection/timing helpers for driving both
  servers (native and community) over stdio with the same code, plus this
  rig's Wayland/Xwayland `XAUTHORITY` workaround (`resolve_env()`).
- **`bench_01_simple.py` / `bench_02_batch_rename.py` / `bench_03_subtitles.py`**:
  native-vs-community timing benchmarks for representative operations.
- **`grain_timeline_toggle.py`**: real, pixel-diffed grade verification (not
  a "still exists" check) — proved timeline-level grain toggling via
  `Timeline.GetNodeGraph()` actually changes rendered pixels.
- **`audio_loudness_check.py`**: export-time YouTube-loudness gate via
  `ffprobe`, since Resolve's own API can set a normalization target but has
  no measurement/readback method.
- **`resolve_mcp_wrapper.sh`**: what the repo-root `.mcp.json` points MCP
  clients at — resolves the right env (`RESOLVE_SCRIPT_API`, the Xwayland
  auth cookie) for whichever backend (`native`/`community`) is requested.

See the main [README.md](../../README.md)'s "AI-driven control via MCP"
section for how this harness fits into the rest of the repo, and
[../../MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md) for the tool/action
catalog these tests were run against.

## Live probe results — 41/41 read-only calls, all succeeded

Ran via a checkpointing script (`mcp_probe.py`, saved every 10 calls) against
the connected Studio instance. All 41 returned structured JSON with no
exceptions. Selected results:

```
resolve_control.get_version   -> product="DaVinci Resolve Studio", version_string="21.0.4.5"
project_manager.list          -> {"projects": ["test1"]}
project_manager.get_current   -> {"name": "Untitled Project", "id": "6964d1e6-..."}
project_manager_database.get_current -> {"db_type": "Disk", "db_name": "Local Database"}
media_storage.get_volumes     -> {"volumes": ["/home/iam/Videos"]}
media_pool.get_root_folder    -> {"name": "Master", "id": "49848a3b-..."}
render.get_formats            -> 22 formats (AVI, BRAW, DCP, DPX, EXR, JPEG2000/HT-J2K, MXF OP1A/Atom, MOV, ...)
media_analysis.capabilities   -> ffprobe, ffmpeg, and whisper_cli all detected as available
knowledge.capabilities        -> 35 knowledge topics indexed (2 repo, 12 workflow, 7 guide, 10 kernel, 4 reference), 62 aliases
graph.get_num_nodes (no timeline open) -> {"error": {"code": "NO_CURRENT_TIMELINE", "category": "precondition",
                                             "retryable": false, "remediation": "Open a timeline via ..."}}
```

`media_analysis.capabilities` correctly auto-detected the optional extras
installed earlier in this session — including finding `whisper` at
`~/resolve-install/davinci-resolve-mcp/venv/bin/whisper`, proving the venv
wiring is actually correct end-to-end, not just `pip install`-successful.

## Real workflow test: AAC audio, timeline edit, draft render — and what it actually found

The 41-action probe above was read-only and against an empty project. This section is
the opposite: a real mutating workflow (import real footage, build a timeline, render)
against the user's own `test1` project and real source clips
(`~/Videos/oldback_nvme/*.mp4`, H.264 video + AAC audio, matching the codec finding in
the main README). It surfaced problems the read-only probe never could.

### 1. AAC decode failure — confirmed at the sample level, not just a spec-sheet claim

Importing an AAC-audio clip into the Media Pool succeeded and even reported
`Audio Codec: AAC` as a clip property — that's just container-metadata reflection,
not proof of decode. Actually pushing it through the render pipeline surfaced the
real behavior, directly in `~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`:

```
IO.Audio | ERROR | Failed to decode clip <.../sample_footage.mp4>, track: 0,
                    position: 2192640 - Failed to decode the audio samples.
```

Repeated **hundreds of times**, at the same handful of sample positions, in an
apparent retry loop — never succeeding, never failing fast. This is the first
first-party, log-level confirmation (not a vendor PDF, not a container-metadata read)
that **AAC genuinely does not decode on this Linux/Studio/NVIDIA combination** — matches
the official codec table in the main README exactly.

**The retry storm is what actually broke the render**, not raw slowness: a render job
covering ~6.6 seconds of 720p footage reported an estimated **5 hours** remaining. The
renderer wasn't grinding through real work — it was stuck retrying audio decode that
was never going to succeed, with no failure/timeout path exposed to the caller.

**Fix confirmed working, for the decode error specifically**: `ffmpeg -c:v copy -c:a
pcm_s16le` (video stream-copied untouched since H.264 decode itself is fine on
Studio+NVIDIA, only audio transcoded to PCM) eliminated every `IO.Audio` error in a
re-run — a real, working workaround for the AAC problem itself, not just an assumption.

### 2. A second, independent problem: the render pipeline doesn't recover cleanly after being interrupted

Stopping a render (`StopRendering()`, called because of the 5-hour estimate) left the
application in a degraded state that outlasted the specific AAC bug:

- **In GUI mode**: every subsequent `ImportMedia`, `SetCurrentFolder`, and even
  `Quit()` call **silently returned `None`** — no exception, no error field, nothing.
  Root cause, found by directly enumerating X11 windows (`xwininfo -tree`) since the
  API itself gave no signal: a modal dialog (a render-path validation prompt, then
  later a save-changes-on-quit prompt) was silently blocking every scripting call
  until a human clicked it — exactly what the project's own docs warn about
  (`resolve_control.runtime_mode`'s docstring), confirmed the hard way.
- **Killing the process while blocked on that dialog caused a real crash** (Blackmagic's
  own "Problem Report" crash handler triggered), not a clean exit — reproduced twice.
  `resolve.Quit()` does not pre-empt or avoid the save-changes prompt it triggers.
- **Headless mode (`-nogui`) avoided the dialog-blocking class of problem** (confirmed:
  a `DeleteRenderJob` call that returned `None` in GUI mode returned `True` immediately
  after relaunching headless) — but did **not** fully fix the underlying issue. After
  the render pipeline had been in a stopped/interrupted state, a fresh render attempt
  in headless mode got stuck at **0% completion with the estimated time climbing** (9s →
  148s and rising) while the process burned ~90% CPU with **zero new log output** for
  4+ minutes and 0% GPU utilization — a real internal stall/deadlock, not a dialog, since
  headless has no GUI to block on. The partial output file it did write had no `moov atom`
  (`ffprobe`: "Invalid data found when processing input") — genuinely corrupt, not just slow.
- **The only reliable recovery found**: a full process kill + clean relaunch. Confirmed
  twice — once fixed the GUI-mode `ImportMedia` lockup, but the fresh headless instance
  hit its own stall on the very next render attempt, suggesting the trigger is something
  about *interrupting a render*, not something specific to one process instance.
- **Important nuance, found afterward**: the crash-on-close only reproduced when the
  process was killed *while a render was still actively stuck/in-progress*. When the
  user instead **cancelled the stuck render job through the GUI first, then exited
  normally**, the app closed cleanly with no crash dialog. So the dangerous sequence
  specifically is kill-while-rendering, not "close Resolve after a render has gone
  wrong" in general — cancel the job before quitting, and normal shutdown works.
  Reinforces that this is a render-lifecycle-specific fragility, not a general
  instability in the app.
- **User-reported prior data point, tempered**: the user recalls H.265 rendering working
  fine previously on a Debian 12 install with an older Resolve version — but explicitly
  flagged that setup had **no MCP/scripting connection active at all**, so it's not a
  clean A/B comparison. Worth keeping as a lead (Debian 12 vs. 13, older Resolve version,
  or the scripting connection itself could each be a variable) but not yet isolated to a
  specific cause — flagged as a hypothesis, not a finding.
- **Also reproduced live in this session**: setting the render codec to H.265 through
  the GUI dropdown (which *does* list it as selectable, unlike what
  `Project.GetRenderCodecs()` reports via the API — a real inconsistency between what
  the GUI offers and what the scripting API says is available) and starting that render
  led to the same stuck-with-blocking-dialog pattern. The user separately reported that
  **cancelling an in-progress render job can itself hang** — not yet disentangled from
  the `File Destination` dialog re-appearing at the same time, which was confirmed
  independently blocking the GUI in that window. Needs a cleaner repro (confirm no
  dialog is open, then cancel a genuinely running render) before treating "cancel itself
  hangs" as a distinct bug rather than the same dialog-blocking issue recurring.
- **Converged root cause, confirmed the following round**: the same H.265 attempt
  reproduced with the `File Destination` window still present per `xwininfo` but
  confirmed `Map State: IsUnMapped` (a stale/invisible Qt object, not actually blocking
  anything) — yet the app was still fully unresponsive to the user. Process inspection
  (`ps -o stat,wchan`, `/proc/<pid>/status` thread states) found **no threads in
  uninterruptible I/O wait (D state)**, one thread pinned at ~99% CPU in `do_sys_poll`,
  377 threads total. That rules out "waiting on a dialog" or "waiting on disk/network
  I/O" as the mechanism here — this is a **genuine livelock/spin**, not a blocked wait.
  Combined with the earlier headless stall (~90% CPU, 0% GPU, zero log output, corrupt
  output file) and the AAC retry storm (hundreds of repeated failed decode attempts,
  never giving up), **the pattern converges across three independent triggers — AAC
  audio decode, ProRes video encode, H.265 video encode — and two independent modes
  (GUI, headless): once the render pipeline gets into a bad state, it does not time out,
  does not fail, and does not recover. It spins.** The only fix found across every
  reproduction was killing the process and relaunching.

### What this means for "is it worth forking"

The read-only/informational surface (Section above, 41/41) is genuinely solid — clean,
typed, well-documented. The gap is specifically in **render-pipeline resilience once a
render has been started and then interrupted or hit a slow/failing clip** — silent
`None` returns with no diagnostic signal, dialog-blocking in GUI mode, and a distinct
stall/deadlock mode even in headless mode that the project's own headless-mode
documentation doesn't claim to fix. This is a narrower, more specific gap than "the
whole project needs rework" — the fix that seems warranted isn't a full fork, but a
**resilience wrapper**: detect a stuck render (0% completion + static output file size
over N seconds), detect a `None` return where a real value was expected and treat it as
a probable-blocked-dialog signal, and default to "kill and restart" as an explicit,
logged recovery path rather than something a human has to diagnose by hand (as was done
here). Worth checking `docs/reference/api-limitations.md` in the checkout and possibly
filing this upstream before assuming a local patch is needed — this may already be a
known, curated gap.

## Final data point before stopping: thumbnails stopped rendering too

After the last kill+restart cycle (following the H.265 livelock), Media Pool clip
thumbnails came back as generic music-note icons instead of real video previews, and
the Cut page's viewer stayed black with the playhead sitting mid-clip and a waveform
visibly present underneath. GPU showed memory allocated (1.4GB/8GB) but near-zero
utilization (5%) — alive, just not being used for thumbnail/preview generation. Not
investigated further (session paused here by the user's call) — plausibly the same
underlying media/GPU pipeline degradation as everything else in this section, but not
confirmed. **Worth checking first thing next session**: whether a full graphical
session restart (not just Resolve) clears it, which would point at GPU/driver-level
state (texture cache, GL context) rather than something inside Resolve's own process.

## Investigation paused here — what's next, not done in this pass

The user called a stop to live debugging at this point given how much was already
found and documented; this is a deliberate pause, not a dead end. Picking this back up:

- **Root-cause the render livelock** before anything else — three independent triggers
  (AAC decode retry storm, ProRes encode, H.265 encode) and two modes (GUI, headless)
  all converge on the same spin/livelock signature. That convergence is the strongest
  lead: whatever's common to all three (the render/encode subsystem's internal state
  machine, most likely) is where the actual bug lives, not the codec-specific paths.
- **Check whether a full graphical session restart** (not just Resolve) clears the
  thumbnail/preview rendering gap noted above — cheap to test, would meaningfully
  narrow whether this is Resolve-process-local or GPU/driver/session state.
- Only 41 of 667 actions were exercised, and only against an empty project —
  deliberately conservative (no `set_*`/`create`/`delete`/`execute_*` against
  real project state without asking first). Mutating-action testing belongs
  in a real editing/export workflow test, not a blind capability sweep. (The
  catalog has since grown to 718 actions across a v4.8.22 update — see the
  2026-09-26 update below — so the untested fraction is larger still, not
  smaller.)
- The Node "advanced" (offline `.drp`/`.drt`/`.drx`) server's 18 tools are
  cataloged in the README but not yet live-probed the way the Python server
  was here.
- Granular mode (`--full`, then 353 tools) not probed — the compound server
  is what's actually planned for use.

All of the above is unchanged since 2026-08-24 and **not re-verified** against
the current community server version or Resolve 21.1 — see the next section
for what *was* re-checked.

## Update (2026-09-26): community server bumped to v4.8.22, re-validated end-to-end

The community server (`~/resolve-install/davinci-resolve-mcp`) had been sitting
at v2.212.1 since it was first cloned (2026-09-07) despite a later note in this
repo's own history claiming it had moved — it hadn't. Brought current via
`git pull --ff-only` (76 commits) to **v4.8.22**, `npm install`, and confirmed
the venv's Python deps already satisfied the updated `requirements.txt`.

**Verified, not just installed:**
- `npm run smoke` → OK, reports v4.8.22.
- The updated `server.py` imports cleanly under the existing venv.
- The vendor's own offline test suite: **3932 tests, 0 failures** (18 skipped),
  via `python -m unittest discover`.
- Live tool listing against the running compound server confirms
  `gallery_stills.grab_and_export` — the one community-specific action
  `resolve_client.py` calls directly — kept the exact same parameters this
  repo's code depends on.
- `validate_client.py`, run for real against the `MCP-Benchmark` fixture and a
  live Resolve **21.1.0.17**: **all checks passed** — rename, LUT
  generate/list/delete, quoted project names, real media import, `grab_still`,
  a real render with a loudness gate, and every negative check (bad calls
  actually raising). `client.fallbacks` stayed empty, confirming native-first
  routing held throughout — community wasn't even exercised by this run.

**One real regression found in the diff, not just additions**: `script_plugin`
lost its `execute` and `run_inline` actions, removed upstream in v3.0.0 ("the
server no longer executes caller-supplied code, and every plugin write is
gated"). Neither was ever called by this repo's client code, so nothing here
needed a fix — but it's the one place the tool surface shrank rather than grew.
See [../../MCP-CAPABILITIES.md](../../MCP-CAPABILITIES.md) for the full
before/after catalog counts.

**What this update did *not* do**: re-run the render-livelock investigation or
the AAC workflow test above against v4.8.22 / Resolve 21.1 — those findings are
still open per the "what's next" list above, unrelated to and unaffected by
this MCP server version bump.


## Update (2026-10-02): `restart_app` was silently dropping headless mode

A health re-check (from the `bridgeai`/Hermes side — see that project's
`HERMES_ARCHITECTURE.md` for the full incident) found `resolve_headless.py
status` reporting `headless: False` hours into what should have been a
headless-only deployment. Root cause, confirmed via `ps -o pid,ppid`:
Resolve's parent process was the MCP `server.py` itself — a `restart_app`
MCP tool call (`src/granular/resolve_control.py:restart_app` →
`src/utils/app_control.py:restart_resolve_app`) had cleanly quit and
relaunched Resolve. On Linux, that relaunch is
`subprocess.Popen([resolve_path])` — **no `-nogui`, no args at all** —
unlike `resolve_headless.py`, which always launches with `-nogui`. Any
`restart_app` call on this community server silently drops headless mode,
with no error or warning anywhere in logs.

**Fix**: one line, Linux branch only —
`subprocess.Popen([resolve_path, '-nogui'])`. Patch file:
[`patches/app_control-headless-restart.patch`](patches/app_control-headless-restart.patch),
applies cleanly to `~/resolve-install/davinci-resolve-mcp`'s
`src/utils/app_control.py`. Committed locally in that repo
(`e7bcfd1`) — **not pushed upstream**, since `origin` there is
`samuelgursky/davinci-resolve-mcp` (the original author's repo, not
ours). That local commit is at risk of being lost on the next `git pull
--ff-only` update of the vendored copy (see the 2026-09-26 update above —
this repo *does* periodically pull fresh upstream versions), so the
patch file here is the actual source of truth going forward. **After any
future `git pull`/reinstall of the vendored `davinci-resolve-mcp`,
reapply with:**

```bash
cd ~/resolve-install/davinci-resolve-mcp
git apply ~/Documents/resolve-linux/pipelines/mcp-benchmark/patches/app_control-headless-restart.patch
```

**Also fixed as part of the same incident** (operator confirmed Resolve
wasn't in use at the time, so this was safe to do live): the already-GUI
instance was cycled back to headless. `resolve_headless.py stop` refused
(not answering scripting calls); `--force` reported success but the
process was still alive (the already-known unreliable-force-stop
behavior — always verify with `pgrep`, not the tool's own report); manual
`SIGTERM` → `SIGKILL` was needed, which triggered the `crash_archive.txt`
gotcha documented earlier in this project (moved aside, not deleted);
`resolve_headless.py start` then succeeded cleanly. Confirmed via
`status`: `running: True, headless: True`, responsive. The MCP server
process itself never went down through any of this.
