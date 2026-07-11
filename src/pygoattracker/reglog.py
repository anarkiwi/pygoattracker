"""SID register write logs for GoatTracker songs.

:class:`RegWrite`, :func:`read_reglog`, :func:`write_reglog` and the framing
loop are the shared :mod:`pysidtracker.reglog` primitives (re-exported here);
:data:`REGLOG_HEADER` is this package's own header line and
:func:`iter_register_writes` frames a :class:`~pygoattracker.player.Player`
(a :class:`pysidtracker.MemPlayer`) into that log via the base
:func:`pysidtracker.reglog.register_writes_from_player`.
"""

from typing import Iterator

from pysidtracker.errors import SidParseError
from pysidtracker.reglog import (
    DEFAULT_WRITE_SPACING,
    RegWrite,
    read_reglog,
    register_writes_from_player,
    write_reglog,
)

from pygoattracker import constants
from pygoattracker.errors import GoatTrackerError
from pygoattracker.model import Song
from pygoattracker.player import Player

REGLOG_HEADER = "# pygoattracker register log: clock reg val"

__all__ = [
    "DEFAULT_WRITE_SPACING",
    "REGLOG_HEADER",
    "RegWrite",
    "iter_register_writes",
    "read_reglog",
    "write_reglog",
]


def _frame_count(song: Song, subtune: int, max_frames, until_loop: bool, **opts) -> int:
    """Frames to log, honoring ``until_loop`` and the song's natural stop."""
    player = Player(song, subtune=subtune, **opts)
    count = 0
    while max_frames is None or count < max_frames:
        if until_loop and min(player.loops) > 0:
            break
        writes = player.play_frame()
        if not player.playing and not writes:
            break
        count += 1
    return count


def iter_register_writes(
    song: Song,
    subtune: int = 0,
    max_frames: int | None = None,
    until_loop: bool = False,
    cycles_per_frame: int = constants.PAL_CYCLES_PER_FRAME,
    write_spacing: int = DEFAULT_WRITE_SPACING,
    **player_options,
) -> Iterator[RegWrite]:
    """Yield :class:`RegWrite` for ``song``, frame by frame.

    The player's post-init register file is emitted at clock 0 and each frame's
    changed registers ``write_spacing`` cycles apart, frames ``cycles_per_frame``
    apart -- the base :func:`~pysidtracker.reglog.register_writes_from_player`
    framing. ``until_loop`` or an open-ended ``max_frames`` first measure the
    frame count (honoring the song's natural stop).
    """
    if until_loop or max_frames is None:
        max_frames = _frame_count(
            song, subtune, max_frames, until_loop, **player_options
        )
    player = Player(song, subtune=subtune, **player_options)
    try:
        yield from register_writes_from_player(
            player, max_frames, cycles_per_frame, write_spacing
        )
    except SidParseError as exc:
        raise GoatTrackerError(str(exc)) from exc
