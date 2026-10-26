"""Highlight-clip pipeline: upload → track → events → analysis.

Off the default synthetic match path. The API only starts a job when someone
uses the Highlights section. Demo jobs need no video file and no GPU.
"""
from highlights.store import HIGHLIGHTS_DIR, get_job, list_jobs

__all__ = ["HIGHLIGHTS_DIR", "get_job", "list_jobs"]
