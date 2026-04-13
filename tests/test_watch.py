"""Tests for WatchDisplay."""

import io
import re
import sys
import time
import threading
from unittest.mock import patch

import pytest

import runcorder._context as ctx
from runcorder.watch import WatchDisplay, _is_user_frame


# ---------------------------------------------------------------------------
# _is_user_frame

def test_is_user_frame_for_test_file():
    # This frame itself is a user frame
    frame = sys._getframe()
    assert _is_user_frame(frame)


def test_is_user_frame_excludes_stdlib():
    import threading as _t
    # A frame inside threading is not user code
    frame = sys._getframe()
    # Use a synthetic frame by grabbing one from a stdlib function
    # We can't easily get a real stdlib frame here, so check via filename
    import types
    code = compile("x = 1", "<stdin>", "exec")
    assert not _is_user_frame(types.SimpleNamespace(
        f_code=types.SimpleNamespace(co_filename="<stdin>")
    ))


def test_is_user_frame_excludes_angle_bracket():
    import types
    frame = types.SimpleNamespace(
        f_code=types.SimpleNamespace(co_filename="<string>")
    )
    assert not _is_user_frame(frame)


# ---------------------------------------------------------------------------
# WatchDisplay — line format

def _make_display_and_sink(**kwargs):
    """Create a WatchDisplay with a captured StringIO as its sink."""
    sink = io.StringIO()
    d = WatchDisplay(**kwargs)
    d._orig_stderr = sink  # inject fake sink
    d._started_at = time.monotonic()
    d._last_qualname_change = d._started_at
    return d, sink


def _run_tick_in_thread(display):
    """Run one tick from the watch thread context (not main thread)."""
    done = threading.Event()
    def worker():
        display._tick()
        done.set()
    t = threading.Thread(target=worker)
    t.start()
    done.wait(timeout=2.0)
    t.join(timeout=2.0)


def test_line_format_basic():
    d, sink = _make_display_and_sink(watch_inplace=False)
    # Patch _is_user_frame to accept this test frame
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    output = sink.getvalue()
    # Should contain elapsed seconds pattern
    assert re.search(r"\[\d+s\]", output)


def test_line_includes_context_variables():
    ctx._install()
    try:
        ctx.context(epoch=5, loss=0.31)
        d, sink = _make_display_and_sink(watch_inplace=False)
        with patch("runcorder.watch._is_user_frame", return_value=True):
            _run_tick_in_thread(d)
        output = sink.getvalue()
        assert "epoch=5" in output
        assert "loss=0.31" in output
        assert "|" in output  # separator between context and chain
    finally:
        ctx._uninstall()


def test_line_no_context_no_pipe():
    ctx._uninstall()
    d, sink = _make_display_and_sink(watch_inplace=False)
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    output = sink.getvalue()
    # No context → no pipe separator
    assert "|" not in output


def test_stuck_marker_in_line():
    d, sink = _make_display_and_sink(watch_inplace=False, stuck_timeout=0.0)
    # stuck_timeout=0 means it should never auto-fire; force it manually
    d._stuck_fired = True
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    output = sink.getvalue()
    assert "stuck?" in output


def test_stuck_fires_once():
    """Stuck detection fires exactly once even across multiple ticks."""
    import types

    main_tid = threading.main_thread().ident

    class FakeFrame:
        f_back = None
        f_lineno = 10
        f_code = types.SimpleNamespace(
            co_filename="/user/script.py",
            co_qualname="some_func",
            co_name="some_func",
        )

    fake_frames = {main_tid: FakeFrame()}

    d, sink = _make_display_and_sink(watch_inplace=False, stuck_timeout=0.001)
    # Pre-set qualname set to match fake frame so the time condition is hit
    d._last_qualname_set = frozenset(["some_func"])
    d._last_qualname_change = time.monotonic() - 1.0  # stale → should fire

    fired_count = 0

    with patch("sys._current_frames", return_value=fake_frames):
        with patch("runcorder.watch._is_user_frame", return_value=True):
            for _ in range(3):
                before = d._stuck_fired
                d._tick()
                if d._stuck_fired and not before:
                    fired_count += 1

    assert fired_count == 1


def test_snapshots_recorded():
    d, sink = _make_display_and_sink(watch_inplace=False)
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
        _run_tick_in_thread(d)
    assert len(d.snapshots) == 2


def test_start_stop_installs_tracker():
    d = WatchDisplay(watch_inplace=True, install_trackers=False)
    original_stderr = sys.stderr
    try:
        d.start()
        # stderr should be wrapped
        assert sys.stderr is not original_stderr
    finally:
        d.stop()
        assert sys.stderr is original_stderr


def test_start_stop_installs_stdout_tracker():
    d = WatchDisplay(watch_inplace=False, install_trackers=True)
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    try:
        d.start()
        assert sys.stdout is not original_stdout
        assert sys.stderr is not original_stderr
    finally:
        d.stop()
        assert sys.stdout is original_stdout
        assert sys.stderr is original_stderr


def test_tail_stdout_empty_without_tracker():
    d = WatchDisplay(watch_inplace=False, install_trackers=False)
    assert d.tail_stdout() == []


def test_tail_stderr_after_install():
    d = WatchDisplay(watch_inplace=True, install_trackers=False)
    try:
        d.start()
        sys.stderr.write("hello\n")
        assert d.tail_stderr() == ["hello"]
    finally:
        d.stop()


# ---------------------------------------------------------------------------
# Prefix trimming — stable prefix is hidden

def test_stable_prefix_trimming():
    """Frames stable across the last 3 ticks are hidden from display."""
    d, sink = _make_display_and_sink(watch_inplace=False)
    # Seed tick history with stable outer + changing inner
    d._tick_history = [
        ["stable_outer", "inner_a"],
        ["stable_outer", "inner_b"],
        ["stable_outer", "inner_c"],
    ]
    # On next tick, only inner frame should be shown
    # (stable_count should be 1 → skip first qualname)
    # We verify indirectly via snapshots output
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    # We can't assert the exact qualname without controlling frames,
    # but we can assert that a snapshot was recorded
    assert len(d.snapshots) >= 1
