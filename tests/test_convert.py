"""GoatTracker to NinjaTracker 2 conversion."""

import pytest

from pygoattracker import ninja
from pygoattracker.convert import gt_to_nt2
from pygoattracker.errors import ConversionError
from pygoattracker.model import (
    Orderlist,
    Pattern,
    PlayPattern,
    Repeat,
    Row,
    Transpose,
)
from pygoattracker.ninja import build_nt2, parse_nt2
from pygoattracker.reader import parse_sng

from tests._fixture_cache import example_song
from tests.conftest import add_test_instrument, basic_song, note


def test_basic_song():
    result = gt_to_nt2(basic_song())
    assert result.first_wave == 0x09
    assert len(result.commands) == 1
    command = result.commands[0]
    assert command.attack_decay == 0x09
    assert command.name == "TEST01"
    # The instrument's wavetable program: pulse waveform, then stop.
    assert result.wavetable.left == [0x41, 0xFF]
    assert result.wavetable.right == [0x00, 0x00]
    # One deduplicated pattern shared by all three channels.
    assert len(result.patterns) == 1
    rows = result.patterns[0].rows
    assert len(rows) == 1
    # C-4, command 1, eight tempo-6 rows merged into 48 frames.
    assert rows[0] == ninja.NinjaRow(note=48, command=1, duration=48)
    for subtune in result.subtunes:
        for track in subtune.channels:
            assert track.entries == [PlayPattern(1)]
    assert parse_nt2(build_nt2(result)) == result


def test_long_hold_chunks():
    song = basic_song(rows={0: Row(note=note("C-4"), instrument=1)}, length=30)
    result = gt_to_nt2(song)
    rows = result.patterns[0].rows
    # 30 rows x 6 frames = 180 = 66 + 66 + 48, second 66 inherited.
    assert [(row.note, row.duration) for row in rows] == [
        (48, 66),
        (0, 0),
        (0, 48),
    ]


def test_set_tempo_durations():
    song = basic_song(
        rows={
            row: Row(note=note("C-4"), instrument=1, command=0xF, data=4)
            for row in range(8)
        }
    )
    rows = gt_to_nt2(song).patterns[0].rows
    assert all(row.note == 48 for row in rows)
    assert rows[0].duration == 4
    assert all(row.duration == 0 for row in rows[1:])  # inherited


def test_funktempo_durations():
    rows = {row: Row(note=note("C-4"), instrument=1) for row in range(8)}
    rows[0] = Row(note=note("C-4"), instrument=1, command=0xE, data=1)
    song = basic_song(rows=rows)
    song.speedtable.add(9, 6)
    durations = [row.duration for row in gt_to_nt2(song).patterns[0].rows]
    assert durations == [9, 6, 9, 6, 9, 6, 9, 6]


def test_toneportamento_and_tie():
    song = basic_song()
    idx = song.speedtable.add(0x02, 0x00)
    song.patterns[0].rows[2] = Row(note=note("E-4"), command=0x3, data=idx)
    song.patterns[0].rows[4] = Row(note=note("G-4"), command=0x3, data=0)
    result = gt_to_nt2(song)
    rows = result.patterns[0].rows
    assert rows[1].note == 52
    assert rows[1].command & 0x80  # slide commands run in legato mode
    slide = result.commands[(rows[1].command & 0x7F) - 1]
    assert slide.name == "slid0200"
    assert result.wavetable.left[slide.wave_ptr - 1] == 0xE2
    assert result.wavetable.right[slide.wave_ptr - 1] == 0x00
    # Tie note: instant slide.
    tie = result.commands[(rows[2].command & 0x7F) - 1]
    assert tie.name == "slid1EFF"


