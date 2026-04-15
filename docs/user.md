# Runcorder User Manual

## Why Runcorder

Runcorder is an always-on flight recorder for yout Python scripts. While your script runs it shows a live watch line — elapsed time, custom context, and the current call chain — so you can see what the script is actually doing instead of staring at a silent terminal. If the script crashes or gets stuck, Runcorder writes a compact Markdown report with the filtered traceback, recent watch snapshots, and the surrounding run context.

It sits in the gap between a raw traceback and a full tracing system. There is no setup cost beyond adding it to the command line, it stays out of your way on successful runs, and the failure artifact is designed to paste straight into an intelligent-tool workflow without extra cleanup. Start with Runcorder on every script; escalate to deeper tracing only when you need it.

## Quickstart

### Install from PyPI

```bash
pip install runcorder
```

Runcorder requires Python 3.13 or newer.

### Run a script

No code changes required — just run your script under Runcorder:

```bash
python -m runcorder my_script.py --arg1 --arg2
```

The script runs as if you had launched it directly with `python my_script.py`, except that a watch line appears on stderr while it runs and a report is written on failure.

### Add context (optional)

Inside your script, call `runcorder.context(...)` to surface variables on the live watch line and in the final report:

```python
import runcorder

for epoch in range(10):
    runcorder.context(epoch=epoch, loss=current_loss)
    train_one_epoch()
```

### Explicit integration

If you prefer to instrument from inside the program rather than using the CLI wrapper:

```python
import runcorder

@runcorder.instrument
def main():
    run_pipeline()
```

or as a scoped context manager:

```python
with runcorder.session(tail=True):
    run_pipeline()
```

### Find and clean reports

Reports land in `~/.cache/runcorder/logs/YYMMDD-HHMMSS.md` by default (platform cache dir on systems without `~/.cache`). The path is printed to stderr the first time a report is written.

```bash
runcorder clean          # delete reports older than 1 day
runcorder clean 7d       # older than 7 days
runcorder clean 12h      # older than 12 hours
```

## API Reference

### `python -m runcorder <script.py> [args...]`

Run `<script.py>` in the same interpreter with Runcorder instrumentation installed before user code executes. Extra arguments are forwarded to the script.

Exit behavior:

- script exits normally → exit status `0`
- script raises `SystemExit` → that status is propagated
- any other uncaught exception → report is written, non-zero exit

### `runcorder clean [AGE]`

Delete reports older than `AGE` from the default log directory. `AGE` is a positive integer followed by `d` (days), `h` (hours), or `m` (minutes). Default: `1d`.

### `runcorder.session(**options)`

Return a context manager that records a session for the enclosed block:

```python
with runcorder.session(output="report.md", tail=True, watch_interval=3.0):
    run_pipeline()
```

### `runcorder.instrument`

Decorator form of `session()`. Supports both bare and keyword forms:

```python
@runcorder.instrument
def main(): ...

@runcorder.instrument(output="run.md", tail=True)
def main(): ...
```

### `runcorder.context(**kwargs)`

Set or update session-level key/value pairs. Keys are additive across calls; pass `None` to remove a key. The current context renders as `key=value` pairs on every watch line and is attached to each watch snapshot in the report. Called outside an active session, it warns once and is otherwise a no-op.

```python
runcorder.context(epoch=5, loss=0.312)
runcorder.context(loss=None)            # remove "loss"
```

### Session options

Shared by `session()` and `instrument`:

| Option | Default | Description |
| --- | --- | --- |
| `output` | auto-named | Report path. When unset, Runcorder writes to `~/.cache/runcorder/logs/YYMMDD-HHMMSS.md`. Applied only when a report is actually emitted. |
| `tail` | `False` | Buffer stdout/stderr and include a rolling tail in the report. |
| `watch_interval` | `3.0` | Seconds between stack samples. Minimum `0.5`. |
| `watch_inplace` | `True` | Rewrite the previous status line in place when no foreign output has appeared and the stderr sink supports in-place updates. Set `False` for native code or subprocesses that write to the terminal. |
| `stuck_timeout` | `30.0` | Seconds of unchanged stack before a stuck notice is emitted and a snapshot is captured. Set `0` to disable. |
| `short_traceback` | `True` | Replace Python's default traceback with a concise two-line notice pointing to the report. Set `False` to keep the full traceback on stderr alongside the report. |

When `short_traceback=True` (the default), an uncaught exception prints:

```
ExceptionType: message
[runcorder] see report at <path>
```

The full traceback is always preserved in the report.

### Integration with logging

Runcorder messages are written using standard Python logging, with one exception: the watch line uses direct stderr writes when `watch_inplace=True` and the process is running interactively on a TTY.

When the watch line is emitted via logging, Runcorder only logs it when the line has changed from the previous sample. Runcorder does not modify logging settings.

### When a report is written

A report is produced only when one of these happens:

- the run exits via an uncaught exception
- stuck detection fires

Successful runs produce no report, even when `output` is set. On the first write, Runcorder prints `[runcorder] report is written to <path>` to stderr.

### Report format

A Markdown file with a YAML front matter block followed by sections:

- **Front matter** — `command`, `cwd`, `python`, `started_at`.
- **Stuck snapshot** — filtered stack at the moment stuck was detected (when present).
- **Exception** — type, message, and filtered traceback (on failure). Each stack frame includes function arguments with their `repr()` values at capture time.
- **Watch snapshots** — recent status lines with context variables.
- **Output tail** — combined stdout/stderr tail (only when `tail=True`).
- **Summary** — `ended_at`, `duration_s`, `exit_status` (integer or `"exception"`). Absent if the process is killed before the session finishes.

### Limitations

- Watch samples the main thread only; no C-extension or subprocess frames.
- Stream tracking is best-effort: native writes that bypass Python stream objects are not detected.
- Not a TUI — the single-line display degrades to append-only output on non-interactive or non-rewritable sinks (redirected logs, notebook cells, batch-system log collectors).

## Example

[The example python script](../tests/example1.py) demonstrates the work of Runcoder in interactive and batch modes. Here is the example output in batch mode:

```
> python tests/example1.py --job
epoch 1: starting 4 steps
2026-04-15 13:16:06 INFO runcorder: [1s] stage=warmup epoch=1 loss=0.0681 | main > ... > train_step(epoch=1, step=3, batch_size=32):34
epoch 2: starting 4 steps
2026-04-15 13:16:07 INFO runcorder: [2s] stage=warmup epoch=2 loss=0.0264 | train_step(epoch=2):34
epoch 3: starting 4 steps
2026-04-15 13:16:08 INFO runcorder: [3s] stage=warmup epoch=3 loss=0.0293 | train_step(epoch=3):34
entering long idle for 4.0s — stuck notice should fire
2026-04-15 13:16:09 INFO runcorder: [4s] stage=idle epoch=3 | fake_stuck_phase(seconds=4.0):53
2026-04-15 13:16:10 INFO runcorder: [5s] stage=idle epoch=3 | fake_stuck_phase:53
2026-04-15 13:16:11 INFO runcorder: [6s] stage=idle epoch=3 | fake_stuck_phase:53
2026-04-15 13:16:12 INFO runcorder: [runcorder] report is written to /Users/sasha/.cache/runcorder/logs/260415-131612.md
2026-04-15 13:16:12 INFO runcorder: [7s stuck?] stage=idle epoch=3 | fake_stuck_phase:53
RuntimeError: synthetic failure at end of pipeline
[runcorder] see report at ~/.cache/runcorder/logs/260415-131612.md
```

