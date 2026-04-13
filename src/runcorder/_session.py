"""InstrumentContext, session(), and the instrument decorator."""

from __future__ import annotations

import functools
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from runcorder import _context, _location
from runcorder._artifact import ArtifactData, classify_frames, filter_stack, format_stack, write as write_artifact
from runcorder._capture import install_exception_hook, uninstall_exception_hook
from runcorder.watch import WatchDisplay


class InstrumentContext:
    """Manages a single runcorder recording session.

    Parameters
    ----------
    output:
        Explicit artifact path.  When *None* the auto-named default is used.
    tail:
        Buffer stdout/stderr and include a rolling tail in the artifact.
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
    ) -> None:
        self._output = Path(output) if output is not None else None
        self._tail = tail
        self._watch_interval = watch_interval
        self._watch_inplace = watch_inplace
        self._stuck_timeout = stuck_timeout

        self._started_at: Optional[datetime] = None
        self._watch: Optional[WatchDisplay] = None
        self._exception_info: Optional[tuple] = None
        self._stopped: bool = False

    # ------------------------------------------------------------------
    # Session lifecycle

    def start(self) -> None:
        _location.check_log_size()
        _context._install()
        self._started_at = datetime.now(tz=timezone.utc)

        self._watch = WatchDisplay(
            watch_interval=self._watch_interval,
            watch_inplace=self._watch_inplace,
            install_trackers=self._tail,
            stuck_timeout=self._stuck_timeout,
        )
        self._watch.start()

        install_exception_hook(self._on_exception)

    def stop(self, exception_info: Optional[tuple] = None) -> None:
        if self._stopped:
            return
        self._stopped = True

        ended_at = datetime.now(tz=timezone.utc)

        # 1. Stop watch display
        if self._watch is not None:
            self._watch.stop()

        # 2. Restore excepthook
        uninstall_exception_hook()

        # 3. Collect artifact data
        exc = exception_info or self._exception_info
        stuck = self._watch is not None and self._watch.stuck_fired

        if exc is not None or stuck:
            self._write_artifact(exc, stuck, ended_at)

        # 4. Uninstall context store
        _context._uninstall()

    def _write_artifact(
        self,
        exc: Optional[tuple],
        stuck: bool,
        ended_at: datetime,
    ) -> None:
        import traceback as tb_mod

        output = self._output or _location.auto_name()
        started = self._started_at or ended_at
        duration = (ended_at - started).total_seconds()

        # Determine output tail
        tail_text: Optional[str] = None
        if self._tail and self._watch is not None:
            stdout_lines = self._watch.tail_stdout()
            stderr_lines = self._watch.tail_stderr()
            all_lines = stdout_lines + stderr_lines
            tail_text = "\n".join(all_lines) if all_lines else None

        # Build exception dict
        exc_dict: Optional[dict] = None
        if exc is not None:
            exc_type, exc_value, exc_tb = exc
            tb_str = "".join(tb_mod.format_exception(exc_type, exc_value, exc_tb))
            exc_dict = {
                "type": exc_type.__name__ if exc_type is not None else "UnknownError",
                "message": str(exc_value),
                "traceback": tb_str,
            }

        # Build stuck snapshot text
        stuck_text: Optional[str] = None
        if stuck and self._watch is not None and self._watch.stuck_snapshot is not None:
            classified = classify_frames(self._watch.stuck_snapshot)
            filtered = filter_stack(classified)
            stuck_text = format_stack(filtered)

        data = ArtifactData(
            command=sys.argv[:],
            cwd=str(Path.cwd()),
            python=sys.version,
            started_at=started.isoformat(),
            ended_at=ended_at.isoformat(),
            duration_s=duration,
            exit_status="exception" if exc is not None else 0,
            exception=exc_dict,
            stuck_snapshot=stuck_text,
            watch_snapshots=self._watch.snapshots if self._watch else [],
            output_tail=tail_text,
        )

        Path(output).parent.mkdir(parents=True, exist_ok=True)
        write_artifact(data, Path(output))

    def _on_exception(self, exc_type, exc_value, exc_tb) -> None:
        self._exception_info = (exc_type, exc_value, exc_tb)

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
# Public factory

def session(**kwargs) -> InstrumentContext:
    """Create, start, and return an :class:`InstrumentContext`.

    Intended for use as a context manager::

        with runcorder.session(tail=True):
            run_pipeline()
    """
    ctx = InstrumentContext(**kwargs)
    ctx.start()
    return ctx


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
        # Bare form: @instrument  (func is the decorated callable)
        return _wrap(func, {})
    else:
        # Kwargs form: @instrument(**kwargs) → returns decorator
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