def test_vibrato_rows_inherit():
    song = basic_song()
    idx = song.speedtable.add(3, 0x40)
    for row in range(2, 6):
        song.patterns[0].rows[row] = Row(command=0x4, data=idx)
    result = gt_to_nt2(song)
    rows = result.patterns[0].rows
    # One vibrato command row; later identical rows merge into it.
    vibrato_rows = [row for row in rows if row.command]
    assert len(vibrato_rows) == 2  # the note plus one vibrato row
    vib = result.commands[(vibrato_rows[1].command & 0x7F) - 1]
    assert vib.name == "vib 01"
    left = result.wavetable.left[vib.wave_ptr - 1]
    right = result.wavetable.right[vib.wave_ptr - 1]
    assert left == 0xC0 + 3
    assert right == 0x40


def test_vibrato_on_note_appends_to_instrument():
    song = basic_song()
    idx = song.speedtable.add(3, 0x40)
    song.patterns[0].rows[0].command = 0x4
    song.patterns[0].rows[0].data = idx
    result = gt_to_nt2(song)
    command = result.commands[(result.patterns[0].rows[0].command & 0x7F) - 1]
    program = result.wavetable.left[command.wave_ptr - 1 :]
    assert program[0] == 0x41
    assert program[1] == 0xC3  # vibrato step follows the waveform


def test_instrument_vibrato_with_delay():
    song = basic_song()
    idx = song.speedtable.add(2, 0x21)
    song.instruments[0].vibrato_param = idx
    song.instruments[0].vibrato_delay = 10
    result = gt_to_nt2(song)
    assert result.wavetable.left == [0x41, 0x90 + 10, 0xC2, 0xFF]
    assert result.wavetable.right == [0x00, 0x00, 0x21, 0x00]


def test_adsr_commands_track_state():
    song = basic_song()
    song.patterns[0].rows[2] = Row(command=0x6, data=0x5A)
    song.patterns[0].rows[4] = Row(command=0x5, data=0x22)
    result = gt_to_nt2(song)
    names = [command.name for command in result.commands]
    assert "adsr095A" in names
    assert "adsr225A" in names


def test_pointer_commands():
    song = basic_song()
    ptr = song.wavetable.add(0x11, 0x00)
    song.wavetable.add(0xFF, 0x00)
    song.patterns[0].rows[2] = Row(command=0x8, data=ptr)
    song.pulsetable.left = [0x88, 0xFF]
    song.pulsetable.right = [0x00, 0x00]
    song.patterns[0].rows[4] = Row(command=0x9, data=1)
    result = gt_to_nt2(song)
    rows = result.patterns[0].rows
    wave_cmd = result.commands[(rows[1].command & 0x7F) - 1]
    assert rows[1].command & 0x80
    assert wave_cmd.name == "wave>03"
    assert result.wavetable.left[wave_cmd.wave_ptr - 1] == 0x11
    pulse_cmd = result.commands[(rows[2].command & 0x7F) - 1]
    assert pulse_cmd.name == "pulse>01"
    # GT set $800 quantizes to NT's mirrored register ($808).
    assert result.pulsetable.left[pulse_cmd.pulse_ptr - 1] == 0x80
    assert result.pulsetable.right[pulse_cmd.pulse_ptr - 1] == 0x08


def test_filter_program_conversion():
    song = basic_song()
    song.filtertable.left = [0x90, 0x00, 0x20, 0xFF]
    song.filtertable.right = [0xF1, 0x40, 0x01, 0x03]
    song.instruments[0].filter_ptr = 1
    result = gt_to_nt2(song)
    command = result.commands[0]
    base = command.filter_ptr - 1
    # Set+cutoff merge: lowpass -> nybble 9, channel mask 1, cutoff $40.
    assert result.filtertable.left[base] == 0x91
    assert result.filtertable.right[base] == 0x40
    assert result.filtertable.left[base + 1] == 0x20
    # The loop jump lands on the modulation row, in absolute terms.
    assert result.filtertable.left[base + 2] == 0xFF
    assert result.filtertable.right[base + 2] == base + 2


