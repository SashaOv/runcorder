"""WatchDisplay — daemon thread that emits a live status line to stderr."""

import os
import sys
import sysconfig
import threading
import time
from pathlib import Path
from typing import Optional

from runcorder import _context

# ---------------------------------------------------------------------------
# Exclusion index (pure-Python fallback; no native extension)

def _build_exclusion_prefixes() -> tuple[str, ...]:
    prefixes: set[str] = set()
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = paths.get(key)
        if p:
            try:
                prefixes.add(str(Path(p).resolve()))
            except (OSError, ValueError):
                pass
    for attr in ("prefix", "exec_prefix", "base_prefix"):
        p = getattr(sys, attr, None)
        if p:
            lib_dir = Path(p) / "lib"
            try:
                prefixes.add(str(lib_dir.resolve()))
            except (OSError, ValueError):
                pass
    return tuple(prefixes)


_EXCLUSION_PREFIXES: tuple[str, ...] = _build_exclusion_prefixes()
_RUNCORDER_PREFIX: str = str(Path(__file__).parent.resolve())


def _is_user_frame(frame) -> bool:
    """Return True if *frame* is from user code (not stdlib/site-packages/runcorder)."""
    filename = frame.f_code.co_filename
    if not filename or filename.startswith("<"):
        return False
    try:
        p = str(Path(filename).resolve())
    except (OSError, ValueError):
        return False
    if p.startswith(_RUNCORDER_PREFIX):
        return False
    for prefix in _EXCLUSION_PREFIXES:
        if p.startswith(prefix):
            return False
    return True


# ---------------------------------------------------------------------------
# WatchDisplay

