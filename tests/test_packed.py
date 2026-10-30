"""greloc's packed data as the typed model and back, and each player's code span.

Hermetic: span images are player.s fragments at fixed addresses; data images are
``test_sid``'s synthetic packed tune.
"""

import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from pysidtracker import SidImage, TunePlayer

from pygoattracker import constants as C
from pygoattracker import packed as P
from pygoattracker import sid
from pygoattracker import typed as T
from pygoattracker.model import Orderlist, PlayPattern, Repeat, Transpose
from pygoattracker.player import Player
from tests.conftest import pass_frames
from tests.test_sid import DATA, _minimal_packed

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


def _unpacked_wave_bytes(wave):
    """The packed left bytes a player of ``wave`` reads that unpack and pack to
    themselves."""
    if wave == P.WAVE_NEW:
        return list(range(256))
    # no waveform packs to $E0-$EF: silent ones are the nibble (as $08-$0F from V2.0
    # to V2.17, where only $01-$07 delay)
    return [b for b in range(256) if not 0xE0 <= b < 0xF0]


@pytest.mark.parametrize("wave", [P.WAVE_NEW, P.WAVE_NONE, P.WAVE_OLD])
def test_the_packed_wavetable_byte_is_greloc_s(wave):
    for left, right in itertools.product(_unpacked_wave_bytes(wave), range(256)):
        row = T.wave_row(*P._wave_editor(left, right, wave))
        assert P._wave_packed(*T.wave_bytes(row), wave) == (left, right)
    assert T.wave_row(*P._wave_editor(0x00, 0x00, P.WAVE_NONE)) == T.WaveStep(
        None, None
    )
    assert T.wave_row(*P._wave_editor(0x13, 0x00, P.WAVE_NEW)) == T.WaveStep(
        T.SetWave(3)
    )
    assert T.wave_row(*P._wave_editor(0x51, 0x0C, P.WAVE_NEW)) == T.WaveStep(
        T.SetWave(0x41), T.WaveNote(12, False)
    )


