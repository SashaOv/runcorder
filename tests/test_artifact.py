"""Tests for _artifact — stack filtering, Markdown output, front matter."""

from pathlib import Path
import pytest

from runcorder._artifact import (
    ArtifactData,
    StackFrame,
    filter_stack,
    format_stack,
    write,
)


# ---------------------------------------------------------------------------
# Helpers

def U(name, lineno=1):
    return StackFrame(filename="/user/code.py", lineno=lineno, name=name, is_user=True)


def N(name, lineno=1):
    return StackFrame(filename="/lib/python3/foo.py", lineno=lineno, name=name, is_user=False)


# ---------------------------------------------------------------------------
# filter_stack — basic rules

def test_empty_stack():
    assert filter_stack([]) == []


def test_all_user_frames_kept():
    frames = [U("a"), U("b"), U("c")]
    result = filter_stack(frames)
    assert result == frames


def test_no_user_frames_fallback():
    frames = [N("a"), N("b"), N("c")]
    result = filter_stack(frames)
    assert result == frames


def test_single_user_frame():
    result = filter_stack([U("main")])
    assert result == [U("main")]


def test_leading_non_user_collapsed():
    frames = [N("a"), N("b"), N("c"), U("user")]
    result = filter_stack(frames)
    # Leading non-user: keep only the last (N("c")) → preceded by "..."
    assert "..." in result
    assert N("c") in result
    assert U("user") in result
    assert N("a") not in result


def test_leading_single_non_user_no_ellipsis():
    frames = [N("a"), U("user")]
    result = filter_stack(frames)
    assert result == [N("a"), U("user")]
    assert "..." not in result


def test_trailing_non_user():
    frames = [U("user"), N("a"), N("b"), N("c")]
    result = filter_stack(frames)
    # Trailing: keep first (N("a")), rest collapsed to "..."
    assert U("user") in result
    assert N("a") in result
    # Last frame always kept
    assert N("c") in result


def test_trailing_single_non_user():
    frames = [U("user"), N("a")]
    result = filter_stack(frames)
    assert result == [U("user"), N("a")]
    assert "..." not in result


def test_sandwiched_non_user_collapsed():
    frames = [U("a"), N("x"), N("y"), N("z"), U("b")]
    result = filter_stack(frames)
    assert U("a") in result
    assert U("b") in result
    assert N("x") in result   # first of span (after user block)
    assert "..." in result
    assert N("z") in result   # last of span (before next user block)
    assert N("y") not in result


def test_sandwiched_two_non_user_no_ellipsis():
    frames = [U("a"), N("x"), N("y"), U("b")]
    result = filter_stack(frames)
    assert result == [U("a"), N("x"), N("y"), U("b")]
    assert "..." not in result


def test_sandwiched_single_non_user():
    frames = [U("a"), N("x"), U("b")]
    result = filter_stack(frames)
    assert result == [U("a"), N("x"), U("b")]


def test_innermost_non_user_always_kept():
    frames = [U("a"), N("x"), N("y")]
    result = filter_stack(frames)
    # N("y") is innermost, must be in result
    assert N("y") in result


def test_all_non_user_between_two_user_blocks():
    # [U U N N N U U]
    frames = [U("a"), U("b"), N("x"), N("y"), N("z"), U("c"), U("d")]
    result = filter_stack(frames)
    user_names = {item.name for item in result if isinstance(item, StackFrame)}
    assert "a" in user_names
    assert "b" in user_names
    assert "c" in user_names
    assert "d" in user_names
    assert "x" in user_names   # first of sandwiched span
    assert "z" in user_names   # last of sandwiched span
    assert "y" not in user_names
    assert "..." in result


# ---------------------------------------------------------------------------
# format_stack

def test_format_stack_user_frame():
    frames = [U("my_func", lineno=42)]
    output = format_stack(frames)
    assert 'File "/user/code.py"' in output
    assert "line 42" in output
    assert "my_func" in output


def test_format_stack_ellipsis():
    output = format_stack(["..."])
    assert "..." in output


# ---------------------------------------------------------------------------
# write — front matter fields

def _make_data(**overrides) -> ArtifactData:
    defaults = dict(
        command=["python", "script.py"],
        cwd="/home/user",
        python="3.13.0 (main, ...)",
        started_at="2026-04-13T10:00:00",
        ended_at="2026-04-13T10:01:00",
        duration_s=60.0,
        exit_status="exception",
    )
    defaults.update(overrides)
    return ArtifactData(**defaults)


def test_write_creates_file(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(), p)
    assert p.exists()


def test_write_front_matter_fields(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(), p)
    content = p.read_text()
    assert "command:" in content
    assert "python" in content
    assert "started_at:" in content
    assert "ended_at:" in content
    assert "duration_s:" in content
    assert "exit_status:" in content
    assert "cwd:" in content


def test_write_yaml_fences(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(), p)
    content = p.read_text()
    assert content.startswith("---\n")
    # Second --- closes front matter
    lines = content.splitlines()
    fence_count = sum(1 for l in lines if l == "---")
    assert fence_count >= 2


def test_write_exception_section(tmp_path):
    p = tmp_path / "artifact.md"
    data = _make_data(
        exception={
            "type": "ValueError",
            "message": "bad input",
            "traceback": "Traceback...\n  File ...\nValueError: bad input\n",
        }
    )
    write(data, p)
    content = p.read_text()
    assert "## Exception" in content
    assert "ValueError" in content
    assert "bad input" in content


def test_write_no_exception_section_when_none(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(exception=None), p)
    assert "## Exception" not in p.read_text()


def test_write_stuck_snapshot_section(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(stuck_snapshot="  stuck at step:42"), p)
    content = p.read_text()
    assert "## Stuck Snapshot" in content
    assert "stuck at step:42" in content


def test_write_watch_snapshots_section(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(watch_snapshots=["[1s] train:10", "[4s] train:11"]), p)
    content = p.read_text()
    assert "## Watch Snapshots" in content
    assert "[1s] train:10" in content


def test_write_output_tail_section(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(output_tail="stdout line\nstderr line\n"), p)
    content = p.read_text()
    assert "## Output Tail" in content
    assert "stdout line" in content


def test_write_no_output_tail_when_none(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(output_tail=None), p)
    assert "## Output Tail" not in p.read_text()


def test_write_command_list(tmp_path):
    p = tmp_path / "artifact.md"
    write(_make_data(command=["python", "-m", "runcorder", "my_script.py"]), p)
    content = p.read_text()
    assert "my_script.py" in content
