"""Writer validation."""

import pytest

from pygoattracker import constants
from pygoattracker.errors import SngValidationError
from pygoattracker.model import (
    Instrument,
    Orderlist,
    Pattern,
    PlayPattern,
    Row,
    Song,
    Subtune,
)
from pygoattracker.writer import build_sng, write_sng

from tests.conftest import basic_song


def expect_invalid(song, match):
    with pytest.raises(SngValidationError, match=match):
        build_sng(song)


def test_no_subtunes():
    song = basic_song()
    song.subtunes = []
    expect_invalid(song, "subtunes")


def test_too_many_subtunes():
    song = basic_song()
    song.subtunes = [Subtune() for _ in range(constants.MAX_SONGS + 1)]
    expect_invalid(song, "subtunes")


def test_wrong_channel_count():
    song = basic_song()
    song.subtunes[0].channels.append(Orderlist())
    expect_invalid(song, "channels")


def test_empty_orderlist():
    song = basic_song()
    song.subtunes[0].channels[0].entries = []
    expect_invalid(song, "empty")


def test_orderlist_too_long():
    song = basic_song()
    song.subtunes[0].channels[0].entries = [PlayPattern(0)] * (
        constants.MAX_SONGLEN + 1
    )
    expect_invalid(song, "longer")


def test_restart_out_of_range():
    song = basic_song()
    song.subtunes[0].channels[0].restart = 1
    expect_invalid(song, "restart")


def test_undefined_pattern_reference():
    song = basic_song()
    song.subtunes[0].channels[0].entries = [PlayPattern(1)]
    expect_invalid(song, "pattern 1 not defined")


def test_too_many_patterns():
    song = basic_song()
    song.patterns = [Pattern.empty(1) for _ in range(constants.MAX_PATT + 1)]
    expect_invalid(song, "patterns")


def test_too_many_instruments():
    song = basic_song()
    song.instruments = [Instrument() for _ in range(constants.MAX_INSTR)]
    expect_invalid(song, "instruments")


def test_undefined_instrument_reference():
    song = basic_song()
    song.patterns[0].rows[0].instrument = 2
    expect_invalid(song, "instrument 2 not defined")


def test_instrument_byte_out_of_range():
    song = basic_song()
    song.instruments[0].attack_decay = 0x100
    expect_invalid(song, "attack_decay")


def test_instrument_pointer_beyond_table():
    song = basic_song()
    song.instruments[0].pulse_ptr = 1
    expect_invalid(song, "pulse_ptr")


def test_instrument_name_too_long():
    song = basic_song()
    song.instruments[0].name = "X" * constants.MAX_INSTRNAMELEN
    expect_invalid(song, "name")


def test_song_name_too_long():
    song = basic_song()
    song.name = "X" * constants.MAX_STR
    expect_invalid(song, "song name")


def test_song_name_not_latin1():
    song = basic_song()
    song.author = "ゴート"
    expect_invalid(song, "latin-1")


def test_unbalanced_table():
    song = basic_song()
    song.wavetable.right.pop()
    expect_invalid(song, "lengths differ")


def test_table_too_long():
    song = basic_song()
    song.speedtable.left = [0] * (constants.MAX_TABLELEN + 1)
    song.speedtable.right = [0] * (constants.MAX_TABLELEN + 1)
    expect_invalid(song, "longer")


def test_table_value_out_of_range():
    song = basic_song()
    song.wavetable.left[0] = -1
    expect_invalid(song, "table 0")


def test_pattern_too_long():
    song = basic_song()
    song.patterns[0] = Pattern.empty(constants.MAX_PATTROWS + 1)
    expect_invalid(song, "rows")


def test_pattern_empty():
    song = basic_song()
    song.patterns[0] = Pattern(rows=[])
    expect_invalid(song, "rows")


def test_bad_note():
    song = basic_song()
    song.patterns[0].rows[1] = Row(note=0x10)
    expect_invalid(song, "bad note")


def test_bad_command():
    song = basic_song()
    song.patterns[0].rows[1] = Row(command=0x10)
    expect_invalid(song, "bad command")


def test_bad_command_data():
    song = basic_song()
    song.patterns[0].rows[1] = Row(data=0x100)
    expect_invalid(song, "command data")


def test_bad_row_instrument():
    song = basic_song()
    song.patterns[0].rows[1] = Row(instrument=constants.MAX_INSTR)
    expect_invalid(song, "bad instrument")


def test_write_rejects_unknown_type():
    with pytest.raises(TypeError):
        write_sng(Song(), 12345)
