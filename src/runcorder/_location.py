"""Artifact path resolution and log-space management."""

import os
import sys
import time
from pathlib import Path


def default_log_dir() -> Path:
    """Return the default log directory for runcorder artifacts.

    On macOS or Windows, if ``~/.cache`` already exists, uses
    ``~/.cache/runcorder/logs/`` instead of the platform-specific location.
    On Linux, always uses ``~/.cache/runcorder/logs/``.
    """
    home = Path.home()
    dot_cache = home / ".cache"
    if sys.platform == "linux":
        return dot_cache / "runcorder" / "logs"
    # macOS or Windows: prefer ~/.cache if it already exists
    if dot_cache.is_dir():
        return dot_cache / "runcorder" / "logs"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "runcorder" / "logs"
        return home / "AppData" / "Local" / "runcorder" / "logs"
    # macOS without ~/.cache
    return home / "Library" / "Caches" / "runcorder" / "logs"


def auto_name() -> Path:
    """Return a timestamped artifact path inside the default log dir.

    Format: ``YYMMDD-HHMMSS.md``
    The directory is created if it does not exist.
    """
    log_dir = default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    ts = datetime.now().strftime("%y%m%d-%H%M%S")
    return log_dir / f"{ts}.md"


def check_log_size() -> None:
    """Check whether the log directory exceeds 100 MB and warn if so.

    Uses a ``size_check`` sentinel file to cache the result; only
    recalculates when the file is absent or older than 1 day.
    """
    log_dir = default_log_dir()
    if not log_dir.exists():
        return

    size_check = log_dir / "size_check"
    total: int | None = None

    if size_check.exists():
        age = time.time() - size_check.stat().st_mtime
        if age < 86400:  # 1 day in seconds
            try:
                total = int(size_check.read_text().strip())
            except (ValueError, OSError):
                pass

    if total is None:
        total = sum(
            f.stat().st_size
            for f in log_dir.iterdir()
            if f.is_file() and f.name != "size_check"
        )
        try:
            size_check.write_text(str(total))
            # Touch to reset mtime explicitly (write_text already does this,
            # but be explicit for clarity)
            os.utime(size_check, None)
        except OSError:
            pass

    mb = total / (1024 * 1024)
    if mb > 100:
        print(
            f"runcorder log size is {mb:.0f} MB. Clean with `runcorder clean`",
            file=sys.stderr,
        )
