"""Tests for _session — InstrumentContext, session(), instrument decorator."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import runcorder._context as ctx
from runcorder._session import InstrumentContext, instrument, session


def _no_check_log_size():
    pass


# ---------------------------------------------------------------------------
# InstrumentContext basics

def test_context_manager_starts_and_stops():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with InstrumentContext(watch_interval=0.5, stuck_timeout=0.0) as ic:
            assert ic._started_at is not None
        assert ic._stopped


def test_context_manager_no_artifact_on_success(tmp_path):
    output = tmp_path / "artifact.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0):
            pass
    assert not output.exists()


def test_artifact_written_on_exception(tmp_path):
    output = tmp_path / "artifact.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with pytest.raises(ValueError):
            with InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0):
                raise ValueError("test error")
    assert output.exists()
    content = output.read_text()
    assert "ValueError" in content
    assert "test error" in content


def test_artifact_contains_front_matter(tmp_path):
    output = tmp_path / "artifact.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with pytest.raises(RuntimeError):
            with InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0):
                raise RuntimeError("boom")
    content = output.read_text()
    assert "command:" in content
    assert "exit_status:" in content
    assert "duration_s:" in content


def test_stop_is_idempotent(tmp_path):
    output = tmp_path / "artifact.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        ic = InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0)
        ic.start()
        ic.stop()
        ic.stop()  # should not raise or re-write


# ---------------------------------------------------------------------------
# session() factory

def test_session_returns_instrument_context():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        s = session(watch_interval=0.5, stuck_timeout=0.0)
        assert isinstance(s, InstrumentContext)
        # session() should not auto-start; __enter__ starts it
        assert s._started_at is None
        s.stop()


def test_session_as_context_manager(tmp_path):
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with session(watch_interval=0.5, stuck_timeout=0.0):
            pass


def test_start_is_idempotent():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        ic = InstrumentContext(watch_interval=0.5, stuck_timeout=0.0)
        ic.start()
        first_started = ic._started_at
        ic.start()  # second call should be a no-op
        assert ic._started_at is first_started
        ic.stop()


def test_artifact_exception_uses_filtered_traceback(tmp_path):
    """Exception traceback in artifact should use the spec's filtered stack view."""
    output = tmp_path / "artifact.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with pytest.raises(ValueError):
            with InstrumentContext(output=output, watch_interval=0.5, stuck_timeout=0.0):
                raise ValueError("filtered test")
    content = output.read_text()
    # Should contain the exception line
    assert "ValueError: filtered test" in content
    # Should contain a File reference from the filtered stack
    assert 'File "' in content


# ---------------------------------------------------------------------------
# instrument decorator — bare form

def test_instrument_bare_no_exception(tmp_path):
    output = tmp_path / "run.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument
        def func():
            pass
        func()
    assert not output.exists()


def test_instrument_bare_writes_artifact_on_exception(tmp_path):
    output = tmp_path / "run.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument(output=output)
        def func():
            raise TypeError("decorator test")

        with pytest.raises(TypeError):
            func()
    assert output.exists()
    assert "TypeError" in output.read_text()


def test_instrument_bare_reraises():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument
        def func():
            raise ValueError("should propagate")

        with pytest.raises(ValueError, match="should propagate"):
            func()


def test_instrument_bare_returns_value():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument
        def func():
            return 42

        assert func() == 42


# ---------------------------------------------------------------------------
# instrument decorator — kwargs form

def test_instrument_kwargs_form(tmp_path):
    output = tmp_path / "run.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument(output=output)
        def func():
            raise KeyError("kwargs form")

        with pytest.raises(KeyError):
            func()
    assert output.exists()


def test_instrument_kwargs_no_artifact_on_success(tmp_path):
    output = tmp_path / "run.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument(output=output, watch_interval=0.5, stuck_timeout=0.0)
        def func():
            return "ok"

        result = func()
    assert result == "ok"
    assert not output.exists()


def test_instrument_preserves_function_name():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        @instrument
        def my_special_function():
            pass
        assert my_special_function.__name__ == "my_special_function"


# ---------------------------------------------------------------------------
# Context variables are accessible during session

def test_context_variables_during_session():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        captured = {}
        with InstrumentContext(watch_interval=0.5, stuck_timeout=0.0):
            ctx.context(step=99)
            captured["ctx"] = ctx.get()
    assert captured["ctx"] == {"step": 99}


def test_context_cleared_after_session():
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with InstrumentContext(watch_interval=0.5, stuck_timeout=0.0):
            ctx.context(x=1)
    assert ctx.get() == {}


# ---------------------------------------------------------------------------
# Tail option

def test_tail_output_in_artifact(tmp_path):
    output = tmp_path / "run.md"
    with patch("runcorder._session._location.check_log_size", _no_check_log_size):
        with pytest.raises(RuntimeError):
            with InstrumentContext(
                output=output, tail=True, watch_interval=0.5, stuck_timeout=0.0
            ):
                sys.stdout.write("captured output\n")
                sys.stdout.flush()
                raise RuntimeError("force artifact")
    content = output.read_text()
    assert "## Output Tail" in content
