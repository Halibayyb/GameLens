import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SMALL = ROOT / "data" / "test_match"


def pytest_configure(config):
    if not (SMALL / "match_meta.json").exists():
        subprocess.run([sys.executable, str(ROOT / "data_gen" / "synthetic_match.py"), "--seed", "7",
                        "--half-minutes", "20", "--out", str(SMALL)], check=True)
    os.environ["MATCH_DIR"] = str(SMALL)
    os.environ.setdefault("PUBLISHED_DIR", str(ROOT / "data" / "test_published"))


@pytest.fixture(scope="session")
def match():
    from engine.moments import Match
    return Match(SMALL)


@pytest.fixture(scope="session")
def proto_dir(tmp_path_factory):
    """match_proto, built once per session: the full match around the prototype's three goals."""
    from data_gen import proto_demo
    folder = tmp_path_factory.mktemp("match_proto")
    proto_demo.build(folder)
    return folder
