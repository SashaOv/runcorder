"""Tests for _display — logging dispatch and WatchSink."""

import io
import logging

from runcorder import _display


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
