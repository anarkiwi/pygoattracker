"""The song data's layout read off the packed player by running it.

The fixtures are GoatTracker 2.76's own packer output (``scripts/pack_fixtures.py``),
whose layouts are greloc's; the HVSC tunes are packer variants the observation
must read the same way (fetched like :mod:`tests.test_hvsc_sid`, never committed).
"""

from pathlib import Path

import pytest
from pysidtracker.emu import wire_mpu
from pysidtracker.testing import resolve_tune

from pygoattracker import observe, sid
from pygoattracker.errors import SidParseError
from tests._fixture_cache import HVSC_CACHE

DATA = Path(__file__).parent / "data"

# Origins follow RAM, a self-modified immediate and an INY, not arithmetic.
_PROGRAM = bytes.fromhex(
    "AC 00 20"  # ldy $2000
    "B9 00 21"  # lda $2100,y
    "85 30"  # sta $30
    "A6 30"  # ldx $30
    "C8"  # iny
    "B9 00 22"  # lda $2200,y
    "8D 05 D4"  # sta $d405
    "C6 30"  # dec $30
    "AD 01 20"  # lda $2001
    "8D 1A 10"  # sta $101a
    "A0 00"  # ldy #$00 (operand stored above)
    "B9 00 23"  # lda $2300,y
    "69 01"  # adc #$01
    "A8"  # tay
    "B9 00 24"  # lda $2400,y
    "A0 00"  # ldy #$00
    "B9 00 26"  # lda $2600,y
    "85 40"  # sta $40
    "B9 00 27"  # lda $2700,y
    "85 41"  # sta $41
    "B1 40"  # lda ($40),y
    "9D 00 28"  # sta $2800,x
    "60"  # rts
)


def _trace():
    subject = bytearray(0x10000)
    subject[0x1000 : 0x1000 + len(_PROGRAM)] = _PROGRAM
    subject[0x2000], subject[0x2001], subject[0x2102] = 2, 1, 5
    subject[0x2700] = 0x29
    trace = observe.Trace(subject, *wire_mpu(subject))
    trace.call(0x1000)
    return trace


def test_each_value_is_followed_to_the_byte_it_was_loaded_from():
    trace = _trace()
    reads = {r.addr: r for r in trace.reads}
    bases = {r.base: r for r in trace.reads}
    assert reads[0x2102][1:] == (0x2100, 0x2102, 0x2000, 2, True, None, 0x2102)
    assert reads[0x2203][3:6] == (0x2000, 2, True)
    assert reads[0x2301][3:6] == (0x2001, 1, True)
    assert bases[0x2400][3:6:2] == (0x2301, False)
    assert reads[0x2900].pointer == ((0x2600, 0x2600), (0x2700, 0x2700))
    assert (0xD405, 0x2203, True, 0x2200, 0x2200) in trace.sid
    assert 0x2102 in trace.counted
    assert observe.Store(0x2805, 0x2900, True) in trace.stores
    assert observe.Store(0x0030, 0x2102, False) in trace.stores


def test_the_note_table_is_where_unchanged_frequencies_are_loaded_from():
    subject = bytearray(0x10000)
    code = "AC 00 30 B9 00 20 8D 00 D4 B9 80 20 8D 01 D4 AD 05 30 8D 07 D4"
    code += " B9 01 20 69 01 8D 0E D4 60"
    subject[0x1000 : 0x1000 + 28] = bytes.fromhex(code)
    subject[0x3000] = 0x10
    trace = observe.Trace(subject, *wire_mpu(subject))
    trace.call(0x1000)
    assert observe._note_bases(trace) == (0x2000, 0x2080)
    subject[0x2010], subject[0x2090] = 0x34, 0x12
    assert sid._freq_table(subject, 0x3000, 12, 8, (0x2000, 0x2080))[0x10] == 0x1234


def test_a_copied_byte_keeps_its_origin_and_its_last_load():
    subject = bytearray(0x10000)
    code = "A0 00 B9 00 30 99 00 40 B9 00 40 85 50 AD 00 50 85 51 B1 50 60"
    subject[0x1000 : 0x1000 + 21] = bytes.fromhex(code)
    trace = observe.Trace(subject, *wire_mpu(subject))
    trace.call(0x1000)
    copy = next(r for r in trace.reads if r.base == 0x4000)
    assert (copy.addr, copy.source) == (0x4000, 0x3000)
    assert trace.reads[-1].pointer == ((0x3000, 0x4000), (0x5000, 0x5000))


def test_the_song_and_pattern_columns_tile_one_block():
    stock = dict(slo=0x100, shi=0x106, plo=0x10C, phi=0x114)
    assert observe._lengths(stock) == (6, 8)
    interleaved = dict(phi=0x100, shi=0x10D, plo=0x110, slo=0x11D)
    assert observe._lengths(interleaved) == (3, 13)
    assert observe._lengths(dict(slo=0x100, shi=0x103, plo=0x200, phi=0x210)) is None


