"""greloc's packed data as the typed model and back, and each player's code span.

Hermetic: span images are player.s fragments at fixed addresses; data images are
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

TEMPO = "A9 05 9D 10 20 A9 01 9D 11 20 9D 12 20"


def test_each_player_in_a_multi_player_image_has_its_own_span():
    m = bytearray(0x10000)
    for base in (0x1000, 0x3000):
        play = base + 0x10
        m[base : base + 6] = bytes((0x4C, 0, base >> 8, 0x4C, play & 0xFF, play >> 8))
        m[base + 0x40 : base + 0x4D] = bytes.fromhex(TEMPO)
    im = SidImage(m, 0, len(m), None, bytes(m), b"")
    assert P.player_span(im, 0x1400) == P.PlayerCode(im, 0x1000, 0x1400)
    assert P.player_span(im, 0x3400) == P.PlayerCode(im, 0x3000, 0x3400)
    assert P.player_span(im, 0x0F00) == P.PlayerCode(im)
    m[0x1003] = 0xEA
    assert P.player_span(im, 0x1400) == P.PlayerCode(im)


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
    assert (ins.first_wave, ins.hard_restart, ins.gate_timer) == (None, True, 2)
    assert d.typed.patterns[0][0] == T.Row(T.Note(C.note_value("C-4") - C.FIRSTNOTE), 1)
    held = bytearray(d.memory)
    held[d.info.layout.tstart] ^= 0x01
    d.memory = bytes(held)
    assert d.repack()[0][0] == d.info.layout.tstart


def test_the_tuning_is_the_frequencies_and_the_player_reads_past_it():
    info = sid.decompile_sid(_minimal_packed()).info
    assert len(info.freq_table) == info.lastnote - info.firstnote + 1 == 13
    assert len(info.note_read) == 128
    span = slice(info.firstnote, info.lastnote + 1)
    assert info.note_read[span] == info.freq_table


def test_the_player_plays_the_typed_song_as_it_plays_the_song():
    d = sid.decompile_sid(_minimal_packed())
    a = Player(d.typed, freq_table=d.info.note_read).render_grid(20)
    b = Player(d.song, freq_table=d.info.note_read).render_grid(20)
    assert a == b


def test_a_pattern_reads_one_row_before_its_end_mark():
    # mt_rest checks for the end mark only after a row: a leading 0 is an
    # instrument byte.
    rows, end = P.pattern_rows(bytes([0x00, 0x40, 0xFF, 0xFF, 0x00]), 0)
    assert end == 5
    assert rows == (
        T.Row(T.KeyOn(), 0, T.DoNothing()),
        T.Row(T.Rest(), None, None, 1),
    )


def test_a_build_showing_no_define_resolves_to_the_editor_s():
    assert P.PlayerBuild().resolve() == P.Build()
    assert P.PlayerBuild().resolve(simplepulse=True, init_regs=((1, 2),)) == P.Build(
        simplepulse=True, init_regs=((1, 2),)
    )


def test_a_build_s_defines_resolve_to_its_playback_options():
    build = P.PlayerBuild(
        adparam=0x0F,
        srparam=0x01,
        pulseopt=False,
        pulsestep=4,
        realtime=False,
        deftempo=7,
        wrapper=True,
    )
    assert build.resolve(live_vibrato=True) == P.Build(
        adparam=0x0F01,
        optimize_pulse=False,
        optimize_realtime=False,
        pulse_step=4,
        live_vibrato=True,
        default_tempo=7,
        wrapper_flush=True,
    )
    assert (
        P.PlayerBuild(pulseopt=True, realtime=True, pulsestep=0).resolve() == P.Build()
    )


def test_channel_count_reads_the_clear_its_init_runs():
    mem = bytearray(0x40)
    mem[0x10:0x19] = bytes.fromhex("8aa2539d0010ca10fa")
    assert P.channel_count(mem, {0x10}) == 6
    assert P.channel_count(mem, {0x11}) == C.MAX_CHN
    mem[0x12] = 0x1B
    assert P.channel_count(mem, {0x10}) == 2
    mem[0x12] = 0x20
    assert P.channel_count(mem, {0x10}) == C.MAX_CHN
    assert (P.stride(2), P.stride(6)) == (C.MAX_CHN, 6)


def test_voices_resolve_from_the_channels_shown():
    assert P.PlayerBuild().resolve().voices is None
    assert P.PlayerBuild(channels=2).resolve().voices == (0, 7)
    assert P.PlayerBuild(channels=4).resolve().voices == (0, 7, 14, 0x20)
    assert P.PlayerBuild(channels=2, voices=(14, None)).resolve().voices == (14, None)
