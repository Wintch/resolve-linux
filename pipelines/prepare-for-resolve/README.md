# prepare-for-resolve

Normalizes a folder of video files so DaVinci Resolve on Linux can actually import and use
them — fixes variable frame rate, transcodes audio codecs Resolve on Linux can't decode,
and deinterlaces where needed.

## Credit

This is a from-scratch Python reimplementation of the idea behind
[`davincirecode10.sh`](https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=136365) by
Boris Kovalev — not a fork of his code, a rewrite of the same real-world approach (his
script's decision logic is the basis this was built from) with a few years of hindsight and
some findings this project made independently along the way. All credit for the original
idea and the years of community iteration on it goes to Boris and the others who
contributed fixes in that thread.

## What changed from the original, and why

- **`ffprobe` instead of `mediainfo` + `ffmpeg` as two separate tools.** Structured JSON
  output (`-print_format json -show_streams -show_format`) instead of grepping
  `mediainfo`'s text output — more robust, and one less dependency to install (ffmpeg was
  already required for the actual transcode).
- **AAC gets a specific, evidence-backed reason, not just "unsupported codec."** This
  project's own testing against a real Resolve Studio 21.0.4.5 instance found hundreds of
  repeated `IO.Audio | ERROR | Failed to decode the audio samples` entries in Resolve's own
  debug log (`~/.local/share/DaVinciResolve/logs/ResolveDebug.txt`) when AAC-audio footage
  was pushed through the pipeline — not a guess, not a spec-sheet claim. Blackmagic's own
  Linux-specific codec table (Resolve 20, Rocky Linux 8.6) confirms this structurally: AAC
  decode/encode is unsupported on Linux in any edition, and embedded-audio-in-container
  decode on Linux covers PCM/mp3/flac only — AAC is explicitly excluded, unlike macOS/
  Windows. See the parent repo's [README](../../README.md) for the full codec writeup.
- **Real CLI flags (`argparse`), not globals you edit inside the script.** Composable,
  scriptable, and safe to run with different settings without touching the file.
- **Parallel by default** (`ProcessPoolExecutor`, defaulting to half your CPU count) —
  oversubscribing tends to hurt more than help since ffmpeg already multithreads
  internally per instance; `--workers` overrides.
- **Resume/skip**: won't reprocess a file if its output already exists (non-empty), unless
  `--overwrite` is passed. Boris's original always reprocessed everything.
- **`--dry-run`**: prints the per-file decision and the reason for it without touching
  ffmpeg — useful for sanity-checking a big batch before committing to it.
- **Preserves the original file's mtime** on the output (`os.utime`), same as the
  original's `touch -r`.
- **A curated known-bad video codec list** (h263, vp8, wmv3, msvideo1, mjpeg — same set the
  original special-cased) forces a transcode even before checking VFR/interlacing.

### What's still a known gap

- **H.265 as a *target* codec** for the `--video-codec` flag inherits the same risk a forum
  reply flagged against the original — ffmpeg's HEVC encoding on Linux is more finicky than
  H.264 in practice. Not specially handled here; test it on a sample file before trusting it
  on a big batch.
- **AC3 decode** landed in Resolve 18.5b1, which the original author noted but never
  incorporated. This rewrite treats AC3 as safe-to-passthrough (see `SAFE_AUDIO_CODECS`),
  which should be correct for any reasonably current Resolve version, but hasn't been
  re-verified against the official codec table the way the AAC finding was.
- No nearest-standard-framerate detection for VFR sources — always targets whatever
  `--framerate` says (default 24), not an inferred "closest sane rate" from the source's
  average.

## Usage

```bash
# See what it would do first
python3 prepare_for_resolve.py ~/Videos/incoming --dry-run

# Normal run
python3 prepare_for_resolve.py ~/Videos/incoming

# Editing-friendly target codec instead of H.264, more workers, custom output dir
python3 prepare_for_resolve.py ~/Videos/incoming \
    --video-codec prores_ks --workers 8 --output-dir ~/Videos/ready

# Keep interlaced footage untouched and let Resolve Studio's neural-engine deinterlace
# handle it instead (higher quality than yadif, but Studio-only — a forum reply in the
# original thread specifically recommended this over the script's own yadif pass)
python3 prepare_for_resolve.py ~/Videos/incoming --no-deinterlace
```

## Scope: this is an import fix, not an export fix

This tool exists to get footage *into* Resolve cleanly. It has nothing to do with a
separate, still-open bug this project found in Resolve's *render/export* pipeline on this
same rig: H.264 and H.265 don't appear as available render codecs at all on this install,
and pushing AAC/ProRes/H.265 through a render job reliably livelocks the process (CPU
pinned, GPU idle, no progress, no error — see the parent repo's
[README](../../README.md) for the full writeup and root cause). Normalizing your source
footage with this script does not touch or fix that — they're different pipeline stages.

## Requirements

`ffmpeg` and `ffprobe` on `PATH`. No Python packages beyond the standard library.