def test_a_table_s_right_column_is_its_row_1_s_other_read():
    def reads(*addrs):
        return [observe.Read(0, 0, a, 0x10, 1, True) for a in addrs]

    assert observe._right(reads(0x200, 0x201, 0x205), 0x10, 0x200) == 0x205
    assert observe._right(reads(0x200, 0x201), 0x10, 0x200) == 0x201
    with pytest.raises(SidParseError):
        observe._right(reads(0x201, 0x202), 0x10, 0x200)


def test_the_tables_and_speed_table_tile_to_the_orderlists():
    lefts = [0x100, 0x10A, 0x10E]
    assert observe._tiling(lefts, 0x111, None, 0x114) == ([5, 2, 3], 0, 0)
    assert observe._tiling(lefts, 0x111, None, 0x116) == ([5, 2, 3], 0, 1)
    assert observe._tiling(lefts, None, 0x115, 0x11A) == ([5, 2, 3], 2, 1)
    assert observe._tiling(lefts, None, 0x10F, 0x110) == ([5, 2, 0], 0, 1)
    for args in ((0x111, None, 0x113), (0x111, None, 0x115), (None, 0x115, 0x114)):
        with pytest.raises(SidParseError):
            observe._tiling(lefts, *args)
    with pytest.raises(SidParseError):
        observe._tiling([0x100, 0x10B], 0x10C, None, 0x120)
    assert observe._tiling([0x100, 0x10A], None, None, 0x112) == ([5, 4], 0, 0)


def test_speed_row_1_is_a_left_and_right_read_ending_the_region():
    def reads(*addrs):
        return [observe.Read(0, 0, a, 0x10, 1, True) for a in addrs]

    assert observe._speed(reads(0x111, 0x115), {0x10}, 0x118) == 0x111
    assert observe._speed(reads(0x115), {0x10}, 0x118) is None


@pytest.mark.parametrize(
    "name,cols,lens",
    [
        ("gt2_minimal", ("ad", "sr", "wave", "gate", "first"), (2, 0, 0, 0, 0)),
        (
            "gt2_full",
            ("ad", "sr", "wave", "pulse", "filt", "vib", "vibdelay", "gate", "first"),
            (5, 3, 4, 2, 1),
        ),
    ],
)
def test_greloc_s_layout_is_what_the_player_reads(name, cols, lens):
    d = sid.decompile_sid((DATA / f"{name}.sid").read_bytes())
    lay = d.info.layout
    assert lay.cols == cols
    assert (lay.wlen, lay.plen, lay.flen, lay.slen, lay.zeros) == lens
    assert lay.songtbl_hi == lay.songtbl + 3 * lay.songs
    assert lay.patt_hi == lay.patt_lo + len(lay.patt_addrs)
    assert d.repack() == []


def test_the_player_s_own_song_is_the_one_its_init_selects():
    raw = (DATA / "gt2_full.sid").read_bytes()
    assert [sid.decompile_sid(raw, s).subtune for s in (0, 1)] == [0, 1]


_VARIANTS = {
    # high song/pattern columns first; SR, vibrato, wave, pulse, filter, AD, gate.
    "MUSICIANS/X/Xiny6581/Z303R.sid": (
        "sr",
        "vib",
        "vibdelay",
        "wave",
        "pulse",
        "filt",
        "ad",
        "gate",
        "first",
    ),
    # the vibrato delay apart from its parameter.
    "MUSICIANS/D/Dalezy/Mannschaftsairbus.sid": (
        "sr",
        "ad",
        "vib",
        "wave",
        "pulse",
        "filt",
        "vibdelay",
    ),
    # tables the executed-row tiling could not segment.
    "DEMOS/A-F/Aristillus.sid": None,
    # a song table its init writes.
    "DEMOS/UNKNOWN/Joelbitar.sid": None,
}
# Orderlists and empty patterns outside the image, in the power-on RAM there.
_OUTSIDE = {"GAMES/A-F/Deathflood.sid": 0xC3A2, "DEMOS/A-F/BM1K.sid": 0x3206}


@pytest.mark.parametrize("rel,cols", _VARIANTS.items())
def test_a_packer_variant_reads_as_its_player_reads_it(rel, cols):
    path = resolve_tune(rel, cache_dir=HVSC_CACHE)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    d = sid.decompile_sid(path.read_bytes())
    assert d.repack() == []
    if cols is not None:
        assert d.info.layout.cols == cols


@pytest.mark.parametrize("rel,addr", _OUTSIDE.items())
def test_song_data_past_the_image_is_read_where_the_player_reads_it(rel, addr):
    path = resolve_tune(rel, cache_dir=HVSC_CACHE)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    d = sid.decompile_sid(path.read_bytes())
    lay = d.info.layout
    assert d.image.end <= addr and addr in lay.order_addrs + lay.patt_addrs


def test_a_goattracker_2_0_player_is_named():
    rel = "MUSICIANS/R/Rusty46/Shadowboxin.sid"
    path = resolve_tune(rel, cache_dir=HVSC_CACHE)
    if path is None:
        pytest.skip(f"{rel} unavailable (local or mirror)")
    with pytest.raises(SidParseError, match="2.0x"):
        sid.decompile_sid(path.read_bytes())
