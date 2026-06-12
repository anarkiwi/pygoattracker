"""Real GoatTracker 2 example songs: parse, round-trip, and play."""

import pytest

from pygoattracker import constants
from pygoattracker.player import Player
from pygoattracker.reader import parse_sng
from pygoattracker.writer import build_sng

from tests._fixture_cache import EXAMPLE_SONGS, example_song

SONGS = sorted(EXAMPLE_SONGS)


@pytest.mark.parametrize("name", SONGS)
def test_byte_identical_round_trip(name):
    data = example_song(name)
    song = parse_sng(data)
    assert build_sng(song) == data


def test_consultant_metadata():
    song = parse_sng(example_song("consultant.sng"))
    assert song.name == "The Consultant"
    assert "Cadaver" in song.author
    assert len(song.subtunes) >= 1
    assert song.instruments
    assert song.patterns


@pytest.mark.parametrize("name", SONGS)
def test_plays_500_frames(name):
    song = parse_sng(example_song(name))
    player = Player(song)
    control_values = set()
    for _ in range(500):
        for reg, value in player.play_frame():
            if reg % constants.VOICE_REG_SIZE == constants.CONTROL_REG:
                control_values.add(value)
    assert player.playing, f"{name} stopped unexpectedly"
    # Real songs gate voices on with real waveforms.
    assert any(value & 0x01 for value in control_values)
    assert any(value >= 0x10 for value in control_values)
