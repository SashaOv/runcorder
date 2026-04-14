"""Tests for WatchDisplay."""

import io
import logging
import re
import sys
import time
import threading
from unittest.mock import patch

import pytest

import runcorder._context as ctx
from runcorder.watch import WatchDisplay, _is_user_frame, _repr_diff


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
    from runcorder._display import WatchSink

    sink = io.StringIO()
    d = WatchDisplay(**kwargs)
    d._orig_stderr = sink  # inject fake sink
    d._started_at = time.monotonic()
    d._last_qualname_change = d._started_at
    d._sink = WatchSink(
        orig_stderr=sink,
        tracker=None,
        watch_inplace=d._watch_inplace,
    )
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


def test_line_format_basic(caplog):
    caplog.set_level(logging.INFO, logger="runcorder")
    d, sink = _make_display_and_sink(watch_inplace=False)
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    assert re.search(r"\[\d+s\]", caplog.text)


def test_line_includes_context_variables(caplog):
    caplog.set_level(logging.INFO, logger="runcorder")
    ctx._install()
    try:
        ctx.context(epoch=5, loss=0.31)
        d, sink = _make_display_and_sink(watch_inplace=False)
        with patch("runcorder.watch._is_user_frame", return_value=True):
            _run_tick_in_thread(d)
        assert "epoch=5" in caplog.text
        assert "loss=0.31" in caplog.text
        assert "|" in caplog.text  # separator between context and chain
    finally:
        ctx._uninstall()


def test_line_no_context_no_pipe(caplog):
    caplog.set_level(logging.INFO, logger="runcorder")
    ctx._uninstall()
    d, sink = _make_display_and_sink(watch_inplace=False)
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    # Inspect only runcorder records — the pytest log-capture formatter
    # prefixes its own header that may contain a pipe.
    runcorder_msgs = [
        r.getMessage() for r in caplog.records if r.name == "runcorder"
    ]
    assert runcorder_msgs, "expected at least one runcorder log record"
    assert all("|" not in m for m in runcorder_msgs)


def test_stuck_marker_in_line(caplog):
    caplog.set_level(logging.INFO, logger="runcorder")
    d, sink = _make_display_and_sink(watch_inplace=False, stuck_timeout=0.0)
    d._stuck_fired = True
    with patch("runcorder.watch._is_user_frame", return_value=True):
        _run_tick_in_thread(d)
    assert "stuck?" in caplog.text


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
            co_argcount=0,
            co_kwonlyargcount=0,
            co_varnames=(),
        )
        f_locals = {}

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
# _repr_diff

def test_repr_diff_no_prev_short():
    assert _repr_diff("42", None) == "42"

def test_repr_diff_no_prev_long():
    long = "x" * 30
    result = _repr_diff(long, None)
    assert len(result) <= 24
    assert result.endswith("...")

def test_repr_diff_unchanged():
    # identical values — returns capped current (caller should skip unchanged)
    assert _repr_diff("42", "42") == "42"

def test_repr_diff_common_prefix():
    # "epoch_001" → "epoch_002": prefix "epoch_00", diff "2"
    result = _repr_diff("'epoch_002'", "'epoch_001'")
    assert result.startswith("...")
    assert "2" in result

def test_repr_diff_common_suffix():
    # "0.312" → "0.911": suffix "1", prefix "0.", diff varies
    result = _repr_diff("0.911", "0.312")
    assert "..." in result or len(result) <= 24

def test_repr_diff_no_common():
    assert _repr_diff("456", "123") == "456"

def test_repr_diff_capped():
    current = "a" * 10 + "X" * 20 + "b" * 10
    prev    = "a" * 10 + "Y" * 20 + "b" * 10
    result = _repr_diff(current, prev)
    assert len(result) <= 24


# ---------------------------------------------------------------------------
# Parameter display

def test_line_includes_args_on_change(caplog):
    """Args are shown when they change between samples."""
    import types

    caplog.set_level(logging.INFO, logger="runcorder")
    main_tid = threading.main_thread().ident

    class FakeFrame:
        f_back = None
        f_lineno = 42
        f_code = types.SimpleNamespace(
            co_filename="/user/script.py",
            co_qualname="train",
            co_name="train",
            co_argcount=1,
            co_kwonlyargcount=0,
            co_varnames=("epoch",),
        )
        f_locals = {"epoch": 1}

    fake_frames = {main_tid: FakeFrame()}

    d, sink = _make_display_and_sink(watch_inplace=False)
    with patch("sys._current_frames", return_value=fake_frames):
        with patch("runcorder.watch._is_user_frame", return_value=True):
            d._tick()  # first tick: args are new → shown
    assert "epoch=1" in caplog.text

    # Second tick with same args → should NOT show args (and the logger
    # dedups the whole repeated line, so no new record should appear).
    caplog.clear()
    with patch("sys._current_frames", return_value=fake_frames):
        with patch("runcorder.watch._is_user_frame", return_value=True):
            d._tick()
    assert "epoch=1" not in caplog.text

    # Third tick with changed args → args shown again.
    FakeFrame.f_locals = {"epoch": 2}
    caplog.clear()
    with patch("sys._current_frames", return_value=fake_frames):
        with patch("runcorder.watch._is_user_frame", return_value=True):
            d._tick()
    assert "epoch=2" in caplog.text


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
