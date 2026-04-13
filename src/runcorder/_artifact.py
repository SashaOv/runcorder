"""Artifact writer: Markdown + YAML front matter."""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Stack representation

@dataclass
class StackFrame:
    filename: str
    lineno: int
    name: str
    is_user: bool


def _classify_frame(frame) -> StackFrame:
    """Convert a live frame object into a :class:`StackFrame`."""
    from runcorder.watch import _is_user_frame

    return StackFrame(
        filename=frame.f_code.co_filename,
        lineno=frame.f_lineno,
        name=(
            frame.f_code.co_qualname
            if hasattr(frame.f_code, "co_qualname")
            else frame.f_code.co_name
        ),
        is_user=_is_user_frame(frame),
    )


def classify_frames(frames) -> list[StackFrame]:
    """Classify a list of live frame objects."""
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
        # No user frames — full fallback
        return list(frames)

    # Group consecutive frames into spans
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
            # Sandwiched — keep first (after prev user) + last (before next user)
            if len(fs) == 1:
                result.append(fs[0])
            elif len(fs) == 2:
                result.extend(fs)
            else:
                result.append(fs[0])
                result.append("...")
                result.append(fs[-1])
        elif has_prev_user:
            # Trailing non-user span after user block
            result.append(fs[0])
            if len(fs) > 1:
                result.append("...")
        elif has_next_user:
            # Leading non-user span before user block
            if len(fs) > 1:
                result.append("...")
            result.append(fs[-1])
        else:
            result.append("...")

    # Always ensure the innermost frame is present
    last_frame = frames[-1]
    if not last_frame.is_user:
        # Find if it's already in result
        in_result = any(
            isinstance(item, StackFrame) and item is last_frame
            for item in result
        )
        if not in_result:
            # Replace trailing "..." or append
            if result and result[-1] == "...":
                result[-1] = last_frame
            else:
                result.append(last_frame)

    return result


def format_stack(filtered: list[StackFrame | str]) -> str:
    """Render a filtered stack to a multi-line string."""
    lines = []
    for item in filtered:
        if isinstance(item, str):
            lines.append(f"  {item}")
        else:
            lines.append(f'  File "{item.filename}", line {item.lineno}, in {item.name}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Artifact data model

@dataclass
class ArtifactData:
    command: list[str]
    cwd: str
    python: str
    started_at: str
    ended_at: str
    duration_s: float
    exit_status: int | str  # integer or "exception"
    exception: dict | None = None   # keys: type, message, traceback
    stuck_snapshot: str | None = None
    watch_snapshots: list[str] = field(default_factory=list)
    output_tail: str | None = None


# ---------------------------------------------------------------------------
# YAML front matter helpers

def _yaml_str(value: str) -> str:
    """Simple YAML scalar — quote if the string contains special characters."""
    if any(c in value for c in ('"', "'", ":", "\n", "#", "[", "]", "{", "}")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _yaml_list(values: list[str]) -> str:
    items = ", ".join(_yaml_str(v) for v in values)
    return f"[{items}]"


# ---------------------------------------------------------------------------
# Writer

def write(data: ArtifactData, path: Path) -> None:
    """Emit a Markdown artifact with YAML front matter to *path*."""
    lines: list[str] = []

    # YAML front matter
    lines.append("---")
    lines.append(f"command: {_yaml_list(data.command)}")
    lines.append(f"cwd: {_yaml_str(data.cwd)}")
    lines.append(f"python: {_yaml_str(data.python)}")
    lines.append(f"started_at: {_yaml_str(data.started_at)}")
    lines.append(f"ended_at: {_yaml_str(data.ended_at)}")
    lines.append(f"duration_s: {data.duration_s:.3f}")
    if isinstance(data.exit_status, str):
        lines.append(f"exit_status: {data.exit_status}")
    else:
        lines.append(f"exit_status: {data.exit_status}")
    lines.append("---")
    lines.append("")

    # Exception section
    if data.exception:
        lines.append("## Exception")
        lines.append("")
        lines.append(f"**Type:** `{data.exception['type']}`")
        lines.append("")
        lines.append(f"**Message:** {data.exception['message']}")
        lines.append("")
        lines.append("```")
        lines.append(data.exception["traceback"].rstrip())
        lines.append("```")
        lines.append("")

    # Stuck snapshot section
    if data.stuck_snapshot:
        lines.append("## Stuck Snapshot")
        lines.append("")
        lines.append("```")
        lines.append(data.stuck_snapshot.rstrip())
        lines.append("```")
        lines.append("")

    # Watch snapshots section
    if data.watch_snapshots:
        lines.append("## Watch Snapshots")
        lines.append("")
        for snap in data.watch_snapshots:
            lines.append(snap)
        lines.append("")

    # Output tail section
    if data.output_tail is not None:
        lines.append("## Output Tail")
        lines.append("")
        lines.append("```")
        lines.append(data.output_tail.rstrip())
        lines.append("```")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
