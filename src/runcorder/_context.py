"""Session-level key/value store for runcorder context variables."""

import warnings

_active_store: dict | None = None
_warned: bool = False


def context(**kwargs) -> None:
    """Set or update session-level context variables.

    Keys are additive; setting a key to None removes it.
    Warns once per process if called outside an active session.
    """
    global _active_store, _warned
    if _active_store is None:
        if not _warned:
            warnings.warn(
                "runcorder.context() called outside an active session; call has no effect",
                stacklevel=2,
            )
            _warned = True
        return
    for k, v in kwargs.items():
        if v is None:
            _active_store.pop(k, None)
        else:
            _active_store[k] = v


def _install() -> dict:
    """Install (activate) the context store. Called by session start."""
    global _active_store, _warned
    _active_store = {}
    _warned = False
    return _active_store


def _uninstall() -> None:
    """Uninstall (deactivate) the context store. Called by session stop."""
    global _active_store
    _active_store = None


def get() -> dict:
    """Return a copy of the current context store, or empty dict if no session."""
    if _active_store is None:
        return {}
    return dict(_active_store)
