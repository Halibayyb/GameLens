#!/usr/bin/env python3
"""Copy the source clips of the prototype match's three goals into media/goal_clips.

  python scripts/install_match_videos.py ~/Downloads/final_sim_football_video

Each clip is found by its ``goal1`` / ``goal2`` / ``goal3`` prefix and a ``<minute>min`` in its name, whatever the version
suffix (``goal1_home_throughball_71min_sound_v3.mp4`` -> ``media/goal_clips/goal1-71min.mp4``). Then run
scripts/merge_goal_videos.py to join each goal's clips into web/public/videos/goalN_full.mp4, the video the page plays.
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "media" / "goal_clips"   # merge_goal_videos.py joins them into web/public/videos
NAME = re.compile(r"^goal(\d)\D.*?(\d{1,3})min", re.IGNORECASE)
EXTENSIONS = {".mp4", ".webm"}


def install(source: Path, dest: Path = DEST) -> dict:
    """Copy goal clips from `source` into `dest`. Returns {"copied": {goal number: dest file}, "missing": [numbers]}."""
    found: dict[int, Path] = {}
    for f in sorted(source.iterdir()):   # sorted: with two versions of a goal, the later name (the newer version) wins
        m = NAME.match(f.name)
        if f.is_file() and f.suffix.lower() in EXTENSIONS and m:
            found[int(m[1])] = f
    dest.mkdir(parents=True, exist_ok=True)
    copied = {}
    for number, f in sorted(found.items()):
        minute = NAME.match(f.name)[2]
        target = dest / f"goal{number}-{minute}min{f.suffix.lower()}"
        shutil.copyfile(f, target)
        copied[number] = target
    return {"copied": copied, "missing": [n for n in (1, 2, 3) if n not in found]}


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not Path(args[0]).expanduser().is_dir():
        print(__doc__.strip().splitlines()[0] + "\n\nusage: install_match_videos.py <folder with the goal videos>")
        return 2
    result = install(Path(args[0]).expanduser())
    for number, target in result["copied"].items():
        print(f"goal {number}: {target.relative_to(ROOT)}  ({target.stat().st_size / 1e6:.1f} MB)")
    for number in result["missing"]:
        print(f"goal {number}: no file starting with goal{number} and a '<minute>min' in its name")
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
