"""Frame inspection utilities shared by watch display and report writer."""

from __future__ import annotations

import sys
import sysconfig
from pathlib import Path


def _build_exclusion_prefixes() -> tuple[str, ...]:
    prefixes: set[str] = set()
    paths = sysconfig.get_paths()
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        p = paths.get(key)
        if p:
            try:
                prefixes.add(str(Path(p).resolve()))
            except (OSError, ValueError):
                pass
    for attr in ("prefix", "exec_prefix", "base_prefix"):
        p = getattr(sys, attr, None)
        if p:
            lib_dir = Path(p) / "lib"
            try:
                prefixes.add(str(lib_dir.resolve()))
            except (OSError, ValueError):
                pass
    return tuple(prefixes)


_EXCLUSION_PREFIXES: tuple[str, ...] = _build_exclusion_prefixes()
_RUNCORDER_PREFIX: str = str(Path(__file__).parent.resolve())


def _is_user_frame(frame) -> bool:
    """Return True if *frame* is from user code (not stdlib/site-packages/runcorder)."""
    filename = frame.f_code.co_filename
    if not filename or filename.startswith("<"):
        return False
    try:
        p = str(Path(filename).resolve())
    except (OSError, ValueError):
        return False
    if p.startswith(_RUNCORDER_PREFIX):
        return False
    for prefix in _EXCLUSION_PREFIXES:
        if p.startswith(prefix):
            return False
    return True


def _get_param_names(code) -> list[str]:
    """Return parameter names (excluding non-param locals) from a code object."""
    n = code.co_argcount + code.co_kwonlyargcount
    return list(code.co_varnames[:n])


def _read_param_reprs(frame) -> dict[str, str]:
    """Return {param_name: repr(value)} for up to 4 parameters of a frame."""
    param_names = _get_param_names(frame.f_code)
    if not param_names:
        return {}
    try:
        locals_dict = frame.f_locals
    except Exception:
        return {}
    result: dict[str, str] = {}
    for name in param_names[:4]:
        if name not in locals_dict:
            continue
        result[name] = repr(locals_dict[name])
    return result
