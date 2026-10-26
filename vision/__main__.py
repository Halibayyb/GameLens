"""Run the vision pipeline on a local clip.

    python -m vision --video clip.mp4 --out data/vision --seconds 8

Weights download the first time YOLO runs. CPU is the default; set VISION_DEVICE=cuda
in the GPU container. A short clip is slow on CPU and that is expected.
"""
import argparse
import os
from pathlib import Path

from vision.pipeline import materialize_video


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="python -m vision")
    parser.add_argument("--video", required=True, help="local match clip, or a file already downloaded from Blob Storage")
    parser.add_argument("--out", default=os.getenv("VISION_MATCH_DIR", "data/vision"))
    parser.add_argument("--seconds", type=float, default=float(os.getenv("VISION_SECONDS", "8")))
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--calibration", default=os.getenv("VISION_CALIBRATION"))
    args = parser.parse_args(argv)
    meta = materialize_video(args.video, Path(args.out), seconds=args.seconds, fps=args.fps, calibration=args.calibration)
    print(f"wrote {args.out} ({meta['data_label']}, {meta['fps']} fps)")


if __name__ == "__main__":
    main()
