import io
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import runcorder._location as loc


# ---------------------------------------------------------------------------
# auto_name

def test_auto_name_creates_dir(tmp_path):
    with patch.object(loc, "default_log_dir", return_value=tmp_path / "logs"):
        p = loc.auto_name()
    assert p.parent.exists()


def test_auto_name_format(tmp_path):
    with patch.object(loc, "default_log_dir", return_value=tmp_path / "logs"):
        p = loc.auto_name()
    assert re.match(r"\d{6}-\d{6}\.md", p.name), f"unexpected name: {p.name}"


def test_auto_name_suffix(tmp_path):
    with patch.object(loc, "default_log_dir", return_value=tmp_path / "logs"):
        p = loc.auto_name()
    assert p.suffix == ".md"


# ---------------------------------------------------------------------------
# default_log_dir

def test_default_log_dir_non_windows():
    if sys.platform == "win32":
        pytest.skip("non-Windows test")
    d = loc.default_log_dir()
    assert "runcorder" in str(d)
    assert "logs" in str(d)


# ---------------------------------------------------------------------------
# check_log_size — no warning below threshold

def test_check_log_size_no_warning_small(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "a.md").write_bytes(b"x" * 1024)  # 1 KB

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        loc.check_log_size()

    captured = capsys.readouterr()
    assert "runcorder log size" not in captured.err


def test_check_log_size_warns_over_100mb(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Write a size_check file with a value > 100 MB to avoid creating huge files
    size_check = log_dir / "size_check"
    size_check.write_text(str(110 * 1024 * 1024))
    # Make it look fresh (< 1 day old)
    os.utime(size_check, None)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        loc.check_log_size()

    captured = capsys.readouterr()
    assert "runcorder log size" in captured.err
    assert "runcorder clean" in captured.err


def test_check_log_size_creates_size_check(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "a.md").write_bytes(b"x" * 100)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        loc.check_log_size()

    assert (log_dir / "size_check").exists()


def test_check_log_size_recalculates_when_stale(tmp_path, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    size_check = log_dir / "size_check"
    # Write stale size_check (2 days old)
    size_check.write_text("0")
    stale_time = time.time() - 2 * 86400
    os.utime(size_check, (stale_time, stale_time))
    # Add a file
    (log_dir / "a.md").write_bytes(b"x" * 100)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        loc.check_log_size()

    new_value = int(size_check.read_text().strip())
    assert new_value == 100


def test_check_log_size_uses_cache_when_fresh(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    size_check = log_dir / "size_check"
    # Fresh cache with value 0 (even though directory has files)
    size_check.write_text("0")
    os.utime(size_check, None)
    (log_dir / "a.md").write_bytes(b"x" * 200)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        loc.check_log_size()

    # Value should NOT be recalculated
    assert size_check.read_text().strip() == "0"


def test_check_log_size_nonexistent_dir_is_noop(tmp_path):
    with patch.object(loc, "default_log_dir", return_value=tmp_path / "nonexistent"):
        loc.check_log_size()  # should not raise
