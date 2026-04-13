# Kick-off: Runcorder Implementation

## Decisions made before implementation

- `_WriteTracker` is installed when watch is active **or** `tail=True` — the two are independent.
- `@instrument` supports both bare `@instrument` and `@instrument(**kwargs)` forms (same pattern as pytest fixtures).
- `runcorder.context()` outside an active session: no-op, warns once per process.
- No boundary capture in v1. Automatic capture points are: `__main__` entry, uncaught exception, periodic stack sample. This keeps overhead near zero and removes `sys.monitoring` complexity entirely.

---

## PyWolf source to cannibalize

| PyWolf file | Cannibalized into | Notes |
|---|---|---|
| `python/pywolf/watch.py` | `src/runcorder/watch.py` | Rename throughout; drop `_native` import; use pure-Python exclusion index; add context variable rendering and stuck detection |
| `python/pywolf/_api.py` | `src/runcorder/_session.py` | Replace trace pipeline with exception hook; add `instrument` bare/kwargs dual form |
| `python/pywolf/_config.py` | `src/runcorder/_location.py` | Replace output path logic with cache dir scheme and size_check logic |

---

## Source files to create

```
src/runcorder/
  __init__.py         # public API: instrument, session, context
  _context.py         # session-level key/value store
  _tracker.py         # _WriteTracker: foreign-write detection + tail buffer
  watch.py            # WatchDisplay (cannibalized from PyWolf)
  _capture.py         # sys.monitoring boundary detection + sys.excepthook
  _artifact.py        # Markdown + YAML front matter writer, stack rendering
  _location.py        # artifact path, log space management
  _session.py         # InstrumentContext, session(), instrument decorator
  cli.py              # cyclopts app: `runcorder clean`
  __main__.py         # `python -m runcorder my_script.py`
```

---

## Phases

### Phase 1 — Foundation (no dependencies between these files)

**`src/runcorder/_context.py`**
- Module-level `_active_store: dict | None = None`
- `context(**kwargs)` — if no active store, warn once and return; else update store (delete keys set to `None`)
- `_install() -> dict` / `_uninstall()` — called by session start/stop
- `get() -> dict` — returns copy of current store (empty dict if no session)

**`src/runcorder/_tracker.py`**
- `_WriteTracker` extracted from PyWolf's `watch.py`
- Extend to also buffer the last N lines (N=500) for tail capture
- `foreign_wrote: bool` flag (reset after each read)
- `tail_lines() -> list[str]` — returns buffered lines

**`src/runcorder/_location.py`**
- `default_log_dir() -> Path` — `~/.cache/runcorder/logs/` on macOS/Linux; `%LOCALAPPDATA%/runcorder/logs` on Windows
- `auto_name() -> Path` — `YYMMDD-HHMMSS.md` in log dir
- `check_log_size()` — reads `size_check` in log dir; recalculates if absent or >1 day old; prints warning to stderr if >100 MB; writes result back

---

### Phase 2 — Watch Display

**`src/runcorder/watch.py`** — cannibalize PyWolf's `watch.py` with these changes:

- Remove `from pywolf import _native`; replace `_is_user_frame` with pure-Python version (PyWolf's `_is_user_frame_fallback`, renamed)
- `WatchDisplay.__init__`: add `stuck_timeout: float = 30.0` parameter
- Stuck detection: track `_last_qualname_change: float` (monotonic); when qualnames differ from previous tick reset it; when `now - _last_qualname_change >= stuck_timeout` and not already fired, set `_stuck_fired = True`, record snapshot
- Context variables: call `_context.get()` each tick; render as `key=value` pairs; line format: `[42s] epoch=5 loss=0.31 | train > step:123`
- `_WriteTracker` imported from `_tracker.py`
- `watch_inplace` replaces `overwrite`; trackers installed when `watch_inplace=True` OR `install_trackers=True` (passed by session when `tail=True`)

---

### Phase 3 — Capture and Artifact

**`src/runcorder/_capture.py`**
- `install_exception_hook(on_exception: Callable)` / `uninstall_exception_hook()` — wraps `sys.excepthook`

**`src/runcorder/_artifact.py`**
- `StackFrame(filename, lineno, name, is_user: bool)`
- `filter_stack(frames) -> list[StackFrame | str]` — spec's stack-rendering rules
- `ArtifactData` dataclass: all fields from spec front matter + sections
- `write(data: ArtifactData, path: Path)` — emits Markdown with YAML front matter

---

### Phase 4 — Session and Public API

**`src/runcorder/_session.py`**

`InstrumentContext.__init__(output, tail, watch_interval, watch_inplace, stuck_timeout)`

`start()`:
1. `_location.check_log_size()`
2. `_context._install()`
3. Install trackers on stdout/stderr if `watch_inplace` or `tail`
4. Start `WatchDisplay`
5. `install_exception_hook(self._on_exception)`
6. Record `started_at`

`stop(exception_info=None)`:
1. Stop `WatchDisplay`
2. Restore `sys.excepthook`
4. Collect `ArtifactData`; write if exception or stuck fired
5. `_context._uninstall()`
6. Restore stdout/stderr

`session(**kwargs) -> InstrumentContext` — creates and starts context.

`instrument` — dual-form: if first arg is callable decorate directly; otherwise return a decorator. Wraps function in `try/finally` calling `stop()`.

**`src/runcorder/__init__.py`**
```python
from runcorder._session import instrument, session
from runcorder._context import context
```

---

### Phase 5 — CLI

**`src/runcorder/__main__.py`**
- `sys.argv = sys.argv[1:]`, then `runpy.run_path(script, run_name="__main__")` inside `InstrumentContext`
- Propagates exit code via `SystemExit`

**`src/runcorder/cli.py`**
- `cyclopts` app; `clean(age: str = "1d")` command — parses age (`1d`, `7d`, `30d`), deletes artifacts older than age from default log dir

---

### Phase 6 — Tests

| File | What it tests |
|---|---|
| `tests/test_context.py` | additive updates, None removal, warning outside session, no-op |
| `tests/test_tracker.py` | foreign write detection, tail buffering, passthrough |
| `tests/test_watch.py` | line format, context rendering, stuck detection fires once, prefix trimming |
| `tests/test_capture.py` | exception hook install/restore, original hook is called after |
| `tests/test_artifact.py` | stack filtering rules, Markdown output, front matter fields |
| `tests/test_location.py` | auto_name format, size_check staleness, 100 MB warning |
| `tests/test_session.py` | `@instrument` bare and kwargs, `session()` context manager, artifact written on exception only |
| `tests/test_cli.py` | exit code propagation, `runcorder clean` age parsing |

---

## Order of implementation

1. `_context.py` + `test_context.py`
2. `_tracker.py` + `test_tracker.py`
3. `_location.py` + `test_location.py`
4. `watch.py` + `test_watch.py`
5. `_capture.py` + `test_capture.py`
6. `_artifact.py` + `test_artifact.py`
7. `_session.py` + `test_session.py`
8. `__init__.py`
9. `__main__.py` + `cli.py` + `test_cli.py`
