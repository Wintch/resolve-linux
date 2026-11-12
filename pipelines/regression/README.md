# Regression suite

One command that tells you whether the Resolve + MCP rig still works after something underneath it
changed. Run it after upgrading any of:

- **DaVinci Resolve** (the scripting API, and the vendor `ResolveMCP` server, which ships inside it);
- **the community server** (`davinci-resolve-mcp`, its venv or its npm bootstrapper);
- **the NVIDIA driver or the kernel** (decoding, rendering, `ExportFrame`, the GPU sandbox);
- **this repo's wrapper and shims** (`../mcp-benchmark/resolve_mcp_wrapper.sh`, `resolve_xvfb_stdio.py`,
  `mcp_filter.py`, the headless patches).

Every check asserts on values that prove the feature works (a frame changes and comes back byte for
byte, a duration changes by the expected amount, a removed effect is really gone), not on "the call
returned without an error".

## Running it

On the machine that has Resolve installed, with **nobody's Resolve open** (the suite starts and stops
its own instance and refuses to start next to a running one):

```bash
cd pipelines/regression
./run.sh                      # everything, a few minutes
./run.sh --offline            # only the offline checks: no Resolve is started, safe at any time
./run.sh --only t30,t40       # a subset (t10 = ../mcp-benchmark/validate_client.py)
./run.sh --offline --update-baselines   # accept this install's API surface (see "Baselines")
./run.sh --gui-xvfb           # also run the GUI-under-Xvfb backend check in t50
./run.sh --list               # modules found, with their one-line description
```

Requirements: the community server checkout with its venv (`~/resolve-install/davinci-resolve-mcp`, or
set `RESOLVE_MCP_COMMUNITY_REPO`; `t25` also starts its Node server, so `node` and its `npm ci`), `Xvfb`, `ffmpeg`,
`fc-list`, and the `MCP-Benchmark` fixture project built once
with `../mcp-benchmark/setup_bench_project.py`.

What `run.sh` does, in order:

1. Refuses (exit 2) if any DaVinci Resolve is running, and prints it: that is either a person's session
   or a leftover, and neither is touched. Resolve is found by executable (`/proc/<pid>/exe`) as well as by
   command line (`rlib.resolve_processes`), so one started through a symlink or another path counts too.
2. Exports the scripting environment (`DISPLAY`, `XAUTHORITY`, `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`,
   `PYTHONPATH`) from `../mcp-benchmark/bench_lib.resolve_env()` and uses the community venv's Python;
   refuses (exit 2) if that discovery fails.
3. Clears this suite's old reports and runs the offline modules.
4. Starts **one** shared `-nogui` Resolve on a private Xvfb (`resolve_xvfb_stdio.py`, display `:99`) and
   runs every live module inside it, in order, through `live.py`. After them `live.py` always sweeps the
   fixture (the `live_cleanup` report): leftover `zz_reg_` timelines, projects and Media Pool clips are
   deleted (a WARN, since a module should have removed them itself) and `bench_tl` must still hold its 8
   items. The whole block has a time limit (one module timeout per module, plus one, plus 10 minutes).
