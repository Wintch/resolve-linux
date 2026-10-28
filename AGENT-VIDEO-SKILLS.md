# Agent Video Skills

What the wider "Claude Code makes video" ecosystem offers, read against what this rig
already does (DaVinci Resolve Studio 21.1.1 on Debian, driven by the community MCP server
plus Blackmagic's native one). Source of truth is an external, auto-generated catalog;
this file is a dated snapshot with this rig's verdicts on top, not a mirror.

**Source:** [zhuyansen/awesome-claude-video-skills](https://github.com/zhuyansen/awesome-claude-video-skills)
— snapshot of commit `73a88d1` ("Daily update 2026-10-28"). The list regenerates every 8
hours from [agentskillshub.top](https://agentskillshub.top/best/claude-video-skills/);
`data/skills.json` in that repo is the machine-readable form (repo, stars, kind,
security grade, language, license, description).

**Nothing below was installed or run on this rig.** Verdicts come from the catalog's own
data, its published end-to-end test, and a read of the READMEs named here. Anything marked
"candidate" still needs the usual live test before it gets a place in a pipeline.

---

## What the catalog is

- **373 repos**: skills, plugins, MCP servers or toolkits that let a coding agent make or
  edit video. Kinds: promo 72, explainer 66, general (frameworks/toolkits) 65, editing 48,
  shorts 44, motion graphics 25, stories 20, music 15, scripts/learning 10, avatars 8.
- **Security grade** per repo, assigned by the hub's own review: 364 `safe`, 9 `caution`
  (listed below). The grade is a third party's reading of a README and repo, not an audit.
- **Inclusion is decided by a model reading each README**, not by hand (the catalog says so
  itself): ≥50 stars gets in on topic alone; under 50 must also clear a README-quality bar.
  Borderline repos can land on either side, so absence is weak evidence.
- Most of the catalog is **generative motion graphics**: HTML/Canvas/SVG, Remotion (React)
  or HyperFrames (HTML + GSAP) rendered to MP4 by headless Chrome + ffmpeg. That is a
  different job from this rig's (finishing real footage in an NLE).

## The catalog's own end-to-end test (2026-10-11)

16 skills, driven by Claude Code (Opus 5.5) in a sandbox with no API keys for video/image/
voice models, made the same 15 s, 1920×1080, 30 fps promo with five fixed lines of copy.
Checked: plays, right length/size, every line readable in frames sampled at 2 Hz, picture
moves. Look was judged by eye, not scored.

- **All 16 passed all 4 checks — and so did Claude Code with no skill at all (4.4 min).**
  The catalog's own conclusion: for a simple promo you may not need a skill; one earns its
  place with a look, a workflow, or footage to edit.
- Time spread **2.0 – 23.5 min**. Fastest: `vincentsch/explainroo` (2.0 min, with sound).
  Slowest: `heygen-com/hyperframes` (23.5 min) — the most-starred renderer in the list.
- 9 of 16 added sound; 10 of 16 had "a look of its own", 6 came out close to the no-skill
  film.
- Their picks: `explainroo` (finished film fast), `feitangyuan/onetake` (one continuous
  idea, no cuts), `remotion-dev/skills` (build your own design in Remotion; reliable, 4.5
  min, generic look unprompted).
- Full table and evidence pages:
  [RESULTS.md](https://github.com/zhuyansen/agent-skills-hub/blob/main/ops/video-runs/RESULTS.md).

## Findings that matter for this rig

### 1. There is no DaVinci Resolve entry in the catalog

Zero repos mention DaVinci or Resolve as their target (grep over the README and
`skills.json`). The NLE bridges that *are* listed are all Adobe:

| Repo | ★ | What |
|---|---:|---|
| `hetpatel-11/Adobe_Premiere_Pro_MCP` | 772 | Premiere Pro MCP |
| `leancoderkavy/premiere-pro-mcp` | 450 | Premiere Pro MCP, local-first |
| `ayushozha/AdobePremiereProMCP` | 129 | Premiere bridge (Go/Rust/Python) |
| `JUNKDOGE-JOE/after-effects-mcp` | 87 | After Effects MCP, 16 tools |

`samuelgursky/davinci-resolve-mcp` (the server in use here, ~3.8k ★, 37 tools, see
[MCP-CAPABILITIES.md](MCP-CAPABILITIES.md)) would clear the inclusion rules on stars alone
and is missing. The catalog invites an issue for misfiled repos; whether to file one is a
call for the operator, not done here. Practical consequence: nothing in this list replaces
or competes with the Resolve MCP stack — the useful parts are *upstream* of Resolve
(generating material) or *beside* it (footage clean-up done outside the NLE).

### 2. The editing skills mostly duplicate what the Resolve MCP already does

The 48 "editing" entries are overwhelmingly ffmpeg + Whisper pipelines for talking-head
footage: silence/filler removal, captions, B-roll, beat-sync, recaps. On this rig the
community server already covers most of that inside Resolve, on the timeline, where the
result stays editable:

| Typical skill feature | Already on this rig |
|---|---|
| Transcription, word-level captions | `timeline_ai create_subtitles`; `openai-whisper` installed in the MCP venv (CUDA, RTX 3060 Ti) |
| Scene/cut detection | `timeline_ai detect_scene_cuts` |
| Beat/bar/phrase detection for cutting to music | `librosa` extra installed (doctor.py `[OK]`) |
| Visual similarity / find shots | `open_clip` extra installed |
| Subtitle styling presets | `timeline_ai list_title_presets` / `set_subtitle_preset` (note the Segoe UI font gap, benchmark README 2026-10-26) |

An ffmpeg-only skill renders a flat MP4: the cut is baked, nothing comes back to Resolve
as edits. That's the main reason none of them is an obvious add here.

### 3. Only one editor in the shortlist exports something Resolve can import

Read the READMEs of the most relevant editors/frameworks for an interchange format
(FCPXML / EDL / OTIO / Resolve). Word-boundary search — a naive `otio` grep matches
"m**otio**n" everywhere and is useless:

| Repo | ★ | License | Interchange out | Notes |
|---|---:|---|---|---|
| `0xsline/OpenChatCut` | 2.7k | **AGPL-3.0** | **FCPXML**, SRT, MP4, CapCut draft | Electron desktop app with its own multitrack timeline; Linux builds on GitHub Releases; external agents drive it over **Streamable HTTP MCP**; Node 24.x; on Linux x64 `npm install` pulls ONNX Runtime CUDA (several hundred MB; `ONNXRUNTIME_NODE_INSTALL=skip` for CPU-only). |
| `FireRedTeam/FireRed-OpenStoryline` | 3.6k | Apache-2.0 | none found | Intention-driven editing agent. |
| `JimLiu/baocut` | 1.6k | unclear | none found | Editable timeline: transcribe, subtitle, translate, dub, cut. |
| `cartesiancs/cartcut` | 818 | MIT | none found | "Video editor for AI agents". |
| `calesthio/OpenMontage` | 72.6k | AGPL-3.0 | none found | 12 production pipelines, 700+ skill files. |

OpenChatCut → FCPXML → Resolve is the only "rough-cut elsewhere, finish in Resolve" path
the list offers. Relevant timing: community MCP **v4.16.0** (installed 2026-10-28) fixed
`import_timeline_checked` for FCPXML *bundle directories* (#324) — not yet tested on this
rig. **Candidate**, untested; AGPL matters only if it gets redistributed or modified.

### 4. Where generators *do* fit: motion graphics as Resolve media

Remotion / HyperFrames / Lottie skills render graphics from code. They don't touch
Resolve, but their output can be imported like any clip (`media_pool import_media`, or
`import_bounded_media` — live-verified 2026-10-08). Things to keep in mind if one gets
used:

- Render to an intermediate Resolve decodes on Linux: ProRes/DNxHR, **not H.264/AAC**
  (no AAC decode on Linux Resolve; the benchmark fixture uses DNxHR + PCM for exactly this
  reason). With alpha, ProRes 4444.
- Match timeline settings (resolution, fps) at render time rather than conforming later.
- These render with headless Chrome. The GPU is shared with Resolve and the always-on
  `tts-hq`/`tts`/`stt`/`transcoder`/`upscaler` containers (8 GB VRAM total) — the
  2026-10-26 `GPU Error 999` hang shows contention is real. Don't render both at once.
- `remotion-dev/skills` (official, 5.8k ★) is the low-risk starting point; `explainroo`
  is the fast end-to-end one (local Kokoro voice + Whisper word timing).

### 5. Security: the `caution` entries, and two target Hermes

The 9 repos graded `caution` by the hub:

| Repo | ★ | Kind |
|---|---:|---|
| `howseen-ai/claude-motion-design` | 712 | promo |
| `wshuyi/remotion-video-skill` | 435 | explainer |
| `zz41354899/mografy` | 47 | motion |
| `ops120/video-recap-skills-plus` | 45 | editing |
| `Dancan254/shenghua` | 29 | shorts |
| **`rahlquist/hermes-video-toolchain`** | 13 | editing |
| `Karnaf0katom/taskfilm` | 10 | promo |
| **`argus-metis/hermes-video-editing`** | 7 | general |
| `10291124jcjc-dotcom/xp-desktop-mv` | 6 | music |

Two of them are **Hermes Agent** video skills — the same agent this rig already wires to
the Resolve MCP ([pipelines/mcp-benchmark/HERMES-SKILL.md](pipelines/mcp-benchmark/HERMES-SKILL.md)).
Low stars plus `caution`: don't install either into the rig's Hermes without reading the
code first. Generally: a skill is instructions plus scripts an agent will run with this
user's permissions on a box that holds the Resolve database — treat every one as untrusted
code until read, `safe` grade or not.

## Candidates, if something gets tried

In order of fit with this rig, none tested:

1. **OpenChatCut → FCPXML → Resolve** — the only round trip into the existing stack.
   Test: export a short cut, import via `timeline import_timeline_checked` on the
   `MCP-Benchmark` fixture, compare clip count/in-outs.
2. **`louisedesadeleer/cut-video`** (114 ★, MIT) — silence/filler removal driven by
   Montreal Forced Aligner word boundaries (~10–20 ms) on top of Whisper, re-verifies
   every kept boundary. Better precision than a Whisper-only cut; output is ffmpeg
   `trim+concat` (flat file), so it's a pre-processing step, not an NLE edit.
3. **`remotion-dev/skills`** — graphics/titles as ProRes 4444 clips for Resolve, for
   anything the Fusion title presets can't do (or can't on Linux — see the Segoe UI gap).

Not worth it here: Premiere/After Effects MCPs (no Adobe on Linux), avatar and
CapCut/JianYing-centric skills, and the large "make a promo from a URL" category (the
catalog's own test shows bare Claude Code already does that).

## Refreshing this file

```bash
git clone --depth 1 https://github.com/zhuyansen/awesome-claude-video-skills /tmp/acvs
python3 -I -c "import json; d=json.load(open('/tmp/acvs/data/skills.json')); \
  print(d['generated'], len(d['skills']))"
grep -niwE 'davinci|resolve' /tmp/acvs/README.md /tmp/acvs/data/skills.json
```

Update the snapshot commit/date at the top, re-check finding 1 (a Resolve entry
appearing would be the most important change), the `caution` list, and the candidates'
READMEs for new interchange formats.
