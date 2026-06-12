"""Register log generation and serialization."""

import io

import pytest

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.reglog import (
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)


def test_clock_layout(song):
    writes = list(iter_register_writes(song, max_frames=2))
    # Frame 0 initializes all 25 registers, 16 cycles apart.
    first = writes[: constants.SID_REGISTERS]
    assert [w.reg for w in first] == list(range(constants.SID_REGISTERS))
    assert [w.clock for w in first] == [16 * n for n in range(25)]
    assert all(w.val == 0 for w in first)
    # Frame 1 starts one PAL frame later with the volume write.
    second = writes[constants.SID_REGISTERS :]
    assert second[0].clock == constants.PAL_CYCLES_PER_FRAME
    assert (constants.MODE_VOL_REG, 0x0F) == (second[0].reg, second[0].val)


def test_clock_options(song):
    writes = list(
        iter_register_writes(song, max_frames=2, cycles_per_frame=1000, write_spacing=2)
    )
    assert writes[1].clock == 2
    assert writes[constants.SID_REGISTERS].clock == 1000


def test_bad_spacing(song):
    with pytest.raises(GoatTrackerError, match="write_spacing"):
        list(iter_register_writes(song, max_frames=1, cycles_per_frame=100))


def test_until_loop(song):
    writes = list(iter_register_writes(song, until_loop=True))
    assert writes[-1].clock < 48 * constants.PAL_CYCLES_PER_FRAME


def test_write_read_path_round_trip(song, tmp_path):
    writes = list(iter_register_writes(song, max_frames=60))
    path = tmp_path / "song.reglog"
    write_reglog(writes, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# pygoattracker register log")
    assert read_reglog(path) == writes
    assert read_reglog(str(path)) == writes
    assert read_reglog(io.StringIO(text)) == writes
    with pytest.raises(TypeError):
        read_reglog(12345)


def test_write_stream_no_header():
    writes = [RegWrite(0, 24, 15), RegWrite(19656, 4, 0x41)]
    out = io.StringIO()
    write_reglog(writes, out, header=False)
    assert out.getvalue() == "0 24 15\n19656 4 65\n"
    assert read_reglog(io.StringIO(out.getvalue())) == writes


def test_read_blank_lines_and_comments():
    text = "# comment\n\n0 24 15  # trailing comment\n"
    assert read_reglog(io.StringIO(text)) == [RegWrite(0, 24, 15)]


@pytest.mark.parametrize("bad", ["0 24", "0 24 15 16", "a b c"])
def test_read_bad_lines(bad):
    with pytest.raises(GoatTrackerError, match="line 1"):
        read_reglog(io.StringIO(bad))
