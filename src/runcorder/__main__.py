"""Entry point for ``python -m runcorder path/to/script.py [args...]``.

(spec) Integration
(spec) UserError
"""

import runpy
import sys

from runcorder._errors import UserError
from runcorder._session import InstrumentContext


def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python -m runcorder <script.py> [args...]\n"
            "       runcorder clean [--age AGE]",
            file=sys.stderr,
        )
        sys.exit(1)

    # If the first argument looks like a sub-command, delegate to the CLI app
    if sys.argv[1] in ("clean",):
        from runcorder.cli import app  # noqa: PLC0415 — only needed for clean subcommand
        app()
        return

    script = sys.argv[1]
    # Shift argv so the script sees its own name and arguments
    sys.argv = sys.argv[1:]

    ctx = InstrumentContext()
    ctx.start()
    exc_info = None
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit:
        ctx.stop()
        raise
    except UserError as e:
        ctx.stop()
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)
    except BaseException:
        exc_info = sys.exc_info()
        ctx.stop(exception_info=exc_info)
        raise
    else:
        ctx.stop()


if __name__ == "__main__":
    main()
