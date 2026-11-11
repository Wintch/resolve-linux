#!/usr/bin/env python3
"""Normalize a folder of video files so DaVinci Resolve on Linux can actually use them.

A from-scratch Python reimplementation of the idea behind davincirecode10.sh by Boris
Kovalev (https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=136365) — same goal
(batch-fix VFR, bad audio codecs, and interlacing before import), rebuilt with ffprobe
instead of mediainfo, real CLI flags instead of file-edited globals, parallelism, resume,
and a dry-run mode. See README.md for the full rationale and what changed.

No third-party dependencies — only the stdlib, plus the `ffmpeg`/`ffprobe` binaries.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

# Codecs whose embedded audio Resolve on Linux actually decodes (official Linux codec
# table: PCM/mp3/flac only — AAC is explicitly excluded on Linux, unlike macOS/Windows).
SAFE_AUDIO_CODECS = {
    "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_s16be", "pcm_s24be",
    "flac", "mp3", "ac3",
}

# Video codecs Resolve on Linux (Studio, NVIDIA) decodes well for import purposes.
# This is an IMPORT/decode list — it says nothing about what Resolve can *render out to*
# on this platform, which is a separate and narrower list (see README's cross-reference).
SAFE_VIDEO_CODECS = {
    "h264", "hevc", "av1", "prores", "dnxhd",  # dnxhd covers DNxHR variants in ffprobe
}

# Known-bad video codecs, same set davincirecode10.sh special-cased.
BAD_VIDEO_CODECS = {"h263", "vp8", "wmv3", "msvideo1", "mjpeg"}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".mxf", ".mts", ".m2ts", ".webm", ".flv", ".wmv",
    ".mpg", ".mpeg", ".m4v",
}


def default_video_codec() -> str:
    """Prefer GPU encoding (NVENC); fall back to CPU (libx264) only if NVENC isn't
    available on this machine, with a loud warning -- GPU should always be tried first,
    CPU only when there's genuinely no alternative."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "h264"
    if "h264_nvenc" in out:
        return "h264_nvenc"
    print(
        "WARNING: NVENC (h264_nvenc) not found on this machine's ffmpeg build -- falling "
        "back to CPU encoding (libx264). This will be noticeably slower; if there's an "
        "NVIDIA GPU here, check the ffmpeg build has NVENC support compiled in.",
        file=sys.stderr,
    )
    return "h264"


# Common broadcast/cinema constant rates. VFR sources get snapped to whichever of these
# their own average is closest to -- never to an unrelated global default. A source shot
# at a deliberate constant rate (25fps PAL, 60fps sports, etc.) is never touched by this at
# all, since that branch only fires when `is_vfr()` is true in the first place.
STANDARD_FRAMERATES = [23.976, 24, 25, 29.97, 30, 47.952, 48, 50, 59.94, 60]


def snap_framerate(avg_fps: float) -> float:
    return min(STANDARD_FRAMERATES, key=lambda f: abs(f - avg_fps))


@dataclass
class Decision:
    transcode_video: bool = False
    transcode_audio: bool = False
    deinterlace: bool = False
    target_framerate: float | None = None  # only set when actually fixing VFR -- never
    # applied just because a codec transcode is happening for an unrelated reason.
    reason: list[str] = field(default_factory=list)

    @property
    def needs_ffmpeg(self) -> bool:
        return self.transcode_video or self.transcode_audio or self.deinterlace


@dataclass
class Outcome:
    path: Path
    action: str  # "copied", "processed", "skipped", "failed"
    detail: str = ""


def probe(path: Path) -> dict:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return json.loads(proc.stdout)


def is_vfr(video_stream: dict) -> bool:
    r = video_stream.get("r_frame_rate", "0/1")
    avg = video_stream.get("avg_frame_rate", "0/1")
    try:
        return Fraction(r) != Fraction(avg) and Fraction(avg) != 0
    except (ZeroDivisionError, ValueError):
        return False


def is_interlaced(video_stream: dict) -> bool:
    field_order = video_stream.get("field_order", "progressive")
    return field_order not in ("progressive", "unknown", "")


def decide(info: dict, args: argparse.Namespace) -> Decision:
    d = Decision()
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if video_streams:
        v = video_streams[0]
        vcodec = v.get("codec_name", "")
        if vcodec in BAD_VIDEO_CODECS:
            d.transcode_video = True
            d.reason.append(f"video codec '{vcodec}' is known-bad on Resolve/Linux import")
        elif vcodec not in SAFE_VIDEO_CODECS:
            d.transcode_video = True
            d.reason.append(f"video codec '{vcodec}' not in the confirmed-safe import list")

        if is_vfr(v):
            d.transcode_video = True
            avg_fps = float(Fraction(v.get("avg_frame_rate", "0/1")))
            if args.framerate is not None:
                target = float(args.framerate)
                d.target_framerate = target
                d.reason.append(
                    f"variable frame rate (source averages ~{avg_fps:.3f}fps) -> forcing "
                    f"constant {target}fps (--framerate override)"
                )
            else:
                target = snap_framerate(avg_fps)
                d.target_framerate = target
                d.reason.append(
                    f"variable frame rate -> snapping to constant {target}fps, the nearest "
                    f"standard rate to this source's own ~{avg_fps:.3f}fps average (not a "
                    "global default -- pass --framerate to override)"
                )

        if is_interlaced(v) and not args.no_deinterlace:
            d.deinterlace = True
            d.reason.append("interlaced source -> deinterlacing with yadif")

    if audio_streams:
        a = audio_streams[0]
        acodec = a.get("codec_name", "")
        if acodec == "aac":
            d.transcode_audio = True
            d.reason.append(
                "AAC does not decode on Resolve/Linux (confirmed via ResolveDebug.txt "
                "IO.Audio errors, not just a spec-sheet claim) -> transcoding to "
                f"{args.audio_codec}"
            )
        elif acodec not in SAFE_AUDIO_CODECS:
            d.transcode_audio = True
            d.reason.append(f"audio codec '{acodec}' not in the safe-passthrough set")

    return d


