"""greloc's packed data as the typed model and back, and the build read from player code.

Hermetic: build images are player.s fragments at fixed addresses; data images are
``test_sid``'s synthetic packed tune.
"""

import itertools

import pytest
from pysidtracker import SidImage

from pygoattracker import constants as C
from pygoattracker import packed as P
from pygoattracker import sid
from pygoattracker import typed as T
from pygoattracker.model import Orderlist, PlayPattern, Repeat, Transpose
from pygoattracker.player import Player
from tests.test_sid import _minimal_packed

DESC = "A2 18 BD 00 20 9D 00 D4 CA 10 F7"
ASC = "A2 00 BD 00 20 9D 00 D4 E8 E0 19 D0 F5"
TEMPO = "A9 05 9D 10 20 A9 01 9D 11 20 9D 12 20"
GATECOL = "BD 11 20 DD 13 20 F0 03"
TICK034 = "9D 30 20 BD 31 20 9D 32 20 60"


def image(*frags):
    """A 64K image with each fragment in the next 0x40-byte slot from $1000."""
    m = bytearray(0x10000)
    for i, f in enumerate(frags):
        b = bytes.fromhex(f)
        m[0x1000 + 0x40 * i : 0x1000 + 0x40 * i + len(b)] = b
    return SidImage(m, 0, len(m), None, bytes(m), b"")


def hr(x, y, n=0x18, ad_first=False):
    """``lda chninstr,x; cmp #n; bcs``, the hard restart's two stores, the gate-off.

    ``x`` goes to SR (``$2021``) then ``y`` to AD (``$2020``), or AD first.
    """
    p, q = (0x20, 0x21) if ad_first else (0x21, 0x20)
    return (
        "BD 12 20 C9 %02X B0 0A A9 %02X 9D %02X 20 A9 %02X 9D %02X 20 A9 FE 9D 22 20"
        % (
            n,
            x,
            p,
            y,
            q,
        )
    )


def legato(slot, first, skiphr):
    """``cmp #FIRSTLEGATOINSTR; bcc mt_skiphr; bcs mt_rest`` in ``slot``."""
    d = (skiphr - (0x1000 + 0x40 * slot + 4)) & 0xFF
    return "C9 %02X 90 %02X B0 00" % (first, d)


def test_player_s_reads_sr_first_and_its_classes_from_where_they_branch():
    skiphr = 0x1000 + 0x40 * 2 + 17
    tick0 = "B9 40 20 4C %02X %02X" % (0x1100 & 0xFF, 0x1100 >> 8)
    frags = (DESC, TEMPO, hr(0, 0x0F), legato(3, 0x18, skiphr), TICK034, tick0, GATECOL)
    assert P.read_build(image(*frags)) == P.PlayerBuild(
        ghost=0x2000,
        alt=False,
        adparam=0x0F,
        srparam=0,
        firstnohr=0x18,
        firstlegato=0x18,
        deftempo=5,
        insvib=True,
        gatecol=True,
        fixed=None,
        pulseopt=False,
        realtime=False,
    )


def test_altplayer_loads_ad_first_and_flushes_ascending():
    b = P.read_build(image(ASC, TEMPO, hr(0xFF, 0x00, n=0x0E, ad_first=True), GATECOL))
    assert (b.alt, b.adparam, b.srparam, b.firstnohr, b.firstlegato) == (
        True,
        0xFF,
        0,
        0x0E,
        P.NONE,
    )
    assert not b.insvib


def test_the_store_targets_not_their_order_tell_ad_from_sr():
    b = P.read_build(image(TEMPO, hr(0xFF, 0x00, ad_first=True), GATECOL))
    assert (b.alt, b.adparam, b.srparam) == (None, 0xFF, 0)
    b = P.read_build(image(DESC, TEMPO, hr(0x00, 0xFF), GATECOL))
    assert (b.alt, b.adparam, b.srparam) == (False, 0xFF, 0)


def test_a_build_without_hard_restart_code_and_without_a_flush():
    b = P.read_build(image(TEMPO, GATECOL))
    assert (b.ghost, b.alt, b.adparam, b.firstnohr) == (None, None, None, 1)
    b = P.read_build(image(TEMPO, "BD 12 20 C9 03 B0 05 A9 FE 9D 22 20", GATECOL))
    assert b.firstnohr == 3


