# Runcorder

Runcorder is an always-on "flight recorder" for Python scripts. It shows a live watch line while the script runs, and writes a compact report when the run fails or appears stuck, with enough context for a human or intelligent tools to diagnose the problem.

## Purpose

The target problem is the gap between a raw traceback and a full trace:

- the script runs, user wants live progress,
- if it crashes, the traceback alone is rarely enough,
- adding logging by hand after the fact is slow and fragile.

Runcorder fills that gap. It is not a tracing tool, a logging framework, or a debugger.

## Architecture

Two active components run during script execution:

- **Watch display** — a daemon thread that periodically samples the main thread stack and writes a status line to stderr. Runcorder owns this component; PyWolf's watch is migrated here.
- **Exception hook** — installed via `sys.excepthook`; captures uncaught exception details at process exit.

A third component assembles the report when one is needed:

- **Report writer** — writes run metadata, stuck snapshots, and exception details to the output file incrementally as events occur. Front matter is emitted once on the first write; further sections are appended.

## Integration

```python
# CLI — no code changes required
python -m runcorder my_script.py

# Decorator for application entry points
@runcorder.instrument
def main():
    run_pipeline()

# Context manager for scoped recording
with runcorder.session():
    run_pipeline()

# With options
with runcorder.session(output="report.md", tail=True, watch_interval=3.0):
    run_pipeline()

# Context variables — displayed on the watch line, persisted in the report
runcorder.context(epoch=5, loss=0.312)   # set or update keys
runcorder.context(loss=None)             # remove a key
```

`session()` and `instrument` share the same knobs:
- `output` — report path; default is auto-named (see Report Location).
- `tail` — include a rolling tail of stdout/stderr in the report; default `False`.
- `watch_interval` — seconds between stack samples; default `3.0`, minimum `0.5`.
- `watch_inplace` — rewrite the previous status line when no foreign output has appeared and the stderr sink supports in-place updates; default `True`. When the sink does not support in-place updates, watch output degrades to append-only status lines. Set `False` when native code or subprocesses write to the terminal.
- `stuck_timeout` — seconds of unchanged stack before a stuck notice is emitted; default `30`. Set `0` to disable.

**CLI wrapper contract.** In v1, `python -m runcorder path/to/script.py ...args...` accepts a filesystem path to a Python script and behaves like ordinary execution of `python path/to/script.py ...args...`, except that runcorder runs in the same interpreter process and installs instrumentation before control reaches user code.

**Exit behavior.** If the target exits normally, runcorder exits with status 0. If the target raises `SystemExit`, runcorder propagates the resulting exit status. If the target terminates with any other uncaught exception, runcorder emits the failure report and then exits non-zero.

## Watch Display

Runcorder owns `WatchDisplay`. The behavior below is authoritative; PyWolf's watch implementation is migrated here and PyWolf depends on runcorder for it.

A daemon thread polls `sys._current_frames()` every `watch_interval` seconds.

**Frame filtering.** The main thread stack is inspected. Frames are filtered using the exclusion index (stdlib, site-packages, runcorder internals). Module-level `<module>` frames are always skipped.

**Line format.** The status line has the form:

```
[42s] epoch=5 loss=0.31 | train > step:123
...
[42s stuck?] epoch=5 loss=0.31 | train > step:123
```

Fields in order: elapsed time, stuck marker (if active), context variables (if any), then the call chain separated by ` | `. The call chain joins visible frames with ` > `; each frame is `qualname(args)` with the innermost frame including `:lineno`. Parameters are shown only for frames where the argument values changed since the previous sample; unchanged arguments are omitted. Repr values: only differing part is displayed with "..." before and after, if necessary, capped at 24 characters. The full line is truncated to terminal width — if truncation is needed, the call chain collapses to `first > ... > last`. When stderr does not support in-place updates, runcorder emits these as append-only lines instead of rewriting a single live status line.

**Context variables.** `runcorder.context(**kwargs)` updates a session-level key/value store. Keys are additive; setting a key to `None` removes it. The current context is rendered as `key=value` pairs on every status line, and is included in each watch snapshot in the report.

**Stable prefix trimming.** Each poll cycle is a tick. A sliding window of the last 3 ticks identifies the deepest frame position whose qualname is the same in every tick. Everything above that boundary is stable context; only the boundary frame and below are shown. This keeps the line focused on the changing part of a loop.

**Stuck detection.** If the set of qualnames in the displayed stack is unchanged across every tick for `stuck_timeout` seconds, the status line is prefixed with `[stuck?]` and a stack snapshot is recorded in the report. The notice fires once per session.