def build_ffmpeg_cmd(
    src: Path, dst: Path, decision: Decision, args: argparse.Namespace
) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src)]

    if decision.transcode_video:
        cmd += ["-c:v", args.video_codec]
        # Only force a constant rate when actually fixing VFR -- a codec-only transcode
        # (e.g. a bad-codec source that's already constant-rate) must not have its frame
        # rate touched just because it's going through ffmpeg for an unrelated reason.
        if decision.target_framerate is not None:
            cmd += ["-r", str(decision.target_framerate)]
    else:
        cmd += ["-c:v", "copy"]

    if decision.transcode_audio:
        cmd += ["-c:a", args.audio_codec]
    else:
        cmd += ["-c:a", "copy"]

    if decision.deinterlace:
        cmd += ["-vf", "yadif"]

    if decision.transcode_video or decision.transcode_audio:
        cmd += ["-max_muxing_queue_size", "1024"]

    if args.extra_args:
        cmd += args.extra_args.split()

    cmd.append(str(dst))
    return cmd


def output_path(src: Path, out_dir: Path, decision: Decision, container: str) -> Path:
    tags = []
    if decision.transcode_video:
        tags.append("v")
    if decision.transcode_audio:
        tags.append("a")
    if decision.deinterlace:
        tags.append("deint")
    suffix = "_" + "".join(tags) if tags else "_container"
    return out_dir / f"{src.stem}{suffix}.{container}"


def process_one(src_str: str, args_dict: dict) -> Outcome:
    args = argparse.Namespace(**args_dict)
    src = Path(src_str)
    out_dir = Path(args.output_dir)

    try:
        info = probe(src)
    except (subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        return Outcome(src, "failed", f"ffprobe failed: {exc}")

    decision = decide(info, args)
    dst = output_path(src, out_dir, decision, args.container)

    if dst.exists() and dst.stat().st_size > 0 and not args.overwrite:
        return Outcome(src, "skipped", f"{dst.name} already exists")

    if args.dry_run:
        why = "; ".join(decision.reason) if decision.reason else "no changes needed"
        return Outcome(src, "dry-run", why)

    try:
        if decision.needs_ffmpeg:
            cmd = build_ffmpeg_cmd(src, dst, decision, args)
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            action, detail = "processed", "; ".join(decision.reason)
        else:
            shutil.copy2(src, dst)
            action, detail = "copied", "no changes needed"
        stat = src.stat()
        os.utime(dst, (stat.st_atime, stat.st_mtime))
        return Outcome(src, action, detail)
    except subprocess.CalledProcessError as exc:
        return Outcome(src, "failed", exc.stderr.strip()[-300:] if exc.stderr else str(exc))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("source_dir", type=Path, help="Directory of source video files")
    p.add_argument("--output-dir", default="exportedvideo", help="Output directory (default: exportedvideo)")
    p.add_argument("--framerate", type=float, default=None, help="Force VFR sources to this constant framerate. Default: infer per-file, snapping each source's own average to the nearest standard rate (23.976/24/25/29.97/30/48/50/59.94/60) -- never a single global value")
    p.add_argument("--audio-codec", default="pcm_s24le", help="Target audio codec (default: pcm_s24le)")
    p.add_argument("--video-codec", default=None, help="Target video codec (default: h264_nvenc if this machine's ffmpeg has NVENC, else CPU h264 with a warning; try prores_ks for editing)")
    p.add_argument("--container", default="mkv", help="Output container extension (default: mkv)")
    p.add_argument("--no-deinterlace", action="store_true", help="Skip yadif deinterlacing (e.g. if you'll use Resolve Studio's own neural deinterlace instead)")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2), help="Parallel workers (default: half of CPU count, since ffmpeg itself multithreads per instance)")
    p.add_argument("--dry-run", action="store_true", help="Print decisions without running ffmpeg")
    p.add_argument("--overwrite", action="store_true", help="Reprocess even if the output file already exists")
    p.add_argument("--extra-args", default="", help="Extra ffmpeg args appended before the output path, e.g. '-crf 17'")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("error: ffmpeg and ffprobe must both be on PATH", file=sys.stderr)
        return 1

    if args.video_codec is None:
        args.video_codec = default_video_codec()

    if not args.source_dir.is_dir():
        print(f"error: {args.source_dir} is not a directory", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(
        f for f in args.source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not files:
        print(f"No video files found in {args.source_dir}")
        return 0

    args_dict = vars(args)
    args_dict["source_dir"] = str(args.source_dir)  # dataclasses/Namespace must stay picklable
    args_dict["output_dir"] = str(out_dir)

    start = time.monotonic()
    outcomes: list[Outcome] = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(process_one, str(f), args_dict): f for f in files
        }
        for future in concurrent.futures.as_completed(futures):
            outcome = future.result()
            outcomes.append(outcome)
            print(f"[{outcome.action}] {outcome.path.name} - {outcome.detail}")

    elapsed = time.monotonic() - start
    counts: dict[str, int] = {}
    for o in outcomes:
        counts[o.action] = counts.get(o.action, 0) + 1

    print()
    print(f"Done: {len(outcomes)} files in {elapsed:.1f}s")
    for action, n in sorted(counts.items()):
        print(f"  {action}: {n}")

    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
