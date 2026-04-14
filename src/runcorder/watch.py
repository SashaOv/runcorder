"""WatchDisplay — daemon thread that emits a live status line to stderr."""

import atexit
import os
import sys
import threading
import time
from pathlib import Path
from collections.abc import Callable
from typing import Optional

from runcorder import _context
from runcorder._display import WatchSink
from runcorder._frames import (
    _EXCLUSION_PREFIXES,
    _RUNCORDER_PREFIX,
    _is_user_frame,
    _get_param_names,
    _read_param_reprs,
)


def _repr_diff(current: str, prev: str | None, cap: int = 24) -> str:
    """Return a compact display of *current* relative to *prev*.

    If *prev* is None (first sample), return *current* capped at *cap* chars.
    Otherwise return only the substring that differs, with ``...`` before/after
    where the common prefix/suffix was omitted, capped at *cap* chars.
    """
    if prev is None or current == prev:
        return current[:cap] if len(current) <= cap else current[:cap - 3] + "..."

    # Common prefix length
    prefix = 0
    for a, b in zip(current, prev):
        if a == b:
            prefix += 1
        else:
            break

    # Common suffix length (not overlapping the prefix)
    suffix = 0
    max_suffix = len(current) - prefix
    for a, b in zip(reversed(current), reversed(prev)):
        if suffix >= max_suffix:
            break
        if a == b:
            suffix += 1
        else:
            break

    diff = current[prefix: len(current) - suffix if suffix else len(current)]
    result = ("..." if prefix else "") + diff + ("..." if suffix else "")
    if len(result) > cap:
        result = result[:cap - 3] + "..."
    return result


def _format_args_with_diff(
    frame, prev_reprs: dict[str, str] | None
) -> tuple[str, dict[str, str]]:
    """Return (formatted_args_string, current_reprs).

    *formatted_args_string* uses diff-repr for each param vs *prev_reprs*.
    Only params whose repr changed (or are new) are included.
    *current_reprs* is the raw repr dict for storage as the next prev.
    """
    current = _read_param_reprs(frame)
    if not current:
        return "", {}

    parts: list[str] = []
    for name, r in current.items():
        prev_r = None if prev_reprs is None else prev_reprs.get(name)
        if prev_r is not None and r == prev_r:
            continue  # unchanged — omit
        parts.append(f"{name}={_repr_diff(r, prev_r)}")

    return ", ".join(parts), current


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
        on_stuck: Optional[Callable] = None,
    ) -> None:
        self._interval = max(0.5, watch_interval)
        self._watch_inplace = watch_inplace
        self._install_trackers = install_trackers
        self._stuck_timeout = stuck_timeout
        self._on_stuck = on_stuck

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._started_at: float = 0.0

        # Snapshots for report
        self._snapshots: list[str] = []
        self._snapshots_lock = threading.Lock()

        # Stuck detection
        self._stuck_fired: bool = False
        self._stuck_snapshot: Optional[list] = None  # list of frame objects
        self._last_qualname_set: Optional[frozenset] = None
        self._last_qualname_change: float = 0.0

        # Stable prefix trimming — sliding window of last 3 ticks' qualname lists
        self._tick_history: list[list[str]] = []

        # Parameter change detection — previous tick's arg strings per frame id
        self._prev_args: dict[int, dict[str, str]] = {}

        # Stream tracking
        self._orig_stdout = None
        self._orig_stderr = None
        self._tracker_stdout = None
        self._tracker_stderr = None

        # Output dispatch (created in start(), once streams are known)
        self._sink: Optional[WatchSink] = None

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

        self._sink = WatchSink(
            orig_stderr=self._orig_stderr,
            tracker=self._tracker_stderr,
            watch_inplace=self._watch_inplace,
        )

        self._thread = threading.Thread(
            target=self._run, daemon=True, name="runcorder-watch"
        )
        self._thread.start()
        atexit.register(self.stop)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

        if self._sink is not None:
            self._sink.clear_inplace()

        # Restore streams
        if self._orig_stderr is not None:
            sys.stderr = self._orig_stderr  # type: ignore[assignment]
            self._orig_stderr = None
        if self._orig_stdout is not None:
            sys.stdout = self._orig_stdout  # type: ignore[assignment]
            self._orig_stdout = None

        try:
            atexit.unregister(self.stop)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Properties for report collection

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
            if self._on_stuck is not None:
                try:
                    self._on_stuck()
                except Exception:
                    pass

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
        # Build status line with parameter display
        elapsed_s = int(now - self._started_at)
        stuck_marker = " stuck?" if self._stuck_fired else ""
        ctx = _context.get()
        ctx_str = " ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""

        # Build arg strings using diff-repr vs previous sample
        current_args: dict[int, dict[str, str]] = {}
        chain_parts: list[str] = []
        for i, df in enumerate(display_frames):
            qn = (
                df.f_code.co_qualname
                if hasattr(df.f_code, "co_qualname")
                else df.f_code.co_name
            )
            frame_key = id(df.f_code)
            prev_reprs = self._prev_args.get(frame_key)
            args_str, cur_reprs = _format_args_with_diff(df, prev_reprs)
            current_args[frame_key] = cur_reprs

            is_leaf = i == len(display_frames) - 1
            if args_str:
                part = f"{qn}({args_str}):{df.f_lineno}" if is_leaf else f"{qn}({args_str})"
            else:
                part = f"{qn}:{df.f_lineno}" if is_leaf else qn
            chain_parts.append(part)

        self._prev_args = current_args
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

        if self._sink is not None:
            self._sink.emit(line)
