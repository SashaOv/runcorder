"""Centralised output for runcorder.

All runcorder-originated messages go through the ``runcorder`` logger so they
route predictably under batch-job logging configurations.  The watch line is
special-cased: when ``watch_inplace=True`` and the session is writing to a
tty, it uses ANSI escape sequences for in-place updates; otherwise it falls
through to the logger with per-line dedup against the previous sample.

Per spec: runcorder does not change logging settings.  If the ``runcorder``
logger (or any ancestor) already has handlers, we respect that and do not
install our own.  When nothing is configured, we attach a minimal stderr
handler so default installs are usable out of the box.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from runcorder._tracker import _WriteTracker


logger = logging.getLogger("runcorder")


class _StderrHandler(logging.Handler):
    """StreamHandler variant that reads ``sys.stderr`` on every emit.

    The stdlib StreamHandler captures ``sys.stderr`` at construction time.
    That breaks when ``sys.stderr`` is later redirected (pytest's capsys,
    batch-job log collectors, etc.), so we resolve it dynamically.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


def _ensure_handler() -> None:
    """Install a default stderr handler if neither the runcorder logger nor
    any ancestor has been configured.  Called on every message so that a
    user who calls ``logging.basicConfig()`` *before* the first runcorder
    message wins — their root handler is used instead of ours, and records
    propagate without duplication."""
    if logger.hasHandlers():
        return
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    if logger.level == logging.NOTSET:
        logger.setLevel(logging.INFO)


def info(msg: str) -> None:
    _ensure_handler()
    logger.info(msg)


def warning(msg: str) -> None:
    _ensure_handler()
    logger.warning(msg)


def error(msg: str) -> None:
    _ensure_handler()
    logger.error(msg)


# ---------------------------------------------------------------------------
# WatchSink


class WatchSink:
    """Routes the watch line to either a tty (in-place escape updates) or the
    runcorder logger (with dedup against the previous line)."""

    def __init__(
        self,
        orig_stderr,
        tracker: Optional["_WriteTracker"],
        watch_inplace: bool,
    ) -> None:
        self._orig_stderr = orig_stderr
        self._tracker = tracker
        self._watch_inplace = watch_inplace
        self._wrote_last_inplace: bool = False
        self._last_logged_line: Optional[str] = None

    @property
    def tty_sink(self):
        return self._orig_stderr if self._orig_stderr is not None else sys.stderr

    def _is_tty(self) -> bool:
        sink = self.tty_sink
        try:
            return hasattr(sink, "isatty") and sink.isatty()
        except Exception:
            return False

    def emit(self, line: str) -> None:
        if self._watch_inplace and self._is_tty():
            self._emit_inplace(line)
        else:
            self._emit_logged(line)

    def _emit_inplace(self, line: str) -> None:
        sink = self.tty_sink
        if self._tracker is not None and self._tracker.foreign_wrote:
            self._tracker.reset_foreign()
            self._wrote_last_inplace = False
        try:
            if self._wrote_last_inplace:
                sink.write(f"\033[A\r\033[K{line}\n")
            else:
                sink.write(f"{line}\n")
            sink.flush()
        except Exception:
            pass
        self._wrote_last_inplace = True
        self._last_logged_line = None

    def _emit_logged(self, line: str) -> None:
        if line == self._last_logged_line:
            return
        _ensure_handler()
        logger.info(line)
        self._last_logged_line = line
        self._wrote_last_inplace = False

    def clear_inplace(self) -> None:
        """Erase the last in-place status line (tty path only)."""
        if not self._wrote_last_inplace:
            return
        sink = self.tty_sink
        try:
            if self._is_tty():
                sink.write("\r\033[K")
                sink.flush()
        except Exception:
            pass
        self._wrote_last_inplace = False
