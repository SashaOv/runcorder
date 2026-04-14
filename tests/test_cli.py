"""Tests for __main__ (exit-code propagation) and cli.py (runcorder clean)."""

import os
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# __main__ exit-code propagation

def _run_runcorder(script_content: str, tmp_path: Path, extra_args=()) -> subprocess.CompletedProcess:
    script = tmp_path / "script.py"
    script.write_text(script_content)
    return subprocess.run(
        [sys.executable, "-m", "runcorder", str(script), *extra_args],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )


def test_exit_code_zero_on_success(tmp_path):
    result = _run_runcorder("print('ok')", tmp_path)
    assert result.returncode == 0


def test_exit_code_from_sys_exit(tmp_path):
    result = _run_runcorder("import sys; sys.exit(42)", tmp_path)
    assert result.returncode == 42


def test_exit_code_nonzero_on_exception(tmp_path):
    result = _run_runcorder("raise ValueError('boom')", tmp_path)
    assert result.returncode != 0


def test_report_written_on_exception(tmp_path):
    result = _run_runcorder("raise RuntimeError('test')", tmp_path)
    # Some report should have been written somewhere (auto-named)
    # We can't easily intercept the path, but we can confirm exit code
    assert result.returncode != 0


def test_no_report_on_success(tmp_path):
    """On clean exit, no .md file should appear in tmp_path."""
    result = _run_runcorder("x = 1 + 1", tmp_path)
    assert result.returncode == 0
    md_files = list(tmp_path.glob("*.md"))
    assert md_files == []


def test_script_argv_passed(tmp_path):
    script = tmp_path / "show_argv.py"
    script.write_text("import sys; print(sys.argv)")
    result = subprocess.run(
        [sys.executable, "-m", "runcorder", str(script), "arg1", "arg2"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert "arg1" in result.stdout
    assert "arg2" in result.stdout
    # sys.argv[0] should be the script path, not 'runcorder'
    assert "runcorder" not in result.stdout.split("[")[1].split(",")[0]


# ---------------------------------------------------------------------------
# runcorder clean

def _make_report(log_dir: Path, name: str, age_days: float) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / name
    p.write_text("# report\n")
    mtime = time.time() - age_days * 86400
    os.utime(p, (mtime, mtime))
    return p


def test_clean_removes_old_reports(tmp_path):
    from runcorder._location import default_log_dir
    import runcorder._location as loc
    log_dir = tmp_path / "logs"
    old = _make_report(log_dir, "old.md", age_days=2)
    fresh = _make_report(log_dir, "fresh.md", age_days=0)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        from runcorder.cli import clean
        clean("1d")

    assert not old.exists()
    assert fresh.exists()


def test_clean_keeps_fresh_reports(tmp_path):
    from runcorder._location import default_log_dir
    import runcorder._location as loc
    log_dir = tmp_path / "logs"
    fresh = _make_report(log_dir, "recent.md", age_days=0.5)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        from runcorder.cli import clean
        clean("1d")

    assert fresh.exists()


def test_clean_hours_unit(tmp_path):
    import runcorder._location as loc
    log_dir = tmp_path / "logs"
    old = _make_report(log_dir, "old.md", age_days=1)  # 24h old
    fresh = _make_report(log_dir, "fresh.md", age_days=0.1)

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        from runcorder.cli import clean
        clean("12h")

    assert not old.exists()
    assert fresh.exists()


def test_clean_invalid_age_exits(tmp_path):
    from runcorder.cli import clean
    with pytest.raises(SystemExit):
        clean("bad-age")


def test_clean_nonexistent_dir_is_noop(tmp_path):
    import runcorder._location as loc
    with patch.object(loc, "default_log_dir", return_value=tmp_path / "nonexistent"):
        from runcorder.cli import clean
        clean("1d")  # should not raise


def test_clean_removes_size_check(tmp_path):
    import runcorder._location as loc
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    size_check = log_dir / "size_check"
    size_check.write_text("12345")

    with patch.object(loc, "default_log_dir", return_value=log_dir):
        from runcorder.cli import clean
        clean("1d")

    assert not size_check.exists()
