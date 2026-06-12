"""Shared helpers for building small test songs."""

import pytest

from pygoattracker import constants
from pygoattracker.model import Instrument, Pattern, Row, Song


def note(name: str) -> int:
    """Pattern note byte for a note name like ``C-4``."""
    return constants.note_value(name)


def add_test_instrument(
    song: Song,
    waveform: int = 0x41,
    attack_decay: int = 0x09,
    sustain_release: int = 0x00,
    gateoff_timer: int = 2,
    first_wave: int = 0x09,
    **fields,
) -> int:
    """Add a one-waveform instrument; returns its instrument number."""
    wave_ptr = song.wavetable.add(waveform, 0x00)
    song.wavetable.add(constants.TABLEJUMP, 0x00)
    song.instruments.append(
        Instrument(
            attack_decay=attack_decay,
            sustain_release=sustain_release,
            wave_ptr=wave_ptr,
            gateoff_timer=gateoff_timer,
            first_wave=first_wave,
            name=f"TEST{len(song.instruments) + 1:02X}",
            **fields,
        )
    )
    return len(song.instruments)


def basic_song(rows=None, length: int = 8) -> Song:
    """One-subtune song with one instrument and one pattern.

    ``rows`` maps row number to :class:`Row`; other rows are rests.
    """
    song = Song(name="TEST", author="PYGOATTRACKER", copyright="2026")
    instrument = add_test_instrument(song)
    pattern = Pattern.empty(length)
    if rows is None:
        rows = {0: Row(note=note("C-4"), instrument=instrument)}
    for row_num, row in rows.items():
        pattern.rows[row_num] = row
    song.patterns = [pattern]
    return song


@pytest.fixture
def song():
    """A basic one-note test song."""
    return basic_song()
