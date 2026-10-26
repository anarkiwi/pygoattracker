"""GoatTracker instrument files (``.ins``), as GoatTracker 2.76's
``saveinstrument``/``loadinstrument`` (``src/gsong.c``) write and read them."""

import pytest

from pygoattracker import constants

from tests.conftest import basic_song


def gti5(wave=((0x41, 0x00), (0xFF, 0x05)), pulse=(), filt=(), speed=()) -> bytes:
    """A ``GTI5`` file: the 9-byte instrument record, its 16-byte name, then each
    table's slice as a length byte, the left column and the right column; table
    pointers and jump targets keep the saving song's positions (here 5)."""
    tables = (wave, pulse, filt, speed)
    ptrs = [5 if t else 0 for t in tables]
    out = bytearray(b"GTI5" + bytes([0x09, 0x00, *ptrs, 0, 2, 0x09]))
    out += b"BASS".ljust(constants.MAX_INSTRNAMELEN, b"\0")
    for rows in tables:
        out += bytes([len(rows), *(r[0] for r in rows), *(r[1] for r in rows)])
    return bytes(out)


@pytest.mark.xfail(
    strict=True, raises=ImportError, reason="#45: no .ins reader or writer"
)
def test_a_gti5_instrument_round_trips():
    from pygoattracker import read_ins, write_ins

    data = gti5()
    assert write_ins(read_ins(data)) == data


@pytest.mark.xfail(
    strict=True, raises=ImportError, reason="#45: no .ins reader or writer"
)
def test_a_loaded_instrument_s_tables_append_with_jumps_rebased():
    from pygoattracker import read_ins

    song = basic_song()
    start = len(song.wavetable.left)
    num = song.add_instrument(read_ins(gti5()))
    ins = song.instruments[num - 1]
    assert ins.wave_ptr == start + 1
    assert song.wavetable.left[start:] == [0x41, 0xFF]
    assert song.wavetable.right[start:] == [0x00, start + 1]
