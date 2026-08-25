"""Generates the scratches/dust texture for the film-damage effect.

It's a grayscale-on-black layer: thin vertical scratches + dust specks,
meant to be blended with "screen" mode (black contributes nothing, only the
scratches add up). Generated procedurally instead of pulling in a stock
texture: that way density/brightness are controlled per preset and there's
no dependency on an external file.

**Loopable**: the texture lasts a few seconds and repeats with -stream_loop
over a 2-minute video. To keep the repetition from showing as a "jump,"
events that start near the end wrap their frames modulo N (a scratch born on
frame 148 of 150 continues on 0, 1, 2...), so there's no discontinuity at the
loop's cut point.

Cached to disk: generating it is cheap but deterministic given the seed,
there's no point redoing it on every run.
"""
import subprocess
from pathlib import Path

import numpy as np

# The texture is generated at half resolution and ffmpeg scales it up to the
# video's size. Film scratches have soft edges (they're not hard pixels), so
# the bilinear upscale improves them instead of ruining them, and generating
# a quarter of the pixels makes this step trivially fast.
TEX_SCALE = 0.5


def _draw_scratch(frames, n_frames, w, h, rng, brightness_max):
    """A thin vertical scratch that lives for a few frames and drifts sideways."""
    f0 = rng.integers(0, n_frames)
    life = int(rng.integers(2, 14))
    x = rng.uniform(0.03, 0.97) * w
    drift = rng.normal(0, 0.35)  # px per frame, almost always imperceptible
    width = 1 if rng.random() < 0.75 else 2
    peak = rng.uniform(0.35, 1.0) * brightness_max
    # many scratches don't cross the whole frame: they're born and die mid-height
    if rng.random() < 0.55:
        y0 = int(rng.uniform(0, 0.6) * h)
        y1 = int(rng.uniform(y0 + h * 0.15, h))
    else:
        y0, y1 = 0, h

    for i in range(life):
        f = (f0 + i) % n_frames
        # fade in/out so it doesn't pop in and out abruptly
        t = (i + 0.5) / life
        env = min(1.0, 2.5 * min(t, 1.0 - t) + 0.15)
        xi = int(round(x + drift * i))
        if xi < 0 or xi + width > w:
            continue
        val = peak * env
        frames[f, y0:y1, xi:xi + width] = np.maximum(
            frames[f, y0:y1, xi:xi + width], val
        )


def _draw_hair(frames, n_frames, w, h, rng, brightness_max):
    """A "hair" caught in the gate: a thin trembling curve, lasts longer than
    a scratch and reads clearly. Used sparingly, or it gets tiring."""
    f0 = rng.integers(0, n_frames)
    life = int(rng.integers(8, 30))
    x0 = rng.uniform(0.1, 0.9) * w
    amp = rng.uniform(4, 22)
    freq = rng.uniform(1.5, 4.0)
    y_start = int(rng.uniform(0, 0.35) * h)
    y_end = int(rng.uniform(0.5, 1.0) * h)
    peak = rng.uniform(0.4, 0.8) * brightness_max
    ys = np.arange(y_start, y_end)
    phase = rng.uniform(0, 6.28)

    for i in range(life):
        f = (f0 + i) % n_frames
        t = (i + 0.5) / life
        env = min(1.0, 3.0 * min(t, 1.0 - t) + 0.1)
        wobble = rng.normal(0, 0.6)  # the hair trembles frame to frame
        xs = x0 + wobble + amp * np.sin(freq * 2 * np.pi * ys / h + phase)
        xi = np.clip(xs.astype(int), 0, w - 1)
        frames[f, ys, xi] = np.maximum(frames[f, ys, xi], peak * env)


def _draw_dust(frames, n_frames, w, h, rng, density, brightness_max):
    """Dust specks: almost all last a single frame (which is why they "flicker")."""
    per_frame = max(1, int(density * (w * h) / 40000))
    for f in range(n_frames):
        n = rng.poisson(per_frame)
        if n == 0:
            continue
        ys = rng.integers(0, h, n)
        xs = rng.integers(0, w, n)
        sizes = rng.choice([1, 1, 1, 2, 2, 3], n)
        vals = rng.uniform(0.25, 1.0, n) * brightness_max
        for y, x, s, v in zip(ys, xs, sizes, vals):
            y1, x1 = min(h, y + s), min(w, x + s)
            frames[f, y:y1, x:x1] = np.maximum(frames[f, y:y1, x:x1], v)


def build_scratch_texture(out_path, width, height, fps=25, seconds=6.0,
                          seed=7, scratch_rate=3.5, hair_rate=0.35,
                          dust_density=1.0, brightness=170):
    """Writes a loopable gray FFV1 mkv with the wear-and-tear texture.

    scratch_rate / hair_rate are in events per second; dust_density is a
    multiplier on the base amount of specks per frame.
    """
    w = max(2, int(width * TEX_SCALE) // 2 * 2)
    h = max(2, int(height * TEX_SCALE) // 2 * 2)
    n_frames = max(1, int(round(seconds * fps)))
    rng = np.random.default_rng(seed)

    frames = np.zeros((n_frames, h, w), dtype=np.float32)
    for _ in range(int(round(scratch_rate * seconds))):
        _draw_scratch(frames, n_frames, w, h, rng, brightness)
    for _ in range(max(0, int(round(hair_rate * seconds)))):
        _draw_hair(frames, n_frames, w, h, rng, brightness)
    _draw_dust(frames, n_frames, w, h, rng, dust_density, brightness)

    data = np.clip(frames, 0, 255).astype(np.uint8).tobytes()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "pipe:0",
        "-c:v", "ffv1", "-level", "3", "-g", "1", str(out_path),
    ]
    proc = subprocess.run(cmd, input=data, capture_output=True)
    if proc.returncode != 0 or not out_path.exists():
        raise SystemExit(f"Could not generate the texture: {proc.stderr.decode()[-400:]}")
    return out_path


def ensure_scratch_texture(cache_dir, width, height, fps=25, **kwargs):
    """Returns the cached texture, generating it only the first time.

    The cache key includes the parameters that change the result, so two
    presets with different density don't clobber each other's file."""
    key_parts = [f"{width}x{height}", f"{fps}fps"]
    for k in sorted(kwargs):
        key_parts.append(f"{k}{kwargs[k]}")
    out_path = Path(cache_dir) / f"scratches_{'_'.join(str(p) for p in key_parts)}.mkv"
    if out_path.exists():
        return out_path
    return build_scratch_texture(out_path, width, height, fps=fps, **kwargs)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Generates the scratches/dust texture standalone, to look at it.")
    ap.add_argument("out")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1280)
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    p = build_scratch_texture(Path(a.out), a.width, a.height, seconds=a.seconds, seed=a.seed)
    print(f"-> {p}")
