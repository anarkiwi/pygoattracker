"""The typed vocabulary: every byte decodes to an operation, and every operation
re-encodes to the byte it came from. Exhaustive over each column's byte space."""

import itertools

import pytest

from pygoattracker import constants as C
from pygoattracker import typed as T
from pygoattracker.model import Instrument as RawInstrument
from pygoattracker.model import Orderlist, Pattern, PlayPattern, Repeat, Row, Song
from pygoattracker.model import Subtune, Table, Transpose

ALL = range(256)


def _kinds(values):
    return {type(v) for v in values}


def test_every_command_byte_pair_is_a_typed_command_and_back():
    seen = set()
    for num, data in itertools.product(range(16), ALL):
        cmd = T.command_of(num, data)
        assert T.command_bytes(cmd) == (num, data)
        seen.add(type(cmd))
    assert seen == set(T.Command.__args__)


@pytest.mark.parametrize(
    "num,data,want",
    [
        (0x1, 0x03, T.PortamentoUp(3)),
        (0x2, 0x04, T.PortamentoDown(4)),
        (0x3, 0x00, T.TonePortamento(0)),
        (0x4, 0x02, T.Vibrato(2)),
        (0x5, 0x0A, T.SetAD(0x0A)),
        (0x6, 0xF8, T.SetSR(0xF8)),
        (0x7, 0x41, T.SetWave(0x41)),
        (0x8, 0x10, T.SetWavePtr(0x10)),
        (0x9, 0x00, T.SetPulsePtr(0)),
        (0xA, 0x05, T.SetFilterPtr(5)),
        (0xB, 0xF3, T.SetFilterCtrl(0xF, 0x3)),
        (0xC, 0x60, T.SetCutoff(0x60)),
        (0xD, 0x0C, T.SetMasterVolume(0x0C)),
        (0xD, 0x1C, T.TimingMark(0x1C)),
        (0xE, 0x07, T.FunkTempo(7)),
        (0xF, 0x06, T.SetTempo(6)),
        (0xF, 0x86, T.SetTempo(6, channel=True)),
        (0x0, 0x33, T.DoNothing(0x33)),
    ],
)
def test_each_command_means_what_the_readme_says(num, data, want):
    assert T.command_of(num, data) == want


def test_every_note_column_byte():
    notes = [T.note_of(b) for b in range(C.FIRSTNOTE, C.KEYON + 1)]
    assert [T.note_byte(n) for n in notes] == list(range(C.FIRSTNOTE, C.KEYON + 1))
    assert notes[0] == T.Note(0) and notes[-3:] == [T.Rest(), T.KeyOff(), T.KeyOn()]


@pytest.mark.parametrize(
    "decode,encode,kinds",
    [
        (T.wave_row, T.wave_bytes, {T.WaveStep, T.WaveCommand, T.Jump, T.Stop}),
        (T.pulse_row, T.pulse_bytes, {T.PulseSet, T.PulseMod, T.Jump, T.Stop}),
        (
            lambda a, b: T.pulse_row(a, b, simple=True),
            T.pulse_bytes,
            {T.SimplePulseSet, T.SimplePulseMod, T.Jump, T.Stop},
        ),
        (
            T.filter_row,
            T.filter_bytes,
            {T.FilterSet, T.FilterMod, T.CutoffSet, T.Jump, T.Stop},
        ),
        (T.speed_row, T.speed_bytes, {T.FixedSpeed, T.CalculatedSpeed}),
    ],
)
def test_every_table_row_is_an_operation_and_back(decode, encode, kinds):
    rows = [decode(a, b) for a, b in itertools.product(ALL, ALL)]
    assert [encode(r) for r in rows] == list(itertools.product(ALL, ALL))
    assert _kinds(rows) == kinds