def test_the_optimizations_and_fixedparams_are_read_from_their_sites():
    realtime = "BD 11 20 F0 05 BC 40 20"
    pulse = "BC 41 20 F0 05 1D 42 20 F0"
    fixed = ("BD 11 20 C9 03 F0 05", "BD 50 20 3D 51 20 9D 52 20", "A9 41 9D 50 20")
    b = P.read_build(image(DESC, TEMPO, realtime, pulse, *fixed))
    assert (b.realtime, b.pulseopt, b.gatecol, b.fixed) == (
        True,
        True,
        False,
        (3, 0x41),
    )


def test_a_define_at_two_sites_or_no_player_states_no_build():
    assert P.read_build(image(DESC, TEMPO, TEMPO)) == P.PlayerBuild()
    assert P.read_build(image(DESC, GATECOL)) == P.PlayerBuild()


def test_a_define_at_two_sites_voids_only_that_define():
    b = P.read_build(image(TEMPO, hr(0, 0x0F), hr(0, 0x0F), TICK034, TICK034, GATECOL))
    assert (b.deftempo, b.gatecol, b.adparam, b.firstnohr, b.firstlegato, b.insvib) == (
        5,
        True,
        None,
        None,
        None,
        None,
    )
    b = P.read_build(image(TEMPO, hr(0, 0x0F), GATECOL, GATECOL))
    assert (b.adparam, b.firstnohr, b.gatecol) == (0x0F, 0x18, None)


def test_a_zero_page_ghost_build_hard_restarts_into_the_ghost():
    skiphr = 0x1000 + 0x40 * 1 + 15
    zphr = "BD 12 20 C9 18 B0 08 A9 00 95 8C A9 0F 95 8B A9 FE 9D 22 20"
    b = P.read_build(image(TEMPO, zphr, legato(2, 0x20, skiphr), GATECOL))
    assert (b.adparam, b.srparam, b.firstnohr, b.firstlegato) == (0x0F, 0, 0x18, 0x20)


def test_each_player_in_a_multi_player_image_states_its_own_build():
    m = bytearray(0x10000)
    for base, flush, tempo in ((0x1000, DESC, 0x05), (0x3000, ASC, 0x11)):
        play = base + 0x10
        m[base : base + 6] = bytes((0x4C, 0, base >> 8, 0x4C, play & 0xFF, play >> 8))
        m[play : play + 13] = bytes.fromhex(flush)
        m[base + 0x40 : base + 0x4D] = bytes.fromhex(TEMPO)
        m[base + 0x41] = tempo
    im = SidImage(m, 0, len(m), None, bytes(m), b"")
    assert P.read_build(im) == P.PlayerBuild()
    first = P.read_build(P.player_span(im, 0x1400))
    second = P.read_build(P.player_span(im, 0x3400))
    assert (first.alt, first.deftempo) == (False, 0x05)
    assert (second.alt, second.deftempo) == (True, 0x11)
    assert P.player_span(im, 0x1400) == P.PlayerCode(im, 0x1000, 0x1400)
    assert P.player_span(im, 0x0F00) == P.PlayerCode(im)
    m[0x1003] = 0xEA
    assert P.player_span(im, 0x1400) == P.PlayerCode(im)


def test_the_first_play_call_starts_from_the_image_and_the_immediates_init_keeps():
    filt = "A9 19 8D 16 20 A9 00 8D 17 20 A9 10 09 0F 8D 18 20"
    funk = "C9 02 B0 08 A8 49 01 9D 12 20 B9 50 20 E9 00"
    im = image(DESC, TEMPO, filt, funk)
    im.mem[0x2000:0x2019] = bytes(range(1, 26))
    im.mem[0x2050:0x2052] = b"\x07\x05"
    state = P.read_state(im, P.read_build(im))
    assert state == P.PlayerState(tuple(range(1, 26)), 0x19, 0x10, 0x0F, (7, 5))
    nofilter = image(DESC, TEMPO, "A9 00 8D 17 20 A9 00 09 0F 8D 18 20")
    nofilter.mem[0x2016] = 0x42
    state = P.read_state(nofilter, P.read_build(nofilter))
    assert (state.cutoff, state.funk) == (0x42, (8, 5))
    assert P.read_state(image(TEMPO), P.PlayerBuild()) is None
    assert P.read_state(image(DESC, TEMPO), P.read_build(image(DESC, TEMPO))) is None


