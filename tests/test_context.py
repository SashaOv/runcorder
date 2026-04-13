import warnings
import pytest
import runcorder._context as ctx


def setup_function():
    ctx._uninstall()


def teardown_function():
    ctx._uninstall()


def test_install_returns_empty_dict():
    store = ctx._install()
    assert store == {}


def test_additive_updates():
    ctx._install()
    ctx.context(a=1, b=2)
    assert ctx.get() == {"a": 1, "b": 2}
    ctx.context(c=3)
    assert ctx.get() == {"a": 1, "b": 2, "c": 3}


def test_update_existing_key():
    ctx._install()
    ctx.context(a=1)
    ctx.context(a=99)
    assert ctx.get() == {"a": 99}


def test_none_removes_key():
    ctx._install()
    ctx.context(a=1, b=2)
    ctx.context(a=None)
    assert ctx.get() == {"b": 2}


def test_none_on_missing_key_is_noop():
    ctx._install()
    ctx.context(missing=None)
    assert ctx.get() == {}


def test_get_returns_copy():
    ctx._install()
    ctx.context(a=1)
    d = ctx.get()
    d["a"] = 999
    assert ctx.get() == {"a": 1}


def test_get_outside_session_returns_empty():
    assert ctx.get() == {}


def test_warning_outside_session():
    ctx._warned = False
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ctx.context(x=1)
    assert len(w) == 1
    assert "outside an active session" in str(w[0].message)


def test_warning_fires_only_once():
    ctx._warned = False
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        ctx.context(x=1)
        ctx.context(y=2)
    assert len(w) == 1


def test_noop_outside_session():
    # Should not raise
    ctx.context(x=1)
    assert ctx.get() == {}


def test_uninstall_clears_store():
    ctx._install()
    ctx.context(a=1)
    ctx._uninstall()
    assert ctx.get() == {}


def test_reinstall_resets_store():
    ctx._install()
    ctx.context(a=1)
    ctx._uninstall()
    ctx._install()
    assert ctx.get() == {}
