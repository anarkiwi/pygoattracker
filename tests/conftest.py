"""Shared helpers for building small test songs."""

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import pytest  # noqa: E402  (after the BLAS thread caps)


from pygoattracker import constants  # noqa: E402
from pygoattracker.model import Instrument, Pattern, Row, Song  # noqa: E402


def note(name: str) -> int:
    """Pattern note byte for a note name like ``C-4``."""
    return constants.note_value(name)


def pass_frames(player) -> int:
    """Play calls of one whole pass of ``player``'s song: through the call in which
    its last channel loops (or ends) its orderlist, or until the playroutine stops.

    ``player`` is a fresh :class:`~pygoattracker.player.Player` or
    :class:`~pygoattracker.gt1player.Gt1Player`; it is played through.
    """
    frames = 0
    while not frames or getattr(player, "playing", True):
        counts = list(getattr(player.loops, "values", lambda: player.loops)())
        if counts and all(counts):
            break
        player.play_frame()
        frames += 1
    return frames


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


@pytest.fixture(autouse=True, scope="session")
def _gt1_player_code():
    """Register every GoatTracker V1.x build's code, assembled or extracted at test time."""
    from tests import _gt1_code  # pylint: disable=import-outside-toplevel

    _gt1_code.register_all()
