"""Report writer: incremental Markdown + YAML front matter.

A *report* is the file runcorder writes when something notable happens
(stuck detection or exception).  It is built up across multiple calls:

- Front matter (static fields only) is written once on first call.
- ``## Stuck Snapshot`` / ``## Exception`` sections are appended as events occur.
- ``## Summary`` (with ``ended_at``, ``duration_s``, ``exit_status``) plus any
  trailing sections are appended by :meth:`ReportWriter.finalize` at session end.

Because sections are appended lazily, partial reports (e.g. stuck fires then
the process is killed) remain valid Markdown on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from runcorder._frames import _is_user_frame, _get_param_names


# ---------------------------------------------------------------------------
# Stack representation

_ARG_REPR_CAP = 80  # max repr length per argument in the report


@dataclass
class StackFrame:
    filename: str
    lineno: int
    name: str
    is_user: bool
    args: list[tuple[str, str]] = field(default_factory=list)


def _classify_frame(frame) -> StackFrame:
    param_names = _get_param_names(frame.f_code)
    args: list[tuple[str, str]] = []
    if param_names:
        try:
            locals_dict = frame.f_locals
            for name in param_names[:4]:
                if name not in locals_dict:
                    continue
                try:
                    r = repr(locals_dict[name])
                except Exception:
                    r = "<unrepr>"
                if len(r) > _ARG_REPR_CAP:
                    r = r[:_ARG_REPR_CAP - 3] + "..."
                args.append((name, r))
        except Exception:
            pass

    return StackFrame(
        filename=frame.f_code.co_filename,
        lineno=frame.f_lineno,
        name=(
            frame.f_code.co_qualname
            if hasattr(frame.f_code, "co_qualname")
            else frame.f_code.co_name
        ),
        is_user=_is_user_frame(frame),
        args=args,
    )


def classify_frames(frames) -> list[StackFrame]:
    return [_classify_frame(f) for f in frames]


def filter_stack(frames: list[StackFrame]) -> list[StackFrame | str]:
    """Apply the spec's stack-rendering rules.

    Rules:
    - Keep all user-code frames.
    - For each user block, keep at most one adjacent non-user frame
      immediately before it and one immediately after it.
    - Collapse omitted non-user spans to ``"..."``.
    - Always keep the innermost (last) frame even if non-user.
    - Fallback: if no user frames, return all frames as-is.
    """
    if not frames:
        return []

    if not any(f.is_user for f in frames):
        return list(frames)

    groups: list[dict] = []
    for frame in frames:
        if groups and groups[-1]["is_user"] == frame.is_user:
            groups[-1]["frames"].append(frame)
        else:
            groups.append({"is_user": frame.is_user, "frames": [frame]})

    result: list[StackFrame | str] = []

    for i, group in enumerate(groups):
        if group["is_user"]:
            result.extend(group["frames"])
            continue

        fs = group["frames"]
        has_prev_user = any(groups[j]["is_user"] for j in range(i))
        has_next_user = any(groups[j]["is_user"] for j in range(i + 1, len(groups)))

        if has_prev_user and has_next_user:
            if len(fs) == 1:
                result.append(fs[0])
            elif len(fs) == 2:
                result.extend(fs)
            else:
                result.append(fs[0])
                result.append("...")
                result.append(fs[-1])
        elif has_prev_user:
            result.append(fs[0])
            if len(fs) > 1:
                result.append("...")
        elif has_next_user:
            if len(fs) > 1:
                result.append("...")
            result.append(fs[-1])
        else:
            result.append("...")

    last_frame = frames[-1]
    if not last_frame.is_user:
        in_result = any(
            isinstance(item, StackFrame) and item is last_frame
            for item in result
        )
        if not in_result:
            if result and result[-1] == "...":
                result[-1] = last_frame
            else:
                result.append(last_frame)

    return result


def format_stack(filtered: list[StackFrame | str]) -> str:
    lines = []
    for item in filtered:
        if isinstance(item, str):
            lines.append(f"  {item}")
        else:
            args_str = ", ".join(f"{k}={v}" for k, v in item.args)
            lines.append(f'  File "{item.filename}", line {item.lineno}, in {item.name}({args_str})')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Report metadata (static fields known at session start)

@dataclass
class ReportMeta:
    command: list[str]
    cwd: str
    python: str
    started_at: str


# ---------------------------------------------------------------------------
# YAML scalar helpers

def _yaml_str(value: str) -> str:
    if any(c in value for c in ('"', "'", ":", "\n", "#", "[", "]", "{", "}")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(values: list[str]) -> str:
    items = ", ".join(_yaml_str(v) for v in values)
    return f"[{items}]"


# ---------------------------------------------------------------------------
# ReportWriter

class ReportWriter:
    """Incremental writer for a runcorder report.

    The front matter is written once on the first call to any write method
    (tracked by ``_header_written``).  Subsequent calls append sections to
    the file.  Call order does not matter; the writer tolerates any sequence
    of ``write_stuck`` / ``write_exception`` / ``finalize``.
    """

    def __init__(self, path: Path, meta: ReportMeta) -> None:
        self._path = path
        self._meta = meta
        self._header_written = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def header_written(self) -> bool:
        return self._header_written

    def _ensure_header(self) -> None:
        if self._header_written:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            f"command: {_yaml_list(self._meta.command)}",
            f"cwd: {_yaml_str(self._meta.cwd)}",
            f"python: {_yaml_str(self._meta.python)}",
            f"started_at: {_yaml_str(self._meta.started_at)}",
            "---",
            "",
        ]
        self._path.write_text("\n".join(lines), encoding="utf-8")
        self._header_written = True

    def _append(self, lines: list[str]) -> None:
        with self._path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def write_stuck(self, frames: list) -> None:
        """Append the ``## Stuck Snapshot`` section; writes header if needed."""
        self._ensure_header()
        stuck_text = format_stack(filter_stack(classify_frames(frames)))
        self._append([
            "## Stuck Snapshot",
            "",
            "```",
            stuck_text.rstrip(),
            "```",
            "",
        ])

    def write_exception(self, exc_dict: dict) -> None:
        """Append the ``## Exception`` section; writes header if needed."""
        self._ensure_header()
        self._append([
            "## Exception",
            "",
            f"**Type:** `{exc_dict['type']}`",
            "",
            f"**Message:** {exc_dict['message']}",
            "",
            "```",
            exc_dict["traceback"].rstrip(),
            "```",
            "",
        ])

    def finalize(
        self,
        ended_at: str,
        duration_s: float,
        exit_status: int | str,
        watch_snapshots: list[str] | None = None,
        output_tail: str | None = None,
    ) -> None:
        """Append watch snapshots, output tail, and ``## Summary``.

        No-op if the header was never written (nothing to finalize).
        """
        if not self._header_written:
            return

        lines: list[str] = []

        if watch_snapshots:
            lines += [
                "## Watch Snapshots",
                "",
                *watch_snapshots,
                "",
            ]

        if output_tail is not None:
            lines += [
                "## Output Tail",
                "",
                "```",
                output_tail.rstrip(),
                "```",
                "",
            ]

        lines += [
            "## Summary",
            "",
            f"ended_at: {_yaml_str(ended_at)}",
            f"duration_s: {duration_s:.3f}",
            f"exit_status: {exit_status}",
            "",
        ]

        self._append(lines)