def test_a_player_before_v2_18_reads_its_wavetable_as_stored():
    # waveforms as they are, $01-$07 delays and $08-$0F the silent waveforms
    assert T.wave_row(*P._wave_editor(0x51, 0x8C, P.WAVE_OLD)) == T.WaveStep(
        T.SetWave(0x51), T.WaveNote(12, True)
    )
    assert T.wave_row(*P._wave_editor(0x03, 0x80, P.WAVE_OLD)) == T.WaveStep(
        T.Delay(3), T.WaveNote(0, True)
    )
    assert T.wave_row(*P._wave_editor(0x09, 0x80, P.WAVE_OLD)) == T.WaveStep(
        T.SetWave(0x09), T.WaveNote(0, True)
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
    frames = pass_frames(Player(d.song, freq_table=d.info.note_read))
    a = Player(d.typed, freq_table=d.info.note_read).render_grid(frames)
    b = Player(d.song, freq_table=d.info.note_read).render_grid(frames)
    assert a == b


def test_a_pattern_reads_one_row_before_its_end_mark():
    # mt_rest checks for the end mark only after a row: a leading 0 is an
    # instrument byte.
    rows, end = P.pattern_rows(bytes([0x00, 0x40, 0xFF, 0xFF, 0x00]), 0)
    assert end == 5
    assert rows == (
        T.Row(T.Gate(0xFF), 0, T.DoNothing()),
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
    )
    assert build.resolve(live_vibrato=True) == P.Build(
        adparam=0x0F01,
        optimize_pulse=False,
        optimize_realtime=False,
        pulse_step=4,
        live_vibrato=True,
        default_tempo=7,
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


def _without_pattern_effects(raw: bytes) -> bytes:
    """``raw`` with every pattern command a no-op, packed into its own layout."""
    d = sid.decompile_sid(raw)
    plain = tuple(
        tuple(
            r if r.command is None else replace(r, command=T.DoNothing())
            for r in pattern
        )
        for pattern in d.typed.patterns
    )
    out = bytearray(raw)
    start = d.header.data_start - d.header.real_load_address
    for addr, data in P.pack(replace(d.typed, patterns=plain), d.info):
        out[start + addr : start + addr + len(data)] = data
    return bytes(out)


@pytest.mark.parametrize("subtune", [0, 1])
def test_a_player_keeping_its_effect_code_plays_a_song_without_commands(subtune):
    # gt2_full's player assembles the full effect path (NOEFFECTS=0); a song
    # left with no pattern or wavetable command still latches vibrato at note-init.
    raw = _without_pattern_effects((DATA / "gt2_full.sid").read_bytes())
    d = sid.decompile_sid(raw, subtune=subtune)
    assert d.repack() == []
    frames = 120
    assert d.player().render_grid(frames) == TunePlayer(raw, subtune).render_grid(
        frames
    )


def _binary_layout(**fields):
    """A binary-relocator layout: one instrument of seven columns, a wave row, the
    song table after them."""
    base = dict(freq_addr=0, firstnote=0, notes=0, songtbl=0x60, songs=1)
    base.update(order_addrs=(0x70,) * 3, patt_lo=0x66, patt_addrs=(0x80,))
    base.update(ins_start=0x10, num_instr=1, pulse=True, filt=True, vib=True)
    base.update(gate=False, tstart=0x17, wlen=1, plen=0, flen=0, slen=0, zeros=0)
    base.update(cols=("ad", "sr", "wave", "pulse", "filt", "vibdelay", "vib"))
    base.update(fields)
    return P.PackedLayout(**base)


def test_a_layout_with_the_song_table_after_the_instruments_is_binary():
    assert _binary_layout().binary
    assert not _binary_layout(songtbl=0x04, ins_start=0x10).binary


def test_a_player_without_a_speed_table_states_its_parameters_as_speed_rows():
    mem = bytearray(0x100)
    mem[0x10:0x17] = bytes((0x09, 0xF0, 1, 0, 0, 3, 0x61))  # AD SR wave .. delay vib
    mem[0x17:0x19] = bytes((0x51, 0x8C))
    lay = _binary_layout(patt_addrs=(0x80,), inline=True)
    header = SimpleNamespace(name="", author="", released="")
    typed = P.decode(mem, lay, P.PlayerBuild(), header, [], P.WAVE_OLD, False)
    # the delay is as stored; vibrato $61 swapped is $16: depth 1, speed $60
    assert typed.instruments[0].vibrato_delay == 3
    assert typed.speedtable[typed.instruments[0].vibrato_speed - 1] == T.FixedSpeed(
        0x0160
    )
    assert typed.wavetable[0] == T.WaveStep(T.SetWave(0x51), T.WaveNote(12, True))
    assert typed.speed0 is None


def test_inline_effect_parameters_become_speedtable_rows_as_gt_2_76_makes_them():
    ins = (T.Instrument(vibrato_speed=0x61, vibrato_delay=2), T.Instrument())
    rows = (
        T.Row(T.Note(1), 1, T.PortamentoUp(0x10)),
        T.Row(T.Note(2), 1, T.Vibrato(0x61)),
        T.Row(T.Rest(), None, T.FunkTempo(0x23)),
        T.Row(T.Note(3), 1, T.TonePortamento(0)),
        T.Row(T.Rest(), None, T.SetAD(5)),
        T.Row(T.Rest()),
    )
    instruments, (patterns,), table = P._speeds(ins, (rows,))
    # vibrato $61 swaps to $16: depth 1, speed $60; portamento $10 << 2; funk +$11
    assert table == (T.FixedSpeed(0x0160), T.FixedSpeed(0x0040), T.FixedSpeed(0x0304))
    assert instruments[0].vibrato_speed == 1 and instruments[1].vibrato_speed == 0
    assert patterns[0].command == T.PortamentoUp(2)
    assert patterns[1].command == T.Vibrato(1)
    assert patterns[2].command == T.FunkTempo(3)
    assert patterns[3].command == T.TonePortamento(0)
    assert patterns[4].command == T.SetAD(5) and patterns[5].command is None


def test_a_player_without_a_speed_table_cannot_be_packed():
    d = sid.decompile_sid(_minimal_packed())
    info = replace(d.info, layout=replace(d.info.layout, inline=True))
    with pytest.raises(ValueError, match="speed table"):
        P.pack(d.typed, info)
