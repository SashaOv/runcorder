"""Tests for _report — stack filtering, ReportWriter incremental output."""

from pathlib import Path

import pytest

from runcorder._report import (
    ReportMeta,
    ReportWriter,
    StackFrame,
    filter_stack,
    format_stack,
)


# ---------------------------------------------------------------------------
# Stack helpers

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
    assert filter_stack(frames) == frames


def test_no_user_frames_fallback():
    frames = [N("a"), N("b"), N("c")]
    assert filter_stack(frames) == frames


def test_single_user_frame():
    assert filter_stack([U("main")]) == [U("main")]


def test_leading_non_user_collapsed():
    frames = [N("a"), N("b"), N("c"), U("user")]
    result = filter_stack(frames)
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
    assert U("user") in result
    assert N("a") in result
    assert N("c") in result


def test_trailing_single_non_user():
    frames = [U("user"), N("a")]
    assert filter_stack(frames) == [U("user"), N("a")]


def test_sandwiched_non_user_collapsed():
    frames = [U("a"), N("x"), N("y"), N("z"), U("b")]
    result = filter_stack(frames)
    assert U("a") in result
    assert U("b") in result
    assert N("x") in result
    assert "..." in result
    assert N("z") in result
    assert N("y") not in result


def test_sandwiched_two_non_user_no_ellipsis():
    frames = [U("a"), N("x"), N("y"), U("b")]
    assert filter_stack(frames) == [U("a"), N("x"), N("y"), U("b")]


def test_sandwiched_single_non_user():
    frames = [U("a"), N("x"), U("b")]
    assert filter_stack(frames) == [U("a"), N("x"), U("b")]


def test_innermost_non_user_always_kept():
    frames = [U("a"), N("x"), N("y")]
    result = filter_stack(frames)
    assert N("y") in result


def test_all_non_user_between_two_user_blocks():
    frames = [U("a"), U("b"), N("x"), N("y"), N("z"), U("c"), U("d")]
    result = filter_stack(frames)
    user_names = {item.name for item in result if isinstance(item, StackFrame)}
    assert "a" in user_names
    assert "b" in user_names
    assert "c" in user_names
    assert "d" in user_names
    assert "x" in user_names
    assert "z" in user_names
    assert "y" not in user_names
    assert "..." in result


# ---------------------------------------------------------------------------
# format_stack

def test_format_stack_user_frame():
    output = format_stack([U("my_func", lineno=42)])
    assert 'File "/user/code.py"' in output
    assert "line 42" in output
    assert "my_func" in output


def test_format_stack_ellipsis():
    assert "..." in format_stack(["..."])


# ---------------------------------------------------------------------------
# ReportWriter — fixtures

def _meta() -> ReportMeta:
    return ReportMeta(
        command=["python", "script.py"],
        cwd="/home/user",
        python="3.13.0 (main, ...)",
        started_at="2026-04-13T10:00:00",
    )


def _exc_dict() -> dict:
    return {
        "type": "ValueError",
        "message": "bad input",
        "traceback": 'Traceback...\n  File "x.py", line 1, in f\nValueError: bad input',
    }


# ---------------------------------------------------------------------------
# ReportWriter — header behavior

def test_header_not_written_until_first_section(tmp_path):
    w = ReportWriter(tmp_path / "r.md", _meta())
    assert not w.header_written
    assert not (tmp_path / "r.md").exists()


