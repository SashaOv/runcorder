"""sys.excepthook install / uninstall helpers."""

import sys
from typing import Callable, Optional

_original_excepthook: Optional[Callable] = None


def install_exception_hook(on_exception: Callable) -> None:
    """Wrap ``sys.excepthook`` to call *on_exception* before delegating.

    *on_exception* receives ``(exc_type, exc_value, exc_tb)``.
    The previously-installed hook (or ``sys.__excepthook__``) is still called
    afterward so that the default traceback printout is preserved.
    """
    global _original_excepthook
    _original_excepthook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            on_exception(exc_type, exc_value, exc_tb)
        except Exception:
            pass  # never let our callback suppress the original behaviour
        previous = _original_excepthook
        if previous is not None and previous is not _hook:
            previous(exc_type, exc_value, exc_tb)
        else:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


def uninstall_exception_hook() -> None:
    """Restore the excepthook that was in place before :func:`install_exception_hook`."""
    global _original_excepthook
    if _original_excepthook is not None:
        sys.excepthook = _original_excepthook
        _original_excepthook = None
