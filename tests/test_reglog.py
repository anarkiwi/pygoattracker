"""GoatTracker register log generation.

The read/write/framing primitives are the shared :mod:`pysidtracker.reglog`
surface (covered by the base package's tests); these tests cover only the
GoatTracker-facing :func:`iter_register_writes` wrapper and this package's
header line.
"""

import io

import pytest

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.reglog import (
    REGLOG_HEADER,
    RegWrite,
    iter_register_writes,
    read_reglog,
    write_reglog,
)


def test_baseline_then_frames(song):
    writes = list(iter_register_writes(song, max_frames=2))
    # The post-init register file is emitted at clock 0, one register every 16
    # cycles, with master volume ($D418) seeded to $0F.
    baseline = writes[: constants.SID_REGISTERS]
    assert [w.reg for w in baseline] == list(range(constants.SID_REGISTERS))
    assert [w.clock for w in baseline] == [16 * n for n in range(25)]
    assert baseline[constants.MODE_VOL_REG].val == 0x0F
    # The first played frame's writes start one PAL frame later.
    assert writes[constants.SID_REGISTERS].clock == constants.PAL_CYCLES_PER_FRAME


def test_clock_options(song):
    writes = list(
        iter_register_writes(song, max_frames=2, cycles_per_frame=1000, write_spacing=2)
    )
    assert writes[1].clock == 2
    assert writes[constants.SID_REGISTERS].clock == 1000


def test_bad_spacing(song):
    with pytest.raises(GoatTrackerError, match="write_spacing"):
        list(iter_register_writes(song, max_frames=1, cycles_per_frame=100))


def test_until_loop_is_finite(song):
    writes = list(iter_register_writes(song, until_loop=True))
    assert writes
    assert writes[-1].clock < 48 * constants.PAL_CYCLES_PER_FRAME


def test_write_read_round_trip(song, tmp_path):
    writes = list(iter_register_writes(song, max_frames=60))
    path = tmp_path / "song.reglog"
    write_reglog(writes, path, header=REGLOG_HEADER)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# pygoattracker register log")
    assert read_reglog(path) == writes
    assert read_reglog(io.StringIO(text)) == writes


def test_write_stream_no_header():
    writes = [RegWrite(0, 24, 15), RegWrite(19656, 4, 0x41)]
    out = io.StringIO()
    write_reglog(writes, out, header=False)
    assert out.getvalue() == "0 24 15\n19656 4 65\n"
    assert read_reglog(io.StringIO(out.getvalue())) == writes
