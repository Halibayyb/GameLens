"""Vision-source adapter.

Off unless ``MATCH_SOURCE=vision``. The API does not import YOLO. This feed
reads a match folder the vision service already wrote, or, when ``VISION_VIDEO``
is set, runs the pipeline once on that local file (CPU by default, slow on a
short clip). Weights are downloaded only in that case.
"""
import os
from pathlib import Path

from feeds.base import FeedError


class VisionFeed:
    source = "vision"

    def materialize(self, folder: Path) -> dict:
        video = os.getenv("VISION_VIDEO")
        if not video:
            raise FeedError(
                "MATCH_SOURCE=vision needs a clip. Set VISION_VIDEO to a local file and "
                "start the API again, or run `python -m vision --video clip.mp4 --out data/vision` "
                "and point MATCH_DIR at that folder. The synthetic default does not download weights. "
                "On Azure the same pipeline is the vision Container App (see the README)."
            )
        from vision.pipeline import materialize_video
        seconds = float(os.getenv("VISION_SECONDS", "8"))
        return materialize_video(video, Path(folder), seconds=seconds, calibration=os.getenv("VISION_CALIBRATION"))
