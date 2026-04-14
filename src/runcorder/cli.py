"""CLI for runcorder — currently exposes the ``clean`` sub-command."""

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import cyclopts

app = cyclopts.App(name="runcorder", help="Runcorder flight-recorder utilities.")


@app.command
def clean(age: str = "1d") -> None:
    """Delete runcorder reports older than AGE from the default log directory.

    AGE format: a positive integer followed by ``d`` (days), ``h`` (hours),
    or ``m`` (minutes).  Examples: ``1d``, ``7d``, ``30d``, ``12h``.
    Default: ``1d``.
    """
    from runcorder._location import default_log_dir

    match = re.fullmatch(r"(\d+)([dhm]?)", age.strip())
    if not match:
        print(
            f"runcorder clean: invalid age {age!r}. "
            "Use a number followed by d/h/m (e.g. 1d, 7d, 12h).",
            file=sys.stderr,
        )
        raise SystemExit(1)

    n_str, unit = match.groups()
    n = int(n_str)
    unit = unit or "d"

    if unit == "d":
        delta = timedelta(days=n)
    elif unit == "h":
        delta = timedelta(hours=n)
    else:  # "m"
        delta = timedelta(minutes=n)

    cutoff = datetime.now() - delta
    log_dir = default_log_dir()

    if not log_dir.exists():
        return

    deleted = 0
    for f in log_dir.iterdir():
        if f.suffix == ".md" and f.is_file():
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                deleted += 1

    if deleted:
        print(f"Deleted {deleted} report(s).")

    # Remove cached size so it is recalculated on next run
    size_check = log_dir / "size_check"
    if size_check.exists():
        size_check.unlink()


if __name__ == "__main__":
    app()
