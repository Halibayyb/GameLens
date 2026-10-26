#!/usr/bin/env python3
"""Join the three clips of each goal into one video the page plays as a single clip.

  python scripts/merge_goal_videos.py          # goals 1, 2 and 3
  python scripts/merge_goal_videos.py 1 3      # only some goals

For goal N it reads, from ``media/goal_clips``, the match clip ``goalN-<minute>min.mp4``, ``goalN_analytics_6s.mp4`` and
``goalN_smart_pov.mp4`` (in that order) and writes ``goalN_full.mp4`` to ``web/public/videos``, the only file the page
plays (the source clips stay out of the deployed site). Every clip is first scaled to
1920 x 1080 at 30 fps (letterboxed, never stretched) with 48 kHz stereo audio, so the joins have no seam; a clip without
an audio track (the analytics and smart-view clips are silent) gets silence of the same length. The source clips are
not changed. Needs ffmpeg (``brew install ffmpeg``).
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIPS = ROOT / "media" / "goal_clips"      # the source clips (not deployed)
VIDEOS = ROOT / "web" / "public" / "videos"  # the joined videos the page plays
W, H, FPS = 1920, 1080, 30


def clips_of(number: int, folder: Path = CLIPS) -> list[Path]:
    """The goal's clips in play order: the match clip, analytics, smart view. Raises FileNotFoundError for a missing one."""
    match = sorted(folder.glob(f"goal{number}-*min.mp4"))
    order = [match[-1] if match else folder / f"goal{number}-<minute>min.mp4",
             folder / f"goal{number}_analytics_6s.mp4", folder / f"goal{number}_smart_pov.mp4"]
    missing = [p.name for p in order if not p.exists()]
    if missing:
        raise FileNotFoundError(f"goal {number}: missing {', '.join(missing)} in {folder}")
    return order


def probe(ffmpeg: str, clip: Path) -> tuple[bool, float]:
    """(has an audio track, duration in seconds). ffmpeg lists both on stderr, so ffprobe is not needed."""
    out = subprocess.run([ffmpeg, "-hide_banner", "-i", str(clip)], capture_output=True, text=True).stderr
    h, m, sec = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out).groups()
    return bool(re.search(r"Stream #\d+:\d+.*Audio:", out)), int(h) * 3600 + int(m) * 60 + float(sec)


def merge(number: int, folder: Path = CLIPS, ffmpeg: str | None = None, out_dir: Path = VIDEOS) -> Path:
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found: install it first (brew install ffmpeg)")
    clips = clips_of(number, folder)
    info = [probe(ffmpeg, c) for c in clips]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"goal{number}_full.mp4"
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    parts = []
    for i in range(len(clips)):
        parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={FPS},format=yuv420p[v{i}]")
        has_audio, seconds = info[i]
        parts.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]" if has_audio
                     else f"anullsrc=r=48000:cl=stereo:d={seconds:.3f}[a{i}]")   # silent clip: silence as long as the picture
    inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
    parts.append(f"{inputs}concat=n={len(clips)}:v=1:a=1[v][a]")
    cmd += ["-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    return out


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    numbers = [int(a) for a in args] if args else [1, 2, 3]
    status = 0
    for n in numbers:
        try:
            out = merge(n)
            print(f"goal {n}: {out.relative_to(ROOT)}  ({out.stat().st_size / 1e6:.1f} MB)")
        except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as e:
            print(f"goal {n}: {e}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
