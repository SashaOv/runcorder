"""InstrumentContext, session(), and the instrument decorator."""

from __future__ import annotations

import functools
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from runcorder import _context, _display, _location
from runcorder._report import (
    ReportMeta,
    ReportWriter,
    classify_frames,
    filter_stack,
    format_stack,
)
from runcorder._capture import install_exception_hook, uninstall_exception_hook
from runcorder.watch import WatchDisplay


class InstrumentContext:
    """Manages a single runcorder recording session.

    Parameters
    ----------
    output:
        Explicit report path.  When *None* the auto-named default is used.
    tail:
        Buffer stdout/stderr and include a rolling tail in the report.
    watch_interval:
        Seconds between stack samples (min 0.5).
    watch_inplace:
        Rewrite the status line in place when no foreign output appeared.
    stuck_timeout:
        Seconds of unchanged stack before the stuck notice fires (0 = off).
    """

    def __init__(
        self,
        output: Optional[str | Path] = None,
        tail: bool = False,
        watch_interval: float = 3.0,
        watch_inplace: bool = True,
        stuck_timeout: float = 30.0,
        short_traceback: bool = True,
    ) -> None:
        self._output = Path(output) if output is not None else None
        self._tail = tail
        self._watch_interval = watch_interval
        self._watch_inplace = watch_inplace
        self._stuck_timeout = stuck_timeout
        self._short_traceback = short_traceback

        self._started_at: Optional[datetime] = None
        self._watch: Optional[WatchDisplay] = None
        self._exception_info: Optional[tuple] = None
        self._meta: Optional[ReportMeta] = None
        self._writer: Optional[ReportWriter] = None
        self._stopped: bool = False

    # ------------------------------------------------------------------
    # Session lifecycle

    def start(self) -> None:
        if self._started_at is not None:
            return
        _location.check_log_size()
        _context._install()
        self._started_at = datetime.now(tz=timezone.utc)

        self._meta = ReportMeta(
            command=sys.argv[:],
            cwd=str(Path.cwd()),
            python=sys.version,
            started_at=self._started_at.isoformat(),
        )

        self._watch = WatchDisplay(
            watch_interval=self._watch_interval,
            watch_inplace=self._watch_inplace,
            install_trackers=self._tail,
            stuck_timeout=self._stuck_timeout,
            on_stuck=self._on_stuck_fired,
        )
        self._watch.start()

        install_exception_hook(self._on_exception)

    def stop(self, exception_info: Optional[tuple] = None) -> None:
        if self._stopped:
            return
        self._stopped = True

        ended_at = datetime.now(tz=timezone.utc)

        if self._watch is not None:
            self._watch.stop()

        uninstall_exception_hook()

        exc = exception_info or self._exception_info
        if exc is not None:
            self._get_or_create_writer().write_exception(self._build_exc_dict(exc))

        if self._writer is not None:
            started = self._started_at or ended_at
            duration = (ended_at - started).total_seconds()
            exit_status: int | str = "exception" if exc is not None else 0

            tail_text: Optional[str] = None
            if self._tail and self._watch is not None:
                all_lines = self._watch.tail_stdout() + self._watch.tail_stderr()
                tail_text = "\n".join(all_lines) if all_lines else None

            watch_snapshots = self._watch.snapshots if self._watch is not None else []

            self._writer.finalize(
                ended_at=ended_at.isoformat(),
                duration_s=duration,
                exit_status=exit_status,
                watch_snapshots=watch_snapshots,
                output_tail=tail_text,
            )

        if (
            self._short_traceback
            and exc is not None
            and self._writer is not None
        ):
            _install_short_traceback_hook(
                report_path=self._writer.path,
                exc_type=exc[0],
                exc_value=exc[1],
            )

        _context._uninstall()

    def _get_or_create_writer(self) -> ReportWriter:
        if self._writer is None:
            path = Path(self._output or _location.auto_name())
            assert self._meta is not None  # set in start()
            self._writer = ReportWriter(path, self._meta)
            _display.info(f"[runcorder] report is written to {path}")
        return self._writer

    def _on_exception(self, exc_type, exc_value, exc_tb) -> None:
        self._exception_info = (exc_type, exc_value, exc_tb)

    def _on_stuck_fired(self) -> None:
        """Called from the watch thread when stuck is detected."""
        if self._watch is None or self._watch.stuck_snapshot is None:
            return
        self._get_or_create_writer().write_stuck(self._watch.stuck_snapshot)

    def _build_exc_dict(self, exc: tuple) -> dict:
        import traceback as tb_mod
        exc_type, exc_value, exc_tb = exc
        raw_frames = []
        tb = exc_tb
        while tb is not None:
            raw_frames.append(tb.tb_frame)
            tb = tb.tb_next
        if raw_frames:
            classified = classify_frames(raw_frames)
            filtered = filter_stack(classified)
            filtered_str = format_stack(filtered)
        else:
            filtered_str = "".join(tb_mod.format_exception(exc_type, exc_value, exc_tb))
        exc_line = f"{exc_type.__name__ if exc_type is not None else 'UnknownError'}: {exc_value}"
        return {
            "type": exc_type.__name__ if exc_type is not None else "UnknownError",
            "message": str(exc_value),
            "traceback": f"{filtered_str}\n{exc_line}",
        }

    # ------------------------------------------------------------------
    # Context manager protocol

    def __enter__(self) -> "InstrumentContext":
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_tb) -> bool:
        if exc_type is not None:
            self.stop(exception_info=(exc_type, exc_value, exc_tb))
        else:
            self.stop()
        return False  # never suppress exceptions


