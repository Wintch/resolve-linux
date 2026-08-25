#!/usr/bin/env python3
"""Applies film damage (grain, halation, scratches, gate weave, light leaks,
chromatic aberration) to an already-assembled video.

This is a pass that runs after 05_build_highlight, not a per-photo step: it
doesn't touch the segment cache, so the effect's intensity can be iterated on
without re-rendering anything. The filter chain lives in
lib_preset.build_film_chain so 05 can use exactly the same one with --film.

Typical usage - first compare intensities on a short section:
    tools/06_film_damage.py output/highlight_final_v1.mp4 --compare --start 45

then apply the chosen one to the whole cut:
    tools/06_film_damage.py output/highlight_final_v1.mp4 --preset medium \\
        --out output/highlight_film_medium.mp4
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib_film_texture import ensure_scratch_texture  # noqa: E402
from lib_preset import build_film_chain, film_scratch_params, list_presets, load_preset  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
TEXTURE_DIR = OUTPUT_DIR / "_textures"

# For previews: fast h264, the point is to compare the effect, not the compression.
PREVIEW_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
# For the real output: same delivery settings 05_build_highlight uses.
AV1_ARGS = ["-c:v", "libsvtav1", "-preset", "4", "-b:v", "3M"]
H264_ARGS = ["-c:v", "libx264", "-preset", "medium", "-b:v", "3M",
             "-maxrate", "3.5M", "-bufsize", "6M"]


def probe(path, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", entries, "-of", "csv=p=0:s=,", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    return out[0].split(",") if out else []


def video_info(path):
    w, h, rate = probe(path, "stream=width,height,r_frame_rate")[:3]
    num, den = (rate.split("/") + ["1"])[:2]
    return int(w), int(h), float(num) / float(den)


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    ).stdout.strip()
    return bool(out)


def apply_film(src, out_path, preset, encoder_args, start=None, duration=None,
               quiet=False):
    width, height, fps = video_info(src)

    inputs = []
    # -ss before -i is a fast seek (jumps straight to the keyframe); for a
    # few-second preview over a 2-minute file the difference versus putting
    # it after is seconds vs. decoding the whole video.
    if start is not None:
        inputs += ["-ss", str(start)]
    inputs += ["-i", str(src)]

    scratch_index = None
    params = film_scratch_params(preset)
    if params:
        tex = ensure_scratch_texture(TEXTURE_DIR, width, height, fps=int(round(fps)), **params)
        # -stream_loop -1: the texture lasts a few seconds and repeats as
        # much as needed; blend=shortest cuts it off when the video ends.
        inputs += ["-stream_loop", "-1", "-i", str(tex)]
        scratch_index = 1

    chain = build_film_chain(preset, width, height, int(round(fps)),
                             scratch_index=scratch_index)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-stats" if not quiet else "-nostats"]
    cmd += inputs
    cmd += ["-filter_complex", chain, "-map", "[vfilm]"]
    if duration is not None:
        cmd += ["-t", str(duration)]
    if has_audio(src):
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
    cmd += [*encoder_args, "-pix_fmt", "yuv420p", str(out_path)]

    proc = subprocess.run(cmd)
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"ffmpeg failed applying preset '{preset['name']}' (see error above)")
    return out_path


def render_plain(src, out_path, start, duration):
    """The same section with no effect, to have the A of the A/B."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(start), "-i", str(src),
           "-t", str(duration), *PREVIEW_ARGS, "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "192k"] if has_audio(src) else []
    cmd += [str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


def build_grid(clips, out_path, cols=2, cell_width=960):
    """Joins the previews into a single labeled grid.

    Four separate files force you to open and close players to compare from
    memory; with the grid all four versions play in sync and the difference
    between intensities is visible at a glance. clips is a list of
    (label, path)."""
    inputs = []
    for _, path in clips:
        inputs += ["-i", str(path)]
    parts = []
    for i, (label, _) in enumerate(clips):
        safe = label.replace("'", "").replace(":", "")
        parts.append(
            f"[{i}:v]scale={cell_width}:-2,"
            f"drawtext=text='{safe}':x=18:y=14:fontsize=34:fontcolor=yellow:"
            f"box=1:boxcolor=black@0.75:boxborderw=9[c{i}]"
        )
    rows = []
    for r in range(0, len(clips), cols):
        row_in = "".join(f"[c{i}]" for i in range(r, min(r + cols, len(clips))))
        n = min(cols, len(clips) - r)
        rows.append(f"{row_in}hstack=inputs={n}[r{r // cols}]")
    parts += rows
    n_rows = len(rows)
    if n_rows > 1:
        parts.append("".join(f"[r{i}]" for i in range(n_rows)) + f"vstack=inputs={n_rows}[grid]")
        final = "[grid]"
    else:
        final = "[r0]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(parts), "-map", final]
    # the first clip's audio is enough: it's the same section in every clip
    cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "128k"]
    cmd += [*PREVIEW_ARGS, "-pix_fmt", "yuv420p", str(out_path)]
    subprocess.run(cmd, check=True)
    return out_path