def test_legato_instrument():
    song = basic_song()
    legato = add_test_instrument(song, gateoff_timer=0x42, first_wave=0)
    song.patterns[0].rows[4] = Row(note=note("E-4"), instrument=legato)
    result = gt_to_nt2(song)
    rows = result.patterns[0].rows
    assert rows[1].note == 52
    assert rows[1].command & 0x80


def test_repeat_transpose_restart():
    song = basic_song(rows={0: Row(note=note("C-4"), instrument=1)}, length=2)
    song.patterns.append(Pattern(rows=[Row(note=note("C-5"), instrument=1)] * 2))
    song.subtunes[0].channels[0] = Orderlist(
        entries=[Transpose(2), Repeat(1), PlayPattern(0), PlayPattern(1)],
        restart=2,
    )
    result = gt_to_nt2(song)
    track = result.subtunes[0].channels[0]
    assert track.entries[0] == Transpose(2)
    # The second play inherits the command, so it gets its own pattern.
    assert [entry.num for entry in track.entries[1:]] == [1, 2, 3]
    assert result.patterns[0].rows[0].command == 1
    assert result.patterns[1].rows[0] == ninja.NinjaRow(note=48, duration=12)
    assert result.patterns[2].rows[0].note == 60
    # The repeat is not re-armed on loop: restart at the second copy.
    assert track.restart == 2


def test_unstable_loop_tempo():
    song = basic_song(rows={7: Row(command=0xF, data=4)})
    with pytest.raises(ConversionError, match="loops"):
        gt_to_nt2(song)
    report: list = []
    gt_to_nt2(song, errors="drop", report=report)
    assert any("loops" in message for message in report)


def test_song_must_loop():
    song = basic_song()
    song.subtunes[0].channels[0].entries = [Transpose(0), PlayPattern(0)]
    song.subtunes[0].channels[0].restart = 0
    # Restarting at the transpose is fine; restarting past the end is
    # modeled as a stop, which cannot convert.
    gt_to_nt2(song)


@pytest.mark.parametrize(
    "command,data,match",
    [
        (0x1, 1, "portamento"),
        (0x2, 1, "portamento"),
        (0x7, 0x21, "7XY"),
        (0xB, 0xF1, "BXY"),
        (0xC, 0x40, "CXY"),
        (0xD, 0x05, "master volume"),
    ],
)
def test_unsupported_commands(command, data, match):
    song = basic_song()
    if command in (0x1, 0x2):
        song.speedtable.add(0, 0x20)
    song.patterns[0].rows[2] = Row(command=command, data=data)
    with pytest.raises(ConversionError, match=match):
        gt_to_nt2(song)
    report: list = []
    result = gt_to_nt2(song, errors="drop", report=report)
    assert any(match in message for message in report)
    assert parse_nt2(build_nt2(result)) == result


def test_note_below_c1():
    song = basic_song(rows={0: Row(note=note("C-0"), instrument=1)})
    with pytest.raises(ConversionError, match="C-1"):
        gt_to_nt2(song)
    result = gt_to_nt2(song, errors="drop")
    assert result.patterns[0].rows[0].note == 12


def test_wavetable_command_execution_unsupported():
    song = basic_song()
    song.wavetable.left = [0x41, 0xF6, 0xFF]
    song.wavetable.right = [0x00, 0x2A, 0x00]
    with pytest.raises(ConversionError, match="wavetable command"):
        gt_to_nt2(song)


def test_bad_errors_value():
    with pytest.raises(ValueError):
        gt_to_nt2(basic_song(), errors="ignore")


@pytest.mark.parametrize("name", ["consultant.sng", "funktest.sng"])
def test_real_songs_convert_leniently(name):
    song = parse_sng(example_song(name))
    report: list = []
    result = gt_to_nt2(song, errors="drop", report=report)
    assert result.patterns
    assert result.commands
    assert parse_nt2(build_nt2(result)) == result
