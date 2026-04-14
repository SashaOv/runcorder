"""_WriteTracker: wraps a text stream to detect foreign writes and buffer a tail."""

from collections import deque
from typing import TextIO


class _WriteTracker:
    """Proxy wrapper around a text stream.

    Detects when something writes through ``sys.stdout`` / ``sys.stderr``
    (foreign write detection for ``watch_inplace`` mode) and keeps a rolling
    buffer of the last *tail_size* lines for report capture.

    The watch display bypasses this wrapper by writing directly to the
    original stream it stored before installing the tracker.
    """

    def __init__(self, wrapped: TextIO, tail_size: int = 500) -> None:
        self._wrapped = wrapped
        self._tail: deque[str] = deque(maxlen=tail_size)
        self._line_buf: str = ""
        self._foreign_wrote: bool = False

    # ------------------------------------------------------------------
    # Foreign-write flag

    @property
    def foreign_wrote(self) -> bool:
        return self._foreign_wrote

    def reset_foreign(self) -> None:
        self._foreign_wrote = False

    # ------------------------------------------------------------------
    # Tail buffer

    def tail_lines(self) -> list[str]:
        """Return a copy of the buffered lines (oldest first)."""
        return list(self._tail)

    # ------------------------------------------------------------------
    # Stream interface

    def write(self, s: str) -> int:
        self._foreign_wrote = True
        result = self._wrapped.write(s)
        # Split into lines and buffer them
        combined = self._line_buf + s
        if "\n" in combined:
            parts = combined.split("\n")
            # All but the last part are complete lines
            for line in parts[:-1]:
                self._tail.append(line)
            self._line_buf = parts[-1]
        else:
            self._line_buf = combined
        return result

    def flush(self) -> None:
        self._wrapped.flush()

    def __getattr__(self, name: str):
        return getattr(self._wrapped, name)

    def __enter__(self):
        return self._wrapped.__enter__()

    def __exit__(self, *args):
        return self._wrapped.__exit__(*args)
