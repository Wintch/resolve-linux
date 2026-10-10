# Resolve 21.x scripting API additions: what was actually tested

Resolve's changelog (`get_whats_new`, 21.0 through 21.0.4) lists new Scripting API
calls. This file records which of them were exercised live on this rig
(Resolve Studio 21.1.1, native MCP `run_script`), on 2026-10-10. The 21.1 changelog
was not available from the MCP, so 21.1-only additions are not covered.

Test project: throwaway `zz_scripting_api_test` (not one of the real projects).
Fixtures, both in `~/Videos/mcp-bench-scratch/` (a registered Media Storage volume;
imports from outside one fail):

- `validate_client_testsrc_av.mov`: 6 s DNxHD 1280x720 + PCM, synthetic test pattern.
- `zz_real_dnxhr.mov`: 8 s real footage (a vertical shot of a room with murals and
  wooden structures, no people), transcoded with ffmpeg to DNxHR SQ 540x960 + PCM from a
  source in `~/Videos/resolve_validation_sources/`. Resolve on this rig cannot import the
  original H.264/AAC files without Studio's GPU decode path, hence the transcode. It is
  untracked local media, not committed.

Everything below ran with the `llm` docker container stopped, except where it says the
GPU was starved (see "Root cause of the import failures").

## Results

| API | Introduced | Result |
|---|---|---|
| `ProjectManager.GetProjectAttributesInCurrentFolder()` | 21.0.3 | Works. Dict of project name → `lastModifiedDate`, `creationDate`, `notes`, `liveCollaborationMode`. |
| `Resolve.GetLayoutPresetList()` | 21.0.3 | Works. Returned `[]` (no saved presets on this rig). |
| `Project.GetAnalyzeForSlateStatus()` | 21.0 | Works. `{"JobStatus": "Inactive"}` when idle. |
| `MediaPool.GetSelectedClips()` | not in the changelog | Works. Also returns a selected **timeline** (returned `["zz_tl"]`), not only clips. |
| `Timeline.GetSelectedClips()` | 21.0.4 | Works. `[]` with nothing selected. Not tested with a real selection. |
| `MediaPoolItem.PerformAudioClassification()` / `ClearAudioClassification()` | 21.0 | Work. `True`; classify took 0.4 s on a 6 s clip. |
| `Project.ResetIntellisearchAnalysis()` | 21.0 | Works. `True`. |
| `Project.GenerateSpeech({...})` | 21.0 | Works. Created `zz_speech_test-Female 1-G1-001.wav` in the media pool. Used `TextInput`, `VoiceModel: "Female 1"`, `Filename`. |
| `Resolve.DisableBackgroundTasksForCurrentResolveSession()` | 21.0 | Works. Returns `None`, no error. |
| `MediaPoolItem.AnalyzeForSlate(color)` / `Folder.AnalyzeForSlate(color)` | 21.0 | **Inconclusive.** No job starts (status stays `Inactive`). Returned `None` on the synthetic clip and `False` on a real clip (`"Blue"`, `"Red"`, `"Green"`, `"Yellow"`). The docs say it requires the *AI Slate ID* Extra; whether it is installed was not checked. |
| `MediaPoolItem.RemoveMotionBlur(opts)` / `Folder.RemoveMotionBlur()` | 21.0 | **Inconclusive.** Returns `None` immediately, no clip created, with `{}`, ProRes422 and H264 options, on both clips (also after freeing the GPU). |
| `MediaPoolItem.AnalyzeForIntellisearch(False, False)` | 21.0 | **Hangs Resolve.** Opens a modal "Analyze for IntelliSearch / Media Indexing - Initializing…" dialog that blocks the scripting thread. GPU stayed at 0 % for minutes; every later `run_script` timed out. `xdotool` (click on Cancel, Escape) could not close the dialog; the user dealt with it by hand and Resolve was found closed afterwards. **That run happened while the GPU was starved (see below) and before checking the Extras**, so the cause (VRAM, a missing *AI IntelliSearch* Extra, or the synthetic clip) is unknown, and the call has **not been retried**. |

## Things that differ from the stubs

- The stub types `markerColor` as "one of the `resolve.*` constants: `MARKER_BLUE`, …",
  but `dir(resolve)` has **no** `MARKER_*` attributes. This may be why
  `AnalyzeForSlate` does nothing; not confirmed.
- `MediaPool.GetSelectedClips()` / `Timeline.GetSelectedClips()` return Resolve objects;
  returning them directly from `run_script` makes the result unserializable
  (`'NoneType' object has no attribute '__name__'`). Map to `GetName()` first.
- `run_script` has a hard timeout of 60 s at most, and a blocking modal dialog
  (IntelliSearch) outlives it: the script times out but Resolve stays blocked.
- On this rig the Resolve window is only reachable with `DISPLAY=:0` plus the
  `XAUTHORITY` of Xwayland (`/run/user/1000/.mutter-Xwaylandauth.*`); without it
  `xwininfo` lists nothing. `import -window <id>` fails on unmapped windows.

## Root cause of the import failures: GPU memory (confirmed)

The GPU has 8 GB. The docker container `llm` (llama-server, ~5.1 GB) plus two `python3`
containers (~1.3 GB) and `sunshine` left Resolve ~0.8 GB. Symptoms: Resolve showed a
"GPU memory exhausted" popup, the debug log had `DVIP | ERROR | cudaMalloc failed:
Requested size 16.0 MiB` (also 8 and 140 MiB), and `MediaPool.ImportMedia` /
`MediaStorage.AddItemListToMediaPool` returned `None` for every file (H.264, ProRes, DNxHR),
even though `MediaStorage.GetFileList` listed them.

After `docker stop llm` (GPU use 7.7 GB -> 2.6 GB) the same `ImportMedia` call on the same
DNxHR file succeeded without relaunching Resolve. **A scripting call that returns `None`
without an exception can mean "out of VRAM"**; check `nvidia-smi` first.

The `llm` container has restart policy `unless-stopped`: it stays stopped until
`docker start llm`.

## What the API docs say about the AI calls

`get_scripting_docs` section "Studio and AI Scripting APIs": these calls return `False`
(or an error status) when the minimum system requirements are not met or the required
**Extras** are not downloaded. Required Extras:

- `AnalyzeForIntellisearch(identifyFaces, isBetterMode)`: *AI IntelliSearch - Faster*
  (`isBetterMode=False`) or *- Better* (`True`).
- `AnalyzeForSlate(markerColor)`: *AI Slate ID*.
- `GenerateSpeech(...)`: *AI Speech Generator* (worked here, so it is installed).

The Extras live in `/opt/resolve/Extras/<hash>/` (9 packages, 19 MB to 6.8 GB); their
manifests are binary and the directory names are hashes, so which package is which could
not be read from disk. Check in Resolve: Extras Download Manager.

## To finish

1. Check the Extras Download Manager for *AI Slate ID* and *AI IntelliSearch*; install
   what is missing, then re-run `AnalyzeForSlate` and `AnalyzeForIntellisearch`
   (the latter last, since it hung Resolve once; that run was under VRAM starvation).
2. Find out what `RemoveMotionBlur` needs (it returns `None` with no message).
3. Test `Timeline.GetSelectedClips()` with an actual selection.
