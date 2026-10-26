"""Container Apps Job / local worker entry point.

    python -m highlights.worker                 # run the authored demo
    python -m highlights.worker --job-id hl_x   # process one queued job
    python -m highlights.worker --video clip.mp4
    python -m highlights.worker --watch         # poll HIGHLIGHTS_DIR for queued jobs

The API image can run the demo. A real clip needs the vision image
(``vision/Dockerfile``) or ``pip install -r vision/requirements.txt``.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from highlights.pipeline import process_job
from highlights.storage import save_from_url
from highlights.store import create_job, list_jobs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GameLens highlight worker")
    parser.add_argument("--job-id", default=os.getenv("HIGHLIGHT_JOB_ID"), help="Process this job id")
    parser.add_argument("--video", help="Create a job from a local file and process it")
    parser.add_argument("--watch", action="store_true", help="Poll for queued jobs")
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)

    if args.watch:
        return _watch(args.interval)
    if args.job_id:
        process_job(args.job_id)
        return 0
    if args.video:
        path = Path(args.video).resolve()
        job = create_job("upload", filename=path.name)
        save_from_url(job["job_id"], path.as_uri())
        process_job(job["job_id"])
        print(job["job_id"])
        return 0
    job = create_job("demo", filename="demo")
    process_job(job["job_id"])
    print(job["job_id"])
    return 0


def _watch(interval: float) -> int:
    print(f"watching {os.getenv('HIGHLIGHTS_DIR') or 'data/highlights'} for queued jobs")
    while True:
        for job in list_jobs(50):
            if job.get("status") == "queued":
                print(f"processing {job['job_id']}")
                try:
                    process_job(job["job_id"])
                except Exception as exc:  # noqa: BLE001 — keep the watcher up
                    print(f"{job['job_id']} failed: {exc}")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
