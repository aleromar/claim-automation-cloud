"""version-display REQ-1.2/1.3: build-version reader.

The stamp file is written by deploy-functions.yml only; everywhere else the
reader falls back to DEV_VERSION. Exact-value asserts live here (injected
path) — test_health.py deliberately checks shape only (review gate R-3).
"""

from app.version import DEV_VERSION, get_build_version

FULL_SHA = "884ac8adeadbeef0123456789abcdef012345678"


def test_missing_file_falls_back_to_dev(tmp_path):
    assert get_build_version(tmp_path / "absent.txt") == DEV_VERSION


def test_empty_file_falls_back_to_dev(tmp_path):
    stamp = tmp_path / "build_version.txt"
    stamp.write_text("\n")
    assert get_build_version(stamp) == DEV_VERSION


def test_stamped_file_is_read_and_stripped(tmp_path):
    stamp = tmp_path / "build_version.txt"
    stamp.write_text(f"{FULL_SHA}\n")
    assert get_build_version(stamp) == FULL_SHA
