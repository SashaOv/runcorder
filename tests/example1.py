"""Manual demo script exercising runcorder's public surface.

Run one of:

    python tests/example1.py                # --script (default): interactive tty watch
    python tests/example1.py --script       # same
    python tests/example1.py --job          # batch-style: stdlib logging config

The script walks through a fake training loop so the watch line shows:
- elapsed time and stuck marker
- context variables (epoch, loss)
- the call chain with changing arguments
- stable-prefix trimming as inner frames change

It then triggers stuck detection, writes captured stdout, and raises an
uncaught exception so the failure report is emitted.  The final path of the
report is printed to stderr by runcorder itself.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time

import runcorder


def train_step(epoch: int, step: int, batch_size: int) -> float:
    """Pretend to train one step.  Args change each call so the watch line
    shows diff-repr on ``step``."""
    time.sleep(0.25)
    loss = max(0.01, 1.0 / (epoch * 10 + step + 1) + random.uniform(-0.02, 0.02))
    return loss


def train_epoch(epoch: int, n_steps: int) -> float:
    runcorder.context(epoch=epoch, loss=None)
    print(f"epoch {epoch}: starting {n_steps} steps", flush=True)
    last_loss = 0.0
    for step in range(n_steps):
        last_loss = train_step(epoch, step, batch_size=32)
        runcorder.context(epoch=epoch, loss=round(last_loss, 4))
    return last_loss


def fake_stuck_phase(seconds: float) -> None:
    """Do nothing for *seconds* so the watch thread sees an unchanging stack
    and fires the stuck notice."""
    print(f"entering long idle for {seconds}s — stuck notice should fire", flush=True)
    time.sleep(seconds)


def run_pipeline(n_epochs: int = 3) -> None:
    runcorder.context(stage="warmup")
    for epoch in range(1, n_epochs + 1):
        train_epoch(epoch, n_steps=4)

    runcorder.context(stage="idle", loss=None)
    fake_stuck_phase(seconds=4.0)

    runcorder.context(stage="finalize")
    # Demonstrate that an uncaught exception produces a report with a
    # filtered traceback.
    raise RuntimeError("synthetic failure at end of pipeline")


def configure_batch_logging() -> None:
    """Typical batch-job logging: timestamped lines to stderr, INFO level."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--script",
        action="store_true",
        help="Interactive script mode (default): in-place watch line on tty.",
    )
    group.add_argument(
        "--job",
        action="store_true",
        help="Batch-job mode: configure stdlib logging; watch line is logged.",
    )
    args = parser.parse_args()

    job_mode = args.job and not args.script

    if job_mode:
        configure_batch_logging()
        session_kwargs = dict(
            tail=True,
            watch_interval=1.0,
            watch_inplace=False,   # force logger path even on a tty
            stuck_timeout=3.0,
        )
    else:
        session_kwargs = dict(
            tail=True,
            watch_interval=1.0,
            watch_inplace=True,
            stuck_timeout=3.0,
        )

    with runcorder.session(**session_kwargs):
        run_pipeline()


if __name__ == "__main__":
    main()