def test_a_wavetable_row_states_its_waveform_delay_and_note():
    assert T.wave_row(0x41, 0x80) == T.WaveStep(T.SetWave(0x41), None)
    assert T.wave_row(0xE1, 0x80) == T.WaveStep(T.SetWave(0x01), None)
    assert T.wave_row(0x05, 0x0C) == T.WaveStep(T.Delay(5), T.WaveNote(12, True))
    assert T.wave_row(0x00, 0x7F) == T.WaveStep(None, T.WaveNote(-1, True))
    assert T.wave_row(0x11, 0x8C) == T.WaveStep(T.SetWave(0x11), T.WaveNote(12, False))
    assert T.wave_row(0xF4, 0x02) == T.WaveCommand(T.Vibrato(2))
    assert T.wave_row(0xFF, 0x00) == T.Stop() and T.wave_row(0xFF, 3) == T.Jump(3)


def test_pulse_filter_and_speed_rows_read_as_their_operations():
    assert T.pulse_row(0x88, 0x00) == T.PulseSet(0x800)
    assert T.pulse_row(0x20, 0xF0) == T.PulseMod(0x20, -16)
    assert T.pulse_row(0x80, 0x48, simple=True) == T.SimplePulseSet(0x48)
    assert T.filter_row(0x90, 0xF1) == T.FilterSet(0x10, 0xF, 0x1)
    assert T.filter_row(0x04, 0xFE) == T.FilterMod(4, -2)
    assert T.filter_row(0x00, 0x60) == T.CutoffSet(0x60)
    fixed, calc = T.speed_row(0x02, 0x30), T.speed_row(0x83, 0x04)
    assert (fixed.value, fixed.rate, fixed.depth) == (0x230, 2, 0x30)
    assert calc == T.CalculatedSpeed(3, 4)


def test_every_instrument_gate_timer_and_first_wave():
    for gt, fw in itertools.product(ALL, ALL):
        raw = RawInstrument(gateoff_timer=gt, first_wave=fw, name="x")
        assert T.Instrument.of(raw).raw() == raw
    ins = T.Instrument.of(RawInstrument(gateoff_timer=0xC2, first_wave=0xFE))
    assert (ins.gate_timer, ins.hard_restart, ins.legato) == (2, False, True)
    assert ins.first_wave == T.GateOff()
    assert T.Instrument.of(RawInstrument(first_wave=0xFF)).first_wave == T.GateOn()
    assert T.Instrument.of(RawInstrument(first_wave=0x41)).first_wave == T.SetWave(0x41)
    assert T.Instrument.of(RawInstrument()).first_wave is None


def _song():
    rows = [Row(0x70, 1, 3, 1), Row(C.REST, 0, 3, 1), Row(C.KEYOFF, 0, 0xD, 0x1F)]
    return Song(
        name="typed",
        subtunes=[
            Subtune([Orderlist([Transpose(-2), Repeat(1), PlayPattern(0)], 1)] * 3)
        ],
        instruments=[RawInstrument(1, 2, 1, 0, 0, 1, 3, 0x82, 0x41, "i1")],
        patterns=[Pattern(rows)],
        wavetable=Table([0x41, 0xFF], [0x80, 0]),
        pulsetable=Table([0x88, 0x20, 0xFF], [0, 0x10, 2]),
        filtertable=Table([0x90, 0, 0xFF], [0xF1, 0x60, 0]),
        speedtable=Table([0x02], [0x30]),
    )


def test_a_song_comes_back_from_its_typed_form_unchanged():
    song = _song()
    typed = T.TypedSong.from_song(song)
    assert typed.to_song() == song
    assert typed.patterns[0][0] == T.Row(T.Note(0x10), 1, T.TonePortamento(1))
    assert typed.speed(typed.patterns[0][0].command) == T.FixedSpeed(0x230)
    assert typed.speed(typed.instrument(1)) == T.FixedSpeed(0x230)
    assert typed.speed(0) is None and typed.instrument(9) == T.Instrument()


def test_a_held_command_and_a_long_rest_spread_into_editor_rows():
    rows = (
        T.Row(T.Note(1), 2, T.Vibrato(1)),
        T.Row(T.Rest(), None, None, 3),
        T.Row(T.KeyOff()),
    )
    assert T.raw_rows(rows) == [
        Row(0x61, 2, 4, 1),
        Row(C.REST, 0, 4, 1),
        Row(C.REST, 0, 4, 1),
        Row(C.REST, 0, 4, 1),
        Row(C.KEYOFF, 0, 4, 1),
    ]