5. Runs the modules that manage their own Resolve sessions, outside the shared one.
6. Checks that no Resolve and no Xvfb (on the session's display or `:99`) is left running (`post_run`).
7. Prints one line per module with its OK/WARN/SKIP/FAIL counts, then every FAIL and WARN line. Every
   selected module, `live_cleanup` and `post_run` must have written a report: a missing one is a FAIL
   (the `missing_reports` line), so a run that was killed half way never passes.

Exit code: 0 when nothing failed (WARN and SKIP allowed), 1 on any FAIL, 2 when it refused to start, 130
when interrupted (Ctrl-C or TERM: the running module is stopped, `live.py` still sweeps the fixture, and
the process check and summary still run). The JSON reports stay in `rlib.REPORTS` (by default
`~/Videos/mcp-bench-scratch/regression/reports`, override with `REGRESSION_REPORTS`). A module that hangs
is killed after `REGRESSION_MODULE_TIMEOUT` seconds (default 1800) and reported as a FAIL.

## Modules

Numbering decides where a module runs: `t00`-`t09` offline, `t10`-`t49` inside the shared live
session, `t50`-`t99` outside it.

| Module | Where | Covers |
|---|---|---|
| `t00_install.py` | offline | Resolve packages agree with the binary's version (held packages count as installed); the binaries resolve all their shared libraries; the community checkout (describe, npm and server versions, the two local patches: `-nogui` relaunch and npm `allowScripts`); the API-surface baselines: scripting stub methods, their parameters and option TypedDict keys, vendor tools and their input properties, community compound tools with their properties, actions and capabilities, versions; every documented community action present in the server's dispatch; the fonts Fusion titles need (Open Sans, "Segoe UI" Black), installed system-wide, where Fusion looks |
| `t10` = `../mcp-benchmark/validate_client.py` | live | end-to-end `ResolveClient`: project and timeline selection, clip rename and restore, LUT round trip, quoted project name, media import, stills and frames, an OFX effect proven with exported frames, render plus loudness gate, and negative cases that must raise |
| `t20_community.py` | live | the community server against what Resolve reports: `get_version`, fixture load, timeline pinning (`timeline_name` / `timeline_id`) for reads and for a write, `timeline_item_id` lookups, `ignored_params`, the `.drx` timeline-grade check (absent and empty `<pTrackVer>` refused, present not), `duplicate` without switching timelines, the FCPXML round trip (the server's comparison, the exported file, the imported timeline read through Resolve, and whether the current timeline is restored), `import_media` outside Media Storage, and a full render (import, timeline, preset, job; ffprobe frame count, audio duration and level of the output) in a throwaway project |
| `t25_editorial.py` | live | a real transition (`add_transition`) and what it does to `item_index` (moves by one) and `timeline_item_id` (stable); the Node server's `drx generate` with `target: "timeline"` (timeline grade written, applied with `apply_grade_from_drx source="timeline"` after the `confirm_token` step, proven by the exported frame); the bundled subtitle presets (Word Highlight, Lollipop) rendered with burned-in subtitles, text present in the frame |
| `t30_vendor.py` | live | the vendor `ResolveMCP`: status, API and doc tools (the served stub is the installed one), rendered frames (`get_image`, `get_thumbnails`), `describe_grade`, the LUT and DCTL file tools (validation included), the Extras listing (read only), the `run_script` sandbox boundary (imports, builtins and the file audit hook) |
| `t40_api212.py` | live | the Resolve 21.2 scripting additions through the vendor `run_script`: effects (add, toggle, delete, proven with exported frames), `ExportFrame` / `ExportThumbnails` options, positions, gaps, `MoveItems`, trims, `RippleDeleteSilence`, AutoColor, CDL, `CopyAttributes`, node label / color / enable, timeline enable, read-only status getters |
| `t50_backends.py` | own sessions | the wrapper backends: a shared `-nogui` session started and stopped by its last client, `mcp_filter` hiding `run_script_unsafe`, the headless backend stopping its Resolve on SIGTERM, the SSH forced command (both Resolve entries and an arbitrary command), clients that exit cleanly on EOF and leave no server process or client registration behind, and (with `--gui-xvfb`) a GUI Resolve under Xvfb (assumes the last project was not left on the Color page) |

The docstring at the top of each module is the authoritative list of what it checks.

## Reading the results

| Status | Meaning | What to do |
|---|---|---|
| **OK** | the feature works; the detail shows the values that proved it | nothing |
| **SKIP** | cannot run in this setup: the tool or API is not there (an older Resolve or server), or the mode cannot do it (`-nogui` has no pages, so no stills) | nothing, unless you expected the feature to exist |
| **WARN** | it ran, but something changed that a person should look at: new tools or actions since the baseline, a module that left scratch objects behind, a renamed fixture item | read the detail; update the baselines or fix the module |
| **FAIL** | broken behaviour, something removed since the baseline, a module that crashed, hung or left a Resolve running | investigate before relying on the rig |

Capabilities are detected by asking where possible (is the tool listed, does the API method exist, does
the tool document the parameter), so the same suite runs against older and newer releases; `t40` also
gates on Resolve >= 21.2 for the calls that release added. Absence is a SKIP only for something the
accepted baselines never had: once a tool, action, method or capability is in `baselines/`, losing it is
a FAIL (the whole vendor server or stub going missing included).

## Baselines

`baselines/` records the API surface of the last accepted install: the scripting stub's methods, their
parameters and the option TypedDict keys (e.g. `RemoveSilenceOptions.minimumToStrip`); the vendor
server's tools and their parameters; the community server's tools, their parameters, actions and the
capabilities the live checks rely on (`rlib.COMMUNITY_CAPS`); and the versions. After an upgrade:

1. Run `./run.sh`. Something **removed** is a FAIL (an upgrade dropped or renamed it), something
   **added** is a WARN, a version change is a WARN.
2. Review what changed: look for callers of anything removed (the client in `../mcp-benchmark`, the
   wrapper, your own scripts) and fix them; skim what was added.
3. Once `./run.sh` passes, accept the new surface with `./run.sh --offline --update-baselines`. This is
   the only way to rewrite the baselines (`run.sh` refuses `--update-baselines` without `--offline`); the
   report lists what was removed and added against the old files, and the versions baseline is not
   rewritten while a value it records cannot be read.
4. Commit the changed `baselines/*.json` together with any fixes, so the diff documents the upgrade.

## Safety

- **Fixture only.** Live checks open only the `MCP-Benchmark` fixture project and `zz_reg_` throwaway
  projects they create and delete again; no other project is opened, modified or deleted.
- **Scratch objects only.** Everything a check creates (timelines, projects, Media Pool clips, files) is
  named with the `zz_reg_` prefix and removed in a `finally` block; `live.py` sweeps any leftover
  timeline, project or clip after the live modules. The synthetic media files in `rlib.SCRATCH` are kept
  as a cache (the clips made from them are removed from the Media Pool).
- **Never next to a person's session.** `run.sh` refuses to start while any Resolve is running, the
  shared session refuses a Resolve on another display, and a live module started by hand refuses to run
  (`rlib.main_guard`): every Resolve must be the `-nogui` one on the module's Xvfb display, and `live.py`
  must have marked the session. Nothing in the suite kills a Resolve it did not start (a leftover is
  reported, not killed). The vendor server is reached through `mcp_filter.py`, so no check can call
  `run_script_unsafe`.
- **No global side effects.** No Extras packages are installed or uninstalled and no user preference
  presets are loaded. Renders and imports stay inside `~/Videos` (a Media Storage volume), except two
  deliberate probes in `t20` that use `zz_reg_` directories under the system temp dir and remove them:
  the FCPXML round trip (the server only writes there) and the import of a file outside every Media
  Storage volume (into the throwaway render project).

## Adding a check

Add it to the module whose area it belongs to, or create a new `tNN_<area>.py` in the right number
range; `run.sh` picks up any `t[0-9][0-9]_*.py` by itself. Run a live module with `./run.sh --only tNN`
(its `main_guard` refuses to run outside the shared session). Use the shared helpers in `rlib.py`:

```python
#!/usr/bin/env python3
"""Live checks of <area> (first docstring line is what `run.sh --list` shows)."""
import rlib
from rlib import Skip, Warn, expect


def main():
    rlib.main_guard()                       # live modules only: refuse outside the suite's shared session
    suite = rlib.Suite("t45_example")
    with rlib.vendor() as v:
        try:
            tl = rlib.scratch_timeline(v, "example")   # zz_reg_example, a copy of bench_tl

            def items_on_v1():
                if "run_script" not in v.tools():
                    raise Skip("this ResolveMCP has no run_script")
                n = rlib.run_script(v, rlib.FIXTURE_PRELUDE + f"result = len(timeline({tl!r}).GetItemListInTrack('video', 1))")
                return expect(n == 8, f"{n} items on V1, expected 8")

            suite.check("scratch copy keeps the 8 items", items_on_v1)
        finally:
            rlib.drop_scratch(v)            # rlib.drop_scratch_clips(v) too if the module imported media
    return suite.finish()


if __name__ == "__main__":
    raise SystemExit(main())
```

Rules for a check:

- return a short detail string with the values that prove it worked; raise `Skip` when the tool or API
  is absent (and was never in the baselines), `Warn` for drift a person should review, anything else
  (`expect` raises `AssertionError`) for a failure;
- a failing check never stops the module, so leave the fixture as you found it in `finally`;
- community tools are compound: `rlib.ca(client, "timeline", "get_items", {"track_type": "video", "index": 1})`;
- a render target directory must already exist inside `~/Videos` (anything else makes a `-nogui` Resolve
  block on a dialog it cannot show), and imports should come from a Media Storage volume
  (`MediaStorage.AddItemListToMediaPool` refuses anything else; `MediaPool.ImportMedia` does not check).
