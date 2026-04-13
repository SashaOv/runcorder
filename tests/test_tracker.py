import io
import pytest
from runcorder._tracker import _WriteTracker


def make_tracker(tail_size=500):
    buf = io.StringIO()
    tracker = _WriteTracker(buf, tail_size=tail_size)
    return tracker, buf


def test_passthrough_write():
    tracker, buf = make_tracker()
    tracker.write("hello world")
    assert buf.getvalue() == "hello world"


def test_passthrough_multiple_writes():
    tracker, buf = make_tracker()
    tracker.write("foo")
    tracker.write("bar")
    assert buf.getvalue() == "foobar"


def test_foreign_wrote_set_on_write():
    tracker, buf = make_tracker()
    assert not tracker.foreign_wrote
    tracker.write("x")
    assert tracker.foreign_wrote


def test_foreign_wrote_reset():
    tracker, buf = make_tracker()
    tracker.write("x")
    assert tracker.foreign_wrote
    tracker.reset_foreign()
    assert not tracker.foreign_wrote


def test_foreign_wrote_set_again_after_reset():
    tracker, buf = make_tracker()
    tracker.write("x")
    tracker.reset_foreign()
    tracker.write("y")
    assert tracker.foreign_wrote


def test_tail_single_line():
    tracker, buf = make_tracker()
    tracker.write("hello\n")
    assert tracker.tail_lines() == ["hello"]


def test_tail_multiple_lines():
    tracker, buf = make_tracker()
    tracker.write("line1\nline2\nline3\n")
    assert tracker.tail_lines() == ["line1", "line2", "line3"]


def test_tail_partial_line_not_buffered():
    tracker, buf = make_tracker()
    tracker.write("partial")
    assert tracker.tail_lines() == []


def test_tail_partial_then_newline():
    tracker, buf = make_tracker()
    tracker.write("par")
    tracker.write("tial\n")
    assert tracker.tail_lines() == ["partial"]


def test_tail_max_size():
    tracker, buf = make_tracker(tail_size=3)
    for i in range(5):
        tracker.write(f"line{i}\n")
    lines = tracker.tail_lines()
    assert len(lines) == 3
    assert lines == ["line2", "line3", "line4"]


def test_tail_returns_copy():
    tracker, buf = make_tracker()
    tracker.write("a\n")
    lines1 = tracker.tail_lines()
    lines1.append("injected")
    lines2 = tracker.tail_lines()
    assert "injected" not in lines2


def test_flush_delegates():
    inner = io.StringIO()
    tracker = _WriteTracker(inner)
    tracker.flush()  # should not raise


def test_getattr_delegates():
    inner = io.StringIO()
    tracker = _WriteTracker(inner)
    # StringIO has 'name' in some implementations; check a safe attr
    assert tracker.writable() is True
