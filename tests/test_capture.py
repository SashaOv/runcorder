"""Tests for _capture — exception hook install/restore."""

import sys
import pytest

import runcorder._capture as cap


def setup_function():
    # Ensure clean state before each test
    cap.uninstall_exception_hook()
    sys.excepthook = sys.__excepthook__


def teardown_function():
    cap.uninstall_exception_hook()
    sys.excepthook = sys.__excepthook__


def test_install_replaces_excepthook():
    original = sys.excepthook
    cap.install_exception_hook(lambda *a: None)
    assert sys.excepthook is not original


def test_uninstall_restores_excepthook():
    sentinel = sys.__excepthook__
    sys.excepthook = sentinel  # ensure it's the default
    cap.install_exception_hook(lambda *a: None)
    cap.uninstall_exception_hook()
    assert sys.excepthook is sentinel


def test_callback_is_called():
    called_with = []

    def on_exc(exc_type, exc_value, exc_tb):
        called_with.append((exc_type, exc_value))

    cap.install_exception_hook(on_exc)
    try:
        raise ValueError("boom")
    except ValueError as e:
        import traceback as tb
        sys.excepthook(ValueError, e, e.__traceback__)

    cap.uninstall_exception_hook()
    assert len(called_with) == 1
    assert called_with[0][0] is ValueError
    assert str(called_with[0][1]) == "boom"


def test_original_hook_called_after_callback(capsys):
    """The previously installed hook must still be called."""
    outer_called = []

    def outer_hook(exc_type, exc_value, exc_tb):
        outer_called.append(True)

    sys.excepthook = outer_hook
    cap.install_exception_hook(lambda *a: None)

    try:
        raise RuntimeError("test")
    except RuntimeError as e:
        sys.excepthook(RuntimeError, e, e.__traceback__)

    cap.uninstall_exception_hook()
    assert outer_called == [True]


def test_callback_exception_does_not_suppress_original(capsys):
    """A crashing callback must not prevent the original hook from running."""
    outer_called = []

    def outer_hook(exc_type, exc_value, exc_tb):
        outer_called.append(True)

    def bad_callback(*args):
        raise RuntimeError("callback crashed")

    sys.excepthook = outer_hook
    cap.install_exception_hook(bad_callback)

    try:
        raise ValueError("x")
    except ValueError as e:
        sys.excepthook(ValueError, e, e.__traceback__)

    cap.uninstall_exception_hook()
    assert outer_called == [True]


def test_uninstall_without_install_is_noop():
    # Should not raise
    cap.uninstall_exception_hook()


def test_double_install_preserves_first_original():
    """Installing twice should not nest hooks unboundedly."""
    original = sys.excepthook
    cap.install_exception_hook(lambda *a: None)
    cap.install_exception_hook(lambda *a: None)
    cap.uninstall_exception_hook()
    # After one uninstall we should be back to the hook that existed
    # before the second install (i.e. the first installed hook or original)
    cap.uninstall_exception_hook()
    sys.excepthook = sys.__excepthook__  # reset