class WatchDisplay:
    """Polls the main thread stack every *watch_interval* seconds and writes
    a compact status line to stderr.

    Parameters
    ----------
    watch_interval:
        Seconds between stack samples (minimum 0.5).
    watch_inplace:
        Rewrite the previous status line when no foreign output has appeared
        *and* the stderr sink supports in-place updates.
    install_trackers:
        Install ``_WriteTracker`` on both stdout **and** stderr (used when
        ``tail=True`` so the session can capture output).  When
        ``watch_inplace=True`` the stderr tracker is always installed;
        this flag additionally installs one on stdout.
    stuck_timeout:
        Seconds of unchanged qualnames before the stuck notice fires.
        Set to 0 to disable.
    """

    def __init__(
        self,
        watch_interval: float = 3.0,
        watch_inplace: bool = True,
        install_trackers: bool = False,
        stuck_timeout: float = 30.0,
    ) -> None:
        self._interval = max(0.5, watch_interval)
        self._watch_inplace = watch_inplace
        self._install_trackers = install_trackers
        self._stuck_timeout = stuck_timeout

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started_at: float = 0.0

        # Snapshots for artifact
        self._snapshots: list[str] = []
        self._snapshots_lock = threading.Lock()

        # Stuck detection
        self._stuck_fired: bool = False
        self._stuck_snapshot: Optional[list] = None  # list of frame objects
        self._last_qualname_set: Optional[frozenset] = None
        self._last_qualname_change: float = 0.0

        # Stable prefix trimming — sliding window of last 3 ticks' qualname lists
        self._tick_history: list[list[str]] = []

        # Stream tracking
        self._orig_stdout = None
        self._orig_stderr = None
        self._tracker_stdout = None
        self._tracker_stderr = None

    # ------------------------------------------------------------------
    # Lifecycle

    def start(self) -> None:
        from runcorder._tracker import _WriteTracker

        self._started_at = time.monotonic()
        self._last_qualname_change = self._started_at

        need_stderr_tracker = self._watch_inplace or self._install_trackers
        if need_stderr_tracker:
            self._orig_stderr = sys.stderr
            self._tracker_stderr = _WriteTracker(sys.stderr)
            sys.stderr = self._tracker_stderr  # type: ignore[assignment]

        if self._install_trackers:
            self._orig_stdout = sys.stdout
            self._tracker_stdout = _WriteTracker(sys.stdout)
            sys.stdout = self._tracker_stdout  # type: ignore[assignment]

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="runcorder-watch"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

        # Restore streams
        if self._orig_stderr is not None:
            sys.stderr = self._orig_stderr  # type: ignore[assignment]
            self._orig_stderr = None
        if self._orig_stdout is not None:
            sys.stdout = self._orig_stdout  # type: ignore[assignment]
            self._orig_stdout = None

    # ------------------------------------------------------------------
    # Properties for artifact collection

    @property
    def stuck_fired(self) -> bool:
        return self._stuck_fired

    @property
    def stuck_snapshot(self) -> Optional[list]:
        return self._stuck_snapshot

    @property
    def snapshots(self) -> list[str]:
        with self._snapshots_lock:
            return list(self._snapshots)

    def tail_stdout(self) -> list[str]:
        if self._tracker_stdout is not None:
            return self._tracker_stdout.tail_lines()
        return []

    def tail_stderr(self) -> list[str]:
        if self._tracker_stderr is not None:
            return self._tracker_stderr.tail_lines()
        return []

    # ------------------------------------------------------------------
    # Watch thread

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval):
            try:
                self._tick()
            except Exception:
                pass  # never crash the daemon thread

    def _tick(self) -> None:
        main_tid = threading.main_thread().ident
        all_frames = sys._current_frames()
        frame = all_frames.get(main_tid)
        if frame is None:
            return

        # Collect frames outer→inner
        stack = []
        f = frame
        while f is not None:
            stack.append(f)
            f = f.f_back
        stack.reverse()

        # Filter to user frames, drop bare <module> frames
        visible = [
            f for f in stack
            if _is_user_frame(f) and f.f_code.co_name != "<module>"
        ]

        if not visible:
            return

        qualnames = [
            f.f_code.co_qualname
            if hasattr(f.f_code, "co_qualname")
            else f.f_code.co_name
            for f in visible
        ]

        # ------------------------------------------------------------------
        # Stuck detection
        now = time.monotonic()
        current_qset = frozenset(qualnames)
        if self._last_qualname_set != current_qset:
            self._last_qualname_set = current_qset
            self._last_qualname_change = now
        elif (
            not self._stuck_fired
            and self._stuck_timeout > 0
            and (now - self._last_qualname_change) >= self._stuck_timeout
        ):
            self._stuck_fired = True
            self._stuck_snapshot = visible[:]

        # ------------------------------------------------------------------
        # Stable prefix trimming — window of last 3 ticks
        self._tick_history.append(qualnames)
        if len(self._tick_history) > 3:
            self._tick_history = self._tick_history[-3:]

        stable_count = 0
        if len(self._tick_history) >= 2:
            min_len = min(len(h) for h in self._tick_history)
            for i in range(min_len):
                if all(h[i] == self._tick_history[0][i] for h in self._tick_history):
                    stable_count = i + 1
                else:
                    break

        display_frames = visible[stable_count:]
        if not display_frames:
            display_frames = visible[-1:]

        # ------------------------------------------------------------------
        # Build status line
        elapsed_s = int(now - self._started_at)
        stuck_marker = " stuck?" if self._stuck_fired else ""
        ctx = _context.get()
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""

        chain_parts: list[str] = []
        for i, df in enumerate(display_frames):
            qn = (
                df.f_code.co_qualname
                if hasattr(df.f_code, "co_qualname")
                else df.f_code.co_name
            )
            part = f"{qn}:{df.f_lineno}" if i == len(display_frames) - 1 else qn
            chain_parts.append(part)

        chain = " > ".join(chain_parts)

        if ctx_str:
            line = f"[{elapsed_s}s{stuck_marker}] {ctx_str} | {chain}"
        else:
            line = f"[{elapsed_s}s{stuck_marker}] {chain}"

        # Truncate to terminal width
        try:
            width = os.get_terminal_size(
                (self._orig_stderr or sys.stderr).fileno()
            ).columns
        except (OSError, AttributeError):
            width = 0

        if width > 0 and len(line) > width:
            first = chain_parts[0] if chain_parts else ""
            last = chain_parts[-1] if chain_parts else ""
            collapsed = f"{first} > ... > {last}"
            if ctx_str:
                line = f"[{elapsed_s}s{stuck_marker}] {ctx_str} | {collapsed}"
            else:
                line = f"[{elapsed_s}s{stuck_marker}] {collapsed}"
            if len(line) > width:
                line = line[:width]

        # Save snapshot
        with self._snapshots_lock:
            self._snapshots.append(line)

        # Write to the real (original) stderr, bypassing the tracker
        sink = self._orig_stderr if self._orig_stderr is not None else sys.stderr

        try:
            is_tty = hasattr(sink, "isatty") and sink.isatty()
        except Exception:
            is_tty = False

        tracker = self._tracker_stderr
        if self._watch_inplace and is_tty:
            if tracker is not None and tracker.foreign_wrote:
                tracker.reset_foreign()
                sink.write(f"{line}\n")
            else:
                # Overwrite the line: CR to column 0, write, then spaces to clear
                sink.write(f"\r{line}\r")
        else:
            sink.write(f"{line}\n")
        sink.flush()