def test_header_written_on_first_exception(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    assert w.header_written
    content = p.read_text()
    assert content.startswith("---\n")
    assert "command:" in content
    assert "started_at:" in content


def test_header_written_on_first_stuck(tmp_path):
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_stuck([_sys._getframe()])
    assert w.header_written
    content = p.read_text()
    assert content.startswith("---\n")


def test_header_not_rewritten_on_second_section(tmp_path):
    """Front matter must appear exactly once across multiple writes."""
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_stuck([_sys._getframe()])
    w.write_exception(_exc_dict())
    content = p.read_text()
    # Exactly two --- fences (open + close front matter)
    assert content.count("\n---\n") == 1  # closing fence
    assert content.startswith("---\n")


# ---------------------------------------------------------------------------
# ReportWriter — front matter contents

def test_front_matter_has_static_fields_only(tmp_path):
    """Front matter should NOT contain ended_at, duration_s, exit_status."""
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    # Extract front matter region (between first two --- fences)
    content = p.read_text()
    lines = content.splitlines()
    assert lines[0] == "---"
    close = lines.index("---", 1)
    front = "\n".join(lines[1:close])
    assert "command:" in front
    assert "cwd:" in front
    assert "python:" in front
    assert "started_at:" in front
    assert "ended_at:" not in front
    assert "duration_s:" not in front
    assert "exit_status:" not in front


# ---------------------------------------------------------------------------
# ReportWriter — sections

def test_write_exception_section(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    content = p.read_text()
    assert "## Exception" in content
    assert "ValueError" in content
    assert "bad input" in content


def test_write_stuck_section(tmp_path):
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_stuck([_sys._getframe()])
    content = p.read_text()
    assert "## Stuck Snapshot" in content


def test_sections_appear_in_call_order(tmp_path):
    """Stuck-then-exception produces stuck section before exception section."""
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_stuck([_sys._getframe()])
    w.write_exception(_exc_dict())
    content = p.read_text()
    assert content.index("## Stuck Snapshot") < content.index("## Exception")


def test_exception_then_stuck_order(tmp_path):
    """Writer tolerates exception-before-stuck (unusual but valid)."""
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.write_stuck([_sys._getframe()])
    content = p.read_text()
    assert content.index("## Exception") < content.index("## Stuck Snapshot")


# ---------------------------------------------------------------------------
# ReportWriter — finalize

def test_finalize_writes_summary(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(
        ended_at="2026-04-13T10:01:00",
        duration_s=60.0,
        exit_status="exception",
    )
    content = p.read_text()
    assert "## Summary" in content
    assert "ended_at:" in content
    assert "duration_s: 60.000" in content
    assert "exit_status: exception" in content


def test_finalize_summary_after_sections(tmp_path):
    """Summary must appear after any content sections."""
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(ended_at="2026-04-13T10:01:00", duration_s=1.0, exit_status=0)
    content = p.read_text()
    assert content.index("## Exception") < content.index("## Summary")


def test_finalize_noop_without_header(tmp_path):
    """finalize() is a no-op if nothing has been written."""
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.finalize(ended_at="2026-04-13T10:01:00", duration_s=1.0, exit_status=0)
    assert not p.exists()
    assert not w.header_written


def test_finalize_includes_watch_snapshots(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(
        ended_at="2026-04-13T10:01:00",
        duration_s=1.0,
        exit_status="exception",
        watch_snapshots=["[1s] train:10", "[4s] train:11"],
    )
    content = p.read_text()
    assert "## Watch Snapshots" in content
    assert "[1s] train:10" in content
    assert "[4s] train:11" in content


def test_finalize_omits_watch_snapshots_when_empty(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(
        ended_at="2026-04-13T10:01:00",
        duration_s=1.0,
        exit_status="exception",
        watch_snapshots=[],
    )
    assert "## Watch Snapshots" not in p.read_text()


def test_finalize_includes_output_tail(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(
        ended_at="2026-04-13T10:01:00",
        duration_s=1.0,
        exit_status="exception",
        output_tail="stdout line\nstderr line",
    )
    content = p.read_text()
    assert "## Output Tail" in content
    assert "stdout line" in content


def test_finalize_omits_output_tail_when_none(tmp_path):
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_exception(_exc_dict())
    w.finalize(ended_at="2026-04-13T10:01:00", duration_s=1.0, exit_status="exception")
    assert "## Output Tail" not in p.read_text()


# ---------------------------------------------------------------------------
# ReportWriter — stuck-only (process killed before finalize)

def test_stuck_only_report_is_valid_without_finalize(tmp_path):
    """If the process is killed after stuck fires (no finalize),
    the report file should still be a self-contained markdown document."""
    import sys as _sys
    p = tmp_path / "r.md"
    w = ReportWriter(p, _meta())
    w.write_stuck([_sys._getframe()])
    content = p.read_text()
    assert content.startswith("---\n")
    assert "## Stuck Snapshot" in content
    # No Summary section — that's expected
    assert "## Summary" not in content


# ---------------------------------------------------------------------------
# ReportWriter — command list

# ---------------------------------------------------------------------------
# StackFrame args rendering

def test_format_stack_frame_with_args():
    """Args are rendered as name(k=v) in the formatted line."""
    frame = StackFrame(
        filename="/user/code.py",
        lineno=10,
        name="train_step",
        is_user=True,
        args=[("epoch", "5"), ("loss", "0.312")],
    )
    output = format_stack([frame])
    assert "train_step(epoch=5, loss=0.312)" in output
    assert 'line 10' in output


def test_format_stack_frame_no_args_empty_parens():
    """Frames with no captured args are rendered with empty parentheses."""
    frame = StackFrame(
        filename="/user/code.py",
        lineno=5,
        name="run",
        is_user=True,
        args=[],
    )
    output = format_stack([frame])
    assert "in run()" in output


def test_classify_frame_captures_args():
    """_classify_frame reads parameter values from a real frame."""
    import sys as _sys
    from runcorder._report import classify_frames

    def _helper(x, y):
        return _sys._getframe()

    frame = _helper(42, "hello")
    classified = classify_frames([frame])
    assert len(classified) == 1
    sf = classified[0]
    arg_dict = dict(sf.args)
    assert arg_dict.get("x") == "42"
    assert arg_dict.get("y") == "'hello'"


def test_classify_frame_caps_long_repr():
    """Repr values longer than 80 chars are capped with '...'."""
    import sys as _sys
    from runcorder._report import classify_frames

    def _helper(big):
        return _sys._getframe()

    long_val = "x" * 200
    frame = _helper(long_val)
    classified = classify_frames([frame])
    arg_dict = dict(classified[0].args)
    r = arg_dict["big"]
    assert len(r) <= 80
    assert r.endswith("...")


def test_classify_frame_no_args_for_no_param_function():
    """Functions with no parameters produce an empty args list."""
    import sys as _sys
    from runcorder._report import classify_frames

    def _helper():
        return _sys._getframe()

    frame = _helper()
    classified = classify_frames([frame])
    assert classified[0].args == []


def test_exception_report_includes_args(tmp_path):
    """When a real exception is written via InstrumentContext, the report's
    traceback shows function arguments."""
    from unittest.mock import patch
    from runcorder._session import InstrumentContext

    def _no_check():
        pass

    output = tmp_path / "report.md"

    def outer(x):
        inner(x * 2)

    def inner(y):
        raise ValueError("boom")

    with patch("runcorder._session._location.check_log_size", _no_check):
        import pytest as _pytest
        with _pytest.raises(ValueError):
            with InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0):
                outer(7)

    content = output.read_text()
    # outer(x=7) and inner(y=14) should appear in the filtered traceback
    assert "x=7" in content
    assert "y=14" in content


# ---------------------------------------------------------------------------
# ReportWriter — command list

def test_command_list_in_front_matter(tmp_path):
    meta = ReportMeta(
        command=["python", "-m", "runcorder", "my_script.py"],
        cwd="/home/user",
        python="3.13.0",
        started_at="2026-04-13T10:00:00",
    )
    p = tmp_path / "r.md"
    w = ReportWriter(p, meta)
    w.write_exception(_exc_dict())
    assert "my_script.py" in p.read_text()