# ---------------------------------------------------------------------------
# Short-traceback excepthook

def _install_short_traceback_hook(
    report_path: Path,
    exc_type: type,
    exc_value: BaseException,
) -> None:
    """Replace sys.excepthook with a one-shot hook that prints a concise
    ``ExceptionType: message`` line and a pointer to the report.

    The hook restores the previous excepthook on first call regardless of
    whether the exception matches, so it never lingers after one use.  If the
    exception does not match (a different exception reached the interpreter
    top level first), the hook delegates to the original.
    """
    original = sys.excepthook
    target_id = id(exc_value)

    def _hook(et: type, ev: BaseException, tb) -> None:
        sys.excepthook = original  # restore immediately (one-shot)
        if id(ev) == target_id and et is exc_type:
            sys.stderr.write(f"{et.__name__}: {ev}\n")
            sys.stderr.write(f"[runcorder] see report at {report_path}\n")
            sys.stderr.flush()
        else:
            original(et, ev, tb)

    sys.excepthook = _hook


# ---------------------------------------------------------------------------
# Public factory

def session(**kwargs) -> InstrumentContext:
    """Create and return an :class:`InstrumentContext`.

    Intended for use as a context manager::

        with runcorder.session(tail=True):
            run_pipeline()
    """
    return InstrumentContext(**kwargs)


# ---------------------------------------------------------------------------
# instrument decorator (bare + kwargs forms)

def instrument(func: Optional[Callable] = None, **kwargs):
    """Decorator that wraps a function in a runcorder session.

    Supports both bare and kwargs forms::

        @instrument
        def main(): ...

        @instrument(output="run.md", tail=True)
        def main(): ...
    """
    if func is not None:
        return _wrap(func, {})
    else:
        def decorator(f: Callable) -> Callable:
            return _wrap(f, kwargs)
        return decorator


def _wrap(func: Callable, kwargs: dict) -> Callable:
    @functools.wraps(func)
    def wrapper(*args, **kw):
        ctx = InstrumentContext(**kwargs)
        ctx.start()
        exc_info = None
        try:
            return func(*args, **kw)
        except BaseException:
            exc_info = sys.exc_info()
            raise
        finally:
            ctx.stop(exception_info=exc_info)

    return wrapper