def selftest():
    """Checks that every preset's chain is correctly plumbed.

    Exists because of a bug that took a while to find: the gate weave's scale
    filter was negotiating yuv420p as its output format (crop, blend, and
    gblur all accept YUV, so nothing complained), and from there on the
    "screen"/"overlay" blends ran on chroma planes instead of color channels.
    The result was a magenta shift across the whole frame, with no error or
    warning at all. On top of that, that same scale left the SAR at 323/322
    and the video came out stretched. Neither of these shows up looking at a
    clean log, only by looking at pixels.
    """
    import re
    import tempfile

    ok = True
    for name in list_presets("film"):
        preset = load_preset(name, category="film")
        # flat gray: with no blown highlights there's no halation, and with
        # no light leak the result has to stay exactly neutral. Any
        # imbalance between channels is broken plumbing, not an aesthetic
        # choice.
        neutral = {k: v for k, v in preset.items() if k != "light_leak"}
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            gray = td / "gray.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                            "-i", "color=c=#808080:s=640x426", "-frames:v", "1", str(gray)],
                           check=True)
            inputs = ["-loop", "1", "-i", str(gray)]
            scratch_index = None
            params = film_scratch_params(neutral)
            if params:
                tex = ensure_scratch_texture(TEXTURE_DIR, 640, 426, fps=25, **params)
                inputs += ["-stream_loop", "-1", "-i", str(tex)]
                scratch_index = 1
            chain = build_film_chain(neutral, 640, 426, 25, scratch_index=scratch_index)
            out = td / "out.mkv"
            proc = subprocess.run(["ffmpeg", "-y", "-v", "verbose", *inputs,
                                   "-filter_complex", chain, "-map", "[vfilm]",
                                   "-t", "0.5", "-r", "25", "-c:v", "ffv1", str(out)],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"  {name}: FAILED - ffmpeg exited with an error\n{proc.stderr[-400:]}")
                ok = False
                continue

            problems = []
            # 1. no intermediate scale can spit out YUV
            for line in proc.stderr.splitlines():
                if re.search(r"Parsed_scale_\d+.*->.*fmt:yuv", line):
                    problems.append("an intermediate scale returns YUV (broken blends)")
                    break
            # 2. square SAR at the output
            sar = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                  "-show_entries", "stream=sample_aspect_ratio",
                                  "-of", "csv=p=0", str(out)],
                                 capture_output=True, text=True).stdout.strip()
            if sar not in ("1:1", "N/A", ""):
                problems.append(f"non-square SAR ({sar})")
            # 3. a flat gray input has to come out gray
            png = td / "f.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(out),
                            "-frames:v", "1", str(png)], check=True)
            import numpy as np
            from PIL import Image
            a = np.asarray(Image.open(png).convert("RGB")).astype(float)
            means = [a[:, :, i].mean() for i in range(3)]
            spread = max(means) - min(means)
            # the tint threshold is deliberately strict: all three channels
            # come from the SAME replicated grain layer, so the difference
            # between them has to be practically zero. Any real separation
            # means the per-channel noise bug or a YUV blend is back.
            if spread > 0.5:
                problems.append(f"color shift on neutral gray (RGB {means[0]:.1f}/"
                                f"{means[1]:.1f}/{means[2]:.1f}, spread {spread:.2f})")
            # brightness is allowed a bit of drift: overlay isn't exactly
            # symmetric around 128 and the noise layer has its own bias.
            # Measured ~-0.8 out of 255 (0.3%), invisible.
            shift = sum(means) / 3 - 128.0
            if abs(shift) > 2.0:
                problems.append(f"mean exposure drifts {shift:+.2f} from 128")

            if problems:
                ok = False
                print(f"  {name}: FAILED - " + "; ".join(problems))
            else:
                print(f"  {name}: ok (RGB {means[0]:.1f}/{means[1]:.1f}/{means[2]:.1f}, SAR {sar})")
    if not ok:
        raise SystemExit("selftest failed")
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="input video (e.g. output/highlight_final_v1.mp4)")
    ap.add_argument("--selftest", action="store_true",
                    help="verifies each preset's chain plumbing (RGB format "
                         "throughout the graph, square SAR, color neutrality)")
    ap.add_argument("--preset", default="medium",
                    help=f"preset from tools/presets/film/ (available: {list_presets('film')})")
    ap.add_argument("--out", default=None, help="output file")
    ap.add_argument("--start", type=float, default=None,
                    help="second to start from (for previews)")
    ap.add_argument("--duration", type=float, default=None,
                    help="seconds to process (for previews)")
    ap.add_argument("--compare", action="store_true",
                    help="renders the same section with no effect + every preset, "
                         "to pick an intensity before touching the whole cut")
    ap.add_argument("--codec", default="h264", choices=["h264", "av1", "preview"],
                    help="output codec. preview = fast h264 crf18")
    args = ap.parse_args()

    if args.selftest:
        TEXTURE_DIR.mkdir(parents=True, exist_ok=True)
        selftest()
        return
    if not args.input:
        ap.error("missing input video (or use --selftest)")

    src = Path(args.input)
    if not src.exists():
        raise SystemExit(f"{src} doesn't exist")
    TEXTURE_DIR.mkdir(parents=True, exist_ok=True)

    if args.compare:
        start = args.start if args.start is not None else 45.0
        dur = args.duration if args.duration is not None else 15.0
        dest = OUTPUT_DIR / "film_preview"
        dest.mkdir(parents=True, exist_ok=True)
        print(f"Comparison section: {start}s to {start + dur}s of {src.name}")
        clips = []
        plain = dest / f"00_no_effect_{int(start)}s.mp4"
        if not plain.exists():
            render_plain(src, plain, start, dur)
        clips.append(("NO EFFECT", plain))
        print(f"  -> {plain.name}")
        # explicit order from least to most, not alphabetical: in the grid it
        # reads as an intensity scale.
        for name in ["subtle", "medium", "strong"]:
            if name not in list_presets("film"):
                continue
            preset = load_preset(name, category="film")
            out = dest / f"{name}_{int(start)}s.mp4"
            if not out.exists():
                apply_film(src, out, preset, PREVIEW_ARGS, start=start, duration=dur, quiet=True)
            clips.append((name.upper(), out))
            print(f"  -> {out.name}")
        grid = dest / f"comparison_{int(start)}s.mp4"
        build_grid(clips, grid)
        print(f"\n  -> {grid.name}  (all 4 versions synchronized in a grid)")
        print(f"\nCompare with: vlc {grid}")
        return

    preset = load_preset(args.preset, category="film")
    encoder = {"h264": H264_ARGS, "av1": AV1_ARGS, "preview": PREVIEW_ARGS}[args.codec]
    out_path = Path(args.out) if args.out else OUTPUT_DIR / f"{src.stem}_film_{preset['name']}.mp4"
    print(f"Preset: {preset['name']} - {preset['description']}")
    apply_film(src, out_path, preset, encoder, start=args.start, duration=args.duration)
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