def test_a_packed_pattern_reads_as_typed_rows_and_writes_back():
    data = bytes(
        [0x01, 0x43, 0x02, 0x70, 0xFC, 0x54, 0x05, 0x5F, 0x05, 0xBE, 0x40, 0xBF, 0]
    )
    rows, end = P.pattern_rows(data, 0)
    assert end == len(data)
    assert rows == (
        T.Row(T.Note(0x10), 1, T.TonePortamento(2)),
        T.Row(T.Rest(), None, None, 4),
        T.Row(T.Rest(), None, T.Vibrato(5)),
        T.Row(T.Rest(), None, T.SetTempo(6)),
        T.Row(T.KeyOff()),
        T.Row(T.KeyOn(), None, T.DoNothing()),
    )
    assert P.pattern_bytes(rows) == data


def test_greloc_s_tempo_offset_is_folded_into_the_tempo():
    assert P.pattern_rows(bytes([0x5F, 0x02, 0]), 0)[0][0].command == T.SetTempo(3)
    assert P.pattern_rows(bytes([0x5F, 0x81, 0]), 0)[0][0].command == T.SetTempo(
        1, True
    )
    assert P.pattern_bytes((T.Row(T.Rest(), None, T.SetTempo(3)),)) == bytes(
        [0x5F, 2, 0]
    )


def test_a_packed_orderlist_puts_a_repeat_after_its_pattern():
    ol = Orderlist([Transpose(-1), Repeat(2), PlayPattern(4), Repeat(1)], 2)
    assert P.orderlist_bytes(ol) == bytes([0xEF, 0x04, 0xD2, 0xD1, 0xFF, 0x02])


@pytest.mark.parametrize("nwd", [False, True])
def test_the_packed_wavetable_byte_is_greloc_s(nwd):
    # NOWAVEDELAY packs no waveform to $E0-$EF: silent ones are their low nibble
    lefts = [b for b in range(256) if not (nwd and 0xE0 <= b < 0xF0)]
    for left, right in itertools.product(lefts, range(256)):
        row = T.wave_row(*P._wave_editor(left, right, nwd))
        assert P._wave_packed(*T.wave_bytes(row), nwd) == (left, right)
    assert T.wave_row(*P._wave_editor(0x00, 0x00, True)) == T.WaveStep(None, None)
    assert T.wave_row(*P._wave_editor(0x13, 0x00, False)) == T.WaveStep(T.SetWave(3))
    assert T.wave_row(*P._wave_editor(0x51, 0x0C, False)) == T.WaveStep(
        T.SetWave(0x41), T.WaveNote(12, False)
    )


def test_the_packed_filter_mode_byte_is_greloc_s():
    for left, right in itertools.product(range(256), range(256)):
        assert P._filter_packed(P._filter_typed(left, right)) == (left, right)
    assert P._filter_typed(0x88, 0xF1) == T.FilterSet(0x10, 0xF, 1)


def test_a_packed_tune_re_encodes_to_its_own_bytes():
    d = sid.decompile_sid(_minimal_packed())
    assert d.repack() == []
    ins = d.typed.instrument(1)
    assert (ins.first_wave, ins.hard_restart, ins.gate_timer) == (T.SetWave(9), True, 2)
    assert d.typed.patterns[0][0] == T.Row(T.Note(C.note_value("C-4") - C.FIRSTNOTE), 1)
    d.image.mem[d.info.layout.tstart] ^= 0x01
    assert d.repack()[0][0] == d.info.layout.tstart


def test_the_tuning_is_the_frequencies_and_the_player_reads_past_it():
    info = sid.decompile_sid(_minimal_packed()).info
    assert len(info.freq_table) == info.lastnote - info.firstnote + 1 == 12
    assert len(info.note_read) == 128
    assert info.note_read[: len(info.freq_table)] == info.freq_table


def test_the_player_plays_the_typed_song_as_it_plays_the_song():
    d = sid.decompile_sid(_minimal_packed())
    a = Player(d.typed, freq_table=d.info.note_read).render_grid(20)
    b = Player(d.song, freq_table=d.info.note_read).render_grid(20)
    assert a == b