**Stream tracking.** `_WriteTracker` wrappers are installed on `sys.stdout` and `sys.stderr` whenever watch is active. They serve two purposes: detecting foreign writes for `watch_inplace` mode, and buffering a rolling tail of output for the report. The two are independent.

**Limitations:**
- Main thread only; no C-extension or subprocess frames.
- Stream tracking is best-effort: native writes bypassing Python stream objects are not detected.
- Not a TUI; prefers a single-line stderr display but degrades to append-only output on non-interactive or non-rewritable sinks such as redirected logs, notebook outputs, and batch-system log collectors.

## Automatic Capture Points

Recorded automatically, targeting <1% CPU overhead:

- `__main__` module entry
- uncaught exception (via `sys.excepthook`)
- periodic stack sample (shared with the watch line)

## Report Location

Runcorder writes an report only when either of these conditions occurs:

- the run exits via an uncaught exception
- stuck detection fires

A run that exits successfully without a stuck event produces no report by default, even if `output` is set.

When an report is emitted, the default location is the user cache directory under `runcorder/logs/YYMMDD-HHMMSS.md`. On macOS or Windows, if `~/.cache` already exists, runcorder uses `~/.cache/runcorder/logs/` instead of the platform-specific cache location. The `output` knob overrides the default path when an report is emitted.

After first time the report is written to, the message is written to stderr:

     [runcoder] report is written to <path>

**Log space management.** On each run, runcorder checks the chosen default log directory and reads its `size_check` file. If that file is absent or older than 1 day, runcorder recalculates the total size of the log directory, writes the result to `size_check`, and updates its modification time. If the size exceeds 100 MB, it prints to stderr:

```
runcorder log size is XXX MB. Clean with `runcorder clean`
```

`runcorder clean <age>` deletes all reports in the default log directory older than `<age>`. Default age: 1 day.

## Report Format

When emitted, the report is a Markdown file with a YAML front matter block followed by human-readable sections. Designed to paste cleanly into LLM workflows; VS Code folds on Markdown headers for large reports.

**Stack rendering.** Exception tracebacks and stuck snapshots use a filtered, boundary-preserving stack view:

- keep all user-code frames
- treat consecutive user-code frames as one user block
- for each user block, keep at most one adjacent non-user frame immediately before it and one immediately after it
- collapse omitted non-user spans to `...`
- always keep the innermost exception frame, even if it is non-user code

If a stack contains no user-code frames, runcorder falls back to the ordinary Python traceback or sampled stack so the report still shows a meaningful failure location.

**Incremental writing.** The report is built up across multiple writes. Front matter is emitted once on the first write (whichever section lands first); subsequent sections are appended. This keeps partial reports valid even when the process is killed before the session ends.

**Front matter fields** (static — known at session start):
- `command` — argv as a list
- `cwd` — working directory
- `python` — Python version string
- `started_at`

**Sections**, in the order they are written:
- **Stuck snapshot** — filtered stack snapshot at the time the stuck notice fired, using the stack-rendering rules above (present only if stuck was detected)
- **Exception** — type, message, and filtered formatted traceback using the stack-rendering rules above (present only on failure)
- **Watch snapshots** — recent status lines from the watch thread, each including the context variables active at that tick
- **Output tail** — stdout and stderr combined, present only when `tail=True`
- **Summary** — appended at session end; contains `ended_at`, `duration_s`, and `exit_status` (integer or `"exception"`). Absent when the process is killed before the session finishes.

## Source Layout

```
runcorder/
  src/
    runcorder/
  tests/
  rust/          # future — native extension
  js/            # future — JS/TS package
  pyproject.toml
```

`tests/` stays at the top level; the `src/` layout keeps the installable package isolated from development reports. `rust/` and `js/` are reserved directories: adding either does not require restructuring the Python package.

## Scope

In scope:
- CPython 3.13+, synchronous scripts, CLIs, batch jobs, notebooks.
- Library (`instrument`, `session()`) and CLI (`-m runcorder`) entry points.
- Single-process, main-thread watch display.
- Notebook support via explicit `session()` scopes or instrumented calls.

Planned:
- Python 3.12 support.
- Full function-level tracing as an in-product escalation path, absorbing PyWolf functionality.

Non-goals (v1):
- Full function-level tracing.
- Distributed tracing, replay, coverage.
- General logging framework or debugger.
- Automatic instrumentation of arbitrary notebook cell execution.
- Async/multi-thread watch display.
