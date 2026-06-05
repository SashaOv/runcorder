"""Tests for _display — logging dispatch and WatchSink."""

import io
import logging

import pytest

from runcorder import _display
from runcorder._display import WatchSink


def _clear_runcorder_handlers():
    for h in _display.logger.handlers[:]:
        _display.logger.removeHandler(h)


def test_no_duplication_with_root_handler_preconfigured():
    """When the user configures the root logger before calling runcorder
    (the --job / batch mode pattern), each message must appear exactly once.

    Previously, _ensure_handler used ``logger.handlers`` (direct only), so
    it installed its own handler even when root already had one.  The record
    then fired from our handler AND propagated to root → two writes."""
    _clear_runcorder_handlers()

    output = io.StringIO()
    root_logger = logging.getLogger()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s"))
    original_level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)
    try:
        _display.info("unique-sentinel-message")
        assert output.getvalue().count("unique-sentinel-message") == 1
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)
        _clear_runcorder_handlers()


def test_default_installs_stderr_handler_when_unconfigured(capsys):
    """When no logging is configured, _ensure_handler installs a stderr
    handler so runcorder messages are visible out of the box."""
    _clear_runcorder_handlers()
    # Remove any pytest root handlers temporarily so hasHandlers() returns False.
    root_logger = logging.getLogger()
    saved = root_logger.handlers[:]
    root_logger.handlers.clear()
    try:
        _display.info("visible-by-default")
        err = capsys.readouterr().err
        assert "visible-by-default" in err
    finally:
        root_logger.handlers[:] = saved
        _clear_runcorder_handlers()


# ---------------------------------------------------------------------------
# WatchSink — logging dedup

def test_watchsink_logged_line_dedup(caplog):
    """The same status line emitted twice via the logging path must produce
    only one log record (spec: 'only displays watchline if it has changed')."""
    caplog.set_level(logging.INFO, logger="runcorder")
    sink = WatchSink(orig_stderr=None, tracker=None, watch_inplace=False)

    sink.emit("[1s] train:10")
    sink.emit("[1s] train:10")  # identical — should be suppressed

    records = [r for r in caplog.records if r.name == "runcorder" and "train:10" in r.getMessage()]
    assert len(records) == 1


def test_watchsink_logged_different_lines_both_emitted(caplog):
    """Different consecutive status lines must both appear as log records."""
    caplog.set_level(logging.INFO, logger="runcorder")
    sink = WatchSink(orig_stderr=None, tracker=None, watch_inplace=False)

    sink.emit("[1s] train:10")
    sink.emit("[4s] train:11")

    msgs = [r.getMessage() for r in caplog.records if r.name == "runcorder"]
    assert any("train:10" in m for m in msgs)
    assert any("train:11" in m for m in msgs)


# ---------------------------------------------------------------------------
# display_result

def test_display_result_scalar(capsys):
    _display.display_result(42)
    assert "42" in capsys.readouterr().out


def test_display_result_dict_yaml_like(capsys):
    _display.display_result({"added_songs": 0, "added_sections": 3})
    out = capsys.readouterr().out
    assert "added_songs: 0" in out
    assert "added_sections: 3" in out
    # YAML-like: no JSON double-quotes around keys
    assert '"added_songs"' not in out


def test_display_result_list(capsys):
    _display.display_result([1, 2, 3])
    out = capsys.readouterr().out
    assert "- 1" in out
    assert "- 2" in out


def test_display_result_empty_dict(capsys):
    _display.display_result({})
    assert "{}" in capsys.readouterr().out


def test_display_result_nested(capsys):
    _display.display_result({"stats": {"added": 5, "removed": 1}})
    out = capsys.readouterr().out
    assert "stats:" in out
    assert "added: 5" in out


def test_display_result_non_serializable(capsys):
    class Custom:
        def __str__(self):
            return "Custom()"
    _display.display_result(Custom())
    assert "Custom()" in capsys.readouterr().out
